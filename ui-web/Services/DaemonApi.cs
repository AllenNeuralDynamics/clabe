using System.Net;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Clabe.Web.Api.Daemon;
using Clabe.Web.Api.Hub;
using Clabe.Web.Api.Run;
using Clabe.Web.Models;

namespace Clabe.Web.Services;

/// <summary>The daemon's API (<c>api/daemon</c>), when the page is served by a daemon rather than by a run.</summary>
public sealed class DaemonApi(HttpClient http, RunApi run)
{
    /// <summary>Whether the page is served by a daemon: it answers <c>api/daemon/info</c> (a run does not).</summary>
    public async Task<bool> IsDaemonAsync()
    {
        try
        {
            var response = await http.GetAsync("api/daemon/info");
            return response.StatusCode != HttpStatusCode.NotFound;
        }
        catch (HttpRequestException)
        {
            return false;
        }
    }

    public async Task<HostInfo> InfoAsync() => (await http.GetFromJsonAsync<HostInfo>("api/daemon/info", Json.Options))!;

    public async Task<List<RepositoryInfo>> RepositoriesAsync() =>
        (await http.GetFromJsonAsync<List<RepositoryInfo>>("api/daemon/repositories", Json.Options))!;

    public async Task<ExperimentListing> ExperimentsAsync(string repository)
    {
        var response = await http.GetAsync($"api/daemon/repositories/{Uri.EscapeDataString(repository)}/experiments");
        await EnsureOk(response);
        return (await response.Content.ReadFromJsonAsync<ExperimentListing>(Json.Options))!;
    }

    public async Task<RunInfo?> CurrentAsync() =>
        await http.GetFromJsonAsync<RunInfo?>("api/daemon/runs/current", Json.Options);

    /// <summary>Starts an experiment; <paramref name="frontend"/> is what it shows on the rig: none, tui or browser.</summary>
    public async Task LaunchAsync(string repository, string file, string experiment, string frontend = "none")
    {
        var body = new JsonObject
        {
            ["repository"] = repository, ["file"] = file, ["experiment"] = experiment, ["frontend"] = frontend,
        };
        await EnsureOk(await http.PostAsJsonAsync("api/daemon/runs", body, Json.Options));
    }

    /// <summary>When a hub serves this page (under <c>rigs/&lt;id&gt;/</c>): the hub's address and the rig's id.</summary>
    public (Uri Hub, string Rig)? ThroughHub
    {
        get
        {
            var path = http.BaseAddress!.AbsolutePath;
            var at = path.IndexOf("/rigs/", StringComparison.Ordinal);
            if (at < 0)
            {
                return null;
            }
            var rig = Uri.UnescapeDataString(path[(at + "/rigs/".Length)..].Split('/')[0]);
            return rig.Length == 0 ? null : (new Uri(http.BaseAddress, path[..(at + 1)]), rig);
        }
    }

    /// <summary>The front end last used on this rig, as the hub remembers it; null without a hub or a choice.</summary>
    public async Task<string?> RememberedFrontendAsync()
    {
        if (ThroughHub is not { } through)
        {
            return null;
        }
        try
        {
            var rigs = await http.GetFromJsonAsync<JsonArray>(new Uri(through.Hub, "api/hub/rigs"), Json.Options);
            var rig = rigs?.FirstOrDefault(r => (string?)r?["id"] == through.Rig);
            return (string?)rig?["frontend"];
        }
        catch (Exception exc) when (exc is HttpRequestException or System.Text.Json.JsonException or InvalidOperationException)
        {
            return null;
        }
    }

    /// <summary>Has the hub remember the front end used on this rig (nothing without a hub).</summary>
    public async Task RememberFrontendAsync(string frontend)
    {
        if (ThroughHub is not { } through)
        {
            return;
        }
        try
        {
            var url = new Uri(through.Hub, $"api/hub/rigs/{Uri.EscapeDataString(through.Rig)}/frontend");
            await http.PutAsJsonAsync(url, new JsonObject { ["frontend"] = frontend }, Json.Options);
        }
        catch (HttpRequestException)
        {
            // remembering is a convenience
        }
    }

    public async Task StopAsync() => await EnsureOk(await http.PostAsync("api/daemon/runs/current/stop", null));

    /// <summary>The run's page, proxied by the daemon (with the token, when there is one).</summary>
    public string RunPageUrl => run.Token is null ? "run/" : $"run/?token={Uri.EscapeDataString(run.Token)}";

    private static async Task EnsureOk(HttpResponseMessage response)
    {
        if (!response.IsSuccessStatusCode)
        {
            var detail = await response.Content.ReadAsStringAsync();
            try
            {
                detail = JsonNode.Parse(detail)?["detail"]?.ToString() ?? detail;
            }
            catch (System.Text.Json.JsonException)
            {
                // not JSON: keep the text
            }
            throw new InvalidOperationException(detail);
        }
    }
}
