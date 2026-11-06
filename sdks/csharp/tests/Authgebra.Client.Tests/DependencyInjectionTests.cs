using Microsoft.Extensions.DependencyInjection;
using Xunit;

namespace Authgebra.Tests;

public class DependencyInjectionTests
{
    [Fact]
    public void AddAuthgebraRegistersASingletonClientBackedByTheHttpClientFactory()
    {
        var services = new ServiceCollection();
        services.AddLogging();
        services.AddAuthgebra(o =>
        {
            o.BaseUrl = "https://authgebra.example.com";
            o.TokenProvider = new StaticTokenProvider("t");
        });
        using var provider = services.BuildServiceProvider();
        var a = provider.GetRequiredService<AuthgebraClient>();
        var b = provider.GetRequiredService<AuthgebraClient>();
        Assert.Same(a, b);
        Assert.NotNull(a.Authorization);
        Assert.NotNull(a.Admin.Users);
    }
}
