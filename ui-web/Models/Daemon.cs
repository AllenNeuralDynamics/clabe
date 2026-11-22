namespace Clabe.Web.Models;

// Mirrors of the daemon's API models (clabe.daemon).

public sealed record RepositoryInfo(string Name, string Path, bool Exists);

public sealed record StepInfo(string Name, string Title, bool Barrier, string? Optional);

public sealed record ExperimentInfo(string File, string Name, List<StepInfo> Steps);

public sealed record ListingError(string File, string Error);

public sealed record ExperimentListing(List<ExperimentInfo> Experiments, List<ListingError> Errors);

public sealed record DaemonRunInfo(
    string Repository,
    string File,
    string? Experiment,
    string Status,
    DateTimeOffset StartedAt,
    int Pid,
    int? Port,
    int? ExitCode,
    List<string> LogTail);

public sealed record HostInfo(string Hostname, string ClabeVersion, string Status, DaemonRunInfo? Run, string? LeasedBy);
