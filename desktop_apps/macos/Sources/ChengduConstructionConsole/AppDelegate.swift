import AppKit
import Foundation
import ServiceManagement

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var menu: NSMenu!
    private var summaryItem: NSMenuItem!
    private var rootItem: NSMenuItem!
    private var actionItems: [ControlAction: NSMenuItem] = [:]
    private var serviceItems: [ServiceID: NSMenuItem] = [:]
    private var loginItem: NSMenuItem!
    private var refreshTimer: Timer?
    private var monitor: HealthMonitor?
    private var controller: ServiceController?
    private let logStore = LogStore()
    private var configuration: ProjectConfiguration?
    private var currentSnapshot = HealthSnapshot.checking()
    private var isBusy = false
    private var configurationGeneration = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        buildMenu()
        configure(rootURL: ProjectLocator.locate())
        refreshTimer = Timer.scheduledTimer(
            timeInterval: 5,
            target: self,
            selector: #selector(refreshStatus),
            userInfo: nil,
            repeats: true
        )
        refreshTimer?.tolerance = 1
        updateLoginItemState()
        refreshStatus()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        presentStatusMenu()
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !isBusy else {
            showAlert(title: "操作正在进行", message: "请等待当前启动、重启或停止操作完成后再退出控制台。")
            return .terminateCancel
        }
        // Exiting the controller never stops business services.
        return .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        refreshTimer?.invalidate()
    }

    private func buildMenu() {
        menu = NSMenu(title: "成都建工控制台")
        menu.autoenablesItems = false

        summaryItem = NSMenuItem(title: "总体状态：正在检查", action: nil, keyEquivalent: "")
        summaryItem.isEnabled = false
        menu.addItem(summaryItem)

        rootItem = NSMenuItem(title: "项目目录：正在查找", action: nil, keyEquivalent: "")
        rootItem.isEnabled = false
        menu.addItem(rootItem)
        menu.addItem(.separator())

        for service in ServiceID.allCases {
            let item = NSMenuItem(title: "\(service.displayName)：正在检查", action: nil, keyEquivalent: "")
            item.isEnabled = false
            serviceItems[service] = item
            menu.addItem(item)
        }

        menu.addItem(.separator())
        addMenuItem(title: "打开税务系统", action: #selector(openTax), keyEquivalent: "1")
        addMenuItem(title: "打开资料与知识系统", action: #selector(openRAG), keyEquivalent: "2")
        addMenuItem(title: "打开老板驾驶舱", action: #selector(openBoss), keyEquivalent: "3")

        menu.addItem(.separator())
        for action in [ControlAction.startAll, .restartAll, .stopAll] {
            let title: String
            switch action {
            case .startAll: title = "启动全部"
            case .restartAll: title = "重启全部"
            case .stopAll: title = "停止全部业务服务（保留数据库）"
            }
            let item = addMenuItem(title: title, action: selector(for: action), keyEquivalent: "")
            actionItems[action] = item
        }

        addMenuItem(title: "重新检查状态", action: #selector(refreshStatus), keyEquivalent: "r")
        addMenuItem(title: "查看运行日志", action: #selector(openLog), keyEquivalent: "l")
        addMenuItem(title: "选择项目目录…", action: #selector(selectProjectRoot), keyEquivalent: "")

        menu.addItem(.separator())
        loginItem = addMenuItem(title: "登录后自动运行", action: #selector(toggleLoginItem), keyEquivalent: "")
        addMenuItem(title: "退出控制台", action: #selector(quit), keyEquivalent: "q")

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        configureStatusBarButton()
        statusItem.menu = menu
        updateStatusButton(for: .checking)
    }

    private func configureStatusBarButton() {
        guard let button = statusItem?.button else {
            NSLog("[ChengduConstructionConsole] NSStatusItem button is unavailable")
            return
        }

        button.image = loadStatusBarLogo()
        button.imagePosition = .imageOnly
        button.imageScaling = .scaleProportionallyDown
        button.title = ""
        button.attributedTitle = NSAttributedString(string: "")
        button.toolTip = "成都建工控制台"
    }

    private func loadStatusBarLogo() -> NSImage? {
        if let url = Bundle.main.url(forResource: "StatusLogo", withExtension: "png"),
           let image = NSImage(contentsOf: url) {
            image.size = NSSize(width: 18, height: 18)
            image.isTemplate = false
            return image
        }

        NSLog("[ChengduConstructionConsole] StatusLogo.png missing from bundle; using fallback symbol")
        if let fallback = NSImage(
            systemSymbolName: "building.2.crop.circle",
            accessibilityDescription: "成都建工"
        ) {
            fallback.size = NSSize(width: 18, height: 18)
            fallback.isTemplate = true
            return fallback
        }

        return nil
    }

    private func presentStatusMenu() {
        DispatchQueue.main.async { [weak self] in
            guard let self,
                  let button = self.statusItem?.button,
                  let menu = self.statusItem?.menu else {
                NSLog("[ChengduConstructionConsole] Unable to present status menu: status item is unavailable")
                return
            }

            // A newly-created status item may briefly report a window at the screen origin.
            // Wait until SystemUIServer has placed it in the menu bar before anchoring the popup.
            if let window = button.window,
               window.frame.origin.x <= 0 && window.frame.origin.y <= 0 {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { [weak self] in
                    self?.presentStatusMenu()
                }
                return
            }

            NSApp.activate(ignoringOtherApps: true)
            menu.popUp(
                positioning: nil,
                at: NSPoint(x: 0, y: button.bounds.height),
                in: button
            )
        }
    }

    @discardableResult
    private func addMenuItem(title: String, action: Selector, keyEquivalent: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: keyEquivalent)
        item.target = self
        menu.addItem(item)
        return item
    }

    private func selector(for action: ControlAction) -> Selector {
        switch action {
        case .startAll: return #selector(startAll)
        case .restartAll: return #selector(restartAll)
        case .stopAll: return #selector(stopAll)
        }
    }

    private func configure(rootURL: URL?) {
        configurationGeneration += 1
        let generation = configurationGeneration
        configuration = rootURL.map { ProjectConfiguration(rootURL: $0) }
        guard let configuration else {
            controller = nil
            monitor = nil
            rootItem.title = "项目目录：未选择"
            currentSnapshot = HealthSnapshot.checking()
            apply(snapshot: currentSnapshot, projectAvailable: false)
            return
        }

        rootItem.title = "项目目录：已连接"
        controller = ServiceController(configuration: configuration, logStore: logStore)
        controller?.onBusyChange = { [weak self] busy in
            guard let self else { return }
            self.isBusy = busy
            self.updateActionAvailability()
            if !busy { self.refreshStatus() }
        }
        monitor = HealthMonitor(configuration: configuration) { [weak self] snapshot in
            guard let self, generation == self.configurationGeneration else { return }
            self.currentSnapshot = snapshot
            self.apply(snapshot: snapshot, projectAvailable: true)
        }
        monitor?.refresh()
        updateActionAvailability()
    }

    @objc private func refreshStatus() {
        guard configuration != nil else {
            apply(snapshot: currentSnapshot, projectAvailable: false)
            return
        }
        monitor?.refresh()
    }

    private func apply(snapshot: HealthSnapshot, projectAvailable: Bool) {
        let overallState = projectAvailable ? snapshot.overall : .stopped
        summaryItem.title = projectAvailable
            ? "总体状态：\(snapshot.overall.title)"
            : "总体状态：未选择项目目录"
        updateStatusButton(for: overallState)

        for service in ServiceID.allCases {
            let value = snapshot.services[service] ?? ServiceSnapshot.checking(service)
            let pidText = value.processConfirmed && value.processPID != nil
                ? " · 进程已确认"
                : ""
            serviceItems[service]?.title = "\(service.displayName)：\(value.state.title)"
            serviceItems[service]?.toolTip = "\(value.detail)\(pidText)"
        }
        updateActionAvailability()
    }

    private func updateStatusButton(for state: ServiceState) {
        guard let button = statusItem?.button else { return }
        button.title = ""
        button.attributedTitle = NSAttributedString(string: "")
        button.imagePosition = .imageOnly
        button.toolTip = "成都建工控制台｜\(state.title)"
    }

    private func updateActionAvailability() {
        let available = configuration != nil && !isBusy
        for item in actionItems.values { item.isEnabled = available }
        let canOpen = configuration != nil
        menu?.items
            .filter { $0.action == #selector(openTax) || $0.action == #selector(openRAG) || $0.action == #selector(openBoss) }
            .forEach { $0.isEnabled = canOpen }
    }

    @objc private func startAll() { perform(.startAll) }
    @objc private func restartAll() { perform(.restartAll) }
    @objc private func stopAll() { perform(.stopAll) }

    private func perform(_ action: ControlAction) {
        guard let controller else {
            showRootMissing()
            return
        }
        controller.perform(action) { [weak self] result in
            guard let self else { return }
            if !result.succeeded {
                self.showAlert(title: "\(action.displayName)未完成", message: result.message)
            }
        }
    }

    @objc private func openTax() {
        guard let configuration else { showRootMissing(); return }
        open(configuration.localURL(port: configuration.taxPort, path: "/"))
    }

    @objc private func openRAG() {
        guard let configuration else { showRootMissing(); return }
        open(configuration.localURL(port: configuration.ragPort, path: "/"))
    }

    @objc private func openBoss() {
        guard let configuration else { showRootMissing(); return }
        open(configuration.bossURL)
    }

    private func open(_ url: URL?) {
        guard let url else {
            showRootMissing()
            return
        }
        NSWorkspace.shared.open(url)
    }

    @objc private func openLog() {
        logStore.open()
    }

    @objc private func selectProjectRoot() {
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

    @objc private func toggleLoginItem(_ sender: NSMenuItem) {
        guard #available(macOS 13.0, *) else { return }
        do {
            if sender.state == .on {
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

    private func updateLoginItemState() {
        guard #available(macOS 13.0, *) else {
            loginItem?.state = .off
            return
        }
        loginItem?.state = SMAppService.mainApp.status == .enabled ? .on : .off
    }

    @objc private func quit() {
        NSApp.terminate(nil)
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
