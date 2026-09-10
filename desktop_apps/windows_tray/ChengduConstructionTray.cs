using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace ChengduConstruction.TrayApp
{
    static class Program
    {
        [STAThread]
        static void Main()
        {
            string logFile = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "tray_startup.log");
            try
            {
                bool createdNew;
                using (Mutex mutex = new Mutex(true, "ChengduConstruction_TrayApp_Mutex_V3", out createdNew))
                {
                    if (!createdNew)
                    {
                        MessageBox.Show("成都建工 V3.0 托盘控制中心已在后台运行中。\n\n【提示】：请点击屏幕右下角任务栏的【^】向上小箭头展开隐藏图标，即可看到成都建工专属徽标！", 
                            "成都建工 V3.0 控制中心", MessageBoxButtons.OK, MessageBoxIcon.Information);
                        return;
                    }

                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);
                    Application.Run(new TrayApplicationContext());
                }
            }
            catch (Exception ex)
            {
                File.AppendAllText(logFile, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " [FATAL ERROR] " + ex.ToString() + "\n");
                MessageBox.Show("托盘程序发生异常:\n" + ex.Message, "启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }

    public class TrayApplicationContext : ApplicationContext
    {
        private NotifyIcon trayIcon;
        private ContextMenuStrip contextMenu;
        private System.Windows.Forms.Timer probeTimer;
        private string rootDir;

        // Status menu items
        private ToolStripMenuItem statusHeaderItem;
        private ToolStripMenuItem webStatusItem;
        private ToolStripMenuItem taxStatusItem;
        private ToolStripMenuItem ragStatusItem;
        private ToolStripMenuItem idpStatusItem;
        private ToolStripMenuItem llmStatusItem;
        private ToolStripMenuItem dbStatusItem;

        // Port definitions
        private const int PORT_WEB = 5173;
        private const int PORT_TAX = 8921;
        private const int PORT_RAG = 8922;
        private const int PORT_IDP = 8933;
        private const int PORT_LLM = 8930;
        private const int PORT_DB_PRIMARY = 54320;
        private const int PORT_DB_FALLBACK = 5432;

        public TrayApplicationContext()
        {
            string logFile = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "tray_startup.log");
            try
            {
                rootDir = FindProjectRootDir();
                EnsureLogsDirectory();
                InitializeTray();

                // Background probe every 2.5s
                probeTimer = new System.Windows.Forms.Timer();
                probeTimer.Interval = 2500;
                probeTimer.Tick += (s, e) => CheckAllServicesAsync();
                probeTimer.Start();

                // Initial probe
                CheckAllServicesAsync();

                // *** AUTO-START ALL SERVICES ON LAUNCH ***
                StartAllServicesSilent();

                try
                {
                    trayIcon.ShowBalloonTip(4000, "成都建工 V3.0 智控中枢", 
                        "正在后台拉起全部系统服务（无终端黑框遮挡）...\n服务状态将自动变为绿色运行中！\n• 双击图标：进入移动驾驶舱\n• 点击【打开大模型终端】：随时查看推理黑框", ToolTipIcon.Info);
                }
                catch { }
            }
            catch (Exception ex)
            {
                File.AppendAllText(logFile, "TrayApplicationContext Exception: " + ex.ToString() + "\n");
                throw;
            }
        }

        private string FindProjectRootDir()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            DirectoryInfo dir = new DirectoryInfo(baseDir);
            for (int i = 0; i < 4 && dir != null; i++)
            {
                if (Directory.Exists(Path.Combine(dir.FullName, "windows_scripts")))
                {
                    return dir.FullName;
                }
                dir = dir.Parent;
            }
            return baseDir;
        }

        private void EnsureLogsDirectory()
        {
            try
            {
                string logsDir = Path.Combine(rootDir, "logs");
                if (!Directory.Exists(logsDir))
                {
                    Directory.CreateDirectory(logsDir);
                }
            }
            catch { }
        }

        private void InitializeTray()
        {
            contextMenu = new ContextMenuStrip();
            contextMenu.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Regular);
            contextMenu.RenderMode = ToolStripRenderMode.System;

            // Title
            var titleItem = new ToolStripMenuItem("🏗️ 成都建工 V3.0 财税智控中枢");
            titleItem.Font = new Font("Microsoft YaHei UI", 10f, FontStyle.Bold);
            titleItem.Enabled = false;
            contextMenu.Items.Add(titleItem);

            contextMenu.Items.Add(new ToolStripSeparator());

            // Status header
            statusHeaderItem = new ToolStripMenuItem("📊 系统服务状态 (实时监测):");
            statusHeaderItem.Enabled = false;
            statusHeaderItem.Font = new Font("Microsoft YaHei UI", 9f, FontStyle.Bold);
            contextMenu.Items.Add(statusHeaderItem);

            webStatusItem = CreateStatusMenuItem("  📱 老板端驾驶舱 (5173)", "http://127.0.0.1:5173");
            taxStatusItem = CreateStatusMenuItem("  🏢 税务管理中台 (8921)", "http://127.0.0.1:8921");
            ragStatusItem = CreateStatusMenuItem("  🧠 RAG事实证据 (8922)", "http://127.0.0.1:8922");
            idpStatusItem = CreateStatusMenuItem("  📄 IDP录入审计 (8933)", "http://127.0.0.1:8933");
            llmStatusItem = CreateStatusMenuItem("  🤖 本地大模型 (8930)", "http://127.0.0.1:8930/v1");
            dbStatusItem = CreateStatusMenuItem("  🗄️ 数据库 PostgreSQL", null);

            contextMenu.Items.Add(webStatusItem);
            contextMenu.Items.Add(taxStatusItem);
            contextMenu.Items.Add(ragStatusItem);
            contextMenu.Items.Add(idpStatusItem);
            contextMenu.Items.Add(llmStatusItem);
            contextMenu.Items.Add(dbStatusItem);

            contextMenu.Items.Add(new ToolStripSeparator());

            // Portal shortcuts
            var portalMenu = new ToolStripMenuItem("🌐 一键打开 Web 驾驶舱");
            portalMenu.DropDownItems.Add("📱 老板端移动驾驶舱 (5173)", null, (s, e) => OpenUrl("http://127.0.0.1:5173"));
            portalMenu.DropDownItems.Add("🏢 税务管理与风控中台 (8921)", null, (s, e) => OpenUrl("http://127.0.0.1:8921"));
            portalMenu.DropDownItems.Add("🧠 RAG 知识证据中枢 (8922)", null, (s, e) => OpenUrl("http://127.0.0.1:8922"));
            portalMenu.DropDownItems.Add("📄 IDP 穿透式录入审计 (8933)", null, (s, e) => OpenUrl("http://127.0.0.1:8933"));
            portalMenu.DropDownItems.Add(new ToolStripSeparator());
            portalMenu.DropDownItems.Add("📚 税务系统 OpenAPI 接口 (8921/docs)", null, (s, e) => OpenUrl("http://127.0.0.1:8921/docs"));
            portalMenu.DropDownItems.Add("📚 RAG 知识库 OpenAPI 接口 (8922/docs)", null, (s, e) => OpenUrl("http://127.0.0.1:8922/docs"));
            contextMenu.Items.Add(portalMenu);

            contextMenu.Items.Add(new ToolStripSeparator());

            // Service controls (Pure silent background execution)
            var startItem = new ToolStripMenuItem("▶️  静默启动全部服务", null, (s, e) => StartAllServicesSilent());
            startItem.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Bold);
            startItem.ForeColor = Color.DarkGreen;
            contextMenu.Items.Add(startItem);

            var restartItem = new ToolStripMenuItem("🔄  一键平滑重启服务", null, (s, e) => RestartAllServicesSilent());
            restartItem.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Bold);
            restartItem.ForeColor = Color.DarkBlue;
            contextMenu.Items.Add(restartItem);

            var stopItem = new ToolStripMenuItem("⏹️  停止全部后台服务", null, (s, e) => StopAllServicesSilent());
            stopItem.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Bold);
            stopItem.ForeColor = Color.DarkRed;
            contextMenu.Items.Add(stopItem);

            contextMenu.Items.Add(new ToolStripSeparator());

            // Diagnostic & LLM window controls
            var llmConsoleItem = new ToolStripMenuItem("🖥️  打开大模型实时终端窗口", null, (s, e) => OpenLlmConsole());
            llmConsoleItem.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Bold);
            llmConsoleItem.ForeColor = Color.DarkSlateBlue;
            llmConsoleItem.ToolTipText = "在屏幕上打开大模型专用黑框终端，实时查看 Token 吞吐、推理过程及日志输出";
            contextMenu.Items.Add(llmConsoleItem);

            var logsMenu = new ToolStripMenuItem("📄  查看服务运行日志");
            logsMenu.DropDownItems.Add("🤖 大模型日志 (llm.log)", null, (s, e) => OpenLogFile("llm.log"));
            logsMenu.DropDownItems.Add("🧠 RAG 中台日志 (rag.log)", null, (s, e) => OpenLogFile("rag.log"));
            logsMenu.DropDownItems.Add("📄 IDP 引擎日志 (idp.log)", null, (s, e) => OpenLogFile("idp.log"));
            logsMenu.DropDownItems.Add("🏢 税务中台日志 (tax.log)", null, (s, e) => OpenLogFile("tax.log"));
            logsMenu.DropDownItems.Add("📱 Web 驾驶舱日志 (web.log)", null, (s, e) => OpenLogFile("web.log"));
            logsMenu.DropDownItems.Add(new ToolStripSeparator());
            logsMenu.DropDownItems.Add("📂 打开日志文件夹", null, (s, e) => OpenLogsFolder());
            contextMenu.Items.Add(logsMenu);

            contextMenu.Items.Add(new ToolStripSeparator());

            var refreshItem = new ToolStripMenuItem("🔄  立即刷新状态", null, (s, e) => CheckAllServicesAsync());
            contextMenu.Items.Add(refreshItem);

            var exitItem = new ToolStripMenuItem("❌  退出控制台", null, (s, e) => ExitApp());
            contextMenu.Items.Add(exitItem);

            Icon appIcon = LoadAppIcon();

            trayIcon = new NotifyIcon();
            trayIcon.Icon = appIcon;
            trayIcon.Text = "成都建工 V3.0 - 财税智控与 IDP 穿透中枢";
            trayIcon.ContextMenuStrip = contextMenu;
            trayIcon.Visible = true;

            trayIcon.DoubleClick += (s, e) => OpenUrl("http://127.0.0.1:5173");
        }

        private Icon LoadAppIcon()
        {
            try
            {
                string icoPath = Path.Combine(rootDir, @"desktop_apps\windows\app.ico");
                if (!File.Exists(icoPath)) icoPath = Path.Combine(rootDir, @"app.ico");
                if (File.Exists(icoPath))
                {
                    return new Icon(icoPath);
                }
            }
            catch { }

            try
            {
                string pngPath = Path.Combine(rootDir, @"desktop_apps\windows\StatusLogo.png");
                if (!File.Exists(pngPath)) pngPath = Path.Combine(rootDir, @"StatusLogo.png");
                if (File.Exists(pngPath))
                {
                    using (Bitmap bmp = new Bitmap(pngPath))
                    {
                        IntPtr hIcon = bmp.GetHicon();
                        return (Icon)Icon.FromHandle(hIcon).Clone();
                    }
                }
            }
            catch { }

            try
            {
                return Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            }
            catch { }

            return SystemIcons.Application;
        }

        private ToolStripMenuItem CreateStatusMenuItem(string label, string url)
        {
            var item = new ToolStripMenuItem(label + ":  ⚪ 探测中...");
            if (!string.IsNullOrEmpty(url))
            {
                item.Click += (s, e) => OpenUrl(url);
                item.ToolTipText = "点击在浏览器中打开: " + url;
            }
            return item;
        }

        private void CheckAllServicesAsync()
        {
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    bool webOnline = IsPortListening(PORT_WEB);
                    bool taxOnline = IsPortListening(PORT_TAX);
                    bool ragOnline = IsPortListening(PORT_RAG);
                    bool idpOnline = IsPortListening(PORT_IDP);
                    bool llmOnline = IsPortListening(PORT_LLM);
                    bool dbOnline = IsPortListening(PORT_DB_PRIMARY) || IsPortListening(PORT_DB_FALLBACK);

                    Action updateAction = () =>
                    {
                        try
                        {
                            UpdateStatusItem(webStatusItem, "  📱 老板端驾驶舱 (5173)", webOnline);
                            UpdateStatusItem(taxStatusItem, "  🏢 税务管理中台 (8921)", taxOnline);
                            UpdateStatusItem(ragStatusItem, "  🧠 RAG事实证据 (8922)", ragOnline);
                            UpdateStatusItem(idpStatusItem, "  📄 IDP录入审计 (8933)", idpOnline);
                            UpdateStatusItem(llmStatusItem, "  🤖 本地大模型 (8930)", llmOnline);
                            UpdateStatusItem(dbStatusItem, "  🗄️ 数据库 PostgreSQL", dbOnline);

                            int runningCount = (webOnline ? 1 : 0) + (taxOnline ? 1 : 0) + (ragOnline ? 1 : 0) +
                                               (idpOnline ? 1 : 0) + (llmOnline ? 1 : 0) + (dbOnline ? 1 : 0);
                            
                            if (statusHeaderItem != null)
                            {
                                statusHeaderItem.Text = string.Format("📊 系统服务状态 ({0}/6 正常):", runningCount);
                            }
                        }
                        catch { }
                    };

                    if (contextMenu != null && !contextMenu.IsDisposed)
                    {
                        if (contextMenu.IsHandleCreated)
                        {
                            contextMenu.BeginInvoke(updateAction);
                        }
                        else
                        {
                            updateAction();
                        }
                    }
                }
                catch { }
            });
        }

        private void UpdateStatusItem(ToolStripMenuItem item, string baseName, bool isOnline)
        {
            if (item == null) return;
            if (isOnline)
            {
                item.Text = baseName + ":  🟢 运行中";
                item.ForeColor = Color.FromArgb(0, 128, 0);
            }
            else
            {
                item.Text = baseName + ":  ⚪ 未启动";
                item.ForeColor = Color.Gray;
            }
        }

        private bool IsPortListening(int port)
        {
            Socket socket = null;
            try
            {
                socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
                socket.Blocking = false;
                try
                {
                    socket.Connect(new IPEndPoint(IPAddress.Loopback, port));
                    return true;
                }
                catch (SocketException se)
                {
                    if (se.NativeErrorCode == 10035) // WSAEWOULDBLOCK
                    {
                        bool write = socket.Poll(150000, SelectMode.SelectWrite);
                        bool error = socket.Poll(1000, SelectMode.SelectError);
                        return write && !error;
                    }
                    return false;
                }
            }
            catch
            {
                return false;
            }
            finally
            {
                if (socket != null)
                {
                    try { socket.Close(); } catch { }
                }
            }
        }

        // ==================== Service Management (Silent Background) ====================

        private void StartAllServicesSilent()
        {
            ThreadPool.QueueUserWorkItem(_ =>
            {
                string logsDir = Path.Combine(rootDir, "logs");

                // 0. PostgreSQL
                if (!IsPortListening(PORT_DB_PRIMARY) && !IsPortListening(PORT_DB_FALLBACK))
                {
                    string pgCtl = Path.Combine(rootDir, @"database\pgsql\bin\pg_ctl.exe");
                    string pgData = FindPgDataDir();
                    if (File.Exists(pgCtl) && !string.IsNullOrEmpty(pgData) && Directory.Exists(pgData))
                    {
                        string pgCmd = string.Format("\"{0}\" start -D \"{1}\" -l \"{2}\"", pgCtl, pgData, Path.Combine(logsDir, "postgres.log"));
                        LaunchCmdDetached(pgCmd, rootDir);
                        Thread.Sleep(1000);
                    }
                }

                // 1. LLM (优先星火 Spark-X2.5-4B，兼容 Ling 与 Qwen)
                if (!IsPortListening(PORT_LLM))
                {
                    string llmExe = Path.Combine(rootDir, @"models\local-llm\runtime-win-cpu-x64\llama-server.exe");
                    string modelPath = null;
                    string modelAlias = "spark-x2.5-4b";

                    string sparkPath = Path.Combine(rootDir, @"models\local-llm\Spark-X2.5-4B-Q4_K_M.gguf");
                    string lingPath = Path.Combine(rootDir, @"models\local-llm\Ling-3.0-tiny-Q4_K_M.gguf");
                    string qwenPath = Path.Combine(rootDir, @"models\local-llm\Qwen3.5-2B-Q4_K_M.gguf");

                    if (File.Exists(sparkPath))
                    {
                        modelPath = sparkPath;
                        modelAlias = "spark-x2.5-4b";
                    }
                    else if (File.Exists(lingPath))
                    {
                        modelPath = lingPath;
                        modelAlias = "ling-3.0-tiny";
                    }
                    else if (File.Exists(qwenPath))
                    {
                        modelPath = qwenPath;
                        modelAlias = "qwen3.5-2b";
                    }

                    if (File.Exists(llmExe) && modelPath != null)
                    {
                        string llmLog = Path.Combine(logsDir, "llm.log");
                        string llmCmd = string.Format("\"{0}\" --model \"{1}\" --host 127.0.0.1 --port 8930 --alias \"{2}\" --ctx-size 16384 --threads 4 --threads-batch 4 --batch-size 512 --ubatch-size 256 --gpu-layers 0 --reasoning off --parallel 1 --jinja > \"{3}\" 2>&1",
                            llmExe, modelPath, modelAlias, llmLog);

                        LaunchCmdDetached(llmCmd, rootDir);
                        Thread.Sleep(1000);
                    }
                }

                // 2. RAG
                if (!IsPortListening(PORT_RAG))
                {
                    string ragDir = Path.Combine(rootDir, @"source_code\0.2_RAG系统\project-rag-v1.1");
                    string pyRag = FindPythonExecutable(ragDir);
                    string ragLog = Path.Combine(logsDir, "rag.log");

                    string ragCmd = string.Format("\"{0}\" -m uvicorn app.main:app --host 127.0.0.1 --port 8922 > \"{1}\" 2>&1",
                        pyRag, ragLog);

                    LaunchCmdDetached(ragCmd, ragDir);
                    Thread.Sleep(800);
                }

                // 3. IDP
                if (!IsPortListening(PORT_IDP))
                {
                    string idpDir = Path.Combine(rootDir, @"source_code\0.4_IDP文档录入引擎_V3.0");
                    string pyIdp = FindPythonExecutable(idpDir);
                    string idpLog = Path.Combine(logsDir, "idp.log");

                    string idpCmd = string.Format("\"{0}\" -m uvicorn app.main:app --host 127.0.0.1 --port 8933 > \"{1}\" 2>&1",
                        pyIdp, idpLog);

                    LaunchCmdDetached(idpCmd, idpDir);
                    Thread.Sleep(800);
                }

                // 4. TAX
                if (!IsPortListening(PORT_TAX))
                {
                    string taxDir = Path.Combine(rootDir, @"source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0");
                    string pyTax = FindPythonExecutable(taxDir);
                    string taxLog = Path.Combine(logsDir, "tax.log");

                    string taxCmd = string.Format("\"{0}\" -m uvicorn app.main:app --host 127.0.0.1 --port 8921 > \"{1}\" 2>&1",
                        pyTax, taxLog);

                    LaunchCmdDetached(taxCmd, taxDir);
                    Thread.Sleep(800);
                }

                // 5. WEB
                if (!IsPortListening(PORT_WEB))
                {
                    string pyWeb = FindPythonExecutable(Path.Combine(rootDir, @"source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"));
                    string webScript = Path.Combine(rootDir, @"windows_scripts\serve_web.py");
                    string webLog = Path.Combine(logsDir, "web.log");

                    string webCmd = string.Format("\"{0}\" \"{1}\" 5173 > \"{2}\" 2>&1",
                        pyWeb, webScript, webLog);

                    LaunchCmdDetached(webCmd, rootDir);
                }

                // Check and update repeatedly for smooth visual feedback
                for (int i = 0; i < 6; i++)
                {
                    Thread.Sleep(2000);
                    CheckAllServicesAsync();
                }
            });
        }

        private string FindPgDataDir()
        {
            try
            {
                string driveRoot = Path.GetPathRoot(rootDir);
                string[] candidates = new string[]
                {
                    Path.Combine(driveRoot, "projectrag_pgdata"),
                    Path.Combine(rootDir, @"database\data"),
                    Path.Combine(rootDir, "projectrag_pgdata"),
                    @"C:\projectrag_pgdata",
                    @"D:\projectrag_pgdata",
                    @"E:\projectrag_pgdata",
                    @"F:\projectrag_pgdata"
                };
                foreach (string path in candidates)
                {
                    if (!string.IsNullOrEmpty(path) && Directory.Exists(path) && File.Exists(Path.Combine(path, "PG_VERSION")))
                    {
                        return path;
                    }
                }
                foreach (string path in candidates)
                {
                    if (!string.IsNullOrEmpty(path) && Directory.Exists(path))
                    {
                        return path;
                    }
                }
            }
            catch { }
            return null;
        }

        private string FindPythonExecutable(string preferredDir)
        {
            try
            {
                if (!string.IsNullOrEmpty(preferredDir))
                {
                    string localPy = Path.Combine(preferredDir, @".venv\Scripts\python.exe");
                    if (File.Exists(localPy)) return localPy;
                }

                string[] searchDirs = new string[]
                {
                    Path.Combine(rootDir, @"source_code\0.2_RAG系统\project-rag-v1.1"),
                    Path.Combine(rootDir, @"source_code\0.1_税务管理\gtp_V1.0_FULL\01_当前完整系统_V1.0\chengdu_construction_tax_system_v1_0"),
                    Path.Combine(rootDir, @"source_code\0.4_IDP文档录入引擎_V3.0")
                };

                foreach (string dir in searchDirs)
                {
                    string py = Path.Combine(dir, @".venv\Scripts\python.exe");
                    if (File.Exists(py)) return py;
                }
            }
            catch { }
            return "python.exe";
        }

        private void LaunchCmdDetached(string commandWithRedirect, string workingDir)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "cmd.exe";
                psi.Arguments = "/c \"" + commandWithRedirect + "\"";
                psi.WorkingDirectory = workingDir;
                psi.CreateNoWindow = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;
                psi.UseShellExecute = true;

                Process.Start(psi);
            }
            catch { }
        }

        private void StopAllServicesSilent()
        {
            trayIcon.ShowBalloonTip(2000, "停止服务", "正在静默释放各端口并关闭后台服务...", ToolTipIcon.Warning);

            ThreadPool.QueueUserWorkItem(_ =>
            {
                ExecuteKillSilent();
                Thread.Sleep(1500);
                CheckAllServicesAsync();
            });
        }

        private void RestartAllServicesSilent()
        {
            trayIcon.ShowBalloonTip(3000, "平滑重启", "正在静默重启全系统服务...", ToolTipIcon.Info);

            ThreadPool.QueueUserWorkItem(_ =>
            {
                ExecuteKillSilent();
                Thread.Sleep(2000);
                StartAllServicesSilent();
            });
        }

        private void ExecuteKillSilent()
        {
            try
            {
                RunHiddenCmd("taskkill /F /IM llama-server.exe >nul 2>nul");
                RunHiddenCmd("powershell -NoProfile -Command \"foreach ($p in 8921,8922,8933,5173) { $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue; foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue } }\"");
            }
            catch { }
        }

        private void RunHiddenCmd(string command)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "cmd.exe";
                psi.Arguments = "/c " + command;
                psi.WorkingDirectory = rootDir;
                psi.CreateNoWindow = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;
                psi.UseShellExecute = true;

                using (Process proc = Process.Start(psi))
                {
                    if (proc != null)
                    {
                        proc.WaitForExit(8000);
                    }
                }
            }
            catch { }
        }

        // ==================== User Interaction / Console View ====================

        private void OpenLlmConsole()
        {
            try
            {
                string scriptPath = Path.Combine(rootDir, @"windows_scripts\OPEN_LLM_CONSOLE.bat");
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "cmd.exe";
                psi.Arguments = "/c \"" + scriptPath + "\"";
                psi.WorkingDirectory = rootDir;
                psi.UseShellExecute = true;
                psi.WindowStyle = ProcessWindowStyle.Normal;

                Process.Start(psi);
            }
            catch (Exception ex)
            {
                MessageBox.Show("打开大模型终端失败: " + ex.Message, "提示", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        private void OpenLogFile(string logFileName)
        {
            try
            {
                string logPath = Path.Combine(rootDir, "logs", logFileName);
                if (!File.Exists(logPath))
                {
                    File.WriteAllText(logPath, "=== 日志记录初始化 ===\n", Encoding.UTF8);
                }
                Process.Start(new ProcessStartInfo("notepad.exe", "\"" + logPath + "\"") { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                MessageBox.Show("打开日志失败: " + ex.Message, "提示", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        private void OpenLogsFolder()
        {
            try
            {
                string logsDir = Path.Combine(rootDir, "logs");
                Process.Start(new ProcessStartInfo("explorer.exe", "\"" + logsDir + "\"") { UseShellExecute = true });
            }
            catch { }
        }

        private void OpenUrl(string url)
        {
            try
            {
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                MessageBox.Show("无法打开浏览器: " + ex.Message, "提示", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        private void ExitApp()
        {
            var result = MessageBox.Show("是否在退出托盘前同时停止所有后台运行的服务？\n\n【是】：停止服务并退出\n【否】：保留服务在后台运行，仅退出托盘\n【取消】：返回", 
                "退出成都建工 V3.0 控制台", MessageBoxButtons.YesNoCancel, MessageBoxIcon.Question);
            
            if (result == DialogResult.Cancel) return;

            if (result == DialogResult.Yes)
            {
                ExecuteKillSilent();
            }

            if (probeTimer != null)
            {
                probeTimer.Stop();
                probeTimer.Dispose();
            }

            if (trayIcon != null)
            {
                trayIcon.Visible = false;
                trayIcon.Dispose();
            }

            ExitThread();
        }
    }
}
