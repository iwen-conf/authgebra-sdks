using System.Text;
using System.Text.Json;
using Xunit;

namespace Authgebra.Tests;

public class BehaviorTests
{
    [Fact]
    public async Task InvalidCallerRequestIdIsDiscardedBeforeSending()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, Rig.DenyBody)));
        using var client = Rig.Client(handler);
        await client.Authorization.CheckAsync(Rig.Check(), new RequestOptions { RequestId = "invalid\r\nHeader: injected" });
        var sent = Assert.Single(handler.Requests).Headers["X-Request-ID"];
        Assert.True(TraceContext.IsValidRequestId(sent));
    }

    private static string Err(string code, string extra = "") =>
        "{\"error\":{\"code\":\"" + code + "\",\"message\":\"m\",\"request_id\":\"req-abcdefgh\"" + extra + "}}";

    // ---- 请求体语义：不自动补默认值 --------------------------------------------------------

    [Fact]
    public async Task UnsetOptionalFieldsAreOmittedAndExplicitNullIsPreserved()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, Rig.DenyBody)));
        using var client = Rig.Client(handler);

        await client.Authorization.CheckAsync(Rig.Check());
        await client.Authorization.CheckAsync(new CheckRequest
        {
            SubjectId = Rig.Subject, Permission = "document:read", Context = Optional<JsonElement>.Null,
        });

        using var first = JsonDocument.Parse(handler.Requests[0].Body);
        Assert.False(first.RootElement.TryGetProperty("context", out _), "未设置的字段必须省略，不能写成 null");
        Assert.False(first.RootElement.TryGetProperty("resource", out _));
        using var second = JsonDocument.Parse(handler.Requests[1].Body);
        Assert.Equal(JsonValueKind.Null, second.RootElement.GetProperty("context").ValueKind);
    }

    [Fact]
    public async Task ExplicitNullWorksForValueTypesToo()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, """{"data":[],"page":{"next_cursor":null,"has_more":false}}""")));
        using var client = Rig.Client(handler);
        await client.Authorization.ListResourcesAsync(new ListResourcesRequest
        {
            SubjectId = Rig.Subject, Permission = "p", ResourceType = "r",
            Limit = Optional<int>.Null, Cursor = Optional<string>.Unset,
        });
        using var doc = JsonDocument.Parse(handler.Requests[0].Body);
        Assert.Equal(JsonValueKind.Null, doc.RootElement.GetProperty("limit").ValueKind);
        Assert.False(doc.RootElement.TryGetProperty("cursor", out _));
    }

    [Fact]
    public async Task SubjectIdIsSerializedAsAUuidString()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, Rig.DenyBody)));
        using var client = Rig.Client(handler);
        await client.Authorization.CheckAsync(Rig.Check());
        using var doc = JsonDocument.Parse(handler.Requests[0].Body);
        Assert.Equal("11111111-1111-4111-8111-111111111111", doc.RootElement.GetProperty("subject_id").GetString());
    }

    // ---- 重试矩阵 ----------------------------------------------------------------------------

    [Fact]
    public async Task ReadsRetryBoundedlyOnRateLimitAndHonourRetryAfterWithACap()
    {
        var delays = new List<TimeSpan>();
        var handler = new FakeHandler((n, _, _) => Task.FromResult(n < 3
            ? FakeHandler.Json(429, Err("RATE_LIMITED", ",\"retry_after_seconds\":3"), ("Retry-After", "3"))
            : FakeHandler.Json(200, Rig.DenyBody)));
        using var client = Rig.Client(handler, o => o.Retry = new RetryPolicy
        {
            MaxRetries = 2, Delay = (d, _) => { delays.Add(d); return Task.CompletedTask; },
        });
        var result = await client.Authorization.CheckAsync(Rig.Check());
        Assert.Equal("deny", result.Data.Decision);
        Assert.Equal(3, handler.Requests.Count);
        Assert.Equal(new[] { TimeSpan.FromSeconds(3), TimeSpan.FromSeconds(3) }, delays);
    }

    [Fact]
    public async Task RetryAfterIsCapped()
    {
        var delays = new List<TimeSpan>();
        var handler = new FakeHandler((n, _, _) => Task.FromResult(n < 2
            ? FakeHandler.Json(429, Err("RATE_LIMITED", ",\"retry_after_seconds\":86400"))
            : FakeHandler.Json(200, Rig.DenyBody)));
        using var client = Rig.Client(handler, o => o.Retry = new RetryPolicy
        {
            MaxRetries = 1, MaxRetryAfter = TimeSpan.FromSeconds(10), Delay = (d, _) => { delays.Add(d); return Task.CompletedTask; },
        });
        await client.Authorization.CheckAsync(Rig.Check());
        Assert.Equal(TimeSpan.FromSeconds(10), Assert.Single(delays));
    }

    [Fact]
    public async Task RetriesAreNeverUnbounded()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(503, Err("NOT_READY"))));
        using var client = Rig.Client(handler, o => o.Retry = new RetryPolicy { MaxRetries = 2, Delay = (_, _) => Task.CompletedTask });
        await Assert.ThrowsAsync<ApiException>(() => client.Authorization.CheckAsync(Rig.Check()));
        Assert.Equal(3, handler.Requests.Count);
        Assert.Throws<ArgumentOutOfRangeException>(() => new RetryPolicy { MaxRetries = RetryPolicy.HardMaxRetries + 1 });
    }

    [Theory]
    [InlineData("FORBIDDEN", 403)]
    [InlineData("CURSOR_STALE", 409)]
    [InlineData("INTERNAL_ERROR", 500)]
    public async Task NonRetryableErrorsAreSentOnce(string code, int status)
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(status, Err(code))));
        using var client = Rig.Client(handler, o => o.Retry = new RetryPolicy { MaxRetries = 5, Delay = (_, _) => Task.CompletedTask });
        await Assert.ThrowsAnyAsync<ApiException>(() => client.Authorization.CheckAsync(Rig.Check()));
        Assert.Single(handler.Requests);
    }

    private static CreateUserRequest NewUser() => new() { Username = "alice", DisplayName = "A", InitialPassword = "pw" };

    [Fact]
    public async Task WritesAreNotResentByDefaultEvenForNotReady()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(503, Err("NOT_READY"))));
        using var client = Rig.Client(handler, o => o.Retry = new RetryPolicy { MaxRetries = 3, Delay = (_, _) => Task.CompletedTask });
        await Assert.ThrowsAsync<ApiException>(() => client.Admin.Users.CreateUserAsync(NewUser(), idempotencyKey: "idem-key-1"));
        Assert.Single(handler.Requests);
    }

    [Fact]
    public async Task OptedInWriteRetriesNeedTheSameIdempotencyKeyAndIdenticalBytes()
    {
        var handler = new FakeHandler((n, _, _) => Task.FromResult(n < 2
            ? FakeHandler.Json(503, Err("NOT_READY"))
            : FakeHandler.Json(201, "{\"data\":" + Rig.UserData + "}")));
        using var client = Rig.Client(handler, o =>
        {
            o.RetryWrites = true;
            o.Retry = new RetryPolicy { MaxRetries = 2, Delay = (_, _) => Task.CompletedTask };
        });
        await client.Admin.Users.CreateUserAsync(NewUser(), idempotencyKey: "idem-key-1");
        Assert.Equal(2, handler.Requests.Count);
        Assert.Equal("idem-key-1", handler.Requests[1].Headers["Idempotency-Key"]);
        Assert.Equal(handler.Requests[0].Body, handler.Requests[1].Body);
    }

    [Fact]
    public async Task OptedInWritesWithoutAnIdempotencyKeyAreStillNotResent()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(503, Err("NOT_READY"))));
        using var client = Rig.Client(handler, o =>
        {
            o.RetryWrites = true;
            o.Retry = new RetryPolicy { MaxRetries = 3, Delay = (_, _) => Task.CompletedTask };
        });
        await Assert.ThrowsAsync<ApiException>(() => client.Admin.Users.CreateUserAsync(NewUser()));
        Assert.Single(handler.Requests);
    }

    [Theory]
    [InlineData("AUTHZ_RELOAD_PENDING")]
    [InlineData("IDENTITY_RELOAD_PENDING")]
    public async Task CommittedButUnpublishedIsNeverResentEvenWhenWriteRetriesAreOn(string code)
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(
            FakeHandler.Json(503, Err(code, ",\"retryable\":false,\"commit_state\":\"committed_unpublished\""))));
        using var client = Rig.Client(handler, o =>
        {
            o.RetryWrites = true;
            o.Retry = new RetryPolicy { MaxRetries = 5, Delay = (_, _) => Task.CompletedTask };
        });
        var ex = await Assert.ThrowsAsync<ApiException>(() => client.Admin.Users.CreateUserAsync(NewUser(), idempotencyKey: "idem-key-1"));
        Assert.Single(handler.Requests);
        Assert.Equal(CommitStateKind.CommittedUnpublished, ex.CommitState);
        Assert.False(ex.Retryable);
    }

    [Fact]
    public async Task WriteTimeoutIsUnknownCommitStateAndReadTimeoutIsNot()
    {
        var handler = new FakeHandler((_, _, _) => throw new TaskCanceledException("timeout", new TimeoutException()));
        using var client = Rig.Client(handler);
        var write = await Assert.ThrowsAsync<TransportException>(() => client.Admin.Users.CreateUserAsync(NewUser()));
        Assert.Equal(CommitStateKind.Unknown, write.CommitState);
        var read = await Assert.ThrowsAsync<TransportException>(() => client.Authorization.CheckAsync(Rig.Check()));
        Assert.Null(read.CommitState);
    }

    [Fact]
    public async Task CallerCancellationPropagatesUnwrapped()
    {
        var handler = new FakeHandler(async (_, _, ct) => { await Task.Delay(Timeout.Infinite, ct); return null!; });
        using var client = Rig.Client(handler);
        using var cts = new CancellationTokenSource(50);
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => client.Authorization.CheckAsync(Rig.Check(), cancellationToken: cts.Token));
    }

    [Fact]
    public async Task PerAttemptTimeoutBecomesATransportTimeout()
    {
        var handler = new FakeHandler(async (_, _, ct) => { await Task.Delay(Timeout.Infinite, ct); return null!; });
        using var client = Rig.Client(handler, o => o.RequestTimeout = TimeSpan.FromMilliseconds(50));
        var ex = await Assert.ThrowsAsync<TransportException>(() => client.Authorization.CheckAsync(Rig.Check()));
        Assert.Equal("timeout", ex.Code);
    }

    // ---- 追踪 --------------------------------------------------------------------------------

    [Fact]
    public async Task EveryRequestCarriesAValidRequestIdAndTraceparent()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, Rig.DenyBody)));
        using var client = Rig.Client(handler);
        await client.Authorization.CheckAsync(Rig.Check());
        var h = handler.Requests[0].Headers;
        Assert.True(TraceContext.IsValidRequestId(h["X-Request-ID"]));
        Assert.True(TraceContext.IsValidTraceparent(h["traceparent"]));
        Assert.StartsWith("authgebra-csharp/", h["User-Agent"]);
    }

    [Fact]
    public async Task CallerSuppliedTraceparentIsForwardedAndInvalidOnesAreRejectedLocally()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, Rig.DenyBody)));
        using var client = Rig.Client(handler);
        const string tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
        await client.Authorization.CheckAsync(Rig.Check(), new RequestOptions { TraceParent = tp, RequestId = "caller-req-0001", TraceState = "vendor=1" });
        Assert.Equal(tp, handler.Requests[0].Headers["traceparent"]);
        Assert.Equal("caller-req-0001", handler.Requests[0].Headers["X-Request-ID"]);
        Assert.Equal("vendor=1", handler.Requests[0].Headers["tracestate"]);

        foreach (var bad in new[] { "garbage", tp.ToUpperInvariant(), "00-00000000000000000000000000000000-00f067aa0ba902b7-01", "ff-" + tp[3..] })
        {
            await Assert.ThrowsAsync<ArgumentException>(() =>
                client.Authorization.CheckAsync(Rig.Check(), new RequestOptions { TraceParent = bad }));
        }
        await client.Authorization.CheckAsync(Rig.Check(), new RequestOptions { RequestId = "short" });
        Assert.Equal(2, handler.Requests.Count);
        Assert.True(TraceContext.IsValidRequestId(handler.Requests[1].Headers["X-Request-ID"]));
    }

    [Fact]
    public void GeneratedTraceparentsAreValidAndNeverAllZero()
    {
        for (var i = 0; i < 200; i++)
        {
            Assert.True(TraceContext.IsValidTraceparent(TraceContext.NewTraceparent()));
        }
    }

    [Fact]
    public async Task ServerReportedIdsAreNotImpersonatedByTheSentOnes()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(403, """{"error":{"code":"FORBIDDEN","message":"m"}}""")));
        using var client = Rig.Client(handler);
        var ex = await Assert.ThrowsAsync<ApiException>(() => client.Authorization.CheckAsync(Rig.Check()));
        Assert.Null(ex.RequestId);
        Assert.Null(ex.TraceId);
        Assert.NotNull(ex.SentRequestId);
        Assert.True(TraceContext.IsValidTraceparent(ex.SentTraceParent));
    }

    // ---- 安全默认 ----------------------------------------------------------------------------

    [Fact]
    public async Task TokensNeverAppearInExceptionsOrToStringOutput()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(401, Err("TOKEN_EXPIRED"))));
        using var client = Rig.Client(handler);
        var ex = await Assert.ThrowsAsync<ApiException>(() => client.Authorization.CheckAsync(Rig.Check()));
        Assert.DoesNotContain(Rig.Token, ex.ToString());
        Assert.DoesNotContain(Rig.Token, new StaticTokenProvider(Rig.Token).ToString());
        Assert.Equal("Bearer " + Rig.Token, handler.Requests[0].Headers["Authorization"]);
    }

    [Fact]
    public void PlainHttpIsRejectedUnlessExplicitlyAllowed()
    {
        Assert.Throws<ArgumentException>(() => new AuthgebraClient(new AuthgebraClientOptions { BaseUrl = "http://localhost:8443" }));
        using var ok = new AuthgebraClient(new AuthgebraClientOptions { BaseUrl = "http://localhost:8443", AllowInsecureHttp = true });
        Assert.Throws<ArgumentException>(() => new AuthgebraClient(new AuthgebraClientOptions { BaseUrl = "not a url" }));
        Assert.Throws<ArgumentException>(() => new AuthgebraClient(new AuthgebraClientOptions { BaseUrl = "https://x.test", RequestTimeout = Timeout.InfiniteTimeSpan }));
    }

    [Fact]
    public void ManagedHeadersCannotBeOverriddenThroughDefaultHeaders()
    {
        var options = new AuthgebraClientOptions { BaseUrl = "https://x.test" };
        options.DefaultHeaders["Authorization"] = "Bearer evil";
        Assert.Throws<ArgumentException>(() => new AuthgebraClient(options));
    }

    [Fact]
    public async Task OversizedResponsesAreRejectedAsProtocolErrors()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, new string('x', 5000))));
        using var client = Rig.Client(handler, o => o.MaxResponseBytes = 1024);
        var ex = await Assert.ThrowsAsync<ProtocolException>(() => client.Authorization.CheckAsync(Rig.Check()));
        Assert.Equal("response_too_large", ex.Reason);
    }

    [Fact]
    public async Task PathValuesAndQueryAreEscaped()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, """{"data":[],"page":{"next_cursor":null,"has_more":false}}""")));
        using var client = Rig.Client(handler);
        await client.Admin.Users.ListUsersAsync(q: "a b&c=d", limit: 5);
        var uri = handler.Requests[0].Uri;
        Assert.Equal("/api/v1/users", uri.AbsolutePath);
        Assert.Equal("?limit=5&q=a%20b%26c%3Dd", uri.Query); // 顺序 = 契约里的参数顺序
    }

    [Fact]
    public async Task AdminReadsExposeNoMetaInsteadOfAFakeZero()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, """{"data":[],"page":{"next_cursor":null,"has_more":false}}""")));
        using var client = Rig.Client(handler);
        var result = await client.Admin.Users.ListUsersAsync();
        Assert.Null(result.Meta);
    }

    [Fact]
    public async Task ResponseHeadersSurfaceEtagRevisionAndReplay()
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(201,
            "{\"data\":" + Rig.UserData + ",\"meta\":{\"authz_revision\":42,\"snapshot_generation\":42}}",
            ("ETag", "W/\"3\""), ("X-Authz-Revision", "42"), ("Idempotency-Replayed", "true"), ("X-Request-ID", "req-abcdefgh"))));
        using var client = Rig.Client(handler);
        var r = await client.Admin.Users.CreateUserAsync(NewUser(), idempotencyKey: "idem-key-1");
        Assert.Equal("W/\"3\"", r.ETag);
        Assert.Equal(42, r.AuthzRevisionHeader);
        Assert.True(r.Replayed);
        Assert.Equal(42, r.Meta!.AuthzRevision);
    }
}
