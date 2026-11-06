using System.Reflection;
using System.Text.Json;
using Authgebra;
using Xunit;

namespace Authgebra.Tests;

/// <summary>Native live tests. Reflection is confined to the untyped negative-input channel;
/// no production API or validation is changed to accommodate malformed inputs.</summary>
public class IntegrationTests
{
    private static string? Base => Environment.GetEnvironmentVariable("AUTHGEBRA_IT_BASE_URL");
    private static string Env(string name) => Environment.GetEnvironmentVariable(name)
        ?? throw new InvalidOperationException("Missing " + name);

    private sealed class Session : IDisposable
    {
        public readonly AuthgebraClient Client;
        public readonly string Prefix = "it-cs-" + Guid.NewGuid().ToString("N")[..12];
        public readonly string Password = Env("AUTHGEBRA_IT_PASSWORD");
        public string Subject = "";
        public Session(string? token = null) => Client = new AuthgebraClient(new AuthgebraClientOptions
        {
            BaseUrl = Base!, AllowInsecureHttp = true, TokenProvider = new StaticTokenProvider(token),
        });
        public void Dispose() => Client.Dispose();
        public async Task<ApiResult<JsonElement>> Call(string method, string path, object? body = null,
            string? key = null, Dictionary<string, string>? query = null, RequestOptions? options = null)
        {
            var specType = typeof(AuthgebraClient).Assembly.GetType("Authgebra.RequestSpec", throwOnError: true)!;
            var spec = specType.GetMethod("Create")!.Invoke(null, new object[] { method, path, true })!;
            if (body is not null) specType.GetMethod("WithBody")!.Invoke(spec, new[] { body });
            if (key is not null) specType.GetMethod("WithIdempotencyKey")!.Invoke(spec, new object[] { key });
            if (path.StartsWith("/api/v1/authz/", StringComparison.Ordinal)) specType.GetMethod("AsRead")!.Invoke(spec, null);
            if (query is not null) foreach (var pair in query)
                specType.GetMethod("WithQuery")!.Invoke(spec, new object[] { pair.Key, pair.Value });
            var send = typeof(AuthgebraClient).GetMethod("SendAsync", BindingFlags.Instance | BindingFlags.NonPublic)!
                .MakeGenericMethod(typeof(JsonElement));
            return await (Task<ApiResult<JsonElement>>)send.Invoke(Client, new object[] {
                spec, options ?? new RequestOptions { RequestId = "it-cs-" + Guid.NewGuid().ToString("N") }, CancellationToken.None })!;
        }
        public async Task<ApiException> Error(string code, int status, string method, string path,
            object? body = null, string? key = null, Dictionary<string, string>? query = null, RequestOptions? options = null)
        {
            var e = await Assert.ThrowsAnyAsync<ApiException>(() => Call(method, path, body, key, query, options));
            Assert.Equal(code, e.Code); Assert.Equal(status, e.Status);
            Assert.False(string.IsNullOrEmpty(e.RequestId)); Assert.False(string.IsNullOrEmpty(e.TraceId));
            return e;
        }
    }
    private static async Task<Session> Login()
    {
        Assert.Equal("1", Env("AUTHGEBRA_IT_CONFIRM_ISOLATED"));
        using var anon = new Session();
        var login = await anon.Call("POST", "/api/v1/auth/login", new { identifier = Env("AUTHGEBRA_IT_USER"), password = anon.Password });
        Assert.Equal("authenticated", login.Data.GetProperty("status").GetString());
        var session = new Session(login.Data.GetProperty("access_token").GetString());
        try
        {
            var me = await session.Client.Admin.Auth.MeAsync();
            session.Subject = me.Data.User.Id.ToString();
            return session;
        }
        catch { session.Dispose(); throw; }
    }
    public sealed class LiveFactAttribute : FactAttribute
    {
        public LiveFactAttribute() { if (Base is null) Skip = "NOT_RUN: AUTHGEBRA_IT_BASE_URL unset"; }
    }
    public sealed class LiveTheoryAttribute : TheoryAttribute
    {
        public LiveTheoryAttribute() { if (Base is null) Skip = "NOT_RUN: AUTHGEBRA_IT_BASE_URL unset"; }
    }
    [LiveFact]
    public async Task RealServerContract()
    {
        using var s = await Login();
        await s.Error("INVALID_CREDENTIALS", 401, "POST", "/api/v1/auth/login", new { identifier = Env("AUTHGEBRA_IT_USER"), password = s.Password + "-wrong" });
        foreach (var pair in new[] { ("user:read", "allow"), ("nope:nope", "deny") })
        {
            var r = await s.Client.Authorization.CheckAsync(new CheckRequest { SubjectId = Guid.Parse(s.Subject), Permission = pair.Item1 });
            Assert.Equal(pair.Item2, r.Data.Decision); Assert.NotNull(r.Meta); Assert.True(r.Meta.AuthzRevision > 0); Assert.True(r.Meta.SnapshotGeneration > 0);
        }
        await s.Call("POST", "/api/v1/resource-types", new { code = s.Prefix, name = s.Prefix });
        var attr = await s.Call("POST", "/api/v1/attributes", new { code = s.Prefix, name = s.Prefix, owner_type = "resource", value_type = "string" });
        var secret = "private-" + Guid.NewGuid().ToString("N");
        var refs = new[] { new { resource_type_code = s.Prefix, external_id = "b" }, new { resource_type_code = s.Prefix, external_id = "a" } };
        foreach (var resource in refs) await s.Call("POST", "/api/v1/resources", new { resource.resource_type_code, resource.external_id, name = resource.external_id,
            attributes = new[] { new { attribute_id = attr.Data.GetProperty("id").GetString(), value = secret } } });
        var missing = new { resource_type_code = s.Prefix, external_id = "missing" };
        var batch = await s.Call("POST", "/api/v1/authz/batch-check", new { subject_id = s.Subject, permission = "user:read", resources = new[] { refs[0], missing, refs[1], refs[0] } });
        Assert.Equal(new[] { "b", "a" }, batch.Data.GetProperty("allowed").EnumerateArray().Select(x => x.GetProperty("resource").GetProperty("external_id").GetString()));
        Assert.Equal("missing", Assert.Single(batch.Data.GetProperty("denied").EnumerateArray()).GetProperty("resource").GetProperty("external_id").GetString());
        var first = await s.Call("POST", "/api/v1/authz/list-resources", new { subject_id = s.Subject, permission = "user:read", resource_type = s.Prefix, limit = 1 });
        Assert.NotNull(first.Page); Assert.True(first.Page.HasMore); Assert.Single(first.Data.EnumerateArray());
        var cursor = first.Page.NextCursor!; Assert.False(string.IsNullOrEmpty(cursor));
        var second = await s.Call("POST", "/api/v1/authz/list-resources", new { subject_id = s.Subject, permission = "user:read", resource_type = s.Prefix, limit = 1, cursor });
        Assert.Single(second.Data.EnumerateArray()); Assert.NotEqual(first.Data.GetRawText(), second.Data.GetRawText());
        Assert.Equal(first.Meta!.SnapshotGeneration, second.Meta!.SnapshotGeneration); Assert.False(second.Page!.HasMore);
        await s.Error("CURSOR_INVALID", 400, "POST", "/api/v1/authz/list-resources", new { subject_id = s.Subject, permission = "user:read", resource_type = s.Prefix, limit = 1, cursor = (cursor[0] == 'A' ? "B" : "A") + cursor[1..] });
        var explain = await s.Call("POST", "/api/v1/authz/explain", new { subject_id = s.Subject, permission = "user:read", resource = refs[0] });
        Assert.Equal("allow", explain.Data.GetProperty("decision").GetString()); Assert.DoesNotContain(secret, explain.Data.GetRawText(), StringComparison.Ordinal);
        var rev = await s.Client.Authorization.RevisionAsync(); Assert.True(rev.Data.DbRevision > 0); Assert.Equal(rev.Data.DbRevision, rev.Data.SnapshotGeneration); Assert.Equal("in_sync", rev.Data.Status);
        var body = new CreateUserRequest { Username = s.Prefix, DisplayName = s.Prefix, InitialPassword = s.Password };
        var key = Guid.NewGuid().ToString("N");
        var created = await s.Client.Admin.Users.CreateUserAsync(body, idempotencyKey: key);
        var replay = await s.Client.Admin.Users.CreateUserAsync(body, idempotencyKey: key);
        Assert.Equal(201, created.Status); Assert.True(replay.Replayed); Assert.Equal(created.Data.Id, replay.Data.Id);
        var concurrent = await Task.WhenAll(s.Client.Admin.Users.CreateUserAsync(body, idempotencyKey: key), s.Client.Admin.Users.CreateUserAsync(body, idempotencyKey: key));
        Assert.All(concurrent, r => { Assert.True(r.Replayed); Assert.Equal(created.Data.Id, r.Data.Id); });
        var conflict = await s.Error("IDEMPOTENCY_CONFLICT", 409, "POST", "/api/v1/users", new { username = s.Prefix, display_name = "different", initial_password = s.Password }, key);
        Assert.Equal(false, conflict.Retryable);
        var found = await s.Client.Admin.Users.ListUsersAsync(q: s.Prefix); Assert.Equal(created.Data.Id, Assert.Single(found.Data).Id);
        var trace = Guid.NewGuid().ToString("N"); var requestId = s.Prefix + "-missing";
        var e = await s.Error("RESOURCE_NOT_FOUND", 404, "GET", "/api/v1/users/" + Guid.NewGuid(), options: new RequestOptions { RequestId = requestId, TraceParent = $"00-{trace}-1234567890abcdef-01" });
        Assert.Equal(trace, e.TraceId); Assert.Equal(requestId, e.RequestId);
        Console.WriteLine("IT-1..9 PASS");
    }
    [LiveTheory]
    [InlineData("query", "invalid_query_parameter")]
    [InlineData("path", "invalid_path_parameter")]
    [InlineData("unknown", "unknown_field")]
    public async Task RealServerInvalidInput(string kind, string reason)
    {
        using var s = await Login();
        var e = kind switch
        {
            "query" => await s.Error("INVALID_REQUEST", 400, "GET", "/api/v1/users", query: new() { ["limit"] = "abc" }),
            "path" => await s.Error("INVALID_REQUEST", 400, "GET", "/api/v1/users/not-a-uuid"),
            _ => await s.Error("INVALID_REQUEST", 400, "POST", "/api/v1/auth/login", new { identifier = Env("AUTHGEBRA_IT_USER"), password = s.Password, extra = true }),
        };
        Assert.NotEmpty(e.Violations); Assert.Equal(reason, e.Violations[0].Reason);
    }
    public sealed class FaultFactAttribute : FactAttribute
    {
        public FaultFactAttribute() { if (Base is null || Environment.GetEnvironmentVariable("AUTHGEBRA_IT_FAULT") != "1") Skip = "NOT_RUN: isolated fault injection not enabled"; }
    }
    private static async Task FaultCommand(string name)
    {
        using var process = new System.Diagnostics.Process { StartInfo = new("sh") { UseShellExecute = false } };
        process.StartInfo.ArgumentList.Add("-c"); process.StartInfo.ArgumentList.Add(Env(name));
        Assert.True(process.Start());
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        try { await process.WaitForExitAsync(timeout.Token); }
        catch { process.Kill(entireProcessTree: true); throw; }
        Assert.Equal(0, process.ExitCode);
    }
    [FaultFact]
    public async Task RealServerCommittedUnpublished()
    {
        using var s = await Login();
        var key = Guid.NewGuid().ToString("N");
        var body = new { username = s.Prefix, display_name = s.Prefix, initial_password = s.Password };
        try
        {
            await FaultCommand("AUTHGEBRA_IT_FAULT_ON_CMD");
            var e = await s.Error("AUTHZ_RELOAD_PENDING", 503, "POST", "/api/v1/users", body, key);
            Assert.Equal(CommitStateKind.CommittedUnpublished, e.CommitState); Assert.Equal(false, e.Retryable);
        }
        finally { await FaultCommand("AUTHGEBRA_IT_FAULT_OFF_CMD"); }
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(60));
        while (true)
        {
            try
            {
                var rev = await s.Client.Authorization.RevisionAsync(cancellationToken: deadline.Token);
                if (rev.Data.DbRevision == rev.Data.SnapshotGeneration) break;
            }
            catch (ApiException pending)
            {
                Assert.Equal("NOT_READY", pending.Code); Assert.Equal(503, pending.Status);
            }
            await Task.Delay(200, deadline.Token);
        }
        var replay = await s.Call("POST", "/api/v1/users", body, key);
        var found = await s.Client.Admin.Users.ListUsersAsync(q: s.Prefix);
        Assert.True(replay.Replayed); Assert.Equal(replay.Data.GetProperty("id").GetString(), Assert.Single(found.Data).Id.ToString());
        Console.WriteLine("IT-11 PASS");
    }

    public sealed class InflightFactAttribute : FactAttribute
    {
        public InflightFactAttribute()
        {
            if (Base is null || Environment.GetEnvironmentVariable("AUTHGEBRA_IT_INFLIGHT") != "1")
                Skip = "NOT_RUN: explicit in-flight barrier required";
        }
    }

    [InflightFact]
    public async Task RealServerInflight()
    {
        using var session = await Login();
        foreach (var different in new[] { false, true })
        {
            var username = "it-flight-cs-" + Guid.NewGuid().ToString("N")[..12];
            var key = Guid.NewGuid().ToString("N");
            var body = new { username, display_name = username, initial_password = session.Password };
            var changed = new { username, display_name = "different", initial_password = session.Password };
            Task<ApiResult<JsonElement>> first;
            Exception? secondFailure = null;
            try
            {
                await FaultCommand("AUTHGEBRA_IT_INFLIGHT_HOLD_CMD");
                first = session.Call("POST", "/api/v1/users", body, key);
                await FaultCommand("AUTHGEBRA_IT_INFLIGHT_WAIT_CMD");
                try { await session.Call("POST", "/api/v1/users", different ? changed : body, key); }
                catch (Exception failure) { secondFailure = failure; }
            }
            finally { await FaultCommand("AUTHGEBRA_IT_INFLIGHT_RELEASE_CMD"); }
            var created = await first.WaitAsync(TimeSpan.FromSeconds(35));
            Assert.Equal(201, created.Status);
            var pending = Assert.IsType<ApiException>(secondFailure);
            Assert.Equal("IDEMPOTENCY_IN_PROGRESS", pending.Code);
            Assert.Equal(409, pending.Status);
            Assert.Equal(true, pending.Retryable);
            var replay = await session.Call("POST", "/api/v1/users", body, key);
            Assert.True(replay.Replayed);
            Assert.Equal(created.Data.GetProperty("id").GetString(), replay.Data.GetProperty("id").GetString());
            var found = await session.Client.Admin.Users.ListUsersAsync(q: username);
            Assert.Equal(created.Data.GetProperty("id").GetString(), Assert.Single(found.Data).Id.ToString());
            if (different)
            {
                var conflict = await session.Error("IDEMPOTENCY_CONFLICT", 409, "POST", "/api/v1/users", changed, key);
                Assert.Equal(false, conflict.Retryable);
            }
        }
        Console.WriteLine("IT-INFLIGHT PASS");
    }

}
