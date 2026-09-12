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

        using var instanceMutex = new Mutex(false, "Local\\ChengduConstructionController.V3");
        try
        {
            if (!instanceMutex.WaitOne(TimeSpan.Zero))
            {
                logger.Warn("控制台已有实例运行；本次启动未执行重复的服务操作。");
                return 0;
            }
        }
        catch (AbandonedMutexException)
        {
            // Previous process was killed; mutex is acquired by current process.
        }

        using var rootResolver = new ProjectRootResolver(logger);
        using var orchestrator = new ServiceOrchestrator(rootResolver, logger);

        if (args.Any(argument => argument.Equals("--start-all", StringComparison.OrdinalIgnoreCase)))
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
