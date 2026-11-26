using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Clabe.Web.Api.Hub;
using Clabe.Web.Models;

namespace Clabe.Web.Services;

/// <summary>What the reports table asks for: filters, sort and page.</summary>
public sealed record ReportQuery(
    string? Q = null,
    string? Rig = null,
    string? Experiment = null,
    string? Status = null,
    string Sort = "created_at",
    bool Descending = true,
    int Limit = 50,
    int Offset = 0);

/// <summary>Where a report's files come from: a stored report, or an export opened for a look.</summary>
public sealed record ReportSource(string Id, bool Inspected)
{
    /// <summary>The API path of the source's files (without the token).</summary>
    public string FilesPath => Inspected ? $"api/hub/inspect/{Id}/files" : $"api/hub/reports/{Id}/files";
}

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

    /// <summary>A rig's page (or, with <paramref name="runPage"/>, its run's page), through the hub.</summary>
    public string RigUrl(string rig, bool runPage = false) =>
        WithToken(runPage ? $"rigs/{rig}/run/" : $"rigs/{rig}/");

    // ---------- reports ----------

    public async Task<ReportPage> ReportsAsync(ReportQuery query)
    {
        var args = new List<string>
        {
            $"sort={query.Sort}",
            $"order={(query.Descending ? "desc" : "asc")}",
            $"limit={query.Limit}",
            $"offset={query.Offset}",
        };
        void Add(string name, string? value)
        {
            if (!string.IsNullOrWhiteSpace(value))
            {
                args.Add($"{name}={Uri.EscapeDataString(value.Trim())}");
            }
        }
        Add("q", query.Q);
        Add("rig", query.Rig);
        Add("experiment", query.Experiment);
        Add("status", query.Status);
        var response = await http.GetAsync($"api/hub/reports?{string.Join('&', args)}");
        await EnsureOk(response);
        return (await response.Content.ReadFromJsonAsync<ReportPage>(Json.Options))!;
    }

    public async Task<StoredReport> ReportAsync(string id)
    {
        var response = await http.GetAsync($"api/hub/reports/{id}");
        await EnsureOk(response);
        return (await response.Content.ReadFromJsonAsync<StoredReport>(Json.Options))!;
    }

    public async Task<StoredReport> SetStatusAsync(string id, string status)
    {
        var response = await http.PatchAsJsonAsync($"api/hub/reports/{id}", new StatusBody(status), Json.Options);
        await EnsureOk(response);
        return (await response.Content.ReadFromJsonAsync<StoredReport>(Json.Options))!;
    }

    public async Task DeleteAsync(string id) => await EnsureOk(await http.DeleteAsync($"api/hub/reports/{id}"));

    public async Task<long> DeleteManyAsync(IEnumerable<string> ids)
    {
        var response = await http.PostAsJsonAsync("api/hub/reports/delete", new DeleteBody([.. ids]), Json.Options);
        await EnsureOk(response);
        return (await response.Content.ReadFromJsonAsync<DeleteResult>(Json.Options))!.Deleted;
    }

    public async Task<List<ZipEntry>> FilesAsync(string id)
    {
        var response = await http.GetAsync($"api/hub/reports/{id}/files");
        await EnsureOk(response);
        return (await response.Content.ReadFromJsonAsync<List<ZipEntry>>(Json.Options))!;
    }

    /// <summary>A file of a report (or of an opened export), as text.</summary>
    public async Task<string> FileTextAsync(ReportSource source, string path)
    {
        var response = await http.GetAsync($"{source.FilesPath}/{EscapePath(path)}");
        await EnsureOk(response);
        return await response.Content.ReadAsStringAsync();
    }

    /// <summary>Opens an export (a zip) for a look, without storing it: sent as the raw request body.</summary>
    public async Task<InspectResult> InspectAsync(Stream zip)
    {
        using var content = new StreamContent(zip);
        content.Headers.ContentType = new MediaTypeHeaderValue("application/zip");
        var response = await http.PostAsync("api/hub/inspect", content);
        await EnsureOk(response);
        return (await response.Content.ReadFromJsonAsync<InspectResult>(Json.Options))!;
    }

    /// <summary>A stored report's zip, to download (the token in the query).</summary>
    public string ZipUrl(string id) => WithToken($"api/hub/reports/{id}/zip");

    /// <summary>A file of a report or export, to download (the token in the query).</summary>
    public string FileUrl(ReportSource source, string path) => WithToken($"{source.FilesPath}/{EscapePath(path)}");

    private static string EscapePath(string path) => string.Join('/', path.Split('/').Select(Uri.EscapeDataString));

    private string WithToken(string path) =>
        run.Token is null ? path : $"{path}{(path.Contains('?') ? '&' : '?')}token={Uri.EscapeDataString(run.Token)}";

    private static async Task EnsureOk(HttpResponseMessage response)
    {
        if (response.IsSuccessStatusCode)
        {
            return;
        }
        if (response.StatusCode == HttpStatusCode.Unauthorized)
        {
            throw new HttpRequestException("The hub requires a token: sign in again.", null, response.StatusCode);
        }
        var detail = await response.Content.ReadAsStringAsync();
        try
        {
            detail = JsonNode.Parse(detail)?["detail"]?.ToString() ?? detail;
        }
        catch (System.Text.Json.JsonException)
        {
            // not JSON: keep the text
        }
        throw new HttpRequestException(string.IsNullOrWhiteSpace(detail) ? response.ReasonPhrase : detail, null, response.StatusCode);
    }
}
