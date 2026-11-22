using System.Text.Encodings.Web;
using System.Text.Json;

namespace Clabe.Web.Components;

/// <summary>A piece of a pretty-printed JSON line, and its highlighting class (<c>tok-key</c>, <c>tok-string</c>…).</summary>
public sealed record JsonTok(string Text, string Class);

/// <summary>One line of pretty-printed JSON. A line opening an object or array knows where it closes, to fold it.</summary>
public sealed class JsonLine
{
    public int Depth { get; init; }
    public List<JsonTok> Tokens { get; } = [];
    /// <summary>The line's parent (the opening line of the object or array it is in); -1 at the top.</summary>
    public int Parent { get; init; } = -1;
    /// <summary>For an opening line: the index of its closing line; else -1.</summary>
    public int End { get; set; } = -1;
    /// <summary>For an opening line: what it holds, shown when folded ("3 keys", "12 items").</summary>
    public string? Summary { get; set; }
    /// <summary>For an opening line: its closing bracket (and comma), shown when folded.</summary>
    public string? Close { get; set; }

    public bool Opens => End >= 0;
}

/// <summary>Pretty-prints JSON into highlighted, foldable lines (System.Text.Json parses; this lays it out).</summary>
public static class JsonPretty
{
    private static readonly JsonSerializerOptions Relaxed = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping };
    private static readonly JsonSerializerOptions Indented = new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        WriteIndented = true,
    };

    /// <summary>The lines of <paramref name="json"/>; null if it is not JSON.</summary>
    public static List<JsonLine>? TryLines(string json)
    {
        try
        {
            using var document = JsonDocument.Parse(json, new JsonDocumentOptions { AllowTrailingCommas = true, CommentHandling = JsonCommentHandling.Skip });
            return Lines(document.RootElement);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    public static List<JsonLine> Lines(JsonElement root)
    {
        var lines = new List<JsonLine>();
        Emit(lines, root, key: null, comma: false, depth: 0, parent: -1);
        return lines;
    }

    /// <summary><paramref name="element"/> re-indented (unicode kept as is).</summary>
    public static string Indent(JsonElement element) => JsonSerializer.Serialize(element, Indented);

    /// <summary>A string as a JSON literal (quoted, escaped; unicode kept as is).</summary>
    public static string Quote(string text) => JsonSerializer.Serialize(text, Relaxed);

    /// <summary>A scalar's literal: strings quoted, numbers and literals as written.</summary>
    public static string Scalar(JsonElement element) =>
        element.ValueKind == JsonValueKind.String ? Quote(element.GetString()!) : element.GetRawText();

    private static void Emit(List<JsonLine> lines, JsonElement element, string? key, bool comma, int depth, int parent)
    {
        var line = new JsonLine { Depth = depth, Parent = parent };
        if (key is not null)
        {
            line.Tokens.Add(new JsonTok(Quote(key), "tok-key"));
            line.Tokens.Add(new JsonTok(": ", "tok-punct"));
        }
        var comma_ = comma ? "," : "";
        switch (element.ValueKind)
        {
            case JsonValueKind.Object or JsonValueKind.Array:
            {
                var isObject = element.ValueKind == JsonValueKind.Object;
                var (open, close) = isObject ? ("{", "}") : ("[", "]");
                var count = isObject ? element.EnumerateObject().Count() : element.GetArrayLength();
                if (count == 0)
                {
                    line.Tokens.Add(new JsonTok(open + close + comma_, "tok-punct"));
                    lines.Add(line);
                    return;
                }
                line.Tokens.Add(new JsonTok(open, "tok-punct"));
                line.Summary = isObject ? $"{count} {(count == 1 ? "key" : "keys")}" : $"{count} {(count == 1 ? "item" : "items")}";
                line.Close = close + comma_;
                var index = lines.Count;
                lines.Add(line);
                var i = 0;
                if (isObject)
                {
                    foreach (var property in element.EnumerateObject())
                    {
                        Emit(lines, property.Value, property.Name, ++i < count, depth + 1, index);
                    }
                }
                else
                {
                    foreach (var item in element.EnumerateArray())
                    {
                        Emit(lines, item, null, ++i < count, depth + 1, index);
                    }
                }
                var closing = new JsonLine { Depth = depth, Parent = parent };
                closing.Tokens.Add(new JsonTok(close + comma_, "tok-punct"));
                line.End = lines.Count;
                lines.Add(closing);
                return;
            }
            case JsonValueKind.String:
                line.Tokens.Add(new JsonTok(Scalar(element), "tok-string"));
                break;
            case JsonValueKind.Number:
                line.Tokens.Add(new JsonTok(element.GetRawText(), "tok-number"));
                break;
            default:
                line.Tokens.Add(new JsonTok(element.GetRawText(), "tok-literal"));
                break;
        }
        if (comma)
        {
            line.Tokens.Add(new JsonTok(",", "tok-punct"));
        }
        lines.Add(line);
    }
}
