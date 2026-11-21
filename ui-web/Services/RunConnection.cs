using System.Text.Json;
using Clabe.Web.Models;
using Microsoft.AspNetCore.Components;
using Microsoft.JSInterop;

namespace Clabe.Web.Services;

/// <summary>
/// The page's view of one run: a snapshot (re-read whenever an event changes it) plus what only the events carry —
/// messages, activities, failures and the full log.
/// </summary>
public sealed class RunConnection(RunApi api, IJSRuntime js, NavigationManager navigation) : IAsyncDisposable
{
    private static readonly HashSet<string> StateChanging =
    [
        "run_started", "run_finished", "step_started", "step_finished", "step_failed", "step_skipped",
        "steps_invalidated", "prompt_opened", "prompt_closed",
    ];

    private readonly Dictionary<string, RunEvent> _activities = [];
    private readonly Dictionary<string, RunEvent> _failures = [];
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

    /// <summary>Set when the prompt on screen was answered by another client (e.g. "Answered on pc-2").</summary>
    public string? ClosedElsewhere { get; private set; }

    public bool Finished => State?.Status is "completed" or "aborted" or "failed";

    public event Action? Changed;

    /// <summary>The failure of <paramref name="step"/> in its current attempt, if any.</summary>
    public RunEvent? FailureOf(string step) => _failures.GetValueOrDefault(step);

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
            Error = exc.Message.Contains("401")
                ? "This run requires a token: open the link clabe printed (it ends with ?token=...)."
                : $"The run is not reachable: {exc.Message}";
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
                break;
            case "step_failed":
                _failures[e.Step!] = e;
                if (e.WarnOnly)
                {
                    Messages.Add(new RunEvent
                    {
                        Seq = e.Seq, Ts = e.Ts, Type = "notify", Level = "warning", Message = $"{e.Step}: {e.Error}",
                    });
                }
                break;
            case "step_finished":
            case "step_skipped":
                _failures.Remove(e.Step!);
                break;
            case "prompt_closed":
                if (e.Outcome == "answered" && e.AnsweredBy != api.ClientName && State?.Prompt?.PromptId == e.PromptId)
                {
                    ClosedElsewhere = $"Answered on {e.AnsweredBy}";
                }
                break;
            case "prompt_opened":
                ClosedElsewhere = null;
                break;
            case "run_finished":
                _activities.Clear();
                break;
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
                    // The server stops once the run ends; the events have told the rest of the story.
                    if (State is not null && Log.LastOrDefault(e => e.Type == "run_finished") is { } finished)
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
