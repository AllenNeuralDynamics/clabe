namespace Clabe.Web.Services;

/// <summary>The rig account an operator entered to restart a daemon over SSH: kept in this tab's memory only (never
/// stored in the browser, never by the hub), so a second restart in the same tab does not ask again.</summary>
public sealed class RigCredentials
{
    public string? Username { get; set; }
    public string? Password { get; set; }

    public bool Known => !string.IsNullOrEmpty(Username) && !string.IsNullOrEmpty(Password);

    public void Forget() => (Username, Password) = (null, null);
}
