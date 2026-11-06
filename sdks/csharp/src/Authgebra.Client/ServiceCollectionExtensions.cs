using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace Authgebra;

public static class ServiceCollectionExtensions
{
    /// <summary>
    /// 注册单例 <see cref="AuthgebraClient"/>，底层 HttpClient 由 IHttpClientFactory 提供
    /// （连接池寿命 5 分钟，证书校验用平台默认，SDK 的 per-attempt 超时取代 HttpClient.Timeout）。
    /// </summary>
    public static IServiceCollection AddAuthgebra(this IServiceCollection services, Action<AuthgebraClientOptions> configure)
    {
        ArgumentNullException.ThrowIfNull(configure);
        services.Configure(configure);
        services.AddHttpClient(HttpClientName, client => client.Timeout = Timeout.InfiniteTimeSpan)
            .ConfigurePrimaryHttpMessageHandler(sp => new SocketsHttpHandler
            {
                ConnectTimeout = sp.GetRequiredService<IOptions<AuthgebraClientOptions>>().Value.ConnectTimeout,
                PooledConnectionLifetime = TimeSpan.FromMinutes(5),
            });
        services.AddSingleton(sp =>
        {
            var options = sp.GetRequiredService<IOptions<AuthgebraClientOptions>>().Value;
            options.Logger ??= sp.GetService<ILoggerFactory>()?.CreateLogger("Authgebra");
            return new AuthgebraClient(options, sp.GetRequiredService<IHttpClientFactory>().CreateClient(HttpClientName));
        });
        return services;
    }

    public const string HttpClientName = "Authgebra";
}
