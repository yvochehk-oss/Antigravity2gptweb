using ChengduConstructionController.Services;
using ChengduConstructionController.UI;

namespace ChengduConstructionController;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);

        var logger = new SafeLogger();
        Application.ThreadException += (_, eventArgs) => logger.Error("界面线程异常", eventArgs.Exception);
        AppDomain.CurrentDomain.UnhandledException += (_, eventArgs) =>
        {
            if (eventArgs.ExceptionObject is Exception exception)
            {
                logger.Error("未处理异常", exception);
            }
        };

        var startAllRequested = args.Any(argument =>
            argument.Equals("--start-all", StringComparison.OrdinalIgnoreCase));

        // The tray UI is a singleton. CLI control operations are deliberately
        // allowed while the tray is open; otherwise START_WINDOWS.bat would
        // report success without executing StartAllAsync when a tray instance
        // already owns the UI mutex.
        using var instanceMutex = new Mutex(false, "Local\\ChengduConstructionController.V3");
        if (!startAllRequested)
        {
            try
            {
                if (!instanceMutex.WaitOne(TimeSpan.Zero))
                {
                    return 0;
                }
            }
            catch (AbandonedMutexException)
            {
                // Previous tray process was killed; mutex is acquired here.
            }
        }

        using var rootResolver = new ProjectRootResolver(logger);
        using var orchestrator = new ServiceOrchestrator(rootResolver, logger);

        if (startAllRequested)
        {
            var result = orchestrator.StartAllAsync().GetAwaiter().GetResult();
            if (!result.Success)
            {
                logger.Warn($"命令行启动全部失败：{result.Message}");
                return 1;
            }

            logger.Info($"命令行启动全部成功：{result.Message}");
            return 0;
        }

        using var monitor = new StatusMonitor(orchestrator, logger);
        using var context = new TrayApplicationContext(rootResolver, orchestrator, monitor, logger);

        monitor.Start();
        Application.Run(context);
        return 0;
    }
}
