namespace Clabe.Web.Components;

/// <summary>One command of a menu (also listed in the command palette). Unavailable commands stay listed, disabled.</summary>
/// <param name="Label">What the item says.</param>
/// <param name="Shortcut">Its key, as shown (e.g. <c>Alt+←</c>, <c>Ctrl+K</c>).</param>
/// <param name="Action">What it does; null for a submenu or a separator.</param>
/// <param name="Enabled">Whether it can be used now.</param>
/// <param name="Icon">A <see cref="RunIcon"/> name.</param>
/// <param name="Checked">For a choice (Theme ▸ Dark): whether it is the current one.</param>
/// <param name="Children">A submenu.</param>
public sealed record MenuItem(
    string Label,
    string? Shortcut = null,
    Func<Task>? Action = null,
    bool Enabled = true,
    string? Icon = null,
    bool? Checked = null,
    IReadOnlyList<MenuItem>? Children = null)
{
    /// <summary>A line between groups of items.</summary>
    public static readonly MenuItem Separator = new("—", Enabled: false);

    public bool IsSeparator => ReferenceEquals(this, Separator);

    /// <summary>Whether keys and the mouse can land on it.</summary>
    public bool Selectable => !IsSeparator && Enabled;
}

/// <summary>One menu of the menu bar.</summary>
public sealed record Menu(string Label, IReadOnlyList<MenuItem> Items);
