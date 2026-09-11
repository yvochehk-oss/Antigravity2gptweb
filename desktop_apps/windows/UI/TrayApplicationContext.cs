using System.Diagnostics;
using ChengduConstructionController.Models;
using ChengduConstructionController.Services;

namespace ChengduConstructionController.UI;

public sealed class TrayApplicationContext : ApplicationContext
{
    private readonly ProjectRootResolver _rootResolver;
    private readonly ServiceOrchestrator _orchestrator;
    private readonly StatusMonitor _monitor;
    private readonly SafeLogger _logger;
    private readonly StartupRegistration _startup;
    private readonly NotifyIcon _notifyIcon;
    private readonly ToolStripMenuItem _overallItem;
    private readonly ToolStripMenuItem _projectRootItem;
    private readonly ToolStripMenuItem _openTaxItem;
    private readonly ToolStripMenuItem _openRagItem;
    private readonly ToolStripMenuItem _openBossItem;
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

    public TrayApplicationContext(
        ProjectRootResolver rootResolver,
        ServiceOrchestrator orchestrator,
        StatusMonitor monitor,
        SafeLogger logger)
    {
        _rootResolver = rootResolver;
        _orchestrator = orchestrator;
        _monitor = monitor;
        _logger = logger;
        _startup = new StartupRegistration(logger);
        _uiContext = SynchronizationContext.Current;

        _overallItem = CreateStatusItem("总体状态：正在检查");
        _projectRootItem = CreateStatusItem("项目目录：未选择");
        _openTaxItem = CreateActionItem("打开智能财税管理系统", "1");
        _openRagItem = CreateActionItem("打开资料输入管理系统", "2");
        _openBossItem = CreateActionItem("打开移动端管理系统", "3");
        _startItem = new ToolStripMenuItem("启动全部");
        _restartItem = new ToolStripMenuItem("重启全部");
        _stopItem = new ToolStripMenuItem("停止全部业务服务（保留数据库）");
        _startupItem = new ToolStripMenuItem("登录后自动运行")
        {
            CheckOnClick = false,
            Checked = _startup.IsEnabled(),
        };

        _notifyIcon = new NotifyIcon
        {
            Text = "成都建工 V3.1 控制台",
            Visible = true,
            ContextMenuStrip = BuildMenu(),
        };
        SetTrayIcon(ServiceSnapshot.Empty(_orchestrator.Definitions));
        ApplyProjectRootState();
        _notifyIcon.MouseClick += OnTrayMouseClick;
        _monitor.SnapshotChanged += OnSnapshotChanged;

        _openTaxItem.Click += (_, _) => OpenUrl("http://127.0.0.1:8921/");
        _openRagItem.Click += (_, _) => OpenUrl("http://127.0.0.1:8922/");
        _openBossItem.Click += (_, _) => OpenUrl("http://127.0.0.1:5173/");
        _startItem.Click += async (_, _) => await RunOperationAsync("启动全部", _orchestrator.StartAllAsync).ConfigureAwait(true);
        _restartItem.Click += async (_, _) => await RunOperationAsync("重启全部", _orchestrator.RestartAllAsync).ConfigureAwait(true);
        _stopItem.Click += async (_, _) => await RunOperationAsync("停止全部业务服务", _orchestrator.StopAllAsync).ConfigureAwait(true);
        _startupItem.Click += (_, _) => ToggleStartup();
    }

    private ContextMenuStrip BuildMenu()
    {
        var menu = new ContextMenuStrip
        {
            ShowImageMargin = false,
            ShowCheckMargin = true,
            ShowItemToolTips = true,
            Font = CreateUiFont(10f),
        };

        menu.Items.Add(_overallItem);
        menu.Items.Add(_projectRootItem);
        menu.Items.Add(new ToolStripSeparator());

        foreach (var definition in ServiceCatalog.OrderForMenu(_orchestrator.Definitions))
        {
            var item = CreateStatusItem($"{definition.DisplayName}：正在检查");
            item.ToolTipText = $"端口：{definition.Port}\nPID：正在检查";
            _statusItems[definition.Kind] = item;
            menu.Items.Add(item);
        }

        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(_openTaxItem);
        menu.Items.Add(_openRagItem);
        menu.Items.Add(_openBossItem);
        menu.Items.Add(new ToolStripSeparator());

        menu.Items.Add(_startItem);
        menu.Items.Add(_restartItem);
        menu.Items.Add(_stopItem);

        var refresh = CreateActionItem("重新检查状态", "R");
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

        var openLog = CreateActionItem("查看运行日志", "L");
        openLog.Click += (_, _) => OpenLog();
        menu.Items.Add(openLog);

        var selectRoot = new ToolStripMenuItem("选择项目目录…");
        selectRoot.Click += async (_, _) => await SelectProjectRootAsync().ConfigureAwait(true);
        menu.Items.Add(selectRoot);
        menu.Items.Add(new ToolStripSeparator());

        menu.Items.Add(_startupItem);

        var exit = CreateActionItem("退出控制台", "Q");
        exit.Click += (_, _) => ExitController();
        menu.Items.Add(exit);

        menu.KeyDown += (_, eventArgs) =>
        {
            if (eventArgs.Modifiers != Keys.None)
            {
                return;
            }

            var target = eventArgs.KeyCode switch
            {
                Keys.D1 or Keys.NumPad1 => _openTaxItem,
                Keys.D2 or Keys.NumPad2 => _openRagItem,
                Keys.D3 or Keys.NumPad3 => _openBossItem,
                Keys.R => refresh,
                Keys.L => openLog,
                Keys.Q => exit,
                _ => null,
            };
            if (target is null || !target.Enabled)
            {
                return;
            }

            target.PerformClick();
            eventArgs.Handled = true;
            eventArgs.SuppressKeyPress = true;
        };
        return menu;
    }

    private static ToolStripMenuItem CreateStatusItem(string text)
    {
        return new ToolStripMenuItem(text)
        {
            Enabled = true,
            AutoToolTip = false,
            Font = CreateUiFont(10f, FontStyle.Bold),
            ForeColor = StatusColor(ServiceCondition.Unknown),
        };
    }

    private static ToolStripMenuItem CreateActionItem(string text, string display)
    {
        return new ToolStripMenuItem(text)
        {
            ShortcutKeyDisplayString = display,
            ShowShortcutKeys = true,
        };
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

        _overallItem.Text = $"总体状态：{OverallMenuText(snapshot.OverallCondition)}";
        _overallItem.ForeColor = StatusColor(snapshot.OverallCondition);
        _overallItem.ToolTipText = snapshot.OverallText;
        ApplyProjectRootState();

        foreach (var status in snapshot.Services.Values)
        {
            if (_statusItems.TryGetValue(status.Definition.Kind, out var item))
            {
                item.Text = $"{status.Definition.DisplayName}：{status.StateText}";
                item.ForeColor = StatusColor(status.Condition);
                item.ToolTipText = BuildServiceToolTip(status);
            }
        }

        SetTrayIcon(snapshot);
        _statusForm?.ApplySnapshot(snapshot);
    }

    private void ApplyProjectRootState()
    {
        var root = _rootResolver.Root;
        var connected = root is not null;
        _projectRootItem.Text = connected ? "项目目录：已连接" : "项目目录：未选择";
        _projectRootItem.ForeColor = connected
            ? StatusColor(ServiceCondition.Healthy)
            : StatusColor(ServiceCondition.Unavailable);
        _projectRootItem.ToolTipText = connected
            ? $"当前项目目录：{root}"
            : $"尚未选择有效项目目录。选择后将保存到：{_rootResolver.PersistencePath}";

        _openTaxItem.Enabled = connected;
        _openRagItem.Enabled = connected;
        _openBossItem.Enabled = connected;
        SetOperationItemsEnabled(_operationRunning == 0);
    }

    private async Task SelectProjectRootAsync()
    {
        if (_operationRunning == 1 || _orchestrator.IsBusy)
        {
            _notifyIcon.ShowBalloonTip(3500, "成都建工控制台", "启停操作正在执行，暂不能切换项目目录。", ToolTipIcon.Warning);
            return;
        }

        using var dialog = new FolderBrowserDialog
        {
            Description = "请选择成都建工 V3.1 项目根目录（目录内必须包含 windows_scripts 和 source_code）",
            ShowNewFolderButton = false,
            UseDescriptionForTitle = true,
        };

        if (_rootResolver.Root is { Length: > 0 } currentRoot)
        {
            dialog.SelectedPath = currentRoot;
        }

        if (dialog.ShowDialog() != DialogResult.OK)
        {
            return;
        }

        if (!_rootResolver.TrySetRoot(dialog.SelectedPath, out var message))
        {
            MessageBox.Show(
                message,
                "项目目录无效",
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning);
            ApplyProjectRootState();
            return;
        }

        ApplyProjectRootState();
        _notifyIcon.ShowBalloonTip(3500, "成都建工控制台", message, ToolTipIcon.Info);
        await _monitor.RefreshNowAsync().ConfigureAwait(true);
    }

    private async Task RunOperationAsync(
        string operationName,
        Func<CancellationToken, Task<OperationResult>> operation)
    {
        if (!_rootResolver.IsResolved)
        {
            _notifyIcon.ShowBalloonTip(3500, "成都建工控制台", "请先选择有效的项目目录。", ToolTipIcon.Warning);
            return;
        }

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
            Volatile.Write(ref _operationRunning, 0);
            SetOperationItemsEnabled(true);
        }
    }

    private void SetOperationItemsEnabled(bool enabled)
    {
        var controlsEnabled = enabled && _rootResolver.IsResolved;
        _startItem.Enabled = controlsEnabled;
        _restartItem.Enabled = controlsEnabled;
        _stopItem.Enabled = controlsEnabled;
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
            _statusForm = new StatusForm(
                ServiceCatalog.OrderForMenu(_orchestrator.Definitions),
                _monitor.RefreshNowAsync);
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

    private static string OverallMenuText(ServiceCondition condition) => condition switch
    {
        ServiceCondition.Healthy => "正常",
        ServiceCondition.Stopped => "已停止",
        ServiceCondition.Starting => "正在启动",
        ServiceCondition.Degraded => "部分异常",
        ServiceCondition.Unavailable => "部分异常",
        _ => "正在检查",
    };

    private static string BuildServiceToolTip(ServiceStatus status)
    {
        var pids = status.Process.ProcessIds.Count == 0
            ? "无"
            : string.Join(", ", status.Process.ProcessIds);
        var endpoint = string.IsNullOrWhiteSpace(status.Http.Endpoint)
            ? "未检查"
            : status.Http.Endpoint;
        var httpStatus = status.Http.StatusCode.HasValue
            ? status.Http.StatusCode.Value.ToString()
            : "无";
        return $"端口：{status.Definition.Port}\nPID：{pids}\nHTTP：{endpoint}（{httpStatus}）\n进程：{status.Process.Detail}\n详情：{status.Detail}";
    }

    private static Color StatusColor(ServiceCondition condition) => condition switch
    {
        ServiceCondition.Healthy => Color.FromArgb(29, 154, 104),
        ServiceCondition.Starting => Color.FromArgb(202, 135, 26),
        ServiceCondition.Degraded => Color.FromArgb(202, 135, 26),
        ServiceCondition.Unavailable => Color.FromArgb(204, 72, 84),
        ServiceCondition.Stopped => Color.FromArgb(126, 142, 168),
        _ => Color.FromArgb(202, 135, 26),
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
