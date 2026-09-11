using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;

namespace ChengduConstructionController.UI;

internal static class TrayIconFactory
{
    public static Icon Create(Color statusColor)
    {
        using var bitmap = new Bitmap(32, 32, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
        using (var graphics = Graphics.FromImage(bitmap))
        {
            graphics.SmoothingMode = SmoothingMode.AntiAlias;
            graphics.Clear(Color.Transparent);
            using var background = new SolidBrush(Color.FromArgb(24, 38, 62));
            graphics.FillEllipse(background, 1, 1, 30, 30);
            using var status = new SolidBrush(statusColor);
            graphics.FillEllipse(status, 22, 2, 8, 8);
            using var border = new Pen(Color.FromArgb(140, 210, 224, 242), 1.2f);
            graphics.DrawEllipse(border, 1.5f, 1.5f, 29, 29);

            var fontName = FindChineseFont();
            using var font = new Font(fontName, 14f, FontStyle.Bold, GraphicsUnit.Point);
            using var textBrush = new SolidBrush(Color.White);
            using var format = new StringFormat
            {
                Alignment = StringAlignment.Center,
                LineAlignment = StringAlignment.Center,
            };
            graphics.DrawString("成", font, textBrush, new RectangleF(2, 2, 28, 28), format);
        }

        var handle = bitmap.GetHicon();
        try
        {
            using var icon = Icon.FromHandle(handle);
            return (Icon)icon.Clone();
        }
        finally
        {
            DestroyIcon(handle);
        }
    }

    private static string FindChineseFont()
    {
        foreach (var name in new[] { "HymOS", "HymOS Sans SC", "HarmonyOS Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI" })
        {
            try
            {
                using var family = new FontFamily(name);
                return family.Name;
            }
            catch (ArgumentException)
            {
                // Continue through the local-only fallback list.
            }
        }

        return FontFamily.GenericSansSerif.Name;
    }

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyIcon(IntPtr handle);
}
