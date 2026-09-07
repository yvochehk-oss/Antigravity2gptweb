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
        (Text(state.summaryPrefix).foregroundColor(Color(nsColor: .labelColor)) + Text(state.summaryTitle).foregroundColor(state.summaryColor).bold())
        (Text(state.rootPrefix).foregroundColor(Color(nsColor: .labelColor)) + Text(state.rootTitle).foregroundColor(state.rootColor).bold())

        Divider()

        ForEach(ServiceID.allCases, id: \.self) { service in
            (Text("\(service.displayName)：").foregroundColor(Color(nsColor: .labelColor)) + Text(state.serviceTitle(service)).foregroundColor(state.serviceColor(service)).bold())
                .help(state.serviceDetail(service))
        }

        Divider()

        Button("打开智能财税管理系统") { state.openTax() }
            .keyboardShortcut("1")
            .disabled(!state.projectAvailable)
        Button("打开资料输入管理系统") { state.openRAG() }
            .keyboardShortcut("2")
            .disabled(!state.projectAvailable)
        Button("打开移动端管理系统") { state.openBoss() }
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
    /// 菜单栏图标：优先使用项目预置的"建筑 + 齿轮"组合 SF Symbol，模板渲染，
    /// 自动适配浅色/深色菜单栏且永不模糊。`StatusLogo.png` 仅作为可选覆写，
    /// 若需要品牌定制位图可放回 Resources；否则使用 SF Symbol 即可获得
    /// 与 macOS Sonoma / Sequoia 系统一致的精致观感。
    static let image: NSImage = {
        let configured = preferredSymbolImage()
            ?? BundleFallbackImage()
            ?? NSImage(size: NSSize(width: 18, height: 18))
        configured.size = NSSize(width: 18, height: 18)
        configured.isTemplate = true
        return configured
    }()

    private static func preferredSymbolImage() -> NSImage? {
        // 优先选择单纯的"房屋/建筑"轮廓，避免之前的"建筑+齿轮"复合图标
        // 给人"复杂、不像产品 logo"的印象。
        let symbolNames = [
            "house.fill",
            "house.circle.fill",
            "house.lodge.fill",
            "building.2.fill"
        ]
        for name in symbolNames {
            if let image = NSImage(
                systemSymbolName: name,
                accessibilityDescription: "成都建工控制台"
            ) {
                return image
            }
        }
        return nil
    }

    private static func BundleFallbackImage() -> NSImage? {
        guard let url = Bundle.main.url(forResource: "StatusLogo", withExtension: "png"),
              let image = NSImage(contentsOf: url) else {
            return nil
        }
        NSLog("[ChengduConstructionConsole] using bundled StatusLogo.png as icon override")
        return image
    }
}
