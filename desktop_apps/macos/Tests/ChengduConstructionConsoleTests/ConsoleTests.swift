import XCTest
@testable import ChengduConstructionConsole

final class ConsoleTests: XCTestCase {
    func testProjectRootCanBeFoundFromAnAncestor() throws {
        let root = try makeProjectRoot()
        let nested = root.appendingPathComponent("desktop_apps/macos/build", isDirectory: true)
        try FileManager.default.createDirectory(at: nested, withIntermediateDirectories: true)

        XCTAssertEqual(ProjectLocator.findInAncestors(of: nested), root.standardizedFileURL)
    }

    func testProjectRootSearchStopsWhenNoAncestorIsAProject() throws {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("cdjg-no-project-\(UUID().uuidString)", isDirectory: true)
            .appendingPathComponent("nested", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer {
            try? FileManager.default.removeItem(
                at: directory.deletingLastPathComponent()
            )
        }

        XCTAssertNil(ProjectLocator.findInAncestors(of: directory))
        XCTAssertNil(ProjectLocator.findInAncestors(of: URL(fileURLWithPath: "/", isDirectory: true)))
    }

    func testEnvironmentRootTakesPrecedenceAndConfigurationUsesSafeValues() throws {
        let root = try makeProjectRoot()
        let envFile = root.appendingPathComponent(".env")
        try "TAX_PORT=9011\nPROJECT_RAG_PORT=9012\nLOCAL_LLM_HOST=remote.example\nLOCAL_LLM_PORT=not-a-port\n".write(to: envFile, atomically: true, encoding: .utf8)
        let suiteName = "console-tests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let found = ProjectLocator.locate(
            bundleURL: root.appendingPathComponent("other/app.app"),
            currentDirectoryURL: URL(fileURLWithPath: "/tmp"),
            environment: ["CHENGDU_PROJECT_ROOT": root.path],
            defaults: defaults
        )
        XCTAssertEqual(found, root.standardizedFileURL)

        let configuration = ProjectConfiguration(rootURL: root, environment: [:])
        XCTAssertEqual(configuration.taxPort, 9011)
        XCTAssertEqual(configuration.ragPort, 9012)
        XCTAssertEqual(configuration.localModelPort, ProjectConfiguration.defaultLocalModelPort)
        XCTAssertEqual(configuration.localModelHost, "127.0.0.1")
        XCTAssertEqual(configuration.bossPort, ProjectConfiguration.defaultBossPort)
        XCTAssertEqual(configuration.taxHealthURL.absoluteString, "http://127.0.0.1:9011/healthz")
        XCTAssertEqual(configuration.ragHealthURL.absoluteString, "http://127.0.0.1:9012/api/v1/health")
        XCTAssertEqual(configuration.idpHealthURL.absoluteString, "http://127.0.0.1:8933/health")
        XCTAssertEqual(configuration.localModelListURL.absoluteString, "http://127.0.0.1:8930/v1/models")
        XCTAssertEqual(configuration.bossURL.absoluteString, "http://127.0.0.1:5173/")
        XCTAssertEqual(configuration.appPIDFileURL, root.appendingPathComponent(".app.pid"))
        XCTAssertEqual(
            configuration.bossViteShimURL,
            root.appendingPathComponent("source_code/0.3_老板端安卓App_天府掌舵/node_modules/.bin/vite")
        )
    }

    /// The `.env` permission check is non-blocking, so a world-readable file
    /// must still be parsed; only a system-log warning is expected.  A strict
    /// `0o600` file must not raise the same warning.
    func testEnvironmentFileLoadingIsIndependentOfLoosePermissions() throws {
        let root = try makeProjectRoot()
        let envFile = root.appendingPathComponent(".env")
        try "TAX_PORT=9033\n".write(to: envFile, atomically: true, encoding: .utf8)

        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: envFile.path)
        XCTAssertEqual(
            ProjectConfiguration(rootURL: root, environment: [:]).taxPort,
            9033,
            "Loose permissions must not block parsing"
        )

        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: envFile.path)
        XCTAssertEqual(
            ProjectConfiguration(rootURL: root, environment: [:]).taxPort,
            9033,
            "Strict permissions must still parse correctly"
        )
    }

    func testConfigurationRereadDetectsAllowlistedEnvChangesAndIgnoresUnrelatedChanges() throws {
        let root = try makeProjectRoot()
        let envFile = root.appendingPathComponent(".env")
        try "LOCAL_LLM_PORT=8930\n".write(to: envFile, atomically: true, encoding: .utf8)

        let initial = ProjectConfiguration(rootURL: root, environment: [:])
        try "LOCAL_LLM_PORT=8931\n".write(to: envFile, atomically: true, encoding: .utf8)
        let changed = ProjectConfiguration(rootURL: root, environment: [:])

        XCTAssertNotEqual(initial, changed)
        XCTAssertEqual(initial.localModelPort, 8930)
        XCTAssertEqual(changed.localModelPort, 8931)

        // Only allow-listed values participate in the configuration
        // comparison.  An unrelated .env edit must not cause a rebuild.
        try "LOCAL_LLM_PORT=8931\nUNRELATED_SETTING=changed\n".write(
            to: envFile,
            atomically: true,
            encoding: .utf8
        )
        let unchanged = ProjectConfiguration(rootURL: root, environment: [:])
        XCTAssertEqual(changed, unchanged)
    }

    /// The controller log must record timestamps with millisecond precision so
    /// that consecutive events can be ordered without ambiguity.
    func testLogStoreRecordsMillisecondPrecision() throws {
        let logURL = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("cdjg-console-ms-\(UUID().uuidString).log")
        defer { try? FileManager.default.removeItem(at: logURL) }
        let logStore = LogStore(fileURL: logURL)
        logStore.record("first")
        logStore.record("second")
        let text = try String(contentsOf: logStore.fileURL, encoding: .utf8)
        let firstLine = text.split(whereSeparator: \.isNewline).first.map(String.init) ?? ""
        // ISO-8601 with fractional seconds matches `YYYY-MM-DDTHH:MM:SS.sssZ`
        XCTAssertTrue(
            firstLine.range(of: #"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"#, options: .regularExpression) != nil,
            "Expected millisecond timestamp, got: \(firstLine)"
        )
    }

    func testHealthOverallStateKeepsPartialAndUnavailableDistinct() {
        let now = Date()
        let normal = ServiceSnapshot(
            service: .tax,
            state: .normal,
            detail: "",
            endpoint: nil,
            processPID: nil,
            processConfirmed: true,
            checkedAt: now
        )
        let partial = ServiceSnapshot(
            service: .rag,
            state: .partial,
            detail: "",
            endpoint: nil,
            processPID: nil,
            processConfirmed: false,
            checkedAt: now
        )
        XCTAssertEqual(HealthSnapshot.overallState(for: [normal, partial]), .partial)
        let stopped = ServiceSnapshot(
            service: .idp,
            state: .stopped,
            detail: "",
            endpoint: nil,
            processPID: nil,
            processConfirmed: false,
            checkedAt: now
        )
        XCTAssertEqual(HealthSnapshot.overallState(for: [normal, stopped]), .partial)
        XCTAssertEqual(HealthSnapshot.overallState(for: [normal]), .normal)
        XCTAssertEqual(HealthSnapshot.overallState(for: [stopped]), .stopped)
        XCTAssertEqual(HealthSnapshot.overallState(for: [
            ServiceSnapshot(
                service: .tax,
                state: .unavailable,
                detail: "",
                endpoint: nil,
                processPID: nil,
                processConfirmed: true,
                checkedAt: now
            )
        ]), .unavailable)
    }

    func testProcessMatchingRequiresProjectEvidenceAndPort() {
        let root = URL(fileURLWithPath: "/tmp/cdjg-test-root", isDirectory: true)
        let serviceDirectory = root.appendingPathComponent("source_code/service", isDirectory: true)
        XCTAssertTrue(ProcessInspector.matches(
            kind: .uvicorn,
            command: "python -m uvicorn app.main:app --host 127.0.0.1 --port 8921",
            workingDirectory: serviceDirectory.path,
            port: 8921,
            rootURL: root,
            bossDirectoryURL: nil
        ))
        XCTAssertFalse(ProcessInspector.matches(
            kind: .uvicorn,
            command: "python -m uvicorn app.main:app --host 127.0.0.1 --port 9999",
            workingDirectory: serviceDirectory.path,
            port: 8921,
            rootURL: root,
            bossDirectoryURL: nil
        ))
        XCTAssertTrue(ProcessInspector.matches(
            kind: .localModel,
            command: "llama-server --model model.gguf --port 8930",
            workingDirectory: root.path,
            port: 8930,
            rootURL: root,
            bossDirectoryURL: nil
        ))
        let bossDirectory = root.appendingPathComponent("source_code/0.3_老板端安卓App_天府掌舵")
        XCTAssertTrue(ProcessInspector.matches(
            kind: .bossWeb,
            command: "node /tmp/cdjg-test-root/source_code/0.3_老板端安卓App_天府掌舵/node_modules/vite/bin/vite.js preview --port 5173",
            workingDirectory: bossDirectory.path,
            port: 5173,
            rootURL: root,
            bossDirectoryURL: bossDirectory
        ))
        XCTAssertFalse(ProcessInspector.matches(
            kind: .bossWeb,
            command: "node /tmp/cdjg-test-root/source_code/0.3_老板端安卓App_天府掌舵/node_modules/vite/bin/vite.js preview --port 3000",
            workingDirectory: bossDirectory.path,
            port: 5173,
            rootURL: root,
            bossDirectoryURL: bossDirectory
        ))
        XCTAssertTrue(ProcessInspector.matches(
            kind: .bossWeb,
            command: "node /tmp/cdjg-test-root/source_code/0.3_老板端安卓App_天府掌舵/node_modules/vite/bin/vite.js preview --port 5173",
            workingDirectory: serviceDirectory.path,
            port: 5173,
            rootURL: root,
            bossDirectoryURL: bossDirectory
        ))
        XCTAssertFalse(ProcessInspector.matches(
            kind: .bossWeb,
            command: "node /tmp/foreign-vite.js preview --port 5173",
            workingDirectory: bossDirectory.path,
            port: 5173,
            rootURL: root,
            bossDirectoryURL: bossDirectory
        ))
        XCTAssertEqual(try XCTUnwrap(ProcessInspector.parseElapsed("1-02:03:04")), 93_784, accuracy: 0.1)
    }

    /// Anchor port matching so that ports whose decimal representation is a
    /// prefix of another port cannot silently match.  The OLD substring check
    /// `contains(":\(port)")` would let port `5` match `:50`; the new regex
    /// requires the next character to be a non-digit.
    func testPortMatchingUsesNonDigitBoundary() {
        let root = URL(fileURLWithPath: "/tmp/cdjg-test-root", isDirectory: true)
        let serviceDirectory = root.appendingPathComponent("source_code/service", isDirectory: true)

        // uvicorn: a single-digit target port must not match the larger port.
        XCTAssertFalse(ProcessInspector.matches(
            kind: .uvicorn,
            command: "python -m uvicorn app.main:app --host 127.0.0.1 --port 5173",
            workingDirectory: serviceDirectory.path,
            port: 51,
            rootURL: root,
            bossDirectoryURL: nil
        ))
        XCTAssertFalse(ProcessInspector.matches(
            kind: .uvicorn,
            command: "python -m uvicorn app.main:app --host 127.0.0.1 --port=5173",
            workingDirectory: serviceDirectory.path,
            port: 517,
            rootURL: root,
            bossDirectoryURL: nil
        ))
        XCTAssertFalse(ProcessInspector.matches(
            kind: .uvicorn,
            command: "python -m uvicorn app.main:app --bind 127.0.0.1:89210",
            workingDirectory: serviceDirectory.path,
            port: 8921,
            rootURL: root,
            bossDirectoryURL: nil
        ))

        // Positive case: the full port must still match when followed by
        // whitespace, end of string, or a non-digit delimiter.
        XCTAssertTrue(ProcessInspector.matches(
            kind: .uvicorn,
            command: "python -m uvicorn app.main:app --bind 127.0.0.1:8921",
            workingDirectory: serviceDirectory.path,
            port: 8921,
            rootURL: root,
            bossDirectoryURL: nil
        ))
        XCTAssertTrue(ProcessInspector.matches(
            kind: .uvicorn,
            command: "python -m uvicorn app.main:app --port 8921 --workers 4",
            workingDirectory: serviceDirectory.path,
            port: 8921,
            rootURL: root,
            bossDirectoryURL: nil
        ))
    }

    func testLocalModelExternalWorkingDirectoryRequiresProjectModelPath() {
        let root = URL(fileURLWithPath: "/tmp/cdjg-test-root", isDirectory: true)
        let externalDirectory = "/tmp/llama-runtime"
        let projectModel = root.appendingPathComponent(
            "models/local-llm/model.gguf"
        ).path
        let externalModel = "/tmp/foreign-model.gguf"

        XCTAssertTrue(ProcessInspector.matches(
            kind: .localModel,
            command: "llama-server --model \"\(projectModel)\" --port 8931",
            workingDirectory: externalDirectory,
            port: 8931,
            rootURL: root,
            bossDirectoryURL: nil
        ))
        XCTAssertTrue(ProcessInspector.matches(
            kind: .localModel,
            command: "llama-server --model=\(projectModel) --port=8931",
            workingDirectory: externalDirectory,
            port: 8931,
            rootURL: root,
            bossDirectoryURL: nil
        ))
        XCTAssertFalse(ProcessInspector.matches(
            kind: .localModel,
            command: "llama-server --model \(externalModel) --port 8931",
            workingDirectory: externalDirectory,
            port: 8931,
            rootURL: root,
            bossDirectoryURL: nil
        ))
        XCTAssertFalse(ProcessInspector.matches(
            kind: .localModel,
            command: "llama-server --model model.gguf --port 8931",
            workingDirectory: externalDirectory,
            port: 8931,
            rootURL: root,
            bossDirectoryURL: nil
        ))
    }

    func testLocalModelPortMatchingRemainsExact() {
        let root = URL(fileURLWithPath: "/tmp/cdjg-test-root", isDirectory: true)
        let projectModel = root.appendingPathComponent("models/model.gguf").path
        let command = "llama-server --model \(projectModel)"

        XCTAssertTrue(ProcessInspector.matches(
            kind: .localModel,
            command: "\(command) --port 8931",
            workingDirectory: "/tmp/llama-runtime",
            port: 8931,
            rootURL: root,
            bossDirectoryURL: nil
        ))
        XCTAssertFalse(ProcessInspector.matches(
            kind: .localModel,
            command: "\(command) --port 89310",
            workingDirectory: "/tmp/llama-runtime",
            port: 8931,
            rootURL: root,
            bossDirectoryURL: nil
        ))
        XCTAssertFalse(ProcessInspector.matches(
            kind: .localModel,
            command: "\(command) --bind 127.0.0.1:89310",
            workingDirectory: "/tmp/llama-runtime",
            port: 8931,
            rootURL: root,
            bossDirectoryURL: nil
        ))
    }

    func testHealthEvidenceRejectsForeignListenerForNonListeningProjectPID() throws {
        let root = try makeProjectRoot()
        let configuration = ProjectConfiguration(rootURL: root)
        let foreignDirectory = root
            .deletingLastPathComponent()
            .appendingPathComponent("cdjg-foreign-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: foreignDirectory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: foreignDirectory) }

        let port = nextTestPort()
        let project = try launchAliasProcess(
            alias: "node vite/bin/vite.js --port \(port)",
            executable: "/bin/sleep",
            arguments: ["60"],
            currentDirectory: configuration.bossDirectoryURL
        )
        defer { terminateTestProcess(project) }
        let foreignListener = try launchAliasProcess(
            alias: "node vite/bin/vite.js --port \(port)",
            executable: "/usr/bin/nc",
            arguments: ["-l", "127.0.0.1", "\(port)"],
            currentDirectory: foreignDirectory
        )
        defer { terminateTestProcess(foreignListener) }
        XCTAssertTrue(waitUntilListening(port: port, containing: foreignListener.processIdentifier))

        try "\(project.processIdentifier)\n".write(
            to: configuration.appPIDFileURL,
            atomically: true,
            encoding: .utf8
        )
        let evidence = ProcessInspector.evidence(
            pidFileURL: configuration.appPIDFileURL,
            port: port,
            kind: .bossWeb,
            rootURL: configuration.rootURL,
            bossDirectoryURL: configuration.bossDirectoryURL
        )

        XCTAssertTrue(evidence.confirmed)
        XCTAssertFalse(evidence.listeningConfirmed)
        XCTAssertEqual(evidence.pid, project.processIdentifier)
    }

    func testHealthEvidenceAcceptsCorrectListenerAndConfirmedParentChildChain() throws {
        let root = try makeProjectRoot()
        let configuration = ProjectConfiguration(rootURL: root)
        let port = nextTestPort()

        let directListener = try launchAliasProcess(
            alias: "node vite/bin/vite.js --port \(port)",
            executable: "/usr/bin/nc",
            arguments: ["-l", "127.0.0.1", "\(port)"],
            currentDirectory: configuration.bossDirectoryURL
        )
        defer { terminateTestProcess(directListener) }
        XCTAssertTrue(waitUntilListening(port: port, containing: directListener.processIdentifier))
        let directEvidence = ProcessInspector.evidence(
            pidFileURL: nil,
            port: port,
            kind: .bossWeb,
            rootURL: configuration.rootURL,
            bossDirectoryURL: configuration.bossDirectoryURL
        )
        XCTAssertTrue(directEvidence.confirmed)
        XCTAssertTrue(directEvidence.listeningConfirmed)
        XCTAssertEqual(directEvidence.pid, directListener.processIdentifier)
        terminateTestProcess(directListener)

        let chainScript = configuration.bossDirectoryURL.appendingPathComponent("vite-parent-wrapper.sh")
        try "#!/bin/bash\n/usr/bin/nc -l 127.0.0.1 \"$2\" >/dev/null 2>&1 &\nchild=$!\ncleanup() { kill \"$child\" >/dev/null 2>&1 || true; wait \"$child\" 2>/dev/null || true; exit 0; }\ntrap cleanup TERM INT\nwait \"$child\"\n"
            .write(to: chainScript, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: chainScript.path)

        let parent = try launchProcess(
            executable: "/bin/bash",
            arguments: [chainScript.path, "--port", "\(port)"],
            currentDirectory: configuration.bossDirectoryURL
        )
        defer { terminateTestProcess(parent) }
        XCTAssertTrue(waitUntilListening(port: port))
        try "\(parent.processIdentifier)\n".write(
            to: configuration.appPIDFileURL,
            atomically: true,
            encoding: .utf8
        )
        let chainEvidence = ProcessInspector.evidence(
            pidFileURL: configuration.appPIDFileURL,
            port: port,
            kind: .bossWeb,
            rootURL: configuration.rootURL,
            bossDirectoryURL: configuration.bossDirectoryURL
        )
        XCTAssertTrue(chainEvidence.confirmed)
        XCTAssertTrue(chainEvidence.listeningConfirmed)
        XCTAssertNotEqual(chainEvidence.pid, parent.processIdentifier)
        XCTAssertTrue(chainEvidence.source.contains("父子进程链"))
    }

    func testHealthEvidenceRejectsListenerThatIsAnAncestorOfProjectPID() throws {
        let root = try makeProjectRoot()
        let configuration = ProjectConfiguration(rootURL: root)
        let port = nextTestPort()
        let listenerScript = configuration.bossDirectoryURL.appendingPathComponent("vite-ancestor-wrapper.py")

        try """
        import os
        import socket
        import subprocess
        import sys
        import time

        port = int(sys.argv[1])
        pid_file = sys.argv[2]
        working_directory = sys.argv[3]
        child = subprocess.Popen(
            [
                "/bin/bash",
                "-c",
                "exec -a 'node vite/bin/vite.js --port \(port)' /bin/sleep 60",
            ],
            cwd=working_directory,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen(1)
        try:
            while True:
                time.sleep(1)
        finally:
            child.terminate()
            child.wait()
            server.close()
        """.write(to: listenerScript, atomically: true, encoding: .utf8)

        let listener = try launchProcess(
            executable: "/usr/bin/python3",
            arguments: [listenerScript.path, "\(port)", configuration.appPIDFileURL.path, configuration.bossDirectoryURL.path],
            currentDirectory: configuration.bossDirectoryURL
        )
        defer { terminateTestProcess(listener) }

        XCTAssertTrue(waitUntilListening(port: port, containing: listener.processIdentifier))
        let evidence = ProcessInspector.evidence(
            pidFileURL: configuration.appPIDFileURL,
            port: port,
            kind: .bossWeb,
            rootURL: configuration.rootURL,
            bossDirectoryURL: configuration.bossDirectoryURL
        )

        XCTAssertTrue(evidence.confirmed)
        XCTAssertFalse(evidence.listeningConfirmed)
        XCTAssertNotEqual(evidence.pid, listener.processIdentifier)
        XCTAssertEqual(evidence.source, "项目 PID 文件")
    }

    func testControllerRejectsOverlappingActionsWithoutTouchingFormalServices() throws {
        let root = try makeProjectRoot(
            startBody: "#!/bin/bash\nsleep 0.5\nexit 0\n",
            bossPort: nextTestPort()
        )
        let logURL = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("cdjg-console-test-\(UUID().uuidString).log")
        let logStore = LogStore(fileURL: logURL)
        let controller = ServiceController(configuration: ProjectConfiguration(rootURL: root), logStore: logStore)

        let first = expectation(description: "first action")
        let second = expectation(description: "overlap rejected")
        let stopped = expectation(description: "managed boss stopped")
        controller.perform(.startAll) { result in
            XCTAssertTrue(result.succeeded)
            XCTAssertFalse(result.message.contains("(boss.message)"))
            XCTAssertFalse(result.message.contains("(pid)"))
            XCTAssertTrue(FileManager.default.fileExists(atPath: root.appendingPathComponent(".app.pid").path))
            if let pidText = try? String(contentsOf: root.appendingPathComponent(".app.pid"), encoding: .utf8) {
                XCTAssertNotEqual(pidText.trimmingCharacters(in: .whitespacesAndNewlines), "(pid)")
                XCTAssertNotNil(Int32(pidText.trimmingCharacters(in: .whitespacesAndNewlines)))
            } else {
                XCTFail("未写入 .app.pid")
            }
            first.fulfill()
            controller.perform(.stopAll) { stopResult in
                XCTAssertTrue(stopResult.succeeded)
                XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent(".app.pid").path))
                stopped.fulfill()
            }
        }
        controller.perform(.restartAll) { result in
            XCTAssertFalse(result.succeeded)
            XCTAssertEqual(result.message, "已有控制操作正在进行，请稍候")
            second.fulfill()
        }
        wait(for: [first, second, stopped], timeout: 10)
        try? FileManager.default.removeItem(at: logURL)
    }

    func testCoreStartFailureDoesNotLaunchBoss() throws {
        let root = try makeProjectRoot(
            startBody: "#!/bin/bash\nexit 7\n",
            bossPort: nextTestPort()
        )
        let logURL = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("cdjg-console-start-failure-\(UUID().uuidString).log")
        let controller = ServiceController(
            configuration: ProjectConfiguration(rootURL: root),
            logStore: LogStore(fileURL: logURL)
        )
        let finished = expectation(description: "core start failure")
        controller.perform(.startAll) { result in
            XCTAssertFalse(result.succeeded)
            XCTAssertEqual(result.exitCode, 7)
            XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent(".app.pid").path))
            finished.fulfill()
        }
        wait(for: [finished], timeout: 5)
        try? FileManager.default.removeItem(at: logURL)
    }

    func testBossStartFailureRollsBackCoreServices() throws {
        let marker = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("cdjg-console-rollback-\(UUID().uuidString).marker")
        let root = try makeProjectRoot(
            startBody: "#!/bin/bash\nexit 0\n",
            stopBody: "#!/bin/bash\ntouch '\(marker.path)'\nexit 0\n",
            includeBossRuntime: false,
            bossPort: nextTestPort()
        )
        let logURL = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("cdjg-console-boss-failure-\(UUID().uuidString).log")
        let controller = ServiceController(
            configuration: ProjectConfiguration(rootURL: root),
            logStore: LogStore(fileURL: logURL)
        )
        let finished = expectation(description: "boss failure rollback")
        controller.perform(.startAll) { result in
            XCTAssertFalse(result.succeeded)
            XCTAssertTrue(result.message.contains("老板驾驶舱"))
            XCTAssertTrue(FileManager.default.fileExists(atPath: marker.path))
            XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent(".app.pid").path))
            finished.fulfill()
        }
        wait(for: [finished], timeout: 5)
        try? FileManager.default.removeItem(at: marker)
        try? FileManager.default.removeItem(at: logURL)
    }

    func testStartAndRestartReplaceConfirmedBossListenerWithoutPID() throws {
        for action in [ControlAction.startAll, ControlAction.restartAll] {
            let port = nextTestPort()
            let eventURL = URL(fileURLWithPath: NSTemporaryDirectory())
                .appendingPathComponent("cdjg-console-replace-\(UUID().uuidString).events")
            let root = try makeProjectRoot(
                startBody: "#!/bin/bash\necho start >> '\(eventURL.path)'\nexit 0\n",
                stopBody: "#!/bin/bash\necho stop >> '\(eventURL.path)'\nexit 0\n"
            )
            try isolatedPorts(bossPort: port).write(
                to: root.appendingPathComponent(".env"),
                atomically: true,
                encoding: .utf8
            )
            let configuration = ProjectConfiguration(rootURL: root)
            let oldBoss = try launchAliasProcess(
                alias: "node \(configuration.bossViteEntryURL.path) preview --host 127.0.0.1 --port \(port) --strictPort",
                executable: "/usr/bin/nc",
                arguments: ["-l", "127.0.0.1", "\(port)"],
                currentDirectory: configuration.bossDirectoryURL
            )
            defer { terminateTestProcess(oldBoss) }
            XCTAssertTrue(waitUntilListening(port: port, containing: oldBoss.processIdentifier))
            XCTAssertFalse(FileManager.default.fileExists(atPath: configuration.appPIDFileURL.path))

            let logURL = URL(fileURLWithPath: NSTemporaryDirectory())
                .appendingPathComponent("cdjg-console-replace-\(UUID().uuidString).log")
            let controller = ServiceController(
                configuration: configuration,
                logStore: LogStore(fileURL: logURL)
            )
            let finished = expectation(description: "\(action.displayName) replaces old Boss")
            var result: ActionResult?
            controller.perform(action) {
                result = $0
                finished.fulfill()
            }
            wait(for: [finished], timeout: 15)

            XCTAssertTrue(result?.succeeded == true, result?.message ?? "未收到启动结果")
            XCTAssertFalse(ProcessInspector.isAlive(oldBoss.processIdentifier))

            let newPID = try XCTUnwrap(ProcessInspector.readPID(at: configuration.appPIDFileURL))
            XCTAssertNotEqual(newPID, oldBoss.processIdentifier)
            XCTAssertTrue(
                ProcessInspector.evidence(
                    forPID: newPID,
                    port: port,
                    kind: .bossWeb,
                    rootURL: configuration.rootURL,
                    bossDirectoryURL: configuration.bossDirectoryURL
                )?.confirmed == true
            )

            let events = try String(contentsOf: eventURL, encoding: .utf8)
                .split(whereSeparator: \.isNewline)
                .map(String.init)
            XCTAssertEqual(events, ["stop", "start"], "\(action.displayName) 应只执行一次停止再启动")

            XCTAssertTrue(ProcessInspector.sendSignal(15, to: newPID))
            XCTAssertTrue(waitUntilStoppedForTest(newPID))
            try? FileManager.default.removeItem(at: configuration.appPIDFileURL)
            try? FileManager.default.removeItem(at: eventURL)
            try? FileManager.default.removeItem(at: logURL)
            try? FileManager.default.removeItem(at: root)
        }
    }

    func testStartAndRestartForceReplaceUnconfirmedBossListener() throws {
        for action in [ControlAction.startAll, ControlAction.restartAll] {
            let port = nextTestPort()
            let eventURL = URL(fileURLWithPath: NSTemporaryDirectory())
                .appendingPathComponent("cdjg-console-foreign-\(UUID().uuidString).events")
            let root = try makeProjectRoot(
                startBody: "#!/bin/bash\necho start >> '\(eventURL.path)'\nexit 0\n",
                stopBody: "#!/bin/bash\necho stop >> '\(eventURL.path)'\nexit 0\n"
            )
            try isolatedPorts(bossPort: port).write(
                to: root.appendingPathComponent(".env"),
                atomically: true,
                encoding: .utf8
            )
            let configuration = ProjectConfiguration(rootURL: root)
            let foreignDirectory = root
                .deletingLastPathComponent()
                .appendingPathComponent("cdjg-foreign-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: foreignDirectory, withIntermediateDirectories: true)
            defer { try? FileManager.default.removeItem(at: foreignDirectory) }

            let foreignBoss = try launchAliasProcess(
                alias: "node /tmp/foreign-vite.js preview --host 127.0.0.1 --port \(port) --strictPort",
                executable: "/usr/bin/nc",
                arguments: ["-l", "127.0.0.1", "\(port)"],
                currentDirectory: foreignDirectory
            )
            defer { terminateTestProcess(foreignBoss) }
            XCTAssertTrue(waitUntilListening(port: port, containing: foreignBoss.processIdentifier))

            let logURL = URL(fileURLWithPath: NSTemporaryDirectory())
                .appendingPathComponent("cdjg-console-foreign-\(UUID().uuidString).log")
            let controller = ServiceController(
                configuration: configuration,
                logStore: LogStore(fileURL: logURL)
            )
            let finished = expectation(description: "强制替换端口旧进程")
            var result: ActionResult?
            controller.perform(action) {
                result = $0
                finished.fulfill()
            }
            wait(for: [finished], timeout: 10)

            XCTAssertTrue(result?.succeeded == true, result?.message ?? "未收到启动结果")
            XCTAssertFalse(ProcessInspector.isAlive(foreignBoss.processIdentifier))
            let events = try String(contentsOf: eventURL, encoding: .utf8)
                .split(whereSeparator: \.isNewline).map(String.init)
            XCTAssertEqual(events, ["stop", "start"])
            let newPID = try XCTUnwrap(ProcessInspector.readPID(at: configuration.appPIDFileURL))
            XCTAssertNotEqual(newPID, foreignBoss.processIdentifier)
            XCTAssertTrue(ProcessInspector.sendSignal(9, to: newPID))
            XCTAssertTrue(waitUntilStoppedForTest(newPID))

            try? FileManager.default.removeItem(at: logURL)
            try? FileManager.default.removeItem(at: root)
        }
    }

    func testForceStopClearsAllServicePortsAndPIDChildrenButLeavesOtherPorts() throws {
        let root = try makeProjectRoot(bossPort: nextTestPort())
        defer { try? FileManager.default.removeItem(at: root) }
        let config = ProjectConfiguration(rootURL: root, environment: [:])
        let ports = [config.taxPort, config.ragPort, config.idpPort, config.localModelPort, config.bossPort]
        var processes: [Process] = []
        defer { processes.forEach { terminateTestProcess($0) } }
        for port in ports {
            let listener = try launchAliasProcess(
                alias: "unknown-service",
                executable: "/usr/bin/nc",
                arguments: ["-l", "127.0.0.1", "\(port)"],
                currentDirectory: URL(fileURLWithPath: NSTemporaryDirectory())
            )
            processes.append(listener)
            XCTAssertTrue(waitUntilListening(port: port, containing: listener.processIdentifier))
        }
        let otherPort = nextTestPort()
        let unrelated = try launchAliasProcess(
            alias: "unrelated-service", executable: "/usr/bin/nc",
            arguments: ["-l", "127.0.0.1", "\(otherPort)"], currentDirectory: root
        )
        defer { terminateTestProcess(unrelated) }
        XCTAssertTrue(waitUntilListening(port: otherPort, containing: unrelated.processIdentifier))
        let childFile = root.appendingPathComponent("child.pid")
        let parent = Process()
        parent.executableURL = URL(fileURLWithPath: "/bin/sh")
        parent.arguments = ["-c", "sleep 120 & echo $! > '\(childFile.path)'; wait"]
        try parent.run()
        defer { terminateTestProcess(parent) }
        let deadline = Date().addingTimeInterval(2)
        while !FileManager.default.fileExists(atPath: childFile.path), Date() < deadline {
            Thread.sleep(forTimeInterval: 0.02)
        }
        let childPID = try XCTUnwrap(ProcessInspector.readPID(at: childFile))
        defer { _ = ProcessInspector.sendSignal(9, to: childPID) }
        try String(parent.processIdentifier).write(to: root.appendingPathComponent(".tax.pid"), atomically: true, encoding: .utf8)
        try "invalid".write(to: root.appendingPathComponent(".app.pid"), atomically: true, encoding: .utf8)

        let result = ForceServiceStopper.stop(configuration: config)
        XCTAssertTrue(result.succeeded, result.message)
        for port in ports { XCTAssertTrue(ProcessInspector.listeningPIDs(port: port).isEmpty) }
        parent.waitUntilExit()
        XCTAssertFalse(parent.isRunning)
        XCTAssertTrue(waitUntilStoppedForTest(childPID), "PID记录的子进程必须停止")
        XCTAssertTrue(ProcessInspector.isAlive(unrelated.processIdentifier))
        XCTAssertFalse(FileManager.default.fileExists(atPath: config.appPIDFileURL.path))
        XCTAssertTrue(ForceServiceStopper.stop(configuration: config).succeeded, "重复停止必须幂等")
    }

    func testManagedMessagesDoNotContainInterpolationPlaceholders() throws {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let sourceFiles = [
            packageRoot.appendingPathComponent("Sources/ChengduConstructionConsole/ServiceController.swift"),
            packageRoot.appendingPathComponent("Sources/ChengduConstructionConsole/ProcessInspector.swift")
        ]
        let forbiddenPlaceholders = [
            "(boss.message)", "(stop.message)", "(start.message)", "(core.message)",
            "(existingPID)", "(cleanupMessage)", "(rollbackMessage)", "(mode)",
            "(configuration.bossPort)", "(pid)", "(port)"
        ]
        for sourceFile in sourceFiles {
            let source = try String(contentsOf: sourceFile, encoding: .utf8)
            for placeholder in forbiddenPlaceholders {
                XCTAssertFalse(
                    containsUnescaped(placeholder, in: source),
                    "发现未展开的字符串插值占位符 \(placeholder)：\(sourceFile.path)"
                )
            }
        }
    }

    private func containsUnescaped(_ placeholder: String, in source: String) -> Bool {
        var index = source.startIndex
        var insideString = false
        var escaped = false
        while index < source.endIndex {
            let character = source[index]
            if insideString {
                if escaped {
                    escaped = false
                } else if character == "\\" {
                    escaped = true
                } else if character == "\"" {
                    insideString = false
                } else if source[index...].hasPrefix(placeholder) {
                    return true
                }
            } else if character == "\"" {
                insideString = true
            }
            index = source.index(after: index)
        }
        return false
    }

    private func makeProjectRoot(
        startBody: String = "#!/bin/bash\nexit 0\n",
        stopBody: String = "#!/bin/bash\nexit 0\n",
        includeBossRuntime: Bool = true,
        bossPort: Int? = nil
    ) throws -> URL {
        let root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("cdjg-console-root-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try startBody.write(to: root.appendingPathComponent("start_all.sh"), atomically: true, encoding: .utf8)
        try stopBody.write(to: root.appendingPathComponent("stop_all.sh"), atomically: true, encoding: .utf8)
        do {
            try isolatedPorts(bossPort: bossPort ?? nextTestPort()).write(
                to: root.appendingPathComponent(".env"),
                atomically: true,
                encoding: .utf8
            )
        }
        if includeBossRuntime {
            let viteShim = root
                .appendingPathComponent("source_code/0.3_老板端安卓App_天府掌舵/node_modules/.bin/vite")
            try FileManager.default.createDirectory(
                at: viteShim.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try "#!/bin/bash\ntrap 'exit 0' TERM INT\nwhile true; do read -r -t 1 _ || true; done\n"
                .write(to: viteShim, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: viteShim.path)
        }
        return root
    }

    private func isolatedPorts(bossPort: Int) -> String {
        return "BOSS_PORT=\(bossPort)\nTAX_PORT=\(nextTestPort())\nPROJECT_RAG_PORT=\(nextTestPort())\nIDP_PORT=\(nextTestPort())\nLOCAL_LLM_PORT=\(nextTestPort())\n"
    }

    private func nextTestPort() -> Int {
        for _ in 0..<20 {
            let candidate = Int.random(in: 20_000...40_000)
            if ProcessInspector.listeningPIDs(port: candidate).isEmpty {
                return candidate
            }
        }
        return 39_999
    }

    private func launchAliasProcess(
        alias: String,
        executable: String,
        arguments: [String],
        currentDirectory: URL
    ) throws -> Process {
        let quotedAlias = alias.replacingOccurrences(of: "'", with: "'\\''")
        let quotedArguments = arguments.map { argument in
            "'\(argument.replacingOccurrences(of: "'", with: "'\\''"))'"
        }.joined(separator: " ")
        return try launchProcess(
            executable: "/bin/bash",
            arguments: ["-c", "exec -a '\(quotedAlias)' \(executable) \(quotedArguments)"],
            currentDirectory: currentDirectory
        )
    }

    private func launchProcess(
        executable: String,
        arguments: [String],
        currentDirectory: URL
    ) throws -> Process {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.currentDirectoryURL = currentDirectory
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        return process
    }

    private func waitUntilListening(
        port: Int,
        containing pid: Int32? = nil,
        timeout: TimeInterval = 3
    ) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let pids = ProcessInspector.listeningPIDs(port: port)
            if !pids.isEmpty, pid == nil || pids.contains(pid!) {
                return true
            }
            Thread.sleep(forTimeInterval: 0.05)
        }
        return false
    }

    private func waitUntilStoppedForTest(_ pid: Int32, timeout: TimeInterval = 5) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while ProcessInspector.isAlive(pid) && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        return !ProcessInspector.isAlive(pid)
    }

    private func terminateTestProcess(_ process: Process) {
        guard process.isRunning else { return }
        process.terminate()
        process.waitUntilExit()
    }

}
