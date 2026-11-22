using System.Text.Json;
using System.Text.Json.Nodes;
using Clabe.Web.Api.Run;
using Clabe.Web.Models;
using Microsoft.AspNetCore.Components;
using Microsoft.JSInterop;

namespace Clabe.Web.Services;

/// <summary>
/// The page's view of one run: a snapshot (re-read whenever an event changes it) plus what only the events carry —
/// messages, activities, failures, step timings, answers and the full log.
/// </summary>
public sealed class RunConnection(RunApi api, IJSRuntime js, NavigationManager navigation) : IAsyncDisposable
{
    private static readonly HashSet<string> StateChanging =
    [
        "run_started", "run_finished", "run_closing", "run_closed", "step_started", "step_finished", "step_failed",
        "step_skipped", "steps_invalidated", "prompt_opened", "prompt_closed",
    ];

    private readonly Dictionary<string, RunEvent> _activities = [];
    private readonly Dictionary<string, List<string>> _outputs = [];
    private readonly Dictionary<string, RunEvent> _failures = [];
    private readonly Dictionary<string, List<BranchView>> _branches = [];
    private readonly Dictionary<string, StepTiming> _timings = [];
    private readonly Dictionary<string, RunEvent> _opened = [];
    private readonly Dictionary<string, JsonNode?> _answers = [];
    private DotNetObjectReference<RunConnection>? _self;
    private IJSObjectReference? _module;
    private IJSObjectReference? _stream;
    private bool _refreshing;
    private bool _refreshAgain;
    private int _lastSeq;

    public RunApi Api => api;
    public ServerInfo? Info { get; private set; }
    public RunState? State { get; private set; }
    public string? Header { get; private set; }

    /// <summary><c>connecting</c>, <c>live</c>, <c>reconnecting</c>, <c>ended</c> or <c>error</c>.</summary>
    public string Status { get; private set; } = "connecting";
    public string? Error { get; private set; }
    public List<RunEvent> Log { get; } = [];
    public List<RunEvent> Messages { get; } = [];
    public IEnumerable<RunEvent> Activities => _activities.Values;

    /// <summary>When the run started (its first event), once known.</summary>
    public DateTimeOffset? StartedAt { get; private set; }

    /// <summary>The run's <c>run_finished</c> event, once it finished.</summary>
    public RunEvent? FinishedEvent { get; private set; }

    /// <summary>The run's <c>run_closed</c> event, once its session was closed.</summary>
    public RunEvent? ClosedEvent { get; private set; }

    /// <summary>The last lines an activity printed (remote jobs), newest last.</summary>
    public IReadOnlyList<string> OutputOf(string activityId) => _outputs.GetValueOrDefault(activityId) ?? [];

    /// <summary>Set when the prompt on screen was answered by another client (e.g. "Answered on pc-2").</summary>
    public string? ClosedElsewhere { get; private set; }

    public bool Finished => State?.Status is "completed" or "aborted" or "failed";

    /// <summary>Whether the session is over: closed by a client, or the server is gone after the run.</summary>
    public bool SessionOver => ClosedEvent is not null || State?.Closing?.Closed == true || (Finished && Status == "ended");

    public event Action? Changed;

    /// <summary>The branches of <paramref name="step"/>'s current attempt, in the order they started.</summary>
    public IReadOnlyList<BranchView> BranchesOf(string step) => _branches.GetValueOrDefault(step) ?? [];

    /// <summary>The failure of <paramref name="step"/> in its current attempt, if any.</summary>
    public RunEvent? FailureOf(string step) => _failures.GetValueOrDefault(step);

    /// <summary>When <paramref name="step"/>'s latest attempt started and ended (null while it runs).</summary>
    public StepTiming? TimingOf(string step) => _timings.TryGetValue(step, out var timing) ? timing : null;

    /// <summary>The latest accepted answer to the prompt of <paramref name="step"/> asking <paramref name="field"/>.</summary>
    public JsonNode? AnswerOf(string step, string field) => _answers.GetValueOrDefault($"{step}\u0000{field}");

    /// <summary>Whether the server asked for a token this page does not have (the page then asks for it).</summary>
    public bool NeedsToken { get; private set; }

    public async Task StartAsync()
    {
        api.UseToken(QueryValue("token"));
        try
        {
            Info = await api.InfoAsync();
            State = await api.StateAsync();
        }
        catch (Exception exc) when (exc is HttpRequestException or JsonException)
        {
            Status = "error";
            NeedsToken = exc is HttpRequestException { StatusCode: System.Net.HttpStatusCode.Unauthorized } || exc.Message.Contains("401");
            Error = NeedsToken ? null : $"The run is not reachable: {exc.Message}";
            Changed?.Invoke();
            return;
        }
        Changed?.Invoke();
        _self = DotNetObjectReference.Create(this);
        _module = await js.InvokeAsync<IJSObjectReference>("import", "./js/events.js");
        _stream = await _module.InvokeAsync<IJSObjectReference>("follow", api.EventsUrl, _self);
    }

    [JSInvokable]
    public void OnStatus(string status)
    {
        if (Status == "ended")
        {
            return;
        }
        Status = status;
        Changed?.Invoke();
    }

    /// <summary>The stream broke after the run finished: keep following only while a closing screen waits.</summary>
    [JSInvokable]
    public async Task<bool> OnStreamLost()
    {
        try
        {
            State = await api.StateAsync();
            Changed?.Invoke();
            return State.Closing is { Closed: false };
        }
        catch (HttpRequestException)
        {
            return false; // the server is gone: the session is over
        }
    }

    [JSInvokable]
    public void OnEvent(string data)
    {
        var e = JsonSerializer.Deserialize<RunEvent>(data, Json.Options);
        if (e is null || e.Seq <= _lastSeq)
        {
            return;
        }
        _lastSeq = e.Seq;
        Log.Add(e);
        Apply(e);
        if (StateChanging.Contains(e.Type))
        {
            _ = RefreshAsync();
        }
        Changed?.Invoke();
    }

    public void DismissClosedElsewhere()
    {
        ClosedElsewhere = null;
        Changed?.Invoke();
    }

    private string? QueryValue(string name)
    {
        var query = new Uri(navigation.Uri).Query.TrimStart('?');
        foreach (var part in query.Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var pair = part.Split('=', 2);
            if (pair[0] == name)
            {
                return pair.Length == 2 ? Uri.UnescapeDataString(pair[1]) : "";
            }
        }
        return null;
    }

    private void Apply(RunEvent e)
    {
        StartedAt ??= e.Ts;
        switch (e.Type)
        {
            case "notify":
                Messages.Add(e);
                break;
            case "header":
                Header = e.Text;
                break;
            case "activity_started":
                _activities[e.ActivityId!] = e;
                break;
            case "activity_ended":
                _activities.Remove(e.ActivityId!);
                _outputs.Remove(e.ActivityId!);
                break;
            case "activity_output":
                var tail = _outputs.TryGetValue(e.ActivityId!, out var known) ? known : _outputs[e.ActivityId!] = [];
                tail.AddRange(e.Lines ?? []);
                if (tail.Count > 200)
                {
                    tail.RemoveRange(0, tail.Count - 200);
                }
                break;
            case "step_failed":
                _failures[e.Step!] = e;
                End(e.Step!, e.Ts);
                if (e.WarnOnly)
                {
                    Messages.Add(new RunEvent
                    {
                        Seq = e.Seq, Ts = e.Ts, Type = "notify", Level = "warning", Message = $"{e.Step}: {e.Error}",
                    });
                }
                break;
            case "step_started":
                _branches.Remove(e.Step!);
                _failures.Remove(e.Step!);
                _timings[e.Step!] = new StepTiming(e.Ts, null);
                break;
            case "branch_started":
            case "branch_ended":
                SetBranch(e);
                break;
            case "step_finished":
                _failures.Remove(e.Step!);
                End(e.Step!, e.Ts);
                break;
            case "step_skipped":
                _failures.Remove(e.Step!);
                _timings.Remove(e.Step!);
                break;
            case "steps_invalidated":
                foreach (var step in e.Steps ?? [])
                {
                    _timings.Remove(step);
                    _failures.Remove(step);
                }
                break;
            case "prompt_closed":
                if (e.Outcome == "answered" && _opened.Remove(e.PromptId!, out var opened) && opened.Field is { } field)
                {
                    _answers[$"{opened.Step}\u0000{field}"] = e.Answer?.DeepClone();
                }
                if (e.Outcome == "answered" && e.AnsweredBy != api.ClientName && State?.Prompt?.PromptId == e.PromptId)
                {
                    ClosedElsewhere = $"Answered on {e.AnsweredBy}";
                }
                break;
            case "prompt_opened":
                _opened[e.PromptId!] = e;
                ClosedElsewhere = null;
                break;
            case "run_finished":
                FinishedEvent = e;
                ClosedElsewhere = null;
                _activities.Clear();
                break;
            case "run_closed":
                ClosedEvent = e;
                break;
        }
    }

    private void End(string step, DateTimeOffset ts)
    {
        if (_timings.TryGetValue(step, out var timing))
        {
            _timings[step] = timing with { End = ts };
        }
    }

    private void SetBranch(RunEvent e)
    {
        var branches = _branches.TryGetValue(e.Step!, out var known) ? known : _branches[e.Step!] = [];
        var view = new BranchView(e.Branch!, e.Type == "branch_started" ? "running" : e.Outcome!, e.Attempt, e.Error);
        var index = branches.FindIndex(b => b.Name == e.Branch);
        if (index < 0)
        {
            branches.Add(view);
        }
        else if (!(e.Type == "branch_ended" && e.Outcome == "skipped" && branches[index].Error is { } error))
        {
            branches[index] = view;
        }
        else
        {
            branches[index] = view with { Error = error }; // skipped after failing: keep why
        }
    }

    /// <summary>Re-reads the snapshot; events arriving meanwhile coalesce into one more read.</summary>
    private async Task RefreshAsync()
    {
        if (_refreshing)
        {
            _refreshAgain = true;
            return;
        }
        _refreshing = true;
        try
        {
            do
            {
                _refreshAgain = false;
                try
                {
                    State = await api.StateAsync();
                }
                catch (HttpRequestException)
                {
                    // The server stops once the session ends; the events have told the rest of the story.
                    if (State is not null && FinishedEvent is { } finished)
                    {
                        State = State with { Status = finished.Status!, Prompt = null, CurrentStep = null };
                    }
                }
                Changed?.Invoke();
            } while (_refreshAgain);
        }
        finally
        {
            _refreshing = false;
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (_stream is not null)
        {
            await _stream.InvokeVoidAsync("close");
            await _stream.DisposeAsync();
        }
        if (_module is not null)
        {
            await _module.DisposeAsync();
        }
        _self?.Dispose();
    }
}

/// <summary>When a step's latest attempt started, and ended (null while it runs).</summary>
public readonly record struct StepTiming(DateTimeOffset Start, DateTimeOffset? End);
