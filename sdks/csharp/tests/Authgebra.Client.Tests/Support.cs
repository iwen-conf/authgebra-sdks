using System.Net;
using System.Text;
using System.Text.Json;

namespace Authgebra.Tests;

internal sealed record Recorded(HttpMethod Method, Uri Uri, Dictionary<string, string> Headers, byte[] Body);

internal sealed class FakeHandler : HttpMessageHandler
{
    private readonly Func<int, HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> _respond;

    public FakeHandler(Func<int, HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> respond) => _respond = respond;

    public List<Recorded> Requests { get; } = new();

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var h in request.Headers)
        {
            headers[h.Key] = string.Join(",", h.Value);
        }
        var body = request.Content is null ? Array.Empty<byte>() : await request.Content.ReadAsByteArrayAsync(cancellationToken);
        Requests.Add(new Recorded(request.Method, request.RequestUri!, headers, body));
        return await _respond(Requests.Count, request, cancellationToken);
    }

    public static HttpResponseMessage Json(int status, string body, params (string Name, string Value)[] headers)
    {
        var response = new HttpResponseMessage((HttpStatusCode)status)
        {
            Content = new ByteArrayContent(Encoding.UTF8.GetBytes(body)),
        };
        response.Content.Headers.ContentType = new("application/json") { CharSet = "utf-8" };
        foreach (var (name, value) in headers)
        {
            response.Headers.TryAddWithoutValidation(name, value);
        }
        return response;
    }
}

internal static class Rig
{
    public const string Token = "tok-super-secret-0123456789";
    public static readonly Guid Subject = Guid.Parse("11111111-1111-4111-8111-111111111111");

    public static AuthgebraClient Client(FakeHandler handler, Action<AuthgebraClientOptions>? tweak = null)
    {
        var options = new AuthgebraClientOptions
        {
            BaseUrl = "https://authgebra.test",
            TokenProvider = new StaticTokenProvider(Token),
            Retry = new RetryPolicy { MaxRetries = 0, Delay = (_, _) => Task.CompletedTask },
        };
        tweak?.Invoke(options);
        return new AuthgebraClient(options, new HttpClient(handler, disposeHandler: false));
    }

    public static CheckRequest Check() => new() { SubjectId = Subject, Permission = "document:read" };

    public const string UserData = """{"id":"11111111-1111-4111-8111-111111111111","username":"alice","display_name":"Alice","status":"active","auth_version":1,"version":1,"etag":"v1","created_at_rfc3339":"2026-11-06T00:00:00Z","mfa_enabled":false,"roles_count":0,"sessions_count":0}""";

    public const string DenyBody = """{"data":{"decision":"deny","reason_code":"default_deny"},"meta":{"authz_revision":7,"snapshot_generation":7}}""";

    public static string RepoFile(string relative)
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "sdks", relative);
            if (File.Exists(candidate) || Directory.Exists(candidate))
            {
                return candidate;
            }
        }
        throw new DirectoryNotFoundException($"cannot locate sdks/{relative} above {AppContext.BaseDirectory}");
    }
}
