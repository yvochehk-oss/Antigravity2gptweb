import Foundation

/// A service whose state can be shown in the menu bar controller.
enum ServiceID: String, CaseIterable, Codable, Hashable {
    case localModel
    case tax
    case rag
    case idp
    case boss

    var displayName: String {
        switch self {
        case .localModel: return "本地语言模型"
        case .tax: return "税务系统"
        case .rag: return "资料与知识系统"
        case .idp: return "文档录入引擎"
        case .boss: return "老板驾驶舱"
        }
    }
}

/// The controller deliberately keeps the state vocabulary small and Chinese-facing.
/// Internal health payload values never escape into the menu without translation.
enum ServiceState: String, Codable, Equatable {
    case checking
    case starting
    case normal
    case partial
    case unavailable
    case stopped

    var title: String {
        switch self {
        case .checking: return "正在检查"
        case .starting: return "正在启动"
        case .normal: return "运行正常"
        case .partial: return "部分异常"
        case .unavailable: return "服务不可用"
        case .stopped: return "已停止"
        }
    }
}

struct ServiceSnapshot: Equatable {
    let service: ServiceID
    let state: ServiceState
    let detail: String
    let endpoint: String?
    let processPID: Int32?
    let processConfirmed: Bool
    let checkedAt: Date

    static func checking(_ service: ServiceID, now: Date = Date()) -> ServiceSnapshot {
        ServiceSnapshot(
            service: service,
            state: .checking,
            detail: "正在检查本机服务状态",
            endpoint: nil,
            processPID: nil,
            processConfirmed: false,
            checkedAt: now
        )
    }
}

struct HealthSnapshot: Equatable {
    let services: [ServiceID: ServiceSnapshot]
    let overall: ServiceState
    let checkedAt: Date

    static func checking(now: Date = Date()) -> HealthSnapshot {
        let states = Dictionary(uniqueKeysWithValues: ServiceID.allCases.map {
            ($0, ServiceSnapshot.checking($0, now: now))
        })
        return HealthSnapshot(services: states, overall: .checking, checkedAt: now)
    }

    static func overallState(for services: [ServiceSnapshot]) -> ServiceState {
        guard !services.isEmpty else { return .stopped }
        let states = Set(services.map(\.state))
        if states.contains(.checking) { return .checking }

        if states == [.stopped] { return .stopped }
        if states == [.normal] { return .normal }
        // A stopped service alongside any running/degraded service means the
        // system is only partially available, even when the healthy service
        // is otherwise normal.
        if states.contains(.stopped) { return .partial }
        if states.contains(.starting) { return .starting }
        if states.contains(.unavailable) {
            return states.contains(where: { $0 == .normal || $0 == .partial || $0 == .starting })
                ? .partial
                : .unavailable
        }
        if states.contains(.partial) { return .partial }
        return .normal
    }
}

enum ControlAction: String {
    case startAll
    case restartAll
    case stopAll

    var displayName: String {
        switch self {
        case .startAll: return "启动全部"
        case .restartAll: return "重启全部"
        case .stopAll: return "停止全部业务服务"
        }
    }
}

struct ActionResult: Equatable {
    let action: ControlAction
    let succeeded: Bool
    let exitCode: Int32?
    let message: String

    static func rejected(_ action: ControlAction) -> ActionResult {
        ActionResult(
            action: action,
            succeeded: false,
            exitCode: nil,
            message: "已有控制操作正在进行，请稍候"
        )
    }
}
