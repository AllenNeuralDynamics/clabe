using System.Net;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
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

    public async Task<DaemonRunInfo?> CurrentAsync() =>
        await http.GetFromJsonAsync<DaemonRunInfo?>("api/daemon/runs/current", Json.Options);

    public async Task LaunchAsync(string repository, string file, string experiment)
    {
        var body = new JsonObject { ["repository"] = repository, ["file"] = file, ["experiment"] = experiment };
        await EnsureOk(await http.PostAsJsonAsync("api/daemon/runs", body, Json.Options));
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
