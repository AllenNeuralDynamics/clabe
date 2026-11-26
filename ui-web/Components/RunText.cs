using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Clabe.Web.Api.Run;

namespace Clabe.Web.Components;

/// <summary>Readable text for the run page: step titles, durations and short summaries of step outputs.</summary>
public static partial class RunText
{
    /// <summary>The launcher's own first step.</summary>
    public const string PreflightStep = "clabe_preflight";

    public static string Title(StepState step) => step.Name == PreflightStep ? "Setup" : step.Title;

    /// <summary>A compact duration: <c>4s</c>, <c>1m 05s</c>, <c>2h 03m</c>.</summary>
    public static string Duration(TimeSpan span)
    {
        if (span < TimeSpan.Zero)
        {
            span = TimeSpan.Zero;
        }
        if (span.TotalSeconds < 10)
        {
            return $"{span.TotalSeconds:0.0}s".Replace(".0s", "s");
        }
        if (span.TotalMinutes < 1)
        {
            return $"{(int)span.TotalSeconds}s";
        }
        if (span.TotalHours < 1)
        {
            return $"{(int)span.TotalMinutes}m {span.Seconds:00}s";
        }
        return $"{(int)span.TotalHours}h {span.Minutes:00}m";
    }

    /// <summary>A clock-like elapsed time: <c>4:07</c>, <c>1:02:09</c>.</summary>
    public static string Clock(TimeSpan span)
    {
        if (span < TimeSpan.Zero)
        {
            span = TimeSpan.Zero;
        }
        return span.TotalHours >= 1 ? $"{(int)span.TotalHours}:{span.Minutes:00}:{span.Seconds:00}" : $"{span.Minutes}:{span.Seconds:00}";
    }

    /// <summary>The last segment of a path (either separator), else the text.</summary>
    public static string Basename(string path)
    {
        var trimmed = path.TrimEnd('/', '\\');
        var cut = trimmed.LastIndexOfAny(['/', '\\']);
        return cut >= 0 && cut < trimmed.Length - 1 ? trimmed[(cut + 1)..] : trimmed;
    }

    public static bool LooksLikePath(string text) =>
        text.Length > 3 && !text.Contains('\n') && (DrivePath().IsMatch(text) || text.StartsWith('/') || text.StartsWith("~/")
            || text.StartsWith(@"\\")) && text.Count(c => c is '/' or '\\') >= 1;

    /// <summary>The <c>key=value</c> fields of a step summary (<c>Session: subject=mouse-7, ...</c>), in order.</summary>
    public static List<KeyValuePair<string, string>> Fields(string? summary)
    {
        var result = new List<KeyValuePair<string, string>>();
        if (string.IsNullOrEmpty(summary))
        {
            return result;
        }
        var body = ModelPrefix().Replace(summary, "", 1);
        var matches = FieldKey().Matches(body);
        for (var i = 0; i < matches.Count; i++)
        {
            var start = matches[i].Index + matches[i].Length;
            var end = i + 1 < matches.Count ? matches[i + 1].Index : body.Length;
            var value = body[start..end].TrimEnd(' ', ',', '…');
            result.Add(new(matches[i].Groups[1].Value, value));
        }
        return result;
    }

    /// <summary>One field of a step summary, if present.</summary>
    public static string? Field(string? summary, string key) =>
        Fields(summary).FirstOrDefault(f => f.Key == key) is { Key: not null } pair ? pair.Value : null;

    /// <summary>A short, readable rendering of a step's summary for the rail.</summary>
    public static string? Short(StepState step, Func<string, string, JsonNode?> answerOf)
    {
        if (step.Summary is not { } summary)
        {
            return null;
        }
        var fields = Fields(summary);
        if (fields.Count == 0)
        {
            return LooksLikePath(summary) ? Basename(summary) : summary;
        }
        if (fields.Any(f => f.Key == "subject"))
        {
            var subject = fields.First(f => f.Key == "subject").Value;
            var who = Experimenter(step.Name, answerOf);
            return who is null ? subject : $"{subject} · {who}";
        }
        var parts = fields
            .Where(f => !Noise(f.Value))
            .Select(f => f.Key is "name" or "id" || LooksLikePath(f.Value) ? Shorten(f.Value) : $"{Humanize(f.Key)} {f.Value}");
        return string.Join(" · ", parts);
    }

    /// <summary>The experimenter(s) answered in <paramref name="step"/>, as typed.</summary>
    public static string? Experimenter(string step, Func<string, string, JsonNode?> answerOf) =>
        answerOf(step, "experimenter") switch
        {
            JsonArray names => string.Join(", ", names.Select(n => n?.ToString())),
            JsonValue value when value.ToString() is { Length: > 0 } text => text,
            _ => null,
        };

    private static string Shorten(string value) => LooksLikePath(value) ? Basename(value) : value;

    private static string Humanize(string key) => key.Replace('_', ' ');

    /// <summary>Values not worth a rail's space: hashes, timestamps.</summary>
    private static bool Noise(string value) => HexHash().IsMatch(value) || IsoTime().IsMatch(value);

    [GeneratedRegex(@"^[A-Za-z]:[\\/]")]
    private static partial Regex DrivePath();

    [GeneratedRegex(@"^[A-Z][A-Za-z0-9_]*: ")]
    private static partial Regex ModelPrefix();

    [GeneratedRegex(@"(?:^|, )([A-Za-z_][A-Za-z0-9_]*)=")]
    private static partial Regex FieldKey();

    [GeneratedRegex(@"^[0-9a-f]{20,}$")]
    private static partial Regex HexHash();

    [GeneratedRegex(@"^\d{4}-\d{2}-\d{2}T")]
    private static partial Regex IsoTime();
}
