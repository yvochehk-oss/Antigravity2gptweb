using System.Diagnostics;
using ChengduConstructionController.Models;
using ChengduConstructionController.Services;

namespace ChengduConstructionController.UI;

public sealed class TrayApplicationContext : ApplicationContext
{
    private readonly ServiceOrchestrator _orchestrator;
    private readonly StatusMonitor _monitor;
    private readonly SafeLogger _logger;
    private readonly StartupRegistration _startup;
    private readonly NotifyIcon _notifyIcon;
    private readonly ToolStripMenuItem _overallItem;
    private readonly ToolStripMenuItem _startItem;
    private readonly ToolStripMenuItem _restartItem;
    private readonly ToolStripMenuItem _stopItem;
    private readonly ToolStripMenuItem _startupItem;
    private readonly Dictionary<ServiceKind, ToolStripMenuItem> _statusItems = new();
    private readonly SynchronizationContext? _uiContext;
    private StatusForm? _statusForm;
    private Icon? _currentIcon;
    private int _operationRunning;
    private bool _disposed;

    public TrayApplicationContext(ServiceOrchestrator orchestrator, StatusMonitor monitor, SafeLogger logger)
    {
        _orchestrator = orchestrator;
        _monitor = monitor;
        _logger = logger;
        _startup = new StartupRegistration(logger);
        _uiContext = SynchronizationContext.Current;

        _overallItem = new ToolStripMenuItem("正在读取服务状态")
        {
            Enabled = false,
            ForeColor = Color.FromArgb(93, 110, 133),
        };
        _startItem = new ToolStripMenuItem("启动全部服务");
        _restartItem = new ToolStripMenuItem("重启全部服务");
        _stopItem = new ToolStripMenuItem("停止全部服务（保留数据库）");
        _startupItem = new ToolStripMenuItem("登录后自动运行")
        {
            CheckOnClick = false,
            Checked = _startup.IsEnabled(),
        };

        _notifyIcon = new NotifyIcon
        {
            Text = "成都建工 V3.0 控制台",
            Visible = true,
            ContextMenuStrip = BuildMenu(),
        };
        SetTrayIcon(ServiceSnapshot.Empty(_orchestrator.Definitions));
        _notifyIcon.MouseClick += OnTrayMouseClick;
        _monitor.SnapshotChanged += OnSnapshotChanged;

        _startItem.Click += async (_, _) => await RunOperationAsync("启动全部服务", _orchestrator.StartAllAsync).ConfigureAwait(true);
        _restartItem.Click += async (_, _) => await RunOperationAsync("重启全部服务", _orchestrator.RestartAllAsync).ConfigureAwait(true);
        _stopItem.Click += async (_, _) => await RunOperationAsync("停止全部服务", _orchestrator.StopAllAsync).ConfigureAwait(true);
        _startupItem.Click += (_, _) => ToggleStartup();
    }

    private ContextMenuStrip BuildMenu()
    {
        var menu = new ContextMenuStrip
        {
            ShowImageMargin = false,
            Font = CreateUiFont(10f),
        };

        var title = new ToolStripMenuItem("成都建工 V3.0 控制台")
        {
            Enabled = false,
            Font = CreateUiFont(10.5f, FontStyle.Bold),
            ForeColor = Color.FromArgb(24, 38, 62),
        };
        menu.Items.Add(title);
        menu.Items.Add(_overallItem);
        menu.Items.Add(new ToolStripSeparator());

        var statusMenu = new ToolStripMenuItem("服务状态");
        foreach (var definition in _orchestrator.Definitions)
        {
            var item = new ToolStripMenuItem($"{definition.DisplayName}：正在检查")
            {
                Enabled = false,
            };
            _statusItems[definition.Kind] = item;
            statusMenu.DropDownItems.Add(item);
        }

        statusMenu.DropDownItems.Add(new ToolStripSeparator());
        var openStatus = new ToolStripMenuItem("打开状态窗口");
        openStatus.Click += (_, _) => ShowStatusWindow();
        statusMenu.DropDownItems.Add(openStatus);
        menu.Items.Add(statusMenu);

        var refresh = new ToolStripMenuItem("立即刷新状态");
        refresh.Click += async (_, _) =>
        {
            refresh.Enabled = false;
            try
            {
                await _monitor.RefreshNowAsync().ConfigureAwait(true);
            }
            finally
            {
                refresh.Enabled = true;
            }
        };
        menu.Items.Add(refresh);
        menu.Items.Add(new ToolStripSeparator());

        var openTax = new ToolStripMenuItem("打开智能财税管理系统");
        openTax.Click += (_, _) => OpenUrl("http://127.0.0.1:8921/");
        var openRag = new ToolStripMenuItem("打开资料输入管理系统");
        openRag.Click += (_, _) => OpenUrl("http://127.0.0.1:8922/");
        var openBoss = new ToolStripMenuItem("打开移动端管理系统");
        openBoss.Click += (_, _) => OpenUrl("http://127.0.0.1:5173/");
        menu.Items.Add(openTax);
        menu.Items.Add(openRag);
        menu.Items.Add(openBoss);
        menu.Items.Add(new ToolStripSeparator());

        menu.Items.Add(_startItem);
        menu.Items.Add(_restartItem);
        menu.Items.Add(_stopItem);
        menu.Items.Add(new ToolStripSeparator());

        var openLog = new ToolStripMenuItem("查看运行日志");
        openLog.Click += (_, _) => OpenLog();
        menu.Items.Add(openLog);
        menu.Items.Add(_startupItem);
        menu.Items.Add(new ToolStripSeparator());

        var exit = new ToolStripMenuItem("退出控制台（不停止服务）");
        exit.Click += (_, _) => ExitController();
        menu.Items.Add(exit);
        return menu;
    }

    private void OnTrayMouseClick(object? sender, MouseEventArgs e)
    {
        if (e.Button == MouseButtons.Left)
        {
            ShowStatusWindow();
        }
    }

    private void OnSnapshotChanged(object? sender, ServiceSnapshot snapshot)
    {
        if (_disposed)
        {
            return;
        }

        if (_uiContext is not null)
        {
            _uiContext.Post(_ => ApplySnapshot(snapshot), null);
        }
        else
        {
            ApplySnapshot(snapshot);
        }
    }

    private void ApplySnapshot(ServiceSnapshot snapshot)
    {
        if (_disposed)
        {
            return;
        }

        _overallItem.Text = snapshot.OverallText;
        _overallItem.ForeColor = StatusColor(snapshot.OverallCondition);

        foreach (var status in snapshot.Services.Values)
        {
            if (_statusItems.TryGetValue(status.Definition.Kind, out var item))
            {
                item.Text = $"{status.Definition.DisplayName}：{status.StateText}";
                item.ForeColor = StatusColor(status.Condition);
            }
        }

        SetTrayIcon(snapshot);
        _statusForm?.ApplySnapshot(snapshot);
    }

    private async Task RunOperationAsync(
        string operationName,
        Func<CancellationToken, Task<OperationResult>> operation)
    {
        if (Interlocked.Exchange(ref _operationRunning, 1) == 1)
        {
            return;
        }

        SetOperationItemsEnabled(false);
        _logger.Info($"开始{operationName}");
        try
        {
            using var cancellation = new CancellationTokenSource(TimeSpan.FromMinutes(10));
            var result = await operation(cancellation.Token).ConfigureAwait(true);
            _logger.Info($"{operationName}：{result.Message}");
            _notifyIcon.ShowBalloonTip(
                4500,
                "成都建工控制台",
                result.Message,
                result.Success ? ToolTipIcon.Info : ToolTipIcon.Warning);
            await _monitor.RefreshNowAsync().ConfigureAwait(true);
        }
        catch (OperationCanceledException)
        {
            _logger.Warn($"{operationName}已超时或取消。");
            _notifyIcon.ShowBalloonTip(4500, "成都建工控制台", $"{operationName}已超时或取消。", ToolTipIcon.Warning);
        }
        catch (Exception exception)
        {
            _logger.Error($"{operationName}未完成", exception);
            _notifyIcon.ShowBalloonTip(4500, "成都建工控制台", $"{operationName}未完成，请查看运行日志。", ToolTipIcon.Error);
        }
        finally
        {
            SetOperationItemsEnabled(true);
            Volatile.Write(ref _operationRunning, 0);
        }
    }

    private void SetOperationItemsEnabled(bool enabled)
    {
        _startItem.Enabled = enabled;
        _restartItem.Enabled = enabled;
        _stopItem.Enabled = enabled;
    }

    private void ToggleStartup()
    {
        var requested = !_startupItem.Checked;
        if (_startup.TrySetEnabled(requested, out var message))
        {
            _startupItem.Checked = requested;
            _notifyIcon.ShowBalloonTip(3500, "成都建工控制台", message, ToolTipIcon.Info);
        }
        else
        {
            _startupItem.Checked = _startup.IsEnabled();
            _notifyIcon.ShowBalloonTip(4500, "成都建工控制台", message, ToolTipIcon.Warning);
        }
    }

    private void ShowStatusWindow()
    {
        if (_statusForm is null || _statusForm.IsDisposed)
        {
            _statusForm = new StatusForm(_orchestrator.Definitions, _monitor.RefreshNowAsync);
        }

        _statusForm.ApplySnapshot(_monitor.Current);
        if (!_statusForm.Visible)
        {
            _statusForm.Show();
        }

        _statusForm.WindowState = FormWindowState.Normal;
        _statusForm.Activate();
    }

    private void OpenUrl(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = url,
                UseShellExecute = true,
            });
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            _logger.Error("打开业务系统失败", exception);
            _notifyIcon.ShowBalloonTip(4500, "成都建工控制台", "默认浏览器无法打开该业务系统。", ToolTipIcon.Warning);
        }
    }

    private void OpenLog()
    {
        try
        {
            _logger.Info("打开控制台运行日志。");
            Process.Start(new ProcessStartInfo
            {
                FileName = "notepad.exe",
                ArgumentList = { _logger.LogPath },
                UseShellExecute = false,
                CreateNoWindow = true,
            });
        }
        catch (Exception exception) when (exception is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            _logger.Error("打开运行日志失败", exception);
            _notifyIcon.ShowBalloonTip(4500, "成都建工控制台", "无法打开运行日志。", ToolTipIcon.Warning);
        }
    }

    private void SetTrayIcon(ServiceSnapshot snapshot)
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var condition = snapshot.OverallCondition;
        var nextIcon = TrayIconFactory.Create(StatusColor(condition));
        var previous = _currentIcon;
        _currentIcon = nextIcon;
        _notifyIcon.Icon = nextIcon;
        previous?.Dispose();
    }

    private void ExitController()
    {
        if (_operationRunning == 1)
        {
            _notifyIcon.ShowBalloonTip(3500, "成都建工控制台", "启停操作尚未完成，暂不能退出。", ToolTipIcon.Warning);
            return;
        }

        // Exiting the tray process intentionally leaves all business processes
        // untouched. Use the explicit stop action to stop project services.
        ExitThread();
    }

    protected override void Dispose(bool disposing)
    {
        if (_disposed)
        {
            base.Dispose(disposing);
            return;
        }

        _disposed = true;
        if (disposing)
        {
            _monitor.SnapshotChanged -= OnSnapshotChanged;
            _statusForm?.CloseForApplicationExit();
            _statusForm?.Dispose();
            _notifyIcon.Visible = false;
            _notifyIcon.Dispose();
            _currentIcon?.Dispose();
            _currentIcon = null;
        }

        base.Dispose(disposing);
    }

    private static Color StatusColor(ServiceCondition condition) => condition switch
    {
        ServiceCondition.Healthy => Color.FromArgb(29, 154, 104),
        ServiceCondition.Starting => Color.FromArgb(48, 125, 218),
        ServiceCondition.Degraded => Color.FromArgb(202, 135, 26),
        ServiceCondition.Unavailable => Color.FromArgb(204, 72, 84),
        ServiceCondition.Stopped => Color.FromArgb(126, 142, 168),
        _ => Color.FromArgb(126, 142, 168),
    };

    private static Font CreateUiFont(float size, FontStyle style = FontStyle.Regular)
    {
        foreach (var name in new[] { "HymOS", "HymOS Sans SC", "HarmonyOS Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI" })
        {
            try
            {
                using var family = new FontFamily(name);
                return new Font(family, size, style, GraphicsUnit.Point);
            }
            catch (ArgumentException)
            {
                // Continue through the local-only fallback list.
            }
        }

        return new Font(FontFamily.GenericSansSerif, size, style, GraphicsUnit.Point);
    }
}
