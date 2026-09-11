using ChengduConstructionController.Models;
using ChengduConstructionController.Services;
using Xunit;

namespace ChengduConstructionController.Tests;

public sealed class PostgresStateTests
{
    [Fact]
    public void PostgresState_SerializeAndDeserialize_RoundtripsCorrectly()
    {
        var original = new PostgresState(
            Port: 54321,
            Host: "127.0.0.1",
            DataDir: @"C:\app\database\data",
            ProjectRoot: @"C:\app",
            PostmasterPid: 1234,
            StartedAt: DateTimeOffset.UtcNow,
            PostgresVersion: "16.1");

        var json = original.Serialize();
        var roundtripped = PostgresState.TryDeserialize(json);

        Assert.NotNull(roundtripped);
        Assert.Equal(original.Port, roundtripped.Port);
        Assert.Equal(original.Host, roundtripped.Host);
        Assert.Equal(original.DataDir, roundtripped.DataDir);
        Assert.Equal(original.ProjectRoot, roundtripped.ProjectRoot);
        Assert.Equal(original.PostmasterPid, roundtripped.PostmasterPid);
        Assert.Equal(original.PostgresVersion, roundtripped.PostgresVersion);
        Assert.Equal(original.PostgresConnectionString, roundtripped.PostgresConnectionString);
    }

    [Fact]
    public void PostgresState_TryDeserialize_HandlesNullAndEmptyString()
    {
        Assert.Null(PostgresState.TryDeserialize(null));
        Assert.Null(PostgresState.TryDeserialize(""));
        Assert.Null(PostgresState.TryDeserialize("   "));
    }

    [Fact]
    public void PostgresState_TryDeserialize_HandlesCorruptJsonWithoutThrowing()
    {
        var corruptJson = "{ \"Port\": 54320, \"Host\": ";
        var result = PostgresState.TryDeserialize(corruptJson);

        Assert.Null(result);
    }

    [Fact]
    public void PostgresState_ConnectionString_FormatsCorrectly()
    {
        var state = new PostgresState(
            Port: 54325,
            Host: "127.0.0.1",
            DataDir: string.Empty,
            ProjectRoot: string.Empty,
            PostmasterPid: 0,
            StartedAt: DateTimeOffset.MinValue,
            PostgresVersion: null);

        Assert.Equal("postgresql://postgres@127.0.0.1:54325/projectrag", state.PostgresConnectionString);
        Assert.Equal("postgresql://postgres@127.0.0.1:54325/projectrag", state.ProjectRagConnectionString);
    }

    [Fact]
    public void PostgreSqlPortNegotiator_NegotiateAvailablePort_ReturnsPreferredPortWhenFree()
    {
        var logger = new SafeLogger();
        var resolver = new ProjectRootResolver(logger);
        var negotiator = new PostgreSqlPortNegotiator(resolver, logger);

        var port = negotiator.NegotiateAvailablePort();

        // In a normal test environment port 54320 should be free or negotiated cleanly in [54320..54369]
        Assert.InRange(port, 54320, 54369);
        Assert.Equal(port, negotiator.ActivePort);
    }

    [Fact]
    public void PostgreSqlPortNegotiator_ResolveOrNegotiatePort_IsIdempotent()
    {
        var logger = new SafeLogger();
        var resolver = new ProjectRootResolver(logger);
        var negotiator = new PostgreSqlPortNegotiator(resolver, logger);

        var first = negotiator.ResolveOrNegotiatePort();
        var second = negotiator.ResolveOrNegotiatePort();

        Assert.Equal(first, second);
        Assert.Equal(first, negotiator.ActivePort);
    }

    [Fact]
    public void PostgreSqlPortNegotiator_WhenPortOccupiedByNonPg_SkipsToNextPort()
    {
        // Bind a non-PG TCP listener on 127.0.0.1:54320 for the duration of this test.
        var listener = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, 54320);
        try
        {
            listener.Start();

            var logger = new SafeLogger();
            var resolver = new ProjectRootResolver(logger);
            var negotiator = new PostgreSqlPortNegotiator(resolver, logger);

            var port = negotiator.NegotiateAvailablePort();

            // Negotiator must skip occupied port 54320 and choose 54321+
            Assert.True(port > 54320, $"Expected negotiated port > 54320, but got {port}");
            Assert.InRange(port, 54321, 54369);
        }
        finally
        {
            listener.Stop();
        }
    }
}
