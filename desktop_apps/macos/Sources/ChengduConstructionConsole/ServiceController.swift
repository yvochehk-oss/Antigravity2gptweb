import Foundation
import Darwin

final class ServiceController {
    typealias BusyHandler = (Bool) -> Void
    typealias CompletionHandler = (ActionResult) -> Void

    private let configuration: ProjectConfiguration
    private let logStore: LogStore
    private let actionQueue = DispatchQueue(
        label: "cn.cdjg.controlpanel.actions",
        qos: .userInitiated
    )
    private let stateLock = NSLock()
    private var actionInFlight = false

    private let bossLaunchTimeout: TimeInterval = 5
    private let bossStopTimeout: TimeInterval = 10

    var onBusyChange: BusyHandler?

    init(configuration: ProjectConfiguration, logStore: LogStore) {
        self.configuration = configuration
        self.logStore = logStore
    }

    var isBusy: Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return actionInFlight
    }

    func perform(_ action: ControlAction, completion: @escaping CompletionHandler) {
        stateLock.lock()
        guard !actionInFlight else {
            stateLock.unlock()
            DispatchQueue.main.async {
                completion(.rejected(action))
            }
            return
        }
        actionInFlight = true
        stateLock.unlock()

        DispatchQueue.main.async { [weak self] in
            self?.onBusyChange?(true)
        }

        actionQueue.async { [weak self] in
            guard let self else { return }
            let result = self.execute(action)
            self.stateLock.lock()
            self.actionInFlight = false
            self.stateLock.unlock()
            DispatchQueue.main.async { [weak self] in
                self?.onBusyChange?(false)
                completion(result)
            }
        }
    }

    private func execute(_ action: ControlAction) -> ActionResult {
        logStore.record("开始执行：\(action.displayName)")

        let result: ActionResult
        switch action {
        case .startAll:
            result = startAll(action: action)
        case .stopAll:
            result = stopAll(action: action)
        case .restartAll:
            // `startAll` owns the complete stop-then-start transaction.  Keep
            // restart on the same path so it cannot stop the project twice or
            // accidentally launch while an old listener is still present.
            result = startAll(action: action)
        }

        logStore.record(result.message)
        return result
    }

    private func startAll(action: ControlAction) -> ActionResult {
        // A start request is a full refresh of the project runtime, not a
        // request to reuse whatever happens to be listening on the ports.  Do
        // the fail-closed cleanup before touching the core services.  In
        // particular, an unknown 5173 listener must abort without stopping
        // Tax/RAG/IDP/LLM or sending a signal to the foreign process.
        let cleanup = stopServicesBeforeStart(action: action)
        guard cleanup.succeeded else {
            return ActionResult(
                action: action,
                succeeded: false,
                exitCode: cleanup.exitCode,
                message: "启动未执行：旧项目进程未能安全停止。\(cleanup.message)"
            )
        }

        let core = runScript(
            configuration.startScriptURL,
            arguments: ["start"],
            action: action,
            successMessage: "核心服务启动入口已完成；未执行安装或数据库迁移"
        )
        guard core.succeeded else {
            // A failed core start must never be followed by a Boss launch.
            return core
        }

        let boss = startBoss()
        guard boss.succeeded else {
            // The core entrypoint may have started several processes before
            // returning a successful exit status.  If the directly managed
            // Boss front end cannot be started, use the existing V3.0 stop
            // entrypoint to roll the core services back.  It does not touch
            // PostgreSQL.
            let rollback = runScript(
                configuration.stopScriptURL,
                arguments: [],
                action: action,
                successMessage: "核心服务已回滚；PostgreSQL 数据库未操作"
            )
            let rollbackMessage = rollback.succeeded
                ? "核心服务已回滚"
                : "核心服务回滚未完成（退出码：\(rollback.exitCode.map(String.init) ?? "未知")）"
            return ActionResult(
                action: action,
                succeeded: false,
                exitCode: rollback.succeeded ? nil : rollback.exitCode,
                message: "启动未完成：\(boss.message)；\(rollbackMessage)"
            )
        }

        return ActionResult(
            action: action,
            succeeded: true,
            exitCode: core.exitCode,
            message: "\(core.message)；\(boss.message)"
        )
    }

    private func stopServicesBeforeStart(action: ControlAction) -> ActionResult {
        // Stop the Boss web front end first so a stale page cannot keep
        // presenting while the backing services are being torn down.  This
        // also discovers a project-owned 5173 listener when `.app.pid` is
        // missing, while refusing any ambiguous listener before core stop.
        let boss = stopBoss()
        guard boss.succeeded else {
            return ActionResult(
                action: action,
                succeeded: false,
                exitCode: nil,
                message: boss.message
            )
        }

        let core = runScript(
            configuration.stopScriptURL,
            arguments: [],
            action: action,
            successMessage: "核心业务服务已请求停止；PostgreSQL 数据库未操作"
        )
        return ActionResult(
            action: action,
            succeeded: core.succeeded,
            exitCode: core.exitCode,
            message: "\(boss.message)；\(core.message)"
        )
    }

    private func stopAll(action: ControlAction) -> ActionResult {
        // Use the same ordered, fail-closed stop path as start/restart.  This
        // keeps an ambiguous Boss listener from being mistaken for a managed
        // process and avoids running the core stop script after that refusal.
        return stopServicesBeforeStart(action: action)
    }

    private struct BossLaunchPlan {
        let executableURL: URL
        let arguments: [String]
        let modeDescription: String
    }

    private struct BossOperation {
        let succeeded: Bool
        let message: String
    }

    private func startBoss() -> BossOperation {
        guard ProjectLocator.validatedRoot(configuration.rootURL) != nil else {
            return BossOperation(succeeded: false, message: "未找到可用的 V3.0 项目目录，未启动老板驾驶舱")
        }

        let fileManager = FileManager.default
        let pidFileURL = configuration.appPIDFileURL

        if fileManager.fileExists(atPath: pidFileURL.path) {
            guard let existingPID = ProcessInspector.readPID(at: pidFileURL) else {
                // This file is our own ownership record.  Removing only an
                // invalid record cannot terminate a process and lets a later
                // start recover from a crashed/partial launch.
                try? fileManager.removeItem(at: pidFileURL)
                return launchBoss()
            }

            if ProcessInspector.isAlive(existingPID) {
                if confirmedBossEvidence(for: existingPID) != nil {
                    return BossOperation(succeeded: true, message: "老板驾驶舱已在运行，保持现有进程")
                }
                return BossOperation(
                    succeeded: false,
                    message: "老板驾驶舱 PID \(existingPID) 无法同时确认命令、工作目录和项目归属，未覆盖或终止"
                )
            }

            try? fileManager.removeItem(at: pidFileURL)
        }

        return launchBoss()
    }

    private func launchBoss() -> BossOperation {
        guard let plan = bossLaunchPlan() else {
            return BossOperation(
                succeeded: false,
                message: "老板驾驶舱未找到本地 Vite 运行时，未执行安装或联网下载"
            )
        }

        let process = Process()
        process.executableURL = plan.executableURL
        process.arguments = plan.arguments
        process.currentDirectoryURL = configuration.bossDirectoryURL
        process.environment = managedEnvironment(includeBossBin: true)
        // A menu-bar app has no terminal.  Do not capture front-end output in
        // the controller log, where it could contain environment details.
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice

        do {
            try process.run()
        } catch {
            return BossOperation(succeeded: false, message: "老板驾驶舱启动失败，未执行安装或联网下载")
        }

        let pid = process.processIdentifier
        guard pid > 1,
              waitForConfirmedBoss(pid: pid, timeout: bossLaunchTimeout) != nil else {
            let cleaned = terminateConfirmedBoss(pid: pid)
            let cleanupMessage = cleaned ? "失败进程已清理" : "未确认失败进程归属，未发送停止信号"
            return BossOperation(succeeded: false, message: "老板驾驶舱启动失败；\(cleanupMessage)")
        }

        do {
            try persistBossPID(pid)
        } catch {
            let cleaned = terminateConfirmedBoss(pid: pid)
            if cleaned {
                try? FileManager.default.removeItem(at: configuration.appPIDFileURL)
            }
            let cleanupMessage = cleaned ? "失败进程已清理" : "未确认失败进程归属，未发送停止信号"
            return BossOperation(succeeded: false, message: "老板驾驶舱 PID 记录写入失败；\(cleanupMessage)")
        }

        let mode = plan.modeDescription
        return BossOperation(succeeded: true, message: "老板驾驶舱已启动（\(mode)，端口 \(configuration.bossPort)）")
    }

    private func stopBoss() -> BossOperation {
        guard ProjectLocator.validatedRoot(configuration.rootURL) != nil else {
            return BossOperation(succeeded: false, message: "未找到可用的 V3.0 项目目录，未停止老板驾驶舱")
        }

        let fileManager = FileManager.default
        let pidFileURL = configuration.appPIDFileURL
        var candidatePIDs = Set<Int32>()
        var hadRecordedPID = false

        if fileManager.fileExists(atPath: pidFileURL.path) {
            guard let recordedPID = ProcessInspector.readPID(at: pidFileURL) else {
                return BossOperation(succeeded: false, message: "老板驾驶舱 PID 文件无效，未发送停止信号")
            }

            if ProcessInspector.isAlive(recordedPID) {
                guard confirmedBossEvidence(for: recordedPID) != nil else {
                    return BossOperation(
                        succeeded: false,
                        message: "老板驾驶舱 PID \(recordedPID) 无法同时确认命令、工作目录和项目归属，未发送停止信号"
                    )
                }
                candidatePIDs.insert(recordedPID)
                hadRecordedPID = true
            } else {
                // A dead PID record is safe to remove, but still inspect 5173
                // below: a manually launched project Vite process can survive
                // after the controller's record was deleted.
                try? fileManager.removeItem(at: pidFileURL)
            }
        }

        // The PID file is only an ownership hint.  Enumerate every listener
        // before sending any signal so a foreign listener sharing the port
        // causes a complete fail-closed refusal rather than partial cleanup.
        let listenerPIDs = ProcessInspector.listeningPIDs(port: configuration.bossPort)
        var confirmedListenerCount = 0
        for listenerPID in listenerPIDs {
            guard confirmedBossEvidence(for: listenerPID, source: "监听端口") != nil else {
                return BossOperation(
                    succeeded: false,
                    message: "老板驾驶舱端口 \(configuration.bossPort) 存在无法确认命令、工作目录和项目归属的监听进程，未发送停止信号"
                )
            }
            candidatePIDs.insert(listenerPID)
            confirmedListenerCount += 1
        }

        guard !candidatePIDs.isEmpty else {
            return BossOperation(
                succeeded: true,
                message: hadRecordedPID
                    ? "老板驾驶舱进程已不存在，已清理陈旧 PID"
                    : "老板驾驶舱未发现运行进程，未终止任何进程"
            )
        }

        for pid in candidatePIDs.sorted() {
            guard terminateConfirmedBoss(pid: pid) else {
                return BossOperation(
                    succeeded: false,
                    message: "老板驾驶舱 PID \(pid) 未能安全停止，未清理 PID 文件"
                )
            }
        }

        try? fileManager.removeItem(at: pidFileURL)
        let sourceMessage = confirmedListenerCount > 0 && !hadRecordedPID
            ? "老板驾驶舱已停止（通过端口监听确认并回收）"
            : "老板驾驶舱已停止"
        return BossOperation(succeeded: true, message: sourceMessage)
    }

    private func bossLaunchPlan() -> BossLaunchPlan? {
        let fileManager = FileManager.default
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(
            atPath: configuration.bossDirectoryURL.path,
            isDirectory: &isDirectory
        ), isDirectory.boolValue else { return nil }

        let hasDist = fileManager.isReadableFile(atPath: configuration.bossDistIndexURL.path)
        let arguments: [String]
        let modeDescription: String
        if hasDist {
            arguments = [
                "preview",
                "--host", "127.0.0.1",
                "--port", String(configuration.bossPort),
                "--strictPort"
            ]
            modeDescription = "本地构建预览"
        } else {
            arguments = [
                "--host", "127.0.0.1",
                "--port", String(configuration.bossPort),
                "--strictPort"
            ]
            modeDescription = "本地开发服务"
        }

        if fileManager.isExecutableFile(atPath: configuration.bossViteShimURL.path) {
            return BossLaunchPlan(
                executableURL: configuration.bossViteShimURL,
                arguments: arguments,
                modeDescription: modeDescription
            )
        }

        guard fileManager.isReadableFile(atPath: configuration.bossViteEntryURL.path),
              let nodeURL = nodeExecutableURL() else { return nil }
        return BossLaunchPlan(
            executableURL: nodeURL,
            arguments: [configuration.bossViteEntryURL.path] + arguments,
            modeDescription: modeDescription
        )
    }

    private func nodeExecutableURL() -> URL? {
        let fileManager = FileManager.default
        let inheritedPath = ProcessInfo.processInfo.environment["PATH"] ?? ""
        let pathCandidates = inheritedPath
            .split(separator: ":")
            .map { String($0) }
            .filter { !$0.isEmpty }
            .map { URL(fileURLWithPath: $0).appendingPathComponent("node") }
        let candidates = pathCandidates + [
            URL(fileURLWithPath: "/opt/homebrew/bin/node"),
            URL(fileURLWithPath: "/usr/local/bin/node"),
            URL(fileURLWithPath: "/usr/bin/node")
        ]
        var seen = Set<String>()
        for candidate in candidates where seen.insert(candidate.path).inserted {
            if fileManager.isExecutableFile(atPath: candidate.path) {
                return candidate
            }
        }
        return nil
    }

    private func managedEnvironment(includeBossBin: Bool = false) -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        let servicePaths = [
            configuration.rootURL.appendingPathComponent(".venv/bin").path,
            configuration.rootURL
                .appendingPathComponent("source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0/.venv/bin")
                .path,
            configuration.rootURL
                .appendingPathComponent("source_code/0.2_RAG系统/project-rag-v1.1/.venv/bin")
                .path,
            configuration.rootURL
                .appendingPathComponent("source_code/0.4_IDP文档录入引擎_V3.0/.venv/bin")
                .path,
            NSHomeDirectory() + "/.local/bin",
            NSHomeDirectory() + "/.cargo/bin",
            "/opt/homebrew/bin",
            "/usr/local/bin"
        ]
        let bossBin = configuration.bossDirectoryURL
            .appendingPathComponent("node_modules/.bin")
            .path
        let inheritedPath = environment["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        let prefix = includeBossBin ? [bossBin] : []
        environment["PATH"] = (prefix + servicePaths + [inheritedPath]).joined(separator: ":")
        environment["BROWSER"] = "none"
        return environment
    }

    private func persistBossPID(_ pid: Int32) throws {
        guard pid > 1 else { throw CocoaError(.fileWriteInvalidFileName) }
        let data = Data("\(pid)\n".utf8)
        try data.write(to: configuration.appPIDFileURL, options: [.atomic])
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: configuration.appPIDFileURL.path
        )
    }

    private func confirmedBossEvidence(for pid: Int32, source: String = "项目 .app.pid") -> ProcessEvidence? {
        ProcessInspector.evidence(
            forPID: pid,
            source: source,
            port: configuration.bossPort,
            kind: .bossWeb,
            rootURL: configuration.rootURL,
            bossDirectoryURL: configuration.bossDirectoryURL
        )
    }

    private func waitForConfirmedBoss(pid: Int32, timeout: TimeInterval) -> ProcessEvidence? {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if let evidence = confirmedBossEvidence(for: pid) {
                return evidence
            }
            guard ProcessInspector.isAlive(pid) else { return nil }
            Thread.sleep(forTimeInterval: 0.1)
        }
        return confirmedBossEvidence(for: pid)
    }

    /// Send a signal only after re-confirming the exact PID, command and cwd.
    /// If the PID is reused while waiting, no second signal is sent.
    private func terminateConfirmedBoss(pid: Int32) -> Bool {
        guard ProcessInspector.isAlive(pid) else { return true }
        guard confirmedBossEvidence(for: pid) != nil else { return false }
        guard ProcessInspector.sendSignal(SIGTERM, to: pid) else { return false }
        if waitUntilStopped(pid: pid, timeout: bossStopTimeout) {
            return true
        }

        guard !ProcessInspector.isAlive(pid) || confirmedBossEvidence(for: pid) != nil else {
            return false
        }
        guard ProcessInspector.isAlive(pid),
              confirmedBossEvidence(for: pid) != nil,
              ProcessInspector.sendSignal(SIGKILL, to: pid) else {
            return !ProcessInspector.isAlive(pid)
        }
        return waitUntilStopped(pid: pid, timeout: 2)
    }

    private func waitUntilStopped(pid: Int32, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while ProcessInspector.isAlive(pid) && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }
        return !ProcessInspector.isAlive(pid)
    }

    private func runScript(
        _ scriptURL: URL,
        arguments: [String],
        action: ControlAction,
        successMessage: String
    ) -> ActionResult {
        guard ProjectLocator.validatedRoot(configuration.rootURL) != nil,
              FileManager.default.isReadableFile(atPath: scriptURL.path) else {
            return ActionResult(
                action: action,
                succeeded: false,
                exitCode: nil,
                message: "未找到可用的 V3.0 控制入口"
            )
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [scriptURL.path] + arguments
        process.currentDirectoryURL = configuration.rootURL
        process.environment = managedEnvironment()
        // A GUI-launched process has no terminal.  Null devices also prevent
        // command output (which may contain environment details) from being
        // recorded by the controller.
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice

        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            return ActionResult(
                action: action,
                succeeded: false,
                exitCode: nil,
                message: "无法启动控制入口"
            )
        }

        let succeeded = process.terminationStatus == 0
        return ActionResult(
            action: action,
            succeeded: succeeded,
            exitCode: process.terminationStatus,
            message: succeeded ? successMessage : "控制入口执行失败，退出码：\(process.terminationStatus)"
        )
    }
}
