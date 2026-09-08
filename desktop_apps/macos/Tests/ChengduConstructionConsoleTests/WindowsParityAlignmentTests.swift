import XCTest
@testable import ChengduConstructionConsole

final class WindowsParityAlignmentTests: XCTestCase {
    func testHealthProbeTimingMatchesWindowsFc7c5ad() {
        XCTAssertEqual(HealthMonitor.requestTimeout, 8)
        XCTAssertEqual(HealthMonitor.slowThreshold, 6)
        XCTAssertGreaterThan(HealthMonitor.requestGuardTimeout, HealthMonitor.requestTimeout)
    }

    func testStartupProtectionMatchesWindowsFc7c5ad() {
        XCTAssertEqual(ServiceController.startupVerificationTimeout, 90)
        XCTAssertGreaterThanOrEqual(
            ServiceController.coreStartScriptTimeout,
            ServiceController.startupVerificationTimeout
        )
        XCTAssertGreaterThanOrEqual(ServiceController.coreStartScriptTimeout, 180)
        XCTAssertEqual(ServiceController.stopScriptTimeout, 60)
    }

    func testExternalPythonUvicornInsideProjectWorkingDirectoryIsRecognized() {
        let root = URL(fileURLWithPath: "/tmp/cdjg-parity-root", isDirectory: true)
        let serviceDirectory = root.appendingPathComponent("source_code/service", isDirectory: true)

        XCTAssertTrue(ProcessInspector.matches(
            kind: .uvicorn,
            command: "/opt/homebrew/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8921",
            workingDirectory: serviceDirectory.path,
            port: 8921,
            rootURL: root,
            bossDirectoryURL: nil
        ))
    }

    func testSymlinkedProjectWorkingDirectoryIsRecognized() throws {
        let base = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("cdjg-parity-\(UUID().uuidString)", isDirectory: true)
        let root = base.appendingPathComponent("real-project", isDirectory: true)
        let serviceDirectory = root.appendingPathComponent("source_code/service", isDirectory: true)
        let alias = base.appendingPathComponent("project-link", isDirectory: true)
        try FileManager.default.createDirectory(at: serviceDirectory, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: root)
        defer { try? FileManager.default.removeItem(at: base) }

        let linkedServiceDirectory = alias.appendingPathComponent("source_code/service", isDirectory: true)
        XCTAssertTrue(ProcessInspector.matches(
            kind: .uvicorn,
            command: "/usr/local/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8922",
            workingDirectory: linkedServiceDirectory.path,
            port: 8922,
            rootURL: root,
            bossDirectoryURL: nil
        ))
    }

    func testExternalPythonWithoutProjectWorkingDirectoryRemainsRejected() {
        let root = URL(fileURLWithPath: "/tmp/cdjg-parity-root", isDirectory: true)

        XCTAssertFalse(ProcessInspector.matches(
            kind: .uvicorn,
            command: "/opt/homebrew/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8921",
            workingDirectory: "/tmp/foreign-runtime",
            port: 8921,
            rootURL: root,
            bossDirectoryURL: nil
        ))
    }
}
