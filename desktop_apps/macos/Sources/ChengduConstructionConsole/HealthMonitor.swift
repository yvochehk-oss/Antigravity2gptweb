import Foundation

final class HealthMonitor {
    typealias UpdateHandler = (HealthSnapshot) -> Void

    // Windows fc7c5ad parity: probes may take up to 8 seconds and only become
    // degraded/slow at 6 seconds.  Keep these internal so regression tests can
    // lock the cross-platform contract without exposing them in the UI.
    static let requestTimeout: TimeInterval = 8
    static let slowThreshold: TimeInterval = 6
    static let requestGuardTimeout: TimeInterval = 9

    private let configuration: ProjectConfiguration
    private let probeQueue = DispatchQueue(
        label: "cn.cdjg.controlpanel.health",
        qos: .utility,
        attributes: .concurrent
    )
    private let stateLock = NSLock()
    private var refreshInFlight = false
    /// 标记是否有被丢弃的刷新等待合并执行，确保状态新鲜度延迟不超过一次探测时长。
    private var pendingRefresh = false
    private let onUpdate: UpdateHandler

    init(configuration: ProjectConfiguration, onUpdate: @escaping UpdateHandler) {
        self.configuration = configuration
        self.onUpdate = onUpdate
    }

    func refresh() {
        stateLock.lock()
        if refreshInFlight {
            pendingRefresh = true
            stateLock.unlock()
            return
        }
        refreshInFlight = true
        stateLock.unlock()

        probeQueue.async { [weak self] in
            guard let self else { return }
            let snapshot = Self.collect(configuration: self.configuration)
            self.stateLock.lock()
            self.refreshInFlight = false
            let hadPending = self.pendingRefresh
            self.pendingRefresh = false
            self.stateLock.unlock()
            DispatchQueue.main.async {
                self.onUpdate(snapshot)
                if hadPending {
                    self.refresh()
                }
            }
        }
    }

    static func collect(configuration: ProjectConfiguration, now: Date = Date()) -> HealthSnapshot {
        let resultLock = NSLock()
        var results: [ServiceID: ServiceSnapshot] = [:]
        let group = DispatchGroup()
        let queue = DispatchQueue(
            label: "cn.cdjg.controlpanel.health.collect",
            qos: .utility,
            attributes: .concurrent
        )

        for service in ServiceID.allCases {
            group.enter()
            queue.async {
                let snapshot = Self.probe(service: service, configuration: configuration, now: now)
                resultLock.lock()
                results[service] = snapshot
                resultLock.unlock()
                group.leave()
            }
        }
        group.wait()

        let ordered = ServiceID.allCases.compactMap { results[$0] }
        return HealthSnapshot(
            services: results,
            overall: HealthSnapshot.overallState(for: ordered),
            checkedAt: now
        )
    }

    private static func probe(
        service: ServiceID,
        configuration: ProjectConfiguration,
        now: Date
    ) -> ServiceSnapshot {
        if service == .idp && !configuration.idpEnabled {
            return ServiceSnapshot(
                service: service,
                state: .stopped,
                detail: "已按配置关闭",
                endpoint: configuration.idpHealthURL.absoluteString,
                processPID: nil,
                processConfirmed: false,
                checkedAt: now
            )
        }
        if service == .localModel && !configuration.localModelEnabled {
            return ServiceSnapshot(
                service: service,
                state: .stopped,
                detail: "已按配置关闭",
                endpoint: configuration.localModelHealthURL.absoluteString,
                processPID: nil,
                processConfirmed: false,
                checkedAt: now
            )
        }

        let definition = Definition(service: service, configuration: configuration)
        let evidence = ProcessInspector.evidence(
            pidFileURL: definition.pidFileURL,
            port: definition.port,
            kind: definition.processKind,
            rootURL: configuration.rootURL,
            bossDirectoryURL: configuration.bossDirectoryURL
        )

        guard let response = request(definition.healthURL) else {
            return unavailableOrStarting(
                service: service,
                endpoint: definition.healthURL,
                evidence: evidence,
                now: now
            )
        }

        guard (200...299).contains(response.statusCode) else {
            return unavailableOrStarting(
                service: service,
                endpoint: definition.healthURL,
                evidence: evidence,
                now: now,
                detail: "健康接口返回异常（HTTP \(response.statusCode)）"
            )
        }

        if service == .localModel {
            return localModelSnapshot(
                response: response,
                definition: definition,
                evidence: evidence,
                now: now
            )
        }

        if service == .boss {
            let slow = response.elapsedSeconds >= slowThreshold
            let state: ServiceState
            let detail: String
            if evidence.listeningConfirmed {
                state = slow ? .partial : .normal
                detail = slow
                    ? "本地网页服务可用，但响应缓慢（\(formatSeconds(response.elapsedSeconds)) 秒）"
                    : "本地网页服务运行正常"
            } else if evidence.confirmed {
                state = .partial
                detail = "网页可访问，但项目进程未确认监听目标端口"
            } else {
                state = .partial
                detail = "网页可访问，但未确认属于本项目的前端进程"
            }
            return ServiceSnapshot(
                service: service,
                state: state,
                detail: detail,
                endpoint: definition.healthURL.absoluteString,
                processPID: evidence.pid,
                processConfirmed: evidence.confirmed,
                checkedAt: now
            )
        }

        let payloadStatus = healthStatus(from: response.data)
        let payloadIsDegraded = payloadStatus == "degraded" || payloadStatus == "down"
        let slow = response.elapsedSeconds >= slowThreshold
        let state: ServiceState
        let detail: String
        if payloadIsDegraded {
            state = .partial
            detail = evidence.confirmed
                ? "接口可访问，但依赖状态存在异常"
                : "接口可访问，但未确认进程归属；依赖状态存在异常"
        } else if evidence.listeningConfirmed {
            state = slow ? .partial : .normal
            detail = slow
                ? "健康接口、目标端口和项目进程均已确认，但响应缓慢（\(formatSeconds(response.elapsedSeconds)) 秒）"
                : "健康接口、目标端口监听者与项目进程均已确认"
        } else if evidence.confirmed {
            state = .partial
            detail = "接口可访问，但项目进程未确认监听目标端口"
        } else {
            state = .partial
            detail = "健康接口可访问，但未确认属于本项目的进程"
        }

        return ServiceSnapshot(
            service: service,
            state: state,
            detail: detail,
            endpoint: definition.healthURL.absoluteString,
            processPID: evidence.pid,
            processConfirmed: evidence.confirmed,
            checkedAt: now
        )
    }

    private static func localModelSnapshot(
        response: HTTPResponse,
        definition: Definition,
        evidence: ProcessEvidence,
        now: Date
    ) -> ServiceSnapshot {
        let healthStatusValue = healthStatus(from: response.data)
        let healthIsReady = healthStatusValue == nil || healthStatusValue == "ok" || healthStatusValue == "ready"
        guard healthIsReady else {
            return unavailableOrStarting(
                service: .localModel,
                endpoint: definition.healthURL,
                evidence: evidence,
                now: now,
                detail: "模型健康接口尚未就绪"
            )
        }

        guard let modelResponse = request(definition.modelListURL),
              (200...299).contains(modelResponse.statusCode),
              modelListIsNonEmpty(modelResponse.data) else {
            let state: ServiceState = evidence.confirmed ? .partial : .unavailable
            return ServiceSnapshot(
                service: .localModel,
                state: state,
                detail: evidence.confirmed
                    ? "健康接口已响应，但模型列表未返回已加载模型"
                    : "健康接口已响应，但未确认 llama-server 进程及已加载模型",
                endpoint: definition.healthURL.absoluteString,
                processPID: evidence.pid,
                processConfirmed: evidence.confirmed,
                checkedAt: now
            )
        }

        let totalElapsed = response.elapsedSeconds + modelResponse.elapsedSeconds
        let slow = response.elapsedSeconds >= slowThreshold || modelResponse.elapsedSeconds >= slowThreshold
        let state: ServiceState
        let detail: String
        if evidence.listeningConfirmed {
            state = slow ? .partial : .normal
            detail = slow
                ? "模型接口、端口、已加载模型和项目进程均已确认，但响应缓慢（合计 \(formatSeconds(totalElapsed)) 秒）"
                : "模型健康接口、目标端口监听者、已加载模型和项目进程均已确认"
        } else if evidence.confirmed {
            state = .partial
            detail = "模型接口可用，但项目进程未确认监听目标端口"
        } else {
            state = .partial
            detail = "模型接口可用，但未确认属于本项目的 llama-server 进程"
        }
        return ServiceSnapshot(
            service: .localModel,
            state: state,
            detail: detail,
            endpoint: definition.healthURL.absoluteString,
            processPID: evidence.pid,
            processConfirmed: evidence.confirmed,
            checkedAt: now
        )
    }

    private static func unavailableOrStarting(
        service: ServiceID,
        endpoint: URL,
        evidence: ProcessEvidence,
        now: Date,
        detail: String? = nil
    ) -> ServiceSnapshot {
        let hasRecentProcess = evidence.confirmed && (evidence.elapsedSeconds == nil || evidence.elapsedSeconds! <= 180)
        let state: ServiceState
        let message: String
        if hasRecentProcess {
            state = .starting
            message = detail ?? "项目进程已启动，正在等待健康接口就绪"
        } else if evidence.confirmed {
            state = .unavailable
            message = detail ?? "项目进程存在，但健康接口未通过"
        } else {
            state = .stopped
            message = "未发现已确认的项目进程"
        }
        return ServiceSnapshot(
            service: service,
            state: state,
            detail: message,
            endpoint: endpoint.absoluteString,
            processPID: evidence.pid,
            processConfirmed: evidence.confirmed,
            checkedAt: now
        )
    }

    private struct Definition {
        let healthURL: URL
        let modelListURL: URL
        let pidFileURL: URL?
        let port: Int
        let processKind: ProcessKind

        init(service: ServiceID, configuration: ProjectConfiguration) {
            switch service {
            case .localModel:
                healthURL = configuration.localModelHealthURL
                modelListURL = configuration.localModelListURL
                pidFileURL = configuration.rootURL.appendingPathComponent(".local_llm.pid")
                port = configuration.localModelPort
                processKind = .localModel
            case .tax:
                healthURL = configuration.taxHealthURL
                modelListURL = healthURL
                pidFileURL = configuration.rootURL.appendingPathComponent(".tax.pid")
                port = configuration.taxPort
                processKind = .uvicorn
            case .rag:
                healthURL = configuration.ragHealthURL
                modelListURL = healthURL
                pidFileURL = configuration.rootURL.appendingPathComponent(".rag.pid")
                port = configuration.ragPort
                processKind = .uvicorn
            case .idp:
                healthURL = configuration.idpHealthURL
                modelListURL = healthURL
                pidFileURL = configuration.rootURL.appendingPathComponent(".idp.pid")
                port = configuration.idpPort
                processKind = .uvicorn
            case .boss:
                healthURL = configuration.bossURL
                modelListURL = healthURL
                pidFileURL = configuration.appPIDFileURL
                port = configuration.bossPort
                processKind = .bossWeb
            }
        }
    }

    private struct HTTPResponse {
        let statusCode: Int
        let data: Data
        let elapsedSeconds: TimeInterval
    }

    private static let sharedSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.httpMaximumConnectionsPerHost = 5
        config.timeoutIntervalForRequest = requestTimeout
        config.timeoutIntervalForResource = 10
        config.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        config.httpShouldSetCookies = false
        config.urlCache = nil
        return URLSession(configuration: config)
    }()

    private static func request(_ url: URL) -> HTTPResponse? {
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.timeoutInterval = requestTimeout
        request.setValue("成都建工控制台/3.0", forHTTPHeaderField: "User-Agent")

        let startedAt = Date()
        let semaphore = DispatchSemaphore(value: 0)
        var response: HTTPResponse?
        let task = sharedSession.dataTask(with: request) { data, urlResponse, _ in
            if let http = urlResponse as? HTTPURLResponse {
                response = HTTPResponse(
                    statusCode: http.statusCode,
                    data: data ?? Data(),
                    elapsedSeconds: Date().timeIntervalSince(startedAt)
                )
            }
            semaphore.signal()
        }
        task.resume()
        if semaphore.wait(timeout: .now() + requestGuardTimeout) == .timedOut {
            task.cancel()
            return nil
        }
        return response
    }

    private static func formatSeconds(_ value: TimeInterval) -> String {
        String(format: "%.1f", value)
    }

    private static func healthStatus(from data: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let dictionary = object as? [String: Any],
              let status = dictionary["status"] as? String else { return nil }
        return status.lowercased()
    }

    private static func modelListIsNonEmpty(_ data: Data) -> Bool {
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let dictionary = object as? [String: Any],
              let models = dictionary["data"] as? [Any] else { return false }
        return !models.isEmpty
    }
}
