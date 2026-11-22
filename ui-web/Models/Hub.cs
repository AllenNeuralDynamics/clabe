namespace Clabe.Web.Models;

// Mirrors of the hub's API models (clabe.hub).

public sealed record Waiting(string PromptId, string Kind, string Label, string? Step, DateTimeOffset Since);

public sealed record RigStatus(
    string Id,
    string Url,
    bool Reachable,
    string? Error,
    string? Hostname,
    string? ClabeVersion,
    string Status,
    string? LeasedBy,
    string? Experiment,
    string? Repository,
    string? RunStatus,
    string? CurrentStep,
    string? CurrentStepTitle,
    string? Header,
    Waiting? Waiting,
    DateTimeOffset? SeenAt);
