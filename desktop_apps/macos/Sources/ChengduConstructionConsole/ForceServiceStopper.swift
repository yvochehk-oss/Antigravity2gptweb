import Foundation
import Darwin

/// Explicit force-stop policy: configured service ports and PID records define
/// the target set. Command/cwd ownership is intentionally not required.
enum ForceServiceStopper {
    struct Result {
        let succeeded: Bool
        let message: String
    }

    /// Stop all configured service ports (tax, rag, idp, localModel, boss).
    static func stop(configuration: ProjectConfiguration) -> Result {
        return stop(configuration: configuration, portsFilter: nil)
    }

    /// Stop only the Boss service port.  Used before launching Boss to prevent
    /// port conflicts without affecting other running business services.
    static func stopBossOnly(configuration: ProjectConfiguration) -> Result {
        return stop(configuration: configuration, portsFilter: [configuration.bossPort])
    }

    /// Stop service ports.  When portsFilter is nil, stops all 5 configured ports.
    /// When portsFilter is provided, stops only the specified port numbers.
    static func stop(configuration: ProjectConfiguration, portsFilter: Set<Int>?) -> Result {
        let allPorts: Set<Int> = [configuration.taxPort, configuration.ragPort,
                                   configuration.idpPort, configuration.localModelPort,
                                   configuration.bossPort]
        let ports = portsFilter ?? allPorts
        let files = [".tax.pid", ".rag.pid", ".idp.pid", ".local_llm.pid", ".app.pid"]
            .map { configuration.rootURL.appendingPathComponent($0) }
        var targets = Set(files.compactMap { ProcessInspector.readPID(at: $0) })
        var signalled = Set<Int32>()
        var identities: [Int32: String] = [:]
        var failures = Set<String>()
        // Never signal PID 0/1 or the controller itself.  Do not include
        // ancestors to avoid accidentally killing unrelated launchd children.
        let protected: Set<Int32> = [0, 1, getpid()]
        let deadline = Date().addingTimeInterval(5)
        repeat {
            for port in ports {
                let result = listeners(port: port)
                if let error = result.error { failures.insert(error) }
                targets.formUnion(result.pids)
            }
            let tree = processTree()
            var expanded = targets.subtracting(protected).subtracting(signalled)
            var changed = true
            while changed {
                let children = Set(tree.filter { expanded.contains($0.value) }.map { $0.key })
                let before = expanded.count
                expanded.formUnion(children.subtracting(protected).subtracting(signalled))
                changed = expanded.count != before
            }
            targets = expanded
            for pid in targets where !signalled.contains(pid) && running(pid) {
                let identity = processIdentity(pid)
                if kill(pid, SIGKILL) == 0 {
                    signalled.insert(pid)
                    identities[pid] = identity
                } else if errno != ESRCH {
                    let port = ports.first { p in
                        listeners(port: p).pids.contains(pid)
                    } ?? 0
                    let errMsg = String(cString: strerror(errno))
                    failures.insert("端口 \(port) 占用 PID \(pid) 无法强制结束：\(errno)（\(errMsg)）")
                }
            }
            Thread.sleep(forTimeInterval: 0.1)
            if !targets.contains(where: running) && !identities.contains(where: { sameProcessRunning($0.key, identity: $0.value) }) {
                var occupied = false
                for port in ports {
                    let result = listeners(port: port)
                    if let error = result.error { failures.insert(error) }
                    occupied = occupied || !result.pids.isEmpty
                }
                if !occupied { break }
            }
        } while Date() < deadline

        // Verify each signalled PID is actually dead.
        for pid in signalled {
            if running(pid) {
                if sameProcessRunning(pid, identity: identities[pid] ?? "") {
                    // PID was reused; skip as it may be a different process.
                    continue
                }
                // Still alive despite SIGKILL.
                let errMsg = String(cString: strerror(errno))
                failures.insert("PID \(pid) 已被 SIGKILL 但仍存活：\(errno)（\(errMsg)）")
            }
        }

        for port in ports {
            let result = listeners(port: port)
            if let error = result.error { failures.insert(error) }
            if !result.pids.isEmpty { failures.insert("端口 \(port) 仍被占用") }
        }
        for pid in targets where !signalled.contains(pid) && running(pid) { failures.insert("PID \(pid) 仍在运行") }
        for (pid, identity) in identities where sameProcessRunning(pid, identity: identity) {
            failures.insert("PID \(pid) 仍在运行")
        }
        guard failures.isEmpty else {
            return Result(succeeded: false, message: "强制清理未完成：" + failures.sorted().joined(separator: "；"))
        }
        do {
            for file in files where FileManager.default.fileExists(atPath: file.path) {
                try FileManager.default.removeItem(at: file)
            }
        } catch {
            return Result(succeeded: false, message: "旧进程已结束，但 PID 文件清理失败：\(error.localizedDescription)")
        }
        return Result(succeeded: true, message: "已强制清理 \(signalled.count) 个旧进程，\(ports.count) 个端口已释放")
    }

    private static func processIdentity(_ pid: Int32) -> String {
        command("/bin/ps", ["-p", String(pid), "-o", "lstart=,command="]).output
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func sameProcessRunning(_ pid: Int32, identity: String) -> Bool {
        !identity.isEmpty && running(pid) && processIdentity(pid) == identity
    }

    private static func running(_ pid: Int32) -> Bool {
        guard pid > 1, kill(pid, 0) == 0 || errno == EPERM else { return false }
        let state = command("/bin/ps", ["-p", String(pid), "-o", "stat="]).output
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return !state.isEmpty && !state.hasPrefix("Z")
    }

    private static func processTree() -> [Int32: Int32] {
        let output = command("/bin/ps", ["-axo", "pid=,ppid="]).output
        var result: [Int32: Int32] = [:]
        for line in output.split(whereSeparator: \.isNewline) {
            let fields = line.split(whereSeparator: \.isWhitespace)
            if fields.count == 2, let pid = Int32(fields[0]), let parent = Int32(fields[1]) {
                result[pid] = parent
            }
        }
        return result
    }

    private static func listeners(port: Int) -> (pids: Set<Int32>, error: String?) {
        let result = command("/usr/sbin/lsof", ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN", "-Fp"])
        // lsof uses exit 1 with empty output for a port with no listener.
        guard result.status == 0 || (result.status == 1 && result.output.isEmpty) else {
            return ([], "端口 \(port) 查询失败（\(result.status)）")
        }
        return (Set(result.output.split(whereSeparator: \.isNewline).compactMap {
            $0.first == "p" ? Int32($0.dropFirst()) : nil
        }), nil)
    }

    private static func command(_ path: String, _ arguments: [String]) -> (status: Int32, output: String) {
        let process = Process()
        let pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = arguments
        process.standardOutput = pipe
        process.standardError = pipe
        do {
            try process.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return (process.terminationStatus, String(decoding: data, as: UTF8.self))
        } catch { return (-1, error.localizedDescription) }
    }
}
