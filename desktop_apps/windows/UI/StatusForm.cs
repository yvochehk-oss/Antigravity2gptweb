using ChengduConstructionController.Models;

namespace ChengduConstructionController.UI;

public sealed class StatusForm : Form
{
    private readonly Label _overallLabel = new();
    private readonly Label _checkedLabel = new();
    private readonly TableLayoutPanel _serviceTable = new();
    private readonly Dictionary<ServiceKind, (Label Dot, Label Name, Label State, Label Detail)> _rows = new();
    private readonly Func<Task<ServiceSnapshot>> _refreshRequested;
    private readonly Label _rootLabel = new();
    private bool _closing;

    public StatusForm(IReadOnlyList<ServiceDefinition> definitions, Func<Task<ServiceSnapshot>> refreshRequested)
    {
        _refreshRequested = refreshRequested;
        Text = "成都建工 V3.0 控制台";
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(620, 420);
        Size = new Size(760, 520);
        MaximizeBox = false;
        ShowInTaskbar = true;
        AutoScaleMode = AutoScaleMode.Dpi;
        Font = CreateUiFont(10f);
        BackColor = Color.FromArgb(246, 249, 252);

        var outer = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 1,
            RowCount = 4,
            Padding = new Padding(24, 20, 24, 18),
            BackColor = BackColor,
        };
        outer.RowStyles.Add(new RowStyle(SizeType.Absolute, 72));
        outer.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        outer.RowStyles.Add(new RowStyle(SizeType.Absolute, 52));
        outer.RowStyles.Add(new RowStyle(SizeType.Absolute, 34));
        Controls.Add(outer);

        var header = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 2,
            RowCount = 2,
        };
        header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        header.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 112));
        header.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
        header.RowStyles.Add(new RowStyle(SizeType.Absolute, 26));
        outer.Controls.Add(header, 0, 0);

        var title = new Label
        {
            Text = "服务运行状态",
            Dock = DockStyle.Fill,
            Font = CreateUiFont(18f, FontStyle.Bold),
            ForeColor = Color.FromArgb(24, 38, 62),
            TextAlign = ContentAlignment.MiddleLeft,
        };
        header.Controls.Add(title, 0, 0);

        _overallLabel.Dock = DockStyle.Fill;
        _overallLabel.Font = CreateUiFont(10.5f, FontStyle.Bold);
        _overallLabel.TextAlign = ContentAlignment.MiddleRight;
        header.Controls.Add(_overallLabel, 1, 0);

        _checkedLabel.Text = "正在读取状态…";
        _checkedLabel.Dock = DockStyle.Fill;
        _checkedLabel.ForeColor = Color.FromArgb(93, 110, 133);
        _checkedLabel.TextAlign = ContentAlignment.MiddleLeft;
        header.Controls.Add(_checkedLabel, 0, 1);

        _serviceTable.Dock = DockStyle.Fill;
        _serviceTable.ColumnCount = 4;
        _serviceTable.RowCount = definitions.Count + 1;
        _serviceTable.CellBorderStyle = TableLayoutPanelCellBorderStyle.Single;
        _serviceTable.BackColor = Color.White;
        _serviceTable.Padding = new Padding(0);
        _serviceTable.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 34));
        _serviceTable.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 165));
        _serviceTable.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 122));
        _serviceTable.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        _serviceTable.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
        for (var i = 0; i < definitions.Count; i++)
        {
            _serviceTable.RowStyles.Add(new RowStyle(SizeType.Absolute, 52));
        }

        AddHeaderCell("状态", 0, 0);
        AddHeaderCell("服务", 1, 0);
        AddHeaderCell("当前状态", 2, 0);
        AddHeaderCell("检查说明", 3, 0);

        for (var row = 0; row < definitions.Count; row++)
        {
            var definition = definitions[row];
            var dot = new Label
            {
                Text = "●",
                Dock = DockStyle.Fill,
                TextAlign = ContentAlignment.MiddleCenter,
                Font = CreateUiFont(14f, FontStyle.Bold),
                ForeColor = Color.FromArgb(126, 142, 168),
            };
            var name = CreateBodyLabel(definition.DisplayName, bold: true);
            var state = CreateBodyLabel("正在检查", bold: true);
            var detail = CreateBodyLabel(definition.LoopbackBaseUrl, bold: false);
            _serviceTable.Controls.Add(dot, 0, row + 1);
            _serviceTable.Controls.Add(name, 1, row + 1);
            _serviceTable.Controls.Add(state, 2, row + 1);
            _serviceTable.Controls.Add(detail, 3, row + 1);
            _rows[definition.Kind] = (dot, name, state, detail);
        }

        outer.Controls.Add(_serviceTable, 0, 1);

        var footer = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 2,
            RowCount = 1,
        };
        footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        footer.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 112));
        outer.Controls.Add(footer, 0, 2);

        _rootLabel.Text = "项目目录：正在解析";
        _rootLabel.Dock = DockStyle.Fill;
        _rootLabel.ForeColor = Color.FromArgb(93, 110, 133);
        _rootLabel.TextAlign = ContentAlignment.MiddleLeft;
        footer.Controls.Add(_rootLabel, 0, 0);

        var refreshButton = new Button
        {
            Text = "立即刷新",
            Dock = DockStyle.Fill,
            FlatStyle = FlatStyle.Flat,
            BackColor = Color.FromArgb(35, 112, 184),
            ForeColor = Color.White,
            Margin = new Padding(8, 8, 0, 8),
            Cursor = Cursors.Hand,
        };
        refreshButton.FlatAppearance.BorderSize = 0;
        refreshButton.Click += async (_, _) =>
        {
            refreshButton.Enabled = false;
            try
            {
                var snapshot = await _refreshRequested().ConfigureAwait(true);
                ApplySnapshot(snapshot);
            }
            finally
            {
                refreshButton.Enabled = true;
            }
        };
        footer.Controls.Add(refreshButton, 1, 0);

        var hint = new Label
        {
            Text = "状态每 5 秒自动刷新；关闭此窗口不会停止业务服务。",
            Dock = DockStyle.Fill,
            ForeColor = Color.FromArgb(109, 123, 143),
            TextAlign = ContentAlignment.MiddleLeft,
        };
        outer.Controls.Add(hint, 0, 3);
    }

    public void ApplySnapshot(ServiceSnapshot snapshot)
    {
        if (IsDisposed)
        {
            return;
        }

        if (InvokeRequired)
        {
            BeginInvoke(() => ApplySnapshot(snapshot));
            return;
        }

        _overallLabel.Text = snapshot.OverallText;
        _overallLabel.ForeColor = StatusColor(snapshot);
        _checkedLabel.Text = snapshot.CheckedAt == DateTimeOffset.MinValue
            ? "正在读取状态…"
            : $"最近检查：{snapshot.CheckedAt.LocalDateTime:yyyy-MM-dd HH:mm:ss}";
        _rootLabel.Text = "项目目录：" + (snapshot.Services.Count > 0 ? "已解析" : "未找到");

        foreach (var status in snapshot.Services.Values)
        {
            if (!_rows.TryGetValue(status.Definition.Kind, out var row))
            {
                continue;
            }

            var color = StatusColor(status.Condition);
            row.Dot.ForeColor = color;
            row.State.Text = status.StateText;
            row.State.ForeColor = color;
            row.Detail.Text = $"{status.PortText}；{status.Detail}";
        }
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (!_closing && e.CloseReason == CloseReason.UserClosing)
        {
            e.Cancel = true;
            Hide();
            return;
        }

        base.OnFormClosing(e);
    }

    public void CloseForApplicationExit()
    {
        _closing = true;
        Close();
    }

    private void AddHeaderCell(string text, int column, int row)
    {
        var label = new Label
        {
            Text = text,
            Dock = DockStyle.Fill,
            TextAlign = ContentAlignment.MiddleLeft,
            Font = CreateUiFont(9f, FontStyle.Bold),
            ForeColor = Color.FromArgb(93, 110, 133),
            Padding = new Padding(8, 0, 8, 0),
        };
        _serviceTable.Controls.Add(label, column, row);
    }

    private static Label CreateBodyLabel(string text, bool bold)
    {
        return new Label
        {
            Text = text,
            Dock = DockStyle.Fill,
            TextAlign = ContentAlignment.MiddleLeft,
            Font = CreateUiFont(10f, bold ? FontStyle.Bold : FontStyle.Regular),
            ForeColor = Color.FromArgb(36, 53, 78),
            Padding = new Padding(8, 0, 8, 0),
            AutoEllipsis = true,
        };
    }

    private static Color StatusColor(ServiceSnapshot snapshot)
    {
        return StatusColor(snapshot.OverallCondition);
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
