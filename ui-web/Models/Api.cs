using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace Clabe.Web.Models;

// The run's events, mapped by hand (one discriminated union); every other API model is generated into
// Models/Generated from ui-web/openapi (see scripts/openapi.py). Request specs and answer schemas stay JSON: their
// shape depends on the prompt kind.

/// <summary>One run event. Only the fields of its <see cref="Type"/> are set.</summary>
public sealed class RunEvent
{
    public int Seq { get; set; }
    public DateTimeOffset Ts { get; set; }
    public string Type { get; set; } = "";
    public string? Step { get; set; }
    public string? Branch { get; set; }
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
    /// <summary>A prompt's earlier answer (see <see cref="CanForward"/>): prefilled, and what Forward re-submits.</summary>
    [JsonPropertyName("previous_answer")] public JsonNode? PreviousAnswer { get; set; }
    [JsonPropertyName("can_forward")] public bool CanForward { get; set; }
    // closing screen
    public DateTimeOffset? Deadline { get; set; }
    [JsonPropertyName("closed_by")] public string? ClosedBy { get; set; }
    // messages and activities
    public string? Message { get; set; }
    public string? Level { get; set; }
    public string? Text { get; set; }
    [JsonPropertyName("activity_id")] public string? ActivityId { get; set; }
    public string? Description { get; set; }
    public string? Machine { get; set; }
    public List<string>? Lines { get; set; }
    public List<string>? Command { get; set; }
    [JsonPropertyName("exit_code")] public int? ExitCode { get; set; }
    public string? Label { get; set; }
    public string? Store { get; set; }
    // context_changed
    public string? Hostname { get; set; }
    public string? Rig { get; set; }
    public string? Subject { get; set; }
    public List<string>? Experimenter { get; set; }

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

/// <summary>A branch of a step (see clabe.workflow.concurrently), as the events tell it.</summary>
public sealed record BranchView(string Name, string Status, int Attempt, string? Error);
