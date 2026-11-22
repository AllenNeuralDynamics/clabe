using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Rendering;

namespace Clabe.Web.Components;

/// <summary>
/// Fuzzy subsequence matching (<c>m7</c> matches <c>mouse-7</c>), scored so contiguous runs, word starts and early
/// matches rank first, and the highlight of what matched.
/// </summary>
public static class ComboboxMatch
{
    /// <summary>The match of <paramref name="query"/> in <paramref name="text"/>: its score (higher is better) and the
    /// matched character positions; null when the query is not a subsequence.</summary>
    public static (int Score, int[] Positions)? Match(string text, string query)
    {
        if (string.IsNullOrEmpty(query))
        {
            return (0, []);
        }
        // the contiguous match, when there is one, is the best
        var index = text.IndexOf(query, StringComparison.OrdinalIgnoreCase);
        if (index >= 0)
        {
            var bonus = index == 0 ? 60 : IsWordStart(text, index) ? 40 : 20;
            return (1000 + bonus - index - (text.Length - query.Length), Enumerable.Range(index, query.Length).ToArray());
        }
        var positions = new int[query.Length];
        var score = 0;
        var at = 0;
        var previous = -2;
        for (var q = 0; q < query.Length; q++)
        {
            var found = -1;
            for (var i = at; i < text.Length; i++)
            {
                if (char.ToLowerInvariant(text[i]) == char.ToLowerInvariant(query[q]))
                {
                    found = i;
                    break;
                }
            }
            if (found < 0)
            {
                return null;
            }
            score += found == previous + 1 ? 15 : 0;
            score += IsWordStart(text, found) ? 10 : 0;
            score -= found - at;
            positions[q] = found;
            previous = found;
            at = found + 1;
        }
        return (score - text.Length / 4, positions);
    }

    private static bool IsWordStart(string text, int i) =>
        i == 0 || !char.IsLetterOrDigit(text[i - 1]) || (char.IsUpper(text[i]) && char.IsLower(text[i - 1]));

    /// <summary><paramref name="text"/> with the matched positions in &lt;mark&gt;.</summary>
    public static RenderFragment Highlight(string text, int[] positions) => builder =>
    {
        if (positions.Length == 0)
        {
            builder.AddContent(0, text);
            return;
        }
        var marked = positions.ToHashSet();
        var seq = 0;
        var i = 0;
        while (i < text.Length)
        {
            var hit = marked.Contains(i);
            var j = i;
            while (j < text.Length && marked.Contains(j) == hit)
            {
                j++;
            }
            if (hit)
            {
                builder.OpenElement(seq++, "mark");
                builder.AddContent(seq++, text[i..j]);
                builder.CloseElement();
            }
            else
            {
                builder.AddContent(seq++, text[i..j]);
            }
            i = j;
        }
    };

    /// <summary>The items matching <paramref name="query"/>, best first (input order when the query is empty).</summary>
    public static List<(T Item, int[] Positions)> Filter<T>(IEnumerable<T> items, Func<T, string> text, string query)
    {
        var results = new List<(T Item, int Score, int Order, int[] Positions)>();
        var order = 0;
        foreach (var item in items)
        {
            if (Match(text(item), query.Trim()) is { } match)
            {
                results.Add((item, match.Score, order, match.Positions));
            }
            order++;
        }
        return results
            .OrderByDescending(r => string.IsNullOrWhiteSpace(query) ? 0 : r.Score)
            .ThenBy(r => r.Order)
            .Select(r => (r.Item, r.Positions))
            .ToList();
    }
}
