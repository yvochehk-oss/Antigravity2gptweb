using System.Text.Json;
using System.Text.Json.Serialization;

namespace ChengduConstructionController.Models;

/// <summary>
/// Single source of truth for the negotiated PostgreSQL endpoint.
///
/// Written by <c>PostgreSqlPortNegotiator</c> after a successful startup
/// handshake and consumed by every Python launcher and BAT script that needs
/// to talk to the bundled database. BAT scripts MUST NOT hard-code port
/// numbers anywhere; they read this file via PowerShell.
/// </summary>
public sealed record PostgresState(
    int Port,
    string Host,
    string DataDir,
    string ProjectRoot,
    int PostmasterPid,
    DateTimeOffset StartedAt,
    string? PostgresVersion)
{
    public const string DefaultHost = "127.0.0.1";
    public const string RelativeFilePath = @"runtime\state\postgres.json";

    public string PostgresConnectionString =>
        $"postgresql://postgres@{Host}:{Port}/projectrag";

    public string ProjectRagConnectionString =>
        $"postgresql://postgres@{Host}:{Port}/projectrag";

    public static PostgresState Empty { get; } = new(
        Port: 0,
        Host: DefaultHost,
        DataDir: string.Empty,
        ProjectRoot: string.Empty,
        PostmasterPid: 0,
        StartedAt: DateTimeOffset.MinValue,
        PostgresVersion: null);

    private static readonly JsonSerializerOptions WriteOptions = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public string Serialize() => JsonSerializer.Serialize(this, WriteOptions);

    public static PostgresState? TryDeserialize(string? json)
    {
        if (string.IsNullOrWhiteSpace(json))
        {
            return null;
        }

        try
        {
            return JsonSerializer.Deserialize<PostgresState>(json, WriteOptions);
        }
        catch (JsonException)
        {
            return null;
        }
    }
}
