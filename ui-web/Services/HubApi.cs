using System.Net;
using System.Net.Http.Json;
using Clabe.Web.Models;

namespace Clabe.Web.Services;

/// <summary>The hub's API (<c>api/hub</c>), when the page is served by a hub.</summary>
public sealed class HubApi(HttpClient http, RunApi run)
{
    /// <summary>Whether the page is served by a hub: it answers <c>api/hub/rigs</c>.</summary>
    public async Task<bool> IsHubAsync()
    {
        try
        {
            var response = await http.GetAsync("api/hub/rigs");
            return response.StatusCode is HttpStatusCode.OK or HttpStatusCode.Unauthorized;
        }
        catch (HttpRequestException)
        {
            return false;
        }
    }

    public async Task<List<RigStatus>> RigsAsync() =>
        (await http.GetFromJsonAsync<List<RigStatus>>("api/hub/rigs", Json.Options))!;

    /// <summary>A rig's page (or, with <paramref name="run"/>, its run's page), through the hub.</summary>
    public string RigUrl(string rig, bool runPage = false)
    {
        var path = runPage ? $"rigs/{rig}/run/" : $"rigs/{rig}/";
        return run.Token is null ? path : $"{path}?token={Uri.EscapeDataString(run.Token)}";
    }
}
