import AppKit
import SwiftUI

@main
struct ChengduConstructionConsoleApp: App {
    @StateObject private var state = AppStateManager()

    var body: some Scene {
        MenuBarExtra {
            ConsoleMenuView(state: state)
        } label: {
            StatusBarLabel(state: state.overallState)
        }
        .menuBarExtraStyle(.menu)
    }
}

private struct ConsoleMenuView: View {
    @ObservedObject var state: AppStateManager

    var body: some View {
        Text(state.summaryTitle)
        Text(state.rootTitle)

        Divider()

        ForEach(ServiceID.allCases, id: \.self) { service in
            Text(state.serviceTitle(service))
                .help(state.serviceDetail(service))
        }

        Divider()

        Button("打开税务系统") { state.openTax() }
            .keyboardShortcut("1")
            .disabled(!state.projectAvailable)
        Button("打开资料与知识系统") { state.openRAG() }
            .keyboardShortcut("2")
            .disabled(!state.projectAvailable)
        Button("打开老板驾驶舱") { state.openBoss() }
            .keyboardShortcut("3")
            .disabled(!state.projectAvailable)

        Divider()

        Button("启动全部") { state.perform(.startAll) }
            .disabled(!state.controlsEnabled)
        Button("重启全部") { state.perform(.restartAll) }
            .disabled(!state.controlsEnabled)
        Button("停止全部业务服务（保留数据库）") { state.perform(.stopAll) }
            .disabled(!state.controlsEnabled)

        Button("重新检查状态") { state.refreshStatus() }
            .keyboardShortcut("r")
        Button("查看运行日志") { state.openLog() }
            .keyboardShortcut("l")
        Button("选择项目目录…") { state.selectProjectRoot() }

        Divider()

        Button(state.loginItemEnabled ? "✓ 登录后自动运行" : "登录后自动运行") {
            state.toggleLoginItem()
        }
        Button("退出控制台") { state.quit() }
            .keyboardShortcut("q")
    }
}

private struct StatusBarLabel: View {
    let state: ServiceState

    var body: some View {
        Image(nsImage: StatusBarLogo.image)
            .renderingMode(.template)
            .resizable()
            .interpolation(.high)
            .frame(width: 18, height: 18)
            .accessibilityLabel("成都建工，\(state.title)")
            .help("成都建工控制台｜\(state.title)")
    }
}

private enum StatusBarLogo {
    static let image: NSImage = {
        if let url = Bundle.main.url(forResource: "StatusLogo", withExtension: "png"),
            let image = NSImage(contentsOf: url) {
            image.size = NSSize(width: 18, height: 18)
            image.isTemplate = true
            return image
        }

        NSLog("[ChengduConstructionConsole] StatusLogo.png missing from bundle; using fallback symbol")
        let fallback = NSImage(
            systemSymbolName: "building.2.crop.circle",
            accessibilityDescription: "成都建工"
        ) ?? NSImage(size: NSSize(width: 18, height: 18))
        fallback.size = NSSize(width: 18, height: 18)
        fallback.isTemplate = true
        return fallback
    }()
}
