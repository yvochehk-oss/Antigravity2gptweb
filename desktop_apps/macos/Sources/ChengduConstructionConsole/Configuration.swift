import Foundation
import os.log

/// Values that are safe and useful for the desktop controller to read from the
/// project environment.  Secrets are intentionally not parsed or inherited from
/// this allow-list.
struct ProjectConfiguration: Equatable {
    let rootURL: URL
    let taxPort: Int
    let ragPort: Int
    let idpPort: Int
    let localModelPort: Int
    let localModelHost: String
    let localModelEnabled: Bool
    let idpEnabled: Bool
    let bossPort: Int
    let bossDirectoryURL: URL

    static let defaultTaxPort = 8921
    static let defaultRAGPort = 8922
    static let defaultIDPPort = 8933
    static let defaultLocalModelPort = 8930
    static let defaultBossPort = 5173

    private static let allowedEnvironmentKeys: Set<String> = [
        "TAX_PORT",
        "PROJECT_RAG_PORT",
        "IDP_PORT",
        "IDP_ENABLED",
        "LOCAL_LLM_ENABLED",
        "LOCAL_LLM_HOST",
        "LOCAL_LLM_PORT",
        "BOSS_PORT"
    ]

    init(rootURL: URL, environment: [String: String] = ProcessInfo.processInfo.environment) {
        self.rootURL = rootURL.standardizedFileURL

        var values = Self.readSafeEnvironmentFile(at: rootURL.appendingPathComponent(".env"))
        for key in Self.allowedEnvironmentKeys {
            if let value = environment[key], !value.isEmpty {
                values[key] = value
            }
        }

        taxPort = Self.port(values["TAX_PORT"], fallback: Self.defaultTaxPort)
        ragPort = Self.port(values["PROJECT_RAG_PORT"], fallback: Self.defaultRAGPort)
        idpPort = Self.port(values["IDP_PORT"], fallback: Self.defaultIDPPort)
        localModelPort = Self.port(values["LOCAL_LLM_PORT"], fallback: Self.defaultLocalModelPort)
        localModelHost = Self.loopbackHost(values["LOCAL_LLM_HOST"])
        localModelEnabled = Self.boolean(values["LOCAL_LLM_ENABLED"], fallback: true)
        idpEnabled = Self.boolean(values["IDP_ENABLED"], fallback: true)
        bossPort = Self.port(values["BOSS_PORT"], fallback: Self.defaultBossPort)
        bossDirectoryURL = rootURL
            .appendingPathComponent("source_code", isDirectory: true)
            .appendingPathComponent("0.3_老板端安卓App_天府掌舵", isDirectory: true)
            .standardizedFileURL
    }

    var taxHealthURL: URL { localURL(port: taxPort, path: "/healthz") }
    var ragHealthURL: URL { localURL(port: ragPort, path: "/api/v1/health") }
    var idpHealthURL: URL { localURL(port: idpPort, path: "/health") }
    var localModelHealthURL: URL { localURL(port: localModelPort, path: "/health", host: localModelHost) }
    var localModelListURL: URL { localURL(port: localModelPort, path: "/v1/models", host: localModelHost) }
    var bossURL: URL { localURL(port: bossPort, path: "/") }

    /// The Boss web process is the only front-end process owned directly by
    /// the menu-bar controller.  Keep its PID at the V3.0 root so both the
    /// controller and the health monitor use the same ownership record.
    var appPIDFileURL: URL { rootURL.appendingPathComponent(".app.pid") }

    var bossDistIndexURL: URL {
        bossDirectoryURL
            .appendingPathComponent("dist", isDirectory: true)
            .appendingPathComponent("index.html")
    }

    var bossViteShimURL: URL {
        bossDirectoryURL
            .appendingPathComponent("node_modules", isDirectory: true)
            .appendingPathComponent(".bin", isDirectory: true)
            .appendingPathComponent("vite")
    }

    var bossViteEntryURL: URL {
        bossDirectoryURL
            .appendingPathComponent("node_modules", isDirectory: true)
            .appendingPathComponent("vite", isDirectory: true)
            .appendingPathComponent("bin", isDirectory: true)
            .appendingPathComponent("vite.js")
    }

    var startScriptURL: URL { rootURL.appendingPathComponent("start_all.sh") }
    var stopScriptURL: URL { rootURL.appendingPathComponent("stop_all.sh") }

    func localURL(port: Int, path: String, host: String = "127.0.0.1") -> URL {
        var components = URLComponents()
        components.scheme = "http"
        components.host = host
        components.port = port
        components.path = path
        return components.url ?? URL(string: "http://127.0.0.1:\(port)\(path)")!
    }

    private static func port(_ raw: String?, fallback: Int) -> Int {
        guard let raw, let value = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)), (1...65535).contains(value) else {
            return fallback
        }
        return value
    }

    private static func boolean(_ raw: String?, fallback: Bool) -> Bool {
        guard let raw else { return fallback }
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "1", "true", "yes", "on": return true
        case "0", "false", "no", "off": return false
        default: return fallback
        }
    }

    private static func loopbackHost(_ raw: String?) -> String {
        guard let value = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
            return "127.0.0.1"
        }
        let normalized = value.lowercased()
        switch normalized {
        case "127.0.0.1", "localhost", "::1": return normalized
        default: return "127.0.0.1"
        }
    }

    private static func readSafeEnvironmentFile(at url: URL) -> [String: String] {
        guard FileManager.default.fileExists(atPath: url.path) else { return [:] }
        warnIfEnvironmentFilePermissionsLoose(at: url)
        guard let content = try? String(contentsOf: url, encoding: .utf8) else { return [:] }
        var values: [String: String] = [:]
        for line in content.split(whereSeparator: \.isNewline) {
            var text = line.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty, !text.hasPrefix("#") else { continue }
            if text.hasPrefix("export ") {
                text.removeFirst("export ".count)
            }
            guard let separator = text.firstIndex(of: "=") else { continue }
            let key = String(text[..<separator]).trimmingCharacters(in: .whitespacesAndNewlines)
            guard allowedEnvironmentKeys.contains(key) else { continue }
            var value = String(text[text.index(after: separator)...]).trimmingCharacters(in: .whitespacesAndNewlines)
            if value.count >= 2,
               ((value.first == "\"" && value.last == "\"") || (value.first == "'" && value.last == "'")) {
                value.removeFirst()
                value.removeLast()
            }
            values[key] = value
        }
        return values
    }

    /// Surface a one-line system-log warning when the project `.env` is
    /// readable by group or other.  Loading is intentionally not blocked so
    /// that the controller can still start; the project rule "Secret 不得写入
    /// 代码、仓库、日志或交付包" forbids relying on the loose file alone.
    private static func warnIfEnvironmentFilePermissionsLoose(at url: URL) {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
              let posix = attributes[.posixPermissions] as? NSNumber else { return }
        let mode = posix.uint16Value & 0o777
        let looseBits = mode & 0o077
        guard looseBits != 0 else { return }
        // 写入系统日志（不会进入控制器用户日志），方便用户在 Console.app 中定位问题
        let message = "检测到 .env 文件权限 0\(String(mode, radix: 8))，组用户或所有用户可读；建议执行 chmod 600 \(url.path) 后重新启动控制台。"
        let logger = Logger(subsystem: "cn.cdjg.controlpanel.macos", category: "configuration")
        logger.error("\(message, privacy: .public)")
    }
}
