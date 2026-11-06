using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using Authgebra;
using Microsoft.Extensions.Logging;
using Xunit;

namespace Authgebra.Tests;

public class TlsTests
{
    public sealed class TlsFactAttribute : FactAttribute
    {
        public TlsFactAttribute()
        {
            if (Environment.GetEnvironmentVariable("SDK_TLS_VALID_URL") is null)
                Skip = "NOT_RUN: explicit SDK_TLS fixture required";
        }
    }

    private static string Env(string name) => Environment.GetEnvironmentVariable(name)
        ?? throw new InvalidOperationException("Missing " + name);

    [TlsFact]
    public async Task RealCertificateHostnameAndHandshakeMatrix()
    {
        const string token = "tls-secret-token-must-not-leak";
        using var ca = X509Certificate2.CreateFromPem(File.ReadAllText(Env("SDK_TLS_CA")));
        foreach (var scenario in new[]
        {
            (Name: "trusted_ca", Url: Env("SDK_TLS_VALID_URL"), Trusted: true, Fail: false),
            (Name: "untrusted_ca", Url: Env("SDK_TLS_VALID_URL"), Trusted: false, Fail: true),
            (Name: "hostname_mismatch", Url: Env("SDK_TLS_MISMATCH_URL"), Trusted: true, Fail: true),
            (Name: "invalid_handshake", Url: Env("SDK_TLS_PLAIN_URL"), Trusted: true, Fail: true),
        })
        {
            using var handler = new HttpClientHandler();
            if (scenario.Trusted)
            {
                handler.ServerCertificateCustomValidationCallback = (_, certificate, _, errors) =>
                {
                    if (certificate is null || (errors & ~SslPolicyErrors.RemoteCertificateChainErrors) != 0)
                        return false;
                    using var chain = new X509Chain();
                    chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
                    chain.ChainPolicy.CustomTrustStore.Add(ca);
                    chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
                    return chain.Build(certificate);
                };
            }
            using var counter = new CountHandler(handler);
            using var http = new HttpClient(counter);
            var logs = new CaptureLogger();
            using var client = new AuthgebraClient(new AuthgebraClientOptions
            {
                BaseUrl = scenario.Url,
                TokenProvider = new StaticTokenProvider(token),
                Retry = new RetryPolicy { MaxRetries = 0 },
                RequestTimeout = TimeSpan.FromSeconds(5),
                Logger = logs,
            }, http);
            if (scenario.Fail)
            {
                var error = await Assert.ThrowsAsync<TransportException>(() => client.Authorization.RevisionAsync());
                Assert.Equal(TransportErrorKind.Tls, error.Kind);
                Assert.DoesNotContain(token, error.ToString(), StringComparison.Ordinal);
            }
            else
            {
                var result = await client.Authorization.RevisionAsync();
                Assert.Equal(1, result.Data.DbRevision);
                Assert.Equal("in_sync", result.Data.Status);
            }
            Assert.Equal(1, counter.Attempts);
            Assert.DoesNotContain(token, string.Join("\n", logs.Messages), StringComparison.Ordinal);
        }
    }

    private sealed class CountHandler(HttpMessageHandler inner) : DelegatingHandler(inner)
    {
        public int Attempts { get; private set; }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Attempts++;
            return base.SendAsync(request, cancellationToken);
        }
    }

    private sealed class CaptureLogger : ILogger
    {
        public List<string> Messages { get; } = new();
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
        public bool IsEnabled(LogLevel level) => true;
        public void Log<TState>(LogLevel level, EventId id, TState state, Exception? exception,
            Func<TState, Exception?, string> formatter) => Messages.Add(formatter(state, exception));
    }
}
