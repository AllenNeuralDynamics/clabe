using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
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

    /// <summary>The event stream's URL; EventSource cannot send headers, so the token goes in the query.</summary>
    public string EventsUrl =>
        new Uri(http.BaseAddress!, Token is null ? "api/events" : $"api/events?token={Uri.EscapeDataString(Token)}").ToString();

    /// <summary>Where the browser loads a media prompt's local file (an img or iframe cannot send headers either).</summary>
    public string MediaUrl(string promptId)
    {
        var path = $"api/prompts/{Uri.EscapeDataString(promptId)}/media";
        return new Uri(http.BaseAddress!, Token is null ? path : $"{path}?token={Uri.EscapeDataString(Token)}").ToString();
    }

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

    public Task<CommandResult> BackAsync(string step) => StepCommandAsync(step, "back");

    public Task<CommandResult> AbortAsync(string reason) =>
        PostAsync("api/abort", new JsonObject { ["reason"] = reason });

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
        PostAsync($"api/steps/{Uri.EscapeDataString(step)}/{command}", new JsonObject { ["client"] = ClientName });

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
