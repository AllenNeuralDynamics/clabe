using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace Clabe.Web.Models;

// Mirrors of the run API's models (clabe.workflow.RunState, clabe.workflow.events, clabe.server._models).
// Request specs and answer schemas stay JSON: their shape depends on the prompt kind.

public sealed record StepState(
    string Name,
    string Title,
    string Status,
    bool Barrier,
    string? Optional,
    bool Locked,
    string? Summary);

public sealed record RunState(
    [property: JsonPropertyName("run_id")] string RunId,
    string Workflow,
    string Status,
    [property: JsonPropertyName("current_step")] string? CurrentStep,
    List<StepState> Steps,
    RunEvent? Prompt,
    [property: JsonPropertyName("last_seq")] int LastSeq);

public sealed record ServerInfo(
    [property: JsonPropertyName("clabe_version")] string ClabeVersion,
    string Hostname,
    [property: JsonPropertyName("run_id")] string RunId,
    string Workflow,
    string Status);

public sealed record CommandResult(bool Ok, string? Reason);

public sealed record DirEntry(string Name, string Path, [property: JsonPropertyName("is_dir")] bool IsDir);

public sealed record DirListing(string Path, string? Parent, List<DirEntry> Entries);

/// <summary>One run event. Only the fields of its <see cref="Type"/> are set.</summary>
public sealed class RunEvent
{
    public int Seq { get; set; }
    public DateTimeOffset Ts { get; set; }
    public string Type { get; set; } = "";
    public string? Step { get; set; }
    public int Attempt { get; set; }
    public string? Summary { get; set; }
    public string? Status { get; set; }
    public string? Reason { get; set; }
    public string? Error { get; set; }
    public string? Traceback { get; set; }
    [JsonPropertyName("warn_only")] public bool WarnOnly { get; set; }
    public List<string>? Steps { get; set; }
    public string? Workflow { get; set; }
    public string? Path { get; set; }
    // prompts
    [JsonPropertyName("prompt_id")] public string? PromptId { get; set; }
    public string? Kind { get; set; }
    public JsonObject? Spec { get; set; }
    [JsonPropertyName("answer_schema")] public JsonObject? AnswerSchema { get; set; }
    public string? Outcome { get; set; }
    [JsonPropertyName("answered_by")] public string? AnsweredBy { get; set; }
    public JsonNode? Answer { get; set; }
    // messages and activities
    public string? Message { get; set; }
    public string? Level { get; set; }
    public string? Text { get; set; }
    [JsonPropertyName("activity_id")] public string? ActivityId { get; set; }
    public string? Description { get; set; }

    /// <summary>The spec's field (e.g. <c>rig.failed</c>), naming what the prompt asks about.</summary>
    [JsonIgnore] public string? Field => Spec?["field"]?.GetValue<string>();
}

public static class Json
{
    public static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };
}
