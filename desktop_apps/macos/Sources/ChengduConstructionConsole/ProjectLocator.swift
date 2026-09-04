import Foundation

struct ProjectLocator {
    static let storedRootKey = "projectRootPath"
    static let environmentKeys = ["CHENGDU_PROJECT_ROOT", "CDJG_PROJECT_ROOT"]

    static func locate(
        bundleURL: URL = Bundle.main.bundleURL,
        currentDirectoryURL: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        environment: [String: String] = ProcessInfo.processInfo.environment,
        defaults: UserDefaults = .standard
    ) -> URL? {
        for key in environmentKeys {
            if let value = environment[key], let url = validatedRoot(URL(fileURLWithPath: value)) {
                return url
            }
        }

        if let stored = defaults.string(forKey: storedRootKey),
           let url = validatedRoot(URL(fileURLWithPath: stored)) {
            return url
        }

        for start in [bundleURL, currentDirectoryURL] {
            if let url = findInAncestors(of: start) {
                return url
            }
        }
        return nil
    }

    static func findInAncestors(of startURL: URL) -> URL? {
        var candidate = startURL.standardizedFileURL
        if !candidate.hasDirectoryPath {
            candidate.deleteLastPathComponent()
        }

        var visitedPaths = Set<String>()
        while visitedPaths.insert(candidate.path).inserted {
            if let valid = validatedRoot(candidate) { return valid }
            let parent = candidate.deletingLastPathComponent().standardizedFileURL
            guard parent.path != candidate.path else { return nil }
            candidate = parent
        }
        return nil
    }

    @discardableResult
    static func persist(_ rootURL: URL, defaults: UserDefaults = .standard) -> Bool {
        guard let valid = validatedRoot(rootURL) else { return false }
        defaults.set(valid.path, forKey: storedRootKey)
        return true
    }

    static func validatedRoot(_ rootURL: URL) -> URL? {
        let url = rootURL.standardizedFileURL
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            return nil
        }
        guard FileManager.default.isReadableFile(atPath: url.appendingPathComponent("start_all.sh").path),
              FileManager.default.isReadableFile(atPath: url.appendingPathComponent("stop_all.sh").path) else {
            return nil
        }
        return url
    }
}
