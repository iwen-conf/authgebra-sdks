using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Text.Json;
using Xunit;

namespace Authgebra.Tests;

/// <summary>跨语言一致性夹具：sdks/fixtures/cases/*.json 的每一个用例都必须在这里通过。</summary>
public class FixtureTests
{
    public static IEnumerable<object[]> Cases() =>
        Directory.GetFiles(Rig.RepoFile("fixtures/cases"), "*.json").OrderBy(p => p, StringComparer.Ordinal)
            .Select(p => new object[] { Path.GetFileNameWithoutExtension(p) });

    [Fact]
    public void FixtureDirectoryHasAllExpectedCases() =>
        Assert.True(Directory.GetFiles(Rig.RepoFile("fixtures/cases"), "*.json").Length >= 25);

    [Theory]
    [MemberData(nameof(Cases))]
    public async Task Case(string id)
    {
        using var doc = JsonDocument.Parse(await File.ReadAllTextAsync(Path.Combine(Rig.RepoFile("fixtures/cases"), id + ".json")));
        var fx = doc.RootElement;
        var expect = fx.GetProperty("expect");
        var calls = 0;

        var handler = new FakeHandler((n, req, ct) =>
        {
            calls = n;
            if (fx.TryGetProperty("fault", out var fault))
            {
                return fault.GetString() switch
                {
                    "timeout" => throw new TaskCanceledException("simulated timeout", new TimeoutException()),
                    "reset" => throw new HttpRequestException("simulated reset", new IOException("connection reset by peer", new SocketException(104))),
                    var other => throw new InvalidOperationException($"unknown fault {other}"),
                };
            }
            var response = fx.GetProperty("response");
            var msg = new HttpResponseMessage((System.Net.HttpStatusCode)response.GetProperty("status").GetInt32());
            var bytes = response.TryGetProperty("body_raw", out var raw)
                ? Encoding.UTF8.GetBytes(raw.GetString()!)
                : Encoding.UTF8.GetBytes(response.GetProperty("body").GetRawText());
            msg.Content = new ByteArrayContent(bytes);
            foreach (var h in response.GetProperty("headers").EnumerateObject())
            {
                if (h.Name.Equals("content-type", StringComparison.OrdinalIgnoreCase))
                {
                    msg.Content.Headers.TryAddWithoutValidation("Content-Type", h.Value.GetString());
                }
                else
                {
                    msg.Headers.TryAddWithoutValidation(h.Name, h.Value.GetString());
                }
            }
            return Task.FromResult(msg);
        });
        using var client = Rig.Client(handler);

        switch (expect.GetProperty("kind").GetString())
        {
            case "ok":
                var ok = await Invoke(client, fx);
                AssertOk(ok, expect, handler);
                break;
            case "api_error":
                var api = await Assert.ThrowsAnyAsync<ApiException>(() => Invoke(client, fx));
                AssertApiError(api, expect);
                break;
            case "transport_error":
                var transport = await Assert.ThrowsAsync<TransportException>(() => Invoke(client, fx));
                Assert.Equal(expect.GetProperty("error").GetString(), transport.Code);
                Assert.Equal(CommitOf(expect), transport.CommitState);
                if (expect.TryGetProperty("auto_resent", out var resent))
                {
                    Assert.Equal(resent.GetBoolean(), calls > 1);
                }
                break;
            case "protocol_error":
                var protocol = await Assert.ThrowsAsync<ProtocolException>(() => Invoke(client, fx));
                Assert.Equal(expect.GetProperty("error").GetString(), protocol.Reason);
                Assert.Equal(expect.GetProperty("status").GetInt32(), protocol.Status);
                break;
            default:
                throw new InvalidOperationException("unknown expect.kind");
        }
    }

    private static CommitStateKind? CommitOf(JsonElement expect) =>
        expect.TryGetProperty("commit_state", out var c) && c.ValueKind == JsonValueKind.String
            ? c.GetString() switch
            {
                "committed_unpublished" => CommitStateKind.CommittedUnpublished,
                "unknown" => CommitStateKind.Unknown,
                var other => throw new InvalidOperationException($"unmapped commit_state {other}"),
            }
            : null;

    private static async Task<IApiResult> Invoke(AuthgebraClient client, JsonElement fx)
    {
        var request = fx.GetProperty("request");
        string? idem = request.TryGetProperty("idempotency_key", out var k) && k.ValueKind == JsonValueKind.String ? k.GetString() : null;
        string username = request.TryGetProperty("body", out var b) && b.TryGetProperty("username", out var u) ? u.GetString()! : "alice";
        switch (fx.GetProperty("operation").GetString())
        {
            case "authz.check":
                return await client.Authorization.CheckAsync(Rig.Check());
            case "authz.list_resources":
                return await client.Authorization.ListResourcesAsync(new ListResourcesRequest
                {
                    SubjectId = Rig.Subject, Permission = "document:read", ResourceType = "document",
                });
            case "users.create" when fx.GetProperty("expect").TryGetProperty("request_body_utf8_exact", out _):
                // This shared wire fixture intentionally contains only a username fragment.
                // Test its exact transport payload without treating it as a complete UserDto.
                var specType = typeof(AuthgebraClient).Assembly.GetType("Authgebra.RequestSpec", throwOnError: true)!;
                var spec = specType.GetMethod("Create")!.Invoke(null, new object[] { "POST", "/api/v1/users", true })!;
                specType.GetMethod("WithBody")!.Invoke(spec, new object[] { request.GetProperty("body").Clone() });
                specType.GetMethod("WithIdempotencyKey")!.Invoke(spec, new object?[] { idem });
                var send = typeof(AuthgebraClient).GetMethod("SendAsync", BindingFlags.Instance | BindingFlags.NonPublic)!
                    .MakeGenericMethod(typeof(JsonElement));
                return await (Task<ApiResult<JsonElement>>)send.Invoke(client, new object[] { spec, new RequestOptions(), CancellationToken.None })!;
            case "users.create":
                return await client.Admin.Users.CreateUserAsync(
                    new CreateUserRequest { Username = username, DisplayName = "Alice", InitialPassword = "pw" }, idempotencyKey: idem);
            case "users.update":
                return await client.Admin.Users.PatchUserAsync(Rig.Subject, new PatchUserRequest(), "W/\"1\"");
            case "users.list":
                return await client.Admin.Users.ListUsersAsync();
            case "auth.change_password":
                return await client.Admin.Auth.ChangePasswordAsync(new ChangePasswordRequest { NewPassword = "new", Reauth = new ReauthProof { Password = "old" } });
            default:
                throw new InvalidOperationException("unmapped fixture operation " + fx.GetProperty("operation").GetString());
        }
    }

    private static void AssertOk(IApiResult result, JsonElement expect, FakeHandler handler)
    {
        if (expect.TryGetProperty("decision", out var d))
        {
            Assert.Equal(d.GetString(), ((CheckResponse)result.DataObject!).Decision);
        }
        if (expect.TryGetProperty("authz_revision", out var rev))
        {
            Assert.Equal(Text(rev), result.Meta?.AuthzRevision.ToString());
        }
        if (expect.TryGetProperty("snapshot_generation", out var gen))
        {
            Assert.Equal(Text(gen), result.Meta?.SnapshotGeneration?.ToString());
        }
        if (expect.TryGetProperty("trace_id", out var t))
        {
            Assert.Equal(t.GetString(), result.TraceId);
        }
        if (expect.TryGetProperty("request_id", out var r))
        {
            Assert.Equal(r.GetString(), result.RequestId);
        }
        if (expect.TryGetProperty("has_more", out var hm))
        {
            Assert.Equal(hm.GetBoolean(), result.Page!.HasMore);
        }
        if (expect.TryGetProperty("echo_username", out var echo))
        {
            Assert.Equal(echo.GetString(), result.DataObject is JsonElement raw
                ? raw.GetProperty("username").GetString() : ((UserDto)result.DataObject!).Username);
        }
        if (expect.TryGetProperty("request_body_utf8_exact", out _))
        {
            var sent = handler.Requests.Single().Body;
            var text = new UTF8Encoding(false, throwOnInvalidBytes: true).GetString(sent);
            Assert.Contains("用户-é-", text, StringComparison.Ordinal); // BMP 字符不得被转义成 \uXXXX
            using var parsed = JsonDocument.Parse(sent);
            Assert.Equal("用户-é-😀", parsed.RootElement.GetProperty("username").GetString());
        }
    }

    private static string? Text(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.Null => null,
        JsonValueKind.String => e.GetString(),
        _ => e.GetRawText(),
    };

    private static void AssertApiError(ApiException api, JsonElement expect)
    {
        Assert.Equal(expect.GetProperty("status").GetInt32(), api.Status);
        Assert.Equal(expect.GetProperty("code").GetString(), api.Code);
        Assert.Equal(expect.GetProperty("known_code").GetBoolean(), api.KnownCode);
        Assert.Equal(Text(expect.GetProperty("request_id")), api.RequestId);
        Assert.Equal(Text(expect.GetProperty("trace_id")), api.TraceId);
        Assert.Equal(expect.GetProperty("retryable").ValueKind == JsonValueKind.Null ? (bool?)null : expect.GetProperty("retryable").GetBoolean(), api.Retryable);
        Assert.Equal(expect.GetProperty("retry_after_seconds").ValueKind == JsonValueKind.Null ? (long?)null : expect.GetProperty("retry_after_seconds").GetInt64(), api.RetryAfterSeconds);
        Assert.Equal(CommitOf(expect), api.CommitState);
        if (expect.GetProperty("code").GetString() == "CURSOR_STALE")
        {
            Assert.IsType<CursorStaleException>(api);
        }
        if (expect.TryGetProperty("violations", out var vs))
        {
            var expected = vs.EnumerateArray().Select(v => (Path: Text(v.GetProperty("path")), Reason: v.GetProperty("reason").GetString())).ToList();
            Assert.Equal(expected, api.Violations.Select(v => (v.Path, (string?)v.Reason)).ToList());
        }
        if (expect.TryGetProperty("body_request_id", out var br))
        {
            Assert.Equal(br.GetString(), api.BodyRequestId);
        }
        if (expect.TryGetProperty("request_id_mismatch", out var mm))
        {
            Assert.Equal(mm.GetBoolean(), api.RequestIdMismatch);
        }
        if (expect.TryGetProperty("request_id_header_valid", out var hv))
        {
            Assert.Equal(hv.GetBoolean(), api.RequestIdHeaderValid);
        }
    }
}
