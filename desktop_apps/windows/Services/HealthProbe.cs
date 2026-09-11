using System.Diagnostics;
using System.Net;
using System.Text.Json;
using ChengduConstructionController.Models;

namespace ChengduConstructionController.Services;

public sealed class HealthProbe : IDisposable
{
    private const int MaxBodyCharacters = 128 * 1024;
    private readonly HttpClient _client;
    private readonly SafeLogger _logger;

    public HealthProbe(SafeLogger logger)
    {
        _logger = logger;
        var handler = new SocketsHttpHandler
        {
            UseProxy = false,
            AllowAutoRedirect = false,
            ConnectTimeout = TimeSpan.FromSeconds(3),
            AutomaticDecompression = System.Net.DecompressionMethods.GZip | System.Net.DecompressionMethods.Deflate,
        };
        _client = new HttpClient(handler)
        {
            Timeout = TimeSpan.FromSeconds(8),
        };
        _client.DefaultRequestHeaders.UserAgent.ParseAdd("ChengduConstructionController/3.1");
    }

    public async Task<HttpProbeResult> ProbeAsync(ServiceDefinition definition, CancellationToken cancellationToken)
    {
        if (definition.Kind == ServiceKind.LocalModel)
        {
            return await ProbeLocalModelAsync(definition, cancellationToken).ConfigureAwait(false);
        }

        HttpProbeResult? last = null;
        foreach (var path in definition.HealthPaths)
        {
            var probe = await ProbeEndpointAsync(definition, path, false, cancellationToken).ConfigureAwait(false);
            last = probe;
            if (probe.Healthy)
            {
                return probe;
            }

            // A missing primary route is allowed to fall back to the documented
            // compatibility route. Other failures are still retried once on the
            // next declared route so stale route assumptions do not look healthy.
        }

        return last ?? HttpProbeResult.NotChecked("没有可用健康检查地址");
    }

    private async Task<HttpProbeResult> ProbeLocalModelAsync(
        ServiceDefinition definition,
        CancellationToken cancellationToken)
    {
        var health = await ProbeEndpointAsync(definition, "/health", false, cancellationToken).ConfigureAwait(false);
        if (!health.Healthy)
        {
            return health with { ModelReady = false };
        }

        var modelList = await ProbeEndpointAsync(definition, "/v1/models", true, cancellationToken).ConfigureAwait(false);
        if (!modelList.Healthy || !modelList.ModelReady)
        {
            return health with
            {
                Healthy = false,
                ModelReady = false,
                Detail = "健康接口已响应，但模型列表未确认就绪",
                Endpoint = health.Endpoint,
                Slow = health.Slow || modelList.Slow,
                Elapsed = health.Elapsed + modelList.Elapsed,
            };
        }

        return health with
        {
            Healthy = true,
            ModelReady = true,
            Slow = health.Slow || modelList.Slow,
            Detail = "健康接口和已加载模型均已确认",
            Elapsed = health.Elapsed + modelList.Elapsed,
        };
    }

    private async Task<HttpProbeResult> ProbeEndpointAsync(
        ServiceDefinition definition,
        string path,
        bool requireModelList,
        CancellationToken cancellationToken)
    {
        var url = definition.LoopbackBaseUrl + path;
        var stopwatch = Stopwatch.StartNew();
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            using var response = await _client.SendAsync(
                request,
                HttpCompletionOption.ResponseHeadersRead,
                cancellationToken).ConfigureAwait(false);
            var body = await ReadBodyAsync(response, cancellationToken).ConfigureAwait(false);
            stopwatch.Stop();
            var statusCode = (int)response.StatusCode;
            var healthy = response.IsSuccessStatusCode;
            var modelReady = requireModelList && healthy && HasLoadedModel(body);
            var detail = healthy
                ? requireModelList
                    ? modelReady ? "模型列表已返回" : "模型列表为空或格式无法确认"
                    : "健康接口已响应"
                : $"健康接口返回 {statusCode}";
            return new HttpProbeResult(
                Responded: true,
                Healthy: healthy,
                ModelReady: modelReady,
                Slow: stopwatch.Elapsed >= TimeSpan.FromSeconds(6),
                StatusCode: statusCode,
                Detail: detail,
                Endpoint: path,
                Elapsed: stopwatch.Elapsed);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            stopwatch.Stop();
            return new HttpProbeResult(
                Responded: false,
                Healthy: false,
                ModelReady: false,
                Slow: true,
                StatusCode: null,
                Detail: "健康检查超时",
                Endpoint: path,
                Elapsed: stopwatch.Elapsed);
        }
        catch (HttpRequestException exception)
        {
            stopwatch.Stop();
            _logger.Warn($"{definition.DisplayName} 健康检查失败：{exception.Message}");
            return new HttpProbeResult(
                Responded: false,
                Healthy: false,
                ModelReady: false,
                Slow: stopwatch.Elapsed >= TimeSpan.FromMilliseconds(800),
                StatusCode: null,
                Detail: "未连接到健康接口",
                Endpoint: path,
                Elapsed: stopwatch.Elapsed);
        }
        catch (Exception exception) when (exception is IOException or InvalidOperationException)
        {
            stopwatch.Stop();
            _logger.Warn($"{definition.DisplayName} 健康检查读取失败：{exception.Message}");
            return new HttpProbeResult(
                Responded: false,
                Healthy: false,
                ModelReady: false,
                Slow: stopwatch.Elapsed >= TimeSpan.FromMilliseconds(800),
                StatusCode: null,
                Detail: "健康响应无法读取",
                Endpoint: path,
                Elapsed: stopwatch.Elapsed);
        }
    }

    private static async Task<string> ReadBodyAsync(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        using var reader = new StreamReader(stream);
        var buffer = new char[4096];
        var builder = new System.Text.StringBuilder();
        while (builder.Length < MaxBodyCharacters)
        {
            var read = await reader.ReadAsync(buffer.AsMemory(), cancellationToken).ConfigureAwait(false);
            if (read == 0)
            {
                break;
            }

            var remaining = MaxBodyCharacters - builder.Length;
            builder.Append(buffer, 0, Math.Min(read, remaining));
            if (read > remaining)
            {
                break;
            }
        }

        return builder.ToString();
    }

    private static bool HasLoadedModel(string body)
    {
        if (string.IsNullOrWhiteSpace(body))
        {
            return false;
        }

        try
        {
            using var document = JsonDocument.Parse(body);
            if (document.RootElement.ValueKind == JsonValueKind.Object
                && document.RootElement.TryGetProperty("data", out var data)
                && data.ValueKind == JsonValueKind.Array)
            {
                return data.GetArrayLength() > 0;
            }
        }
        catch (JsonException)
        {
            return false;
        }

        return false;
    }

    public void Dispose() => _client.Dispose();
}
