//! Behavioural tests against the loopback mock: retry matrix, never-resend rules, tracing
//! headers, authentication, pagination, precision and the OpenAPI coverage guarantees.

const std = @import("std");
const Io = std.Io;
const Allocator = std.mem.Allocator;
const build_options = @import("build_options");
const sdk = @import("authgebra");
const errors = sdk.errors;
const mock = @import("mock.zig");

fn http(comptime status_line: []const u8, comptime extra_headers: []const u8, comptime body: []const u8) []const u8 {
    return "HTTP/1.1 " ++ status_line ++ "\r\ncontent-type: application/json\r\n" ++ extra_headers ++
        std.fmt.comptimePrint("content-length: {d}\r\nconnection: close\r\n\r\n", .{body.len}) ++ body;
}

const not_ready = http("503 Service Unavailable", "x-request-id: req-notready1\r\n", "{\"error\":{\"code\":\"NOT_READY\",\"message\":\"m\",\"request_id\":\"req-notready1\",\"retryable\":true}}");
const reload_pending = http("503 Service Unavailable", "x-request-id: req-reload001\r\n", "{\"error\":{\"code\":\"AUTHZ_RELOAD_PENDING\",\"message\":\"m\",\"request_id\":\"req-reload001\",\"retryable\":false,\"commit_state\":\"committed_unpublished\"}}");
const ok_check = http("200 OK", "x-request-id: req-okcheck01\r\n", "{\"data\":{\"decision\":\"allow\",\"reason_code\":\"allow\"},\"meta\":{\"authz_revision\":7,\"snapshot_generation\":7}}");
const ok_created = http("201 Created", "", "{\"data\":{\"id\":\"x\"},\"meta\":{\"authz_revision\":8,\"snapshot_generation\":8}}");
const subject = "00000000-0000-4000-8000-000000000001";

fn indexOfIgnoreCase(hay: []const u8, needle: []const u8) ?usize {
    if (needle.len > hay.len) return null;
    var i: usize = 0;
    while (i + needle.len <= hay.len) : (i += 1) {
        if (std.ascii.eqlIgnoreCase(hay[i .. i + needle.len], needle)) return i;
    }
    return null;
}

const Env = struct {
    srv: *mock.Mock,
    client: sdk.Client,
    url_buf: [64]u8,

    fn start(gpa: Allocator, steps: []const mock.Step, config_fn: *const fn (*sdk.Config) void) !*Env {
        const env = try gpa.create(Env);
        env.srv = try mock.Mock.start(gpa, std.testing.io, steps);
        var cfg: sdk.Config = .{ .base_url = env.srv.baseUrl(&env.url_buf), .total_timeout_ms = 2000 };
        cfg.retry = .{ .read_max_retries = 2, .write_max_retries = 3, .base_delay_ms = 1, .max_delay_ms = 5 };
        config_fn(&cfg);
        env.client = sdk.Client.init(gpa, std.testing.io, cfg);
        return env;
    }

    fn stop(self: *Env, gpa: Allocator) void {
        self.client.deinit();
        self.srv.stop();
        gpa.destroy(self);
    }
};

fn defaults(_: *sdk.Config) void {}

test "read: NOT_READY is retried (bounded) and then succeeds" {
    const gpa = std.testing.allocator;
    const steps = [_]mock.Step{ .{ .respond = not_ready }, .{ .respond = not_ready }, .{ .respond = ok_check } };
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    var out = try sdk.AuthorizationClient.init(&env.client).check(.{ .subject_id = subject, .permission = "p" }, .{});
    defer out.deinit();
    try std.testing.expect(out.isOk());
    try std.testing.expectEqual(@as(usize, 3), env.srv.connections());
}

test "read: retries are capped, the last api_error is returned" {
    const gpa = std.testing.allocator;
    const steps = [_]mock.Step{.{ .respond = not_ready }};
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    var out = try sdk.AuthorizationClient.init(&env.client).check(.{ .subject_id = subject, .permission = "p" }, .{});
    defer out.deinit();
    try std.testing.expectEqualStrings("NOT_READY", out.apiError().?.code);
    // 1 attempt + read_max_retries(2) — never unbounded.
    try std.testing.expectEqual(@as(usize, 3), env.srv.connections());
}

test "NOT_READY is never converted into a Deny" {
    const gpa = std.testing.allocator;
    const steps = [_]mock.Step{.{ .respond = not_ready }};
    const env = try Env.start(gpa, &steps, struct {
        fn f(c: *sdk.Config) void {
            c.retry.read_max_retries = 0;
        }
    }.f);
    defer env.stop(gpa);
    var out = try sdk.AuthorizationClient.init(&env.client).check(.{ .subject_id = subject, .permission = "p" }, .{});
    defer out.deinit();
    try std.testing.expect(!out.isOk());
    try std.testing.expect(out.ok() == null);
    try std.testing.expectEqual(@as(u16, 503), out.apiError().?.status);
}

test "write without Idempotency-Key is never re-sent, even with write retries enabled" {
    const gpa = std.testing.allocator;
    const steps = [_]mock.Step{.{ .respond = not_ready }};
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    const admin = sdk.RawAdminClient.init(&env.client);
    var out = try admin.createUser(.{ .body = .{ .object = try std.json.ObjectMap.init(gpa, &.{}, &.{}) } });
    defer out.deinit();
    try std.testing.expectEqualStrings("NOT_READY", out.apiError().?.code);
    try std.testing.expectEqual(@as(usize, 1), env.srv.connections());
}

test "write with the same Idempotency-Key is retried on NOT_READY with identical payload" {
    const gpa = std.testing.allocator;
    const steps = [_]mock.Step{ .{ .respond = not_ready }, .{ .respond = ok_created } };
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    const admin = sdk.RawAdminClient.init(&env.client);
    var body = try std.json.ObjectMap.init(gpa, &.{}, &.{});
    defer body.deinit(gpa);
    try body.put(gpa, "username", .{ .string = "alice" });
    var out = try admin.createUser(.{ .idempotency_key = "idem-0001", .body = .{ .object = body } });
    defer out.deinit();
    try std.testing.expect(out.isOk());
    try std.testing.expectEqual(@as(usize, 2), env.srv.connections());
    const first = env.srv.request(0).?;
    const second = env.srv.request(1).?;
    try std.testing.expectEqualStrings(first.body, second.body);
    try std.testing.expect(indexOfIgnoreCase(second.head, "idempotency-key: idem-0001") != null);
}

test "AUTHZ_RELOAD_PENDING is never re-sent, even with a key and write retries" {
    const gpa = std.testing.allocator;
    const steps = [_]mock.Step{ .{ .respond = reload_pending }, .{ .respond = ok_created } };
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    const admin = sdk.RawAdminClient.init(&env.client);
    var out = try admin.createUser(.{ .idempotency_key = "idem-0002", .body = .{ .object = try std.json.ObjectMap.init(gpa, &.{}, &.{}) } });
    defer out.deinit();
    const e = out.apiError().?;
    try std.testing.expectEqualStrings("AUTHZ_RELOAD_PENDING", e.code);
    try std.testing.expectEqual(errors.CommitState.committed_unpublished, e.commit_state.?);
    try std.testing.expectEqual(@as(?bool, false), e.retryable);
    try std.testing.expectEqual(@as(usize, 1), env.srv.connections());
}

test "429 honours Retry-After but caps the delay" {
    const gpa = std.testing.allocator;
    const limited = http("429 Too Many Requests", "retry-after: 3600\r\nx-request-id: req-limited01\r\n", "{\"error\":{\"code\":\"RATE_LIMITED\",\"message\":\"m\",\"request_id\":\"req-limited01\",\"retry_after_seconds\":3600}}");
    const steps = [_]mock.Step{ .{ .respond = limited }, .{ .respond = ok_check } };
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    var out = try sdk.AuthorizationClient.init(&env.client).check(.{ .subject_id = subject, .permission = "p" }, .{});
    defer out.deinit();
    // max_delay_ms = 5, so an hour-long Retry-After does not stall the call.
    try std.testing.expect(out.isOk());
}

test "tracing: traceparent is generated, request id passes through, invalid ones are dropped" {
    const gpa = std.testing.allocator;
    const steps = [_]mock.Step{.{ .respond = ok_check }};
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    const authz = sdk.AuthorizationClient.init(&env.client);

    var a = try authz.check(.{ .subject_id = subject, .permission = "p" }, .{ .request_id = "req-caller-0001" });
    a.deinit();
    var b = try authz.check(.{ .subject_id = subject, .permission = "p" }, .{ .request_id = "bad id" });
    b.deinit();
    var c = try authz.check(.{ .subject_id = subject, .permission = "p" }, .{ .traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01" });
    c.deinit();

    const r0 = env.srv.request(0).?.head;
    try std.testing.expect(indexOfIgnoreCase(r0, "x-request-id: req-caller-0001") != null);
    const tp = indexOfIgnoreCase(r0, "traceparent: ").?;
    try std.testing.expect(sdk.trace.isValidTraceparent(r0[tp + 13 .. tp + 13 + 55]));
    try std.testing.expect(indexOfIgnoreCase(r0, "user-agent: authgebra-zig/") != null);

    const r1 = env.srv.request(1).?.head;
    try std.testing.expect(indexOfIgnoreCase(r1, "x-request-id") == null);

    const r2 = env.srv.request(2).?.head;
    try std.testing.expect(std.mem.indexOf(u8, r2, "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01") != null);

    // A malformed caller traceparent is rejected locally, nothing is sent.
    const before = env.srv.connections();
    try std.testing.expectError(error.InvalidTraceparent, authz.check(.{ .subject_id = subject, .permission = "p" }, .{ .traceparent = "nope" }));
    try std.testing.expectEqual(before, env.srv.connections());
}

fn tokenFn(_: ?*anyopaque, gpa: Allocator) anyerror![]u8 {
    return gpa.dupe(u8, "s3cret-token");
}

test "bearer token comes from the provider and is only sent as Authorization" {
    const gpa = std.testing.allocator;
    const steps = [_]mock.Step{.{ .respond = ok_check }};
    const env = try Env.start(gpa, &steps, struct {
        fn f(c: *sdk.Config) void {
            c.token_provider = .{ .getFn = tokenFn };
        }
    }.f);
    defer env.stop(gpa);
    var out = try sdk.AuthorizationClient.init(&env.client).check(.{ .subject_id = subject, .permission = "p" }, .{});
    defer out.deinit();
    try std.testing.expect(indexOfIgnoreCase(env.srv.request(0).?.head, "authorization: Bearer s3cret-token") != null);
}

test "header injection is refused before anything is sent" {
    const gpa = std.testing.allocator;
    const steps = [_]mock.Step{.{ .respond = ok_check }};
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    const admin = sdk.RawAdminClient.init(&env.client);
    try std.testing.expectError(error.InvalidIdempotencyKey, admin.createUser(.{ .idempotency_key = "a\r\nX: 1", .body = .{ .object = try std.json.ObjectMap.init(gpa, &.{}, &.{}) } }));
    try std.testing.expectError(error.InvalidHeaderValue, admin.patchUser(.{ .id = "1", .if_match = "\"1\"\r\nX: y", .body = .{ .object = try std.json.ObjectMap.init(gpa, &.{}, &.{}) } }));
    try std.testing.expectEqual(@as(usize, 0), env.srv.connections());
}

test "int64 revision precision is lossless and empty meta is not 0" {
    const gpa = std.testing.allocator;
    const big = http("200 OK", "", "{\"data\":{\"db_revision\":9223372036854775807,\"snapshot_generation\":-9223372036854775808,\"status\":\"in_sync\"}}");
    const steps = [_]mock.Step{.{ .respond = big }};
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    var out = try sdk.AuthorizationClient.init(&env.client).revision(.{});
    defer out.deinit();
    const ok = out.ok().?;
    try std.testing.expectEqual(@as(i64, std.math.maxInt(i64)), ok.data.db_revision);
    try std.testing.expectEqual(@as(i64, std.math.minInt(i64)), ok.data.snapshot_generation);
    try std.testing.expect(ok.meta == null);
}

test "unknown decision value is a protocol error, not a Deny" {
    const gpa = std.testing.allocator;
    const weird = http("200 OK", "", "{\"data\":{\"decision\":\"maybe\",\"reason_code\":\"x\"}}");
    const steps = [_]mock.Step{.{ .respond = weird }};
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    var out = try sdk.AuthorizationClient.init(&env.client).check(.{ .subject_id = subject, .permission = "p" }, .{});
    defer out.deinit();
    try std.testing.expectEqual(errors.ProtocolKind.malformed_success_body, out.result.protocol_error.kind);
}

test "oversized response is refused without buffering it all" {
    const gpa = std.testing.allocator;
    const steps = [_]mock.Step{.{ .respond = ok_check }};
    const env = try Env.start(gpa, &steps, struct {
        fn f(c: *sdk.Config) void {
            c.max_response_bytes = 16;
            c.retry.read_max_retries = 0;
        }
    }.f);
    defer env.stop(gpa);
    var out = try sdk.AuthorizationClient.init(&env.client).check(.{ .subject_id = subject, .permission = "p" }, .{});
    defer out.deinit();
    try std.testing.expectEqual(errors.TransportKind.response_too_large, out.result.transport_error.kind);
}

test "pager surfaces CURSOR_STALE and never restarts on its own" {
    const gpa = std.testing.allocator;
    const page1 = http("200 OK", "", "{\"data\":[{\"resource_type_code\":\"doc\",\"external_id\":\"a\"}],\"page\":{\"next_cursor\":\"opaque-1\",\"has_more\":true},\"meta\":{\"authz_revision\":5,\"snapshot_generation\":5}}");
    const stale = http("409 Conflict", "", "{\"error\":{\"code\":\"CURSOR_STALE\",\"message\":\"m\",\"request_id\":\"req-stale0001\",\"retryable\":false}}");
    const steps = [_]mock.Step{ .{ .respond = page1 }, .{ .respond = stale } };
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    var pager = sdk.AuthorizationClient.init(&env.client).pager(.{ .subject_id = subject, .permission = "p", .resource_type = "doc" }, .{});
    defer pager.deinit();

    var first = (try pager.next()).?;
    defer first.deinit();
    try std.testing.expectEqual(@as(usize, 1), first.ok().?.data.len);

    var second = (try pager.next()).?;
    defer second.deinit();
    try std.testing.expectEqualStrings("CURSOR_STALE", second.apiError().?.code);

    // Iteration ended; no automatic restart (no third request).
    try std.testing.expect((try pager.next()) == null);
    try std.testing.expectEqual(@as(usize, 2), env.srv.connections());
    // The second request carried the opaque cursor verbatim.
    try std.testing.expect(std.mem.indexOf(u8, env.srv.request(1).?.body, "\"cursor\":\"opaque-1\"") != null);
}

test "pager stops at max_pages" {
    const gpa = std.testing.allocator;
    const more = http("200 OK", "", "{\"data\":[],\"page\":{\"next_cursor\":\"c\",\"has_more\":true}}");
    const steps = [_]mock.Step{.{ .respond = more }};
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    var pager = sdk.AuthorizationClient.init(&env.client).pager(.{ .subject_id = subject, .permission = "p", .resource_type = "doc" }, .{ .max_pages = 2 });
    defer pager.deinit();
    for (0..2) |_| {
        var o = (try pager.next()).?;
        o.deinit();
    }
    try std.testing.expectError(error.PageLimitExceeded, pager.next());
}

test "connection refused is a transport_error and a write stays commit-state free" {
    const gpa = std.testing.allocator;
    var client = sdk.Client.init(gpa, std.testing.io, .{
        .base_url = "http://127.0.0.1:1",
        .total_timeout_ms = 2000,
        .retry = .{ .read_max_retries = 0 },
    });
    defer client.deinit();
    const admin = sdk.RawAdminClient.init(&client);
    var out = try admin.createUser(.{ .body = .{ .object = try std.json.ObjectMap.init(gpa, &.{}, &.{}) } });
    defer out.deinit();
    const t = out.result.transport_error;
    try std.testing.expectEqual(errors.TransportKind.connection_failed, t.kind);
    // Nothing was ever sent, so no commit-state claim is made.
    try std.testing.expect(t.commit_state == null);
}

// ---------------------------------------------------------------------------------------------
// Contract coverage
// ---------------------------------------------------------------------------------------------

fn camel(a: Allocator, id: []const u8) ![]u8 {
    var out: std.ArrayList(u8) = .empty;
    var upper = false;
    for (id) |c| {
        if (c == '_') {
            upper = true;
            continue;
        }
        try out.append(a, if (upper) std.ascii.toUpper(c) else c);
        upper = false;
    }
    return out.items;
}

test "every OpenAPI operationId has a generated method (and the table matches the spec)" {
    const gpa = std.testing.allocator;
    const io = std.testing.io;
    var arena_state = std.heap.ArenaAllocator.init(gpa);
    defer arena_state.deinit();
    const a = arena_state.allocator();

    // Force analysis of every generated method body (Zig analyses lazily).
    std.testing.refAllDecls(sdk.AdminClient);

    const text = try Io.Dir.cwd().readFileAlloc(io, build_options.openapi_path, a, .limited(16 << 20));
    const spec = try std.json.parseFromSliceLeaky(std.json.Value, a, text, .{});
    const paths = spec.object.get("paths").?.object;

    var seen: usize = 0;
    var pit = paths.iterator();
    while (pit.next()) |p| {
        var mit = p.value_ptr.object.iterator();
        while (mit.next()) |m| {
            const id = m.value_ptr.object.get("operationId").?.string;
            const name = try camel(a, id);
            var found = false;
            inline for (comptime std.meta.declarations(sdk.AdminClient)) |d| {
                if (std.mem.eql(u8, d, name)) found = true;
            }
            if (!found) {
                std.debug.print("operationId without a method: {s} ({s})\n", .{ id, name });
                return error.MissingOperation;
            }
            seen += 1;
        }
    }
    try std.testing.expectEqual(sdk.admin.operations.len, seen);
}

test "known error codes equal the ApiErrorCode enum in the reviewed OpenAPI" {
    const gpa = std.testing.allocator;
    const io = std.testing.io;
    var arena_state = std.heap.ArenaAllocator.init(gpa);
    defer arena_state.deinit();
    const a = arena_state.allocator();
    const text = try Io.Dir.cwd().readFileAlloc(io, build_options.openapi_path, a, .limited(16 << 20));
    const spec = try std.json.parseFromSliceLeaky(std.json.Value, a, text, .{});
    const values = spec.object.get("components").?.object.get("schemas").?.object.get("ApiErrorCode").?.object.get("enum").?.array.items;
    try std.testing.expectEqual(errors.known_codes.len, values.len);
    for (values) |v| try std.testing.expect(errors.isKnownCode(v.string));
}

test "success preserves Idempotency-Replayed metadata" {
    const gpa = std.testing.allocator;
    const response = http("201 Created", "idempotency-replayed: true\r\n", "{\"data\":{\"id\":\"same-entity\"}}");
    const steps = [_]mock.Step{.{ .respond = response }};
    const env = try Env.start(gpa, &steps, defaults);
    defer env.stop(gpa);
    var out = try env.client.send(std.json.Value, .{ .method = .POST, .path = "/api/v1/users", .body = "{}", .idempotency_key = "same-key" });
    defer out.deinit();
    try std.testing.expect(out.isOk());
    try std.testing.expect(@hasField(@TypeOf(out.ok().?.*), "replayed"));
    if (comptime @hasField(@TypeOf(out.ok().?.*), "replayed")) {
        try std.testing.expect(@field(out.ok().?.*, "replayed"));
    }
}
