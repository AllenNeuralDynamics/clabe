using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Clabe.Web.Api.Run;
using Clabe.Web.Models;

namespace Clabe.Web.Services;

/// <summary>The run API, relative to the page (so it also works behind a hub's prefix).</summary>
public sealed class RunApi(HttpClient http)
{
    /// <summary>The shared token, when the server requires one (taken from the page's <c>?token=</c>).</summary>
    public string? Token { get; private set; }

    /// <summary>Recorded as the client of every answer and command.</summary>
    public string ClientName { get; } = $"web-{Guid.NewGuid().ToString("N")[..4]}";

    public void UseToken(string? token)
    {
        Token = string.IsNullOrEmpty(token) ? null : token;
        http.DefaultRequestHeaders.Authorization = Token is null ? null : new AuthenticationHeaderValue("Bearer", Token);
    }

    /// <summary>An API URL the browser loads itself (EventSource, img, iframe, a download cannot send headers): the
    /// token goes in the query.</summary>
    private string BrowserUrl(string path) =>
        new Uri(http.BaseAddress!, Token is null ? path : $"{path}?token={Uri.EscapeDataString(Token)}").ToString();

    /// <summary>The event stream's URL.</summary>
    public string EventsUrl => BrowserUrl("api/events");

    /// <summary>Where the browser loads a media prompt's local file.</summary>
    public string MediaUrl(string promptId) => BrowserUrl($"api/prompts/{Uri.EscapeDataString(promptId)}/media");

    /// <summary>The run's logs and records as a zip (Session → Export logs).</summary>
    public string ExportUrl => BrowserUrl("api/export");

    public async Task<ServerInfo> InfoAsync() =>
        (await http.GetFromJsonAsync<ServerInfo>("api/info", Json.Options))!;

    public async Task<RunState> StateAsync() =>
        (await http.GetFromJsonAsync<RunState>("api/state", Json.Options))!;

    public Task<CommandResult> AnswerAsync(string promptId, JsonNode? value) =>
        PostAsync($"api/prompts/{Uri.EscapeDataString(promptId)}/answer", new JsonObject
        {
            ["value"] = value?.DeepClone(),
            ["client"] = ClientName,
        });

    /// <summary>The options of an autocomplete prompt (or of its group <paramref name="item"/>) for the typed text.</summary>
    public async Task<List<string>> OptionsAsync(string promptId, string typed, string? item = null)
    {
        var path = $"api/prompts/{Uri.EscapeDataString(promptId)}/options?q={Uri.EscapeDataString(typed)}";
        if (item is not null)
        {
            path += $"&item={Uri.EscapeDataString(item)}";
        }
        return await http.GetFromJsonAsync<List<string>>(path, Json.Options) ?? [];
    }

    /// <summary>Goes back to step <paramref name="step"/> (the rail, Go to step…).</summary>
    public Task<CommandResult> BackAsync(string step) => StepCommandAsync(step, "back");

    /// <summary>Goes back to the previous step (the state's <c>back_target</c>).</summary>
    public Task<CommandResult> BackAsync() => ClientCommandAsync("api/back");

    /// <summary>Answers the open prompt with its earlier answer.</summary>
    public Task<CommandResult> ForwardAsync() => ClientCommandAsync("api/forward");

    public Task<CommandResult> RetryAsync(string step) => StepCommandAsync(step, "retry");

    public Task<CommandResult> SkipAsync(string step) => StepCommandAsync(step, "skip");

    /// <summary>Closes the finished run's session (the closing screen's Close session).</summary>
    public Task<CommandResult> CloseAsync() => ClientCommandAsync("api/close");

    public Task<CommandResult> AbortAsync(string reason) =>
        PostAsync("api/abort", new JsonObject { ["reason"] = reason });

    /// <summary>Files an issue report; the hub collects it from this machine.</summary>
    public async Task<(Clabe.Web.Api.Run.ReportInfo? Report, string? Error)> ReportAsync(string comment)
    {
        try
        {
            var response = await http.PostAsJsonAsync("api/report", new JsonObject
            {
                ["comment"] = comment,
                ["client"] = ClientName,
            }, Json.Options);
            if (!response.IsSuccessStatusCode)
            {
                return (null, $"{(int)response.StatusCode}: {await response.Content.ReadAsStringAsync()}");
            }
            return (await response.Content.ReadFromJsonAsync<Clabe.Web.Api.Run.ReportInfo>(Json.Options), null);
        }
        catch (HttpRequestException exc)
        {
            return (null, $"The run is not reachable: {exc.Message}");
        }
    }

    public async Task<DirListing> ListDirAsync(string? path, string kind, IEnumerable<string>? extensions)
    {
        var body = new JsonObject
        {
            ["path"] = path,
            ["kind"] = kind,
            ["extensions"] = extensions is null ? null : new JsonArray(extensions.Select(e => (JsonNode?)e).ToArray()),
        };
        var response = await http.PostAsJsonAsync("api/fs/list", body, Json.Options);
        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException(await response.Content.ReadAsStringAsync());
        }
        return (await response.Content.ReadFromJsonAsync<DirListing>(Json.Options))!;
    }

    private Task<CommandResult> StepCommandAsync(string step, string command) =>
        ClientCommandAsync($"api/steps/{Uri.EscapeDataString(step)}/{command}");

    private Task<CommandResult> ClientCommandAsync(string path) =>
        PostAsync(path, new JsonObject { ["client"] = ClientName });

    private async Task<CommandResult> PostAsync(string path, JsonObject body)
    {
        try
        {
            var response = await http.PostAsJsonAsync(path, body, Json.Options);
            if (!response.IsSuccessStatusCode)
            {
                return new CommandResult(false, $"{(int)response.StatusCode}: {await response.Content.ReadAsStringAsync()}");
            }
            return (await response.Content.ReadFromJsonAsync<CommandResult>(Json.Options))!;
        }
        catch (HttpRequestException exc)
        {
            return new CommandResult(false, $"The run is not reachable: {exc.Message}");
        }
    }
}
