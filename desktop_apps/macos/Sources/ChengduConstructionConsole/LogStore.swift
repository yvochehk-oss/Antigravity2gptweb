import Foundation
import AppKit

final class LogStore {
    let fileURL: URL
    private let lock = NSLock()

    private static let timestampFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    init(fileURL: URL? = nil, fileManager: FileManager = .default) {
        if let fileURL {
            let directory = fileURL.deletingLastPathComponent()
            try? fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
            self.fileURL = fileURL
            return
        }
        let library = fileManager.urls(for: .libraryDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        let directory = library
            .appendingPathComponent("Logs", isDirectory: true)
            .appendingPathComponent("成都建工控制台", isDirectory: true)
        try? fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        self.fileURL = directory.appendingPathComponent("控制台.log")
    }

    func record(_ message: String, date: Date = Date()) {
        let safe = Self.sanitize(message)
        let line = "[\(Self.timestampFormatter.string(from: date))] \(safe)\n"
        lock.lock()
        defer { lock.unlock() }
        if !FileManager.default.fileExists(atPath: fileURL.path) {
            FileManager.default.createFile(atPath: fileURL.path, contents: nil)
        }
        guard let handle = try? FileHandle(forWritingTo: fileURL) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: Data(line.utf8))
    }

    func open() {
        if !FileManager.default.fileExists(atPath: fileURL.path) {
            record("尚无控制操作记录")
        }
        NSWorkspace.shared.open(fileURL)
    }

    private static func sanitize(_ message: String) -> String {
        let lowercased = message.lowercased()
        let sensitiveMarkers = [
            "password", "passwd", "secret", "token", "api_key", "apikey",
            "authorization", "cookie", "private_key", "access_key"
        ]
        if sensitiveMarkers.contains(where: { lowercased.contains($0) }) {
            return "[已隐藏敏感信息]"
        }
        return message
            .replacingOccurrences(of: "\r", with: " ")
            .replacingOccurrences(of: "\n", with: " ")
    }
}
