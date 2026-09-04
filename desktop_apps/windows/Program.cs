using ChengduConstructionController.Services;
using ChengduConstructionController.UI;

namespace ChengduConstructionController;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
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

        using var instanceMutex = new Mutex(false, "Local\\ChengduConstructionController.V3");
        if (!instanceMutex.WaitOne(TimeSpan.Zero))
        {
            // A second launch is intentionally a no-op. The running tray icon remains the
            // single owner of start/stop operations and avoids duplicate service launches.
            return;
        }

        using var rootResolver = new ProjectRootResolver(logger);
        using var orchestrator = new ServiceOrchestrator(rootResolver, logger);
        using var monitor = new StatusMonitor(orchestrator, logger);
        using var context = new TrayApplicationContext(orchestrator, monitor, logger);

        monitor.Start();
        Application.Run(context);
    }
}
