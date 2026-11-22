using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Rendering;

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

/// <summary>Times as the hub shows them: relative ("3 min ago"), and exact in the browser's time zone.</summary>
public static class ReportTime
{
    /// <summary>The browser's offset from UTC (the app has no time zone data; the page sets it once).</summary>
    public static TimeSpan Offset { get; set; }

    public static DateTimeOffset Local(DateTimeOffset time) => time.ToOffset(Offset);

    public static string Exact(DateTimeOffset time) => Local(time).ToString("yyyy-MM-dd HH:mm:ss");

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
