using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Rendering;
using Microsoft.JSInterop;

namespace Clabe.Web.Components;

/// <summary>Search in the report viewers: counting matches and marking them (<c>.search-hit</c>, the current one
/// <c>.search-hit.current</c>). Matching ignores case.</summary>
public static class ReportSearch
{
    /// <summary>How many times <paramref name="query"/> occurs in <paramref name="text"/>.</summary>
    public static int Count(string text, string? query)
    {
        if (string.IsNullOrEmpty(query) || string.IsNullOrEmpty(text))
        {
            return 0;
        }
        var count = 0;
        for (var at = text.IndexOf(query, StringComparison.OrdinalIgnoreCase); at >= 0;
             at = text.IndexOf(query, at + query.Length, StringComparison.OrdinalIgnoreCase))
        {
            count++;
        }
        return count;
    }

    public static bool Contains(string? text, string? query) =>
        string.IsNullOrEmpty(query) || (text?.Contains(query, StringComparison.OrdinalIgnoreCase) ?? false);

    /// <summary><paramref name="text"/> in a span of class <paramref name="cls"/> (none if null), its matches of
    /// <paramref name="query"/> marked. Matches are numbered from <paramref name="first"/>; the one numbered
    /// <paramref name="current"/> is marked current.</summary>
    public static RenderFragment Marked(string text, string? cls, string? query, int first = 0, int current = -1) => builder =>
    {
        if (cls is not null)
        {
            builder.OpenElement(0, "span");
            builder.AddAttribute(1, "class", cls);
        }
        Write(builder, text, query, first, current);
        if (cls is not null)
        {
            builder.CloseElement();
        }
    };

    private static void Write(RenderTreeBuilder builder, string text, string? query, int ordinal, int current)
    {
        if (string.IsNullOrEmpty(query))
        {
            builder.AddContent(2, text);
            return;
        }
        var start = 0;
        for (var at = text.IndexOf(query, StringComparison.OrdinalIgnoreCase); at >= 0;
             at = text.IndexOf(query, start, StringComparison.OrdinalIgnoreCase))
        {
            if (at > start)
            {
                builder.AddContent(3, text[start..at]);
            }
            builder.OpenElement(4, "mark");
            builder.AddAttribute(5, "class", ordinal == current ? "search-hit current" : "search-hit");
            builder.AddContent(6, text.Substring(at, query.Length));
            builder.CloseElement();
            ordinal++;
            start = at + query.Length;
        }
        if (start < text.Length)
        {
            builder.AddContent(7, text[start..]);
        }
    }
}

/// <summary>
/// Times as every page shows them: relative ("3 min ago"), or exact in the browser's local time (the default) or in
/// UTC, as the user chose (remembered in the browser). Everything is stored and sent in UTC; only the display changes.
/// </summary>
public static class ReportTime
{
    private const string StorageKey = "clabe.time";
    private static IJSInProcessRuntime? _js;
    private static readonly Dictionary<long, TimeSpan> Offsets = [];

    /// <summary>Whether times are shown in UTC rather than local time.</summary>
    public static bool Utc { get; private set; }

    /// <summary>Raised when the user switches between local time and UTC.</summary>
    public static event Action? Changed;

    /// <summary>Called once at startup: the app has no time zone data, so the browser converts (with daylight saving).</summary>
    public static void Init(IJSInProcessRuntime js)
    {
        _js = js;
        Utc = js.Invoke<string?>("localStorage.getItem", StorageKey) == "utc";
    }

    public static void SetUtc(bool utc)
    {
        Utc = utc;
        _js?.InvokeVoid("localStorage.setItem", StorageKey, utc ? "utc" : "local");
        Changed?.Invoke();
    }

    /// <summary>"UTC", or "local time".</summary>
    public static string ZoneName => Utc ? "UTC" : "local time";

    /// <summary>The browser's offset from UTC at that instant (cached per quarter hour).</summary>
    private static TimeSpan OffsetAt(DateTimeOffset time)
    {
        if (_js is null)
        {
            return TimeSpan.Zero;
        }
        var ms = time.ToUnixTimeMilliseconds();
        var key = Math.DivRem(ms, 900_000, out _);
        if (!Offsets.TryGetValue(key, out var offset))
        {
            offset = TimeSpan.FromMinutes(_js.Invoke<int>("clabeTime.offset", (double)ms));
            Offsets[key] = offset;
        }
        return offset;
    }

    /// <summary>The time as shown: in local time or in UTC.</summary>
    public static DateTimeOffset Local(DateTimeOffset time) => Utc ? time.ToUniversalTime() : time.ToOffset(OffsetAt(time));

    /// <summary>"14:03:27" (or "14:03:27.120").</summary>
    public static string Clock(DateTimeOffset time, bool milliseconds = false) =>
        Local(time).ToString(milliseconds ? "HH:mm:ss.fff" : "HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture);

    /// <summary>"2026-11-22 14:03:27", with " UTC" when shown in UTC.</summary>
    public static string Exact(DateTimeOffset time) =>
        Local(time).ToString("yyyy-MM-dd HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture) + (Utc ? " UTC" : "");

    /// <summary>A tooltip with the time in both zones.</summary>
    public static string Both(DateTimeOffset time) =>
        $"{time.ToOffset(OffsetAt(time)):yyyy-MM-dd HH:mm:ss.fff} local · {time.ToUniversalTime():yyyy-MM-dd HH:mm:ss.fff} UTC";

    private static readonly System.Text.RegularExpressions.Regex Stamp = new(
        @"^(\d{4}-\d{2}-\d{2})[T ](\d{2}):?(\d{2}):?(\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})",
        System.Text.RegularExpressions.RegexOptions.Compiled);

    /// <summary>A log line whose leading timestamp carries a zone ("2026-11-23T003917Z - …") with that timestamp
    /// shown as chosen ("2026-11-22 16:39:17 - …"); other lines unchanged.</summary>
    public static string LogLine(string line)
    {
        var match = Stamp.Match(line);
        if (!match.Success)
        {
            return line;
        }
        var g = match.Groups;
        var zone = g[6].Value == "Z" ? "+00:00" : g[6].Value.Length == 5 ? g[6].Value.Insert(3, ":") : g[6].Value;
        var iso = $"{g[1].Value}T{g[2].Value}:{g[3].Value}:{g[4].Value}{g[5].Value}{zone}";
        if (!DateTimeOffset.TryParse(iso, System.Globalization.CultureInfo.InvariantCulture, System.Globalization.DateTimeStyles.None, out var time))
        {
            return line;
        }
        var shown = Local(time).ToString(g[5].Success ? "yyyy-MM-dd HH:mm:ss.fff" : "yyyy-MM-dd HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture);
        return shown + (Utc ? "Z" : "") + line[match.Length..];
    }

    public static string Ago(DateTimeOffset time)
    {
        var elapsed = DateTimeOffset.UtcNow - time;
        if (elapsed.TotalSeconds < 45)
        {
            return "just now";
        }
        if (elapsed.TotalMinutes < 60)
        {
            return $"{Math.Max(1, (int)Math.Round(elapsed.TotalMinutes))} min ago";
        }
        if (elapsed.TotalHours < 24)
        {
            return $"{(int)elapsed.TotalHours} h ago";
        }
        if (elapsed.TotalDays < 7)
        {
            var days = (int)elapsed.TotalDays;
            return days == 1 ? "yesterday" : $"{days} days ago";
        }
        var local = Local(time);
        return local.Year == Local(DateTimeOffset.UtcNow).Year ? local.ToString("MMM d") : local.ToString("MMM d, yyyy");
    }

    /// <summary>A duration, short: "42 s", "7 min", "1 h 05".</summary>
    public static string Duration(TimeSpan elapsed) =>
        elapsed.TotalMinutes < 1 ? $"{Math.Max(0, (int)elapsed.TotalSeconds)} s"
        : elapsed.TotalHours < 1 ? $"{(int)elapsed.TotalMinutes} min"
        : $"{(int)elapsed.TotalHours} h {elapsed.Minutes:00}";

    public static string Size(long bytes) =>
        bytes < 1024 ? $"{bytes} B"
        : bytes < 1024 * 1024 ? $"{bytes / 1024.0:0.#} KB"
        : $"{bytes / (1024.0 * 1024):0.#} MB";
}
