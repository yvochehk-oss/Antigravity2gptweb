import AppKit
import Combine
import Foundation
import ServiceManagement

@MainActor
final class AppStateManager: ObservableObject {
    @Published private(set) var snapshot = HealthSnapshot.checking()
    @Published private(set) var projectAvailable = false
    @Published private(set) var isBusy = false
    @Published private(set) var loginItemEnabled = false

    private var refreshTimer: Timer?
    private var monitor: HealthMonitor?
    private var controller: ServiceController?
    private let logStore = LogStore()
    private var configuration: ProjectConfiguration?
    private var configurationGeneration = 0

    init() {
        configure(rootURL: ProjectLocator.locate())
        startRefreshTimer()
        updateLoginItemState()
        refreshStatus()
    }

    deinit {
        refreshTimer?.invalidate()
    }

    var overallState: ServiceState {
        projectAvailable ? snapshot.overall : .stopped
    }

    var summaryTitle: String {
        projectAvailable
            ? "总体状态：\(snapshot.overall.title)"
            : "总体状态：未选择项目目录"
    }

    var rootTitle: String {
        projectAvailable ? "项目目录：已连接" : "项目目录：未选择"
    }

    var controlsEnabled: Bool {
        projectAvailable && !isBusy
    }

    func serviceTitle(_ service: ServiceID) -> String {
        let value = snapshot.services[service] ?? ServiceSnapshot.checking(service)
        return "\(service.displayName)：\(value.state.title)"
    }

    func serviceDetail(_ service: ServiceID) -> String {
        let value = snapshot.services[service] ?? ServiceSnapshot.checking(service)
        let pidText = value.processConfirmed && value.processPID != nil
            ? " · 进程已确认"
            : ""
        return "\(value.detail)\(pidText)"
    }

    func refreshStatus() {
        guard configuration != nil else {
            projectAvailable = false
            return
        }
        monitor?.refresh()
    }

    func perform(_ action: ControlAction) {
        guard let controller else {
            showRootMissing()
            return
        }
        controller.perform(action) { [weak self] result in
            guard let self, !result.succeeded else { return }
            self.showAlert(title: "\(action.displayName)未完成", message: result.message)
        }
    }

    func openTax() {
        guard let configuration else { showRootMissing(); return }
        open(configuration.localURL(port: configuration.taxPort, path: "/"))
    }

    func openRAG() {
        guard let configuration else { showRootMissing(); return }
        open(configuration.localURL(port: configuration.ragPort, path: "/"))
    }

    func openBoss() {
        guard let configuration else { showRootMissing(); return }
        open(configuration.bossURL)
    }

    func openLog() {
        logStore.open()
    }

    func selectProjectRoot() {
        let panel = NSOpenPanel()
        panel.title = "选择成都建工 V3.0 项目目录"
        panel.message = "请选择同时包含 start_all.sh 和 stop_all.sh 的 V3.0 根目录。"
        panel.prompt = "连接目录"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.begin { [weak self] response in
            guard let self, response == .OK, let url = panel.url else { return }
            guard ProjectLocator.persist(url) else {
                self.showAlert(title: "目录不可用", message: "所选目录不是有效的 V3.0 项目目录。")
                return
            }
            self.configure(rootURL: url)
        }
    }

    func toggleLoginItem() {
        do {
            if loginItemEnabled {
                try SMAppService.mainApp.unregister()
                logStore.record("已关闭登录后自动运行")
            } else {
                try SMAppService.mainApp.register()
                logStore.record("已开启登录后自动运行")
            }
            updateLoginItemState()
        } catch {
            logStore.record("登录后自动运行设置未完成")
            showAlert(title: "设置未完成", message: "系统未能更新登录后自动运行设置，请稍后在系统设置中确认。")
        }
    }

    func quit() {
        guard !isBusy else {
            showAlert(title: "操作正在进行", message: "请等待当前启动、重启或停止操作完成后再退出控制台。")
            return
        }
        refreshTimer?.invalidate()
        NSApplication.shared.terminate(nil)
    }

    private func startRefreshTimer() {
        refreshTimer?.invalidate()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.refreshStatus()
            }
        }
        refreshTimer?.tolerance = 1
    }

    private func configure(rootURL: URL?) {
        configurationGeneration += 1
        let generation = configurationGeneration
        configuration = rootURL.map { ProjectConfiguration(rootURL: $0) }
        guard let configuration else {
            controller = nil
            monitor = nil
            projectAvailable = false
            snapshot = .checking()
            return
        }

        projectAvailable = true
        controller = ServiceController(configuration: configuration, logStore: logStore)
        controller?.onBusyChange = { [weak self] busy in
            guard let self else { return }
            self.isBusy = busy
            if !busy { self.refreshStatus() }
        }
        monitor = HealthMonitor(configuration: configuration) { [weak self] snapshot in
            guard let self, generation == self.configurationGeneration else { return }
            self.snapshot = snapshot
            self.projectAvailable = true
        }
        monitor?.refresh()
    }

    private func updateLoginItemState() {
        loginItemEnabled = SMAppService.mainApp.status == .enabled
    }

    private func open(_ url: URL?) {
        guard let url else {
            showRootMissing()
            return
        }
        NSWorkspace.shared.open(url)
    }

    private func showRootMissing() {
        showAlert(title: "尚未连接项目", message: "请先选择包含 start_all.sh 和 stop_all.sh 的 V3.0 项目目录。")
    }

    private func showAlert(title: String, message: String) {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = message
        alert.addButton(withTitle: "知道了")
        alert.runModal()
    }
}
