import Foundation
import Darwin

enum ProcessKind {
    case uvicorn
    case localModel
    case bossWeb
}

struct ProcessEvidence: Equatable {
    let confirmed: Bool
    let pid: Int32?
    let source: String
    let command: String?
    let elapsedSeconds: TimeInterval?
    /// True only when `pid` is the process listening on the inspected target
    /// port, or when that listener is linked to a separately confirmed project
    /// process through its parent/child chain.
    let listeningConfirmed: Bool

    init(
        confirmed: Bool,
        pid: Int32?,
        source: String,
        command: String?,
        elapsedSeconds: TimeInterval?,
        listeningConfirmed: Bool = false
    ) {
        self.confirmed = confirmed
        self.pid = pid
        self.source = source
        self.command = command
        self.elapsedSeconds = elapsedSeconds
        self.listeningConfirmed = listeningConfirmed
    }

    static let missing = ProcessEvidence(
        confirmed: false,
        pid: nil,
        source: "未发现进程",
        command: nil,
        elapsedSeconds: nil
    )
}

/// Read-only process inspection used to prevent a healthy-looking port from
/// being mistaken for one of the project's managed services.
struct ProcessInspector {
    static func evidence(
        pidFileURL: URL?,
        port: Int,
        kind: ProcessKind,
        rootURL: URL,
        bossDirectoryURL: URL? = nil
    ) -> ProcessEvidence {
        let projectPID = pidFileURL.flatMap(readPID(at:))
        let listenerPIDs = listeningPIDs(port: port)

        // Prefer the process that is actually bound to the target port.  A
        // PID file alone is not enough to turn a healthy response into a
        // normal state.
        for listenerPID in listenerPIDs {
            if let direct = evidence(
                forPID: listenerPID,
                source: "监听端口 (\(port))",
                port: port,
                kind: kind,
                rootURL: rootURL,
                bossDirectoryURL: bossDirectoryURL
            ) {
                return direct.withListeningConfirmation(source: "监听端口 (\(port))")
            }

            if let projectPID,
               let chained = listenerChainEvidence(
                   listenerPID: listenerPID,
                   projectPID: projectPID,
                   port: port,
                   kind: kind,
                   rootURL: rootURL,
                   bossDirectoryURL: bossDirectoryURL
               ) {
                return chained
            }
        }

        // Keep the project PID as useful startup evidence even when it is not
        // the listener.  HealthMonitor deliberately treats this as starting
        // or partial, never as normal.
        if let projectPID,
           let projectEvidence = evidence(
               forPID: projectPID,
               source: "项目 PID 文件",
               port: port,
               kind: kind,
               rootURL: rootURL,
               bossDirectoryURL: bossDirectoryURL
           ) {
            return projectEvidence
        }

        return .missing
    }

    /// Inspect one exact PID without falling back to a port candidate.  Stop
    /// operations use this path so a PID reuse or a foreign process can never
    /// receive a signal merely because it happens to use the same port.
    static func evidence(
        forPID pid: Int32,
        source: String = "指定 PID",
        port: Int,
        kind: ProcessKind,
        rootURL: URL,
        bossDirectoryURL: URL? = nil
    ) -> ProcessEvidence? {
        guard isAlive(pid),
              let command = commandLine(for: pid),
              !command.isEmpty else { return nil }
        let cwd = workingDirectory(for: pid)
        guard matches(
            kind: kind,
            command: command,
            workingDirectory: cwd,
            port: port,
            rootURL: rootURL,
            bossDirectoryURL: bossDirectoryURL
        ) else { return nil }

        return ProcessEvidence(
            confirmed: true,
            pid: pid,
            source: source,
            command: command,
            elapsedSeconds: elapsedSeconds(for: pid)
        )
    }

    /// Confirm an actual listener whose process command is wrapped by a
    /// project-owned parent/child process.  The listener must be a descendant
    /// of the project PID and located under the expected project directory.
    /// The project endpoint identity still comes from `evidence(forPID:)`.
    private static func listenerChainEvidence(
        listenerPID: Int32,
        projectPID: Int32,
        port: Int,
        kind: ProcessKind,
        rootURL: URL,
        bossDirectoryURL: URL?
    ) -> ProcessEvidence? {
        guard listenerPID != projectPID,
              isAncestor(projectPID, of: listenerPID),
              let projectEvidence = evidence(
                  forPID: projectPID,
                  source: "项目 PID 文件",
                  port: port,
                  kind: kind,
                  rootURL: rootURL,
                  bossDirectoryURL: bossDirectoryURL
              ),
              let listenerCommand = commandLine(for: listenerPID),
              !listenerCommand.isEmpty,
              let listenerWorkingDirectory = workingDirectory(for: listenerPID) else {
            return nil
        }

        let expectedDirectory = bossDirectoryURL ?? rootURL
        guard pathIsInside(listenerWorkingDirectory, rootURL: expectedDirectory) else {
            return nil
        }

        return ProcessEvidence(
            confirmed: projectEvidence.confirmed,
            pid: listenerPID,
            source: "监听端口 (\(port))（项目父子进程链）",
            command: listenerCommand,
            elapsedSeconds: elapsedSeconds(for: listenerPID),
            listeningConfirmed: true
        )
    }

    private static func isAncestor(_ ancestorPID: Int32, of childPID: Int32) -> Bool {
        var currentPID = childPID
        var visited = Set<Int32>()
        while currentPID > 1, visited.insert(currentPID).inserted {
            guard let parent = parentPID(for: currentPID), parent > 1 else { return false }
            if parent == ancestorPID { return true }
            currentPID = parent
        }
        return false
    }

    private static func parentPID(for pid: Int32) -> Int32? {
        let value = run("/bin/ps", arguments: ["-p", String(pid), "-o", "ppid="])
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parent = Int32(value), parent > 1 else { return nil }
        return parent
    }

    static func readPID(at url: URL) -> Int32? {
        guard let content = try? String(contentsOf: url, encoding: .utf8),
              let value = Int32(content.trimmingCharacters(in: .whitespacesAndNewlines)),
              value > 1 else { return nil }
        return value
    }

    static func isAlive(_ pid: Int32) -> Bool {
        guard pid > 1 else { return false }
        let killResult = kill(pid, 0)
        if killResult != 0, errno != EPERM { return false }
        // A zombie (state starts with `Z`) still answers `kill(pid, 0) == 0`
        // but is no longer schedulable and cannot receive real signals.  Use
        // `ps -o stat=` to exclude it.
        let stat = run("/bin/ps", arguments: ["-p", String(pid), "-o", "stat="])
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if stat.hasPrefix("Z") { return false }
        return true
    }

    static func commandLine(for pid: Int32) -> String? {
        run("/bin/ps", arguments: ["-p", String(pid), "-o", "command="])
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .nilIfEmpty
    }

    static func workingDirectory(for pid: Int32) -> String? {
        let output = run("/usr/sbin/lsof", arguments: ["-a", "-p", String(pid), "-d", "cwd", "-Fn"])
        for line in output.split(whereSeparator: \.isNewline) {
            let text = String(line)
            if text.hasPrefix("n") { return String(text.dropFirst()) }
        }
        return nil
    }

    static func listeningPIDs(port: Int) -> [Int32] {
        let output = run(
            "/usr/sbin/lsof",
            arguments: ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN", "-Fp"]
        )
        return output.split(whereSeparator: \.isNewline).compactMap { line in
            let text = String(line)
            guard text.first == "p" else { return nil }
            return Int32(text.dropFirst())
        }
    }

    static func matches(
        kind: ProcessKind,
        command: String,
        workingDirectory: String?,
        port: Int,
        rootURL: URL,
        bossDirectoryURL: URL?
    ) -> Bool {
        let normalizedCommand = command.lowercased()
        let portArgument = commandContainsPort(normalizedCommand, port: port)

        switch kind {
        case .uvicorn:
            return normalizedCommand.contains("uvicorn")
                && portArgument
                && pathIsInside(workingDirectory, rootURL: rootURL)
        case .localModel:
            return normalizedCommand.contains("llama-server")
                && portArgument
                && localModelOwnershipMatches(
                    command: command,
                    workingDirectory: workingDirectory,
                    rootURL: rootURL
                )
        case .bossWeb:
            let expectedDirectory = bossDirectoryURL ?? rootURL
            return bossCommandOwnershipMatches(
                    command: command,
                    normalizedCommand: normalizedCommand,
                    expectedDirectory: expectedDirectory
                )
                && portArgument
                && pathIsInside(workingDirectory, rootURL: expectedDirectory)
        }
    }

    /// A process is only a project Boss process when its executable/entrypoint
    /// is the project's Vite entry (or the project package-manager command),
    /// in addition to having the expected cwd and port.  Looking only for the
    /// substring `vite` would allow an unrelated `/tmp/foreign-vite.js` to be
    /// treated as owned merely because it happened to run from this cwd.
    private static func bossCommandOwnershipMatches(
        command: String,
        normalizedCommand: String,
        expectedDirectory: URL
    ) -> Bool {
        if normalizedCommand.contains("npm run dev") {
            return true
        }

        let tokens = commandTokens(command)
        if normalizedCommand.contains("npm exec") {
            return tokens.contains { token in
                let normalizedToken = token.lowercased()
                return normalizedToken == "vite" || normalizedToken == "vite.js"
                    || normalizedToken.hasSuffix("/vite/bin/vite.js")
                    || normalizedToken.hasSuffix("/node_modules/.bin/vite")
            }
        }

        return tokens.contains { token in
            let normalizedToken = token.lowercased()
            if normalizedToken == "vite" || normalizedToken == "vite.js"
                || normalizedToken == "vite/bin/vite.js"
                || normalizedToken.hasSuffix("/vite/bin/vite.js")
                || normalizedToken.hasSuffix("/node_modules/.bin/vite") {
                return true
            }
            return normalizedToken.hasPrefix("/")
                && pathIsInside(normalizedToken, rootURL: expectedDirectory)
                && normalizedToken.contains("vite")
        }
    }

    /// A llama-server launched by the V3 runtime can have a temporary
    /// runtime directory as its cwd (for example a directory under /tmp).
    /// That is still project-owned when its explicit model argument resolves
    /// inside the selected project.  Keep the cwd check as the fast/default
    /// path, and require an absolute, project-contained `--model`/`-m` path
    /// before accepting an external cwd.  Relative model arguments cannot
    /// establish project ownership from an external cwd and are rejected.
    private static func localModelOwnershipMatches(
        command: String,
        workingDirectory: String?,
        rootURL: URL
    ) -> Bool {
        if pathIsInside(workingDirectory, rootURL: rootURL) {
            return true
        }

        guard let modelPath = explicitModelPath(in: command),
              modelPath.hasPrefix("/"),
              pathIsInside(modelPath, rootURL: rootURL) else {
            return false
        }
        return true
    }

    /// Extract only the explicit model option.  Tokenizing quotes keeps paths
    /// with spaces intact while avoiding a loose substring check that could
    /// mistake an unrelated argument for a project model path.
    private static func explicitModelPath(in command: String) -> String? {
        let tokens = commandTokens(command)
        for index in tokens.indices {
            let token = tokens[index]
            if token == "--model" || token == "-m" {
                let next = tokens.index(after: index)
                guard next < tokens.endIndex else { return nil }
                return tokens[next]
            }
            if token.hasPrefix("--model=") {
                let value = String(token.dropFirst("--model=".count))
                return value.isEmpty ? nil : value
            }
            if token.hasPrefix("-m=") {
                let value = String(token.dropFirst("-m=".count))
                return value.isEmpty ? nil : value
            }
        }
        return nil
    }

    private static func commandTokens(_ command: String) -> [String] {
        var tokens: [String] = []
        var current = ""
        var quote: Character?
        var escaped = false
        var hasToken = false

        for character in command {
            if escaped {
                current.append(character)
                escaped = false
                hasToken = true
                continue
            }
            if character == "\\" {
                escaped = true
                hasToken = true
                continue
            }
            if let activeQuote = quote {
                if character == activeQuote {
                    quote = nil
                } else {
                    current.append(character)
                }
                hasToken = true
                continue
            }
            if character == "'" || character == "\"" {
                quote = character
                hasToken = true
                continue
            }
            if character.isWhitespace {
                if hasToken {
                    tokens.append(current)
                    current = ""
                    hasToken = false
                }
            } else {
                current.append(character)
                hasToken = true
            }
        }

        // An unmatched trailing escape is safest represented literally; an
        // incomplete command still cannot pass the project path check unless
        // its parsed model argument is an unambiguous absolute path.
        if escaped { current.append("\\") }
        if hasToken { tokens.append(current) }
        return tokens
    }

    static func elapsedSeconds(for pid: Int32) -> TimeInterval? {
        let value = run("/bin/ps", arguments: ["-p", String(pid), "-o", "etime="])
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return parseElapsed(value)
    }

    static func parseElapsed(_ value: String) -> TimeInterval? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }

        var daySeconds: TimeInterval = 0
        var time = trimmed
        if let dash = time.firstIndex(of: "-") {
            daySeconds = (Double(time[..<dash]) ?? 0) * 86_400
            time = String(time[time.index(after: dash)...])
        }
        let pieces = time.split(separator: ":").compactMap { Double($0) }
        guard pieces.count == 2 || pieces.count == 3 else { return nil }
        if pieces.count == 2 {
            return daySeconds + pieces[0] * 60 + pieces[1]
        }
        return daySeconds + pieces[0] * 3_600 + pieces[1] * 60 + pieces[2]
    }

    @discardableResult
    static func sendSignal(_ signal: Int32, to pid: Int32) -> Bool {
        guard isAlive(pid) else { return true }
        return kill(pid, signal) == 0
    }

    private static func pathIsInside(_ path: String?, rootURL: URL) -> Bool {
        guard let path else { return false }
        // `lsof` reports macOS temporary directories through their canonical
        // `/private` path while launch arguments may retain `/var`.  Resolve
        // both sides before comparing so a project process is not rejected
        // solely because the two OS APIs chose different spellings.  Symlink
        // resolution also prevents a path that escapes the project from being
        // accepted under a symlinked project directory.
        let candidate = URL(fileURLWithPath: path)
            .standardizedFileURL
            .resolvingSymlinksInPath()
            .path
        let root = rootURL
            .standardizedFileURL
            .resolvingSymlinksInPath()
            .path
        return candidate == root || candidate.hasPrefix(root + "/")
    }

    /// Match `--port <p>`, `--port=<p>` or `:<p>` so that ports whose decimal
    /// representation is a prefix of another port (for example port `5` vs
    /// `:50`, or port `8921` vs `:89210`) cannot silently collide.
    private static func commandContainsPort(_ command: String, port: Int) -> Bool {
        let token = NSRegularExpression.escapedPattern(for: String(port))
        let patterns = [
            "--port \(token)(?![0-9])",
            "--port=\(token)(?![0-9])",
            ":\(token)(?![0-9])"
        ]
        for pattern in patterns {
            if command.range(of: pattern, options: .regularExpression) != nil {
                return true
            }
        }
        return false
    }

    private static func run(_ executable: String, arguments: [String]) -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            return ""
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return String(data: data, encoding: .utf8) ?? ""
    }
}

private extension ProcessEvidence {
    func withListeningConfirmation(source: String) -> ProcessEvidence {
        ProcessEvidence(
            confirmed: confirmed,
            pid: pid,
            source: source,
            command: command,
            elapsedSeconds: elapsedSeconds,
            listeningConfirmed: true
        )
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
