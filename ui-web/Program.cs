using Clabe.Web;
using Clabe.Web.Services;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.AspNetCore.Components.WebAssembly.Hosting;
using Microsoft.JSInterop;

var builder = WebAssemblyHostBuilder.CreateDefault(args);
builder.RootComponents.Add<App>("#app");
builder.RootComponents.Add<HeadOutlet>("head::after");

// The UI is served by the run's server (or a hub, under a prefix): the API lives next to the page.
builder.Services.AddScoped(_ => new HttpClient { BaseAddress = new Uri(builder.HostEnvironment.BaseAddress) });
builder.Services.AddScoped<RunApi>();
builder.Services.AddScoped<RunConnection>();
builder.Services.AddScoped<DaemonApi>();
builder.Services.AddScoped<HubApi>();
builder.Services.AddScoped<RigCredentials>();

var host = builder.Build();
Clabe.Web.Components.ReportTime.Init((IJSInProcessRuntime)host.Services.GetRequiredService<IJSRuntime>());
await host.RunAsync();
