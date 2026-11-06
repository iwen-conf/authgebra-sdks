//! Native live-server scenarios. No test surface => SkipZigTest, never mock integration evidence.
const std = @import("std");
const sdk = @import("authgebra");
const V = std.json.Value;
const A = std.mem.Allocator;

fn encoded(a: A, v: anytype) !V {
    const bytes = try std.json.Stringify.valueAlloc(a, v, .{ .emit_null_optional_fields = false });
    return std.json.parseFromSliceLeaky(V, a, bytes, .{ .allocate = .alloc_always });
}
fn get(v: V, key: []const u8) V {
    return v.object.get(key) orelse .null;
}
fn text(v: V) []const u8 {
    return v.string;
}
fn eq(v: V, s: []const u8) !void {
    try std.testing.expectEqualStrings(s, text(v));
}

const Session = struct {
    gpa: A,
    client: sdk.Client,
    prefix: []const u8,
    password: []const u8,
    subject: []const u8 = "",
    token: []const u8 = "",
    traceparent: ?[]const u8 = null,
    request_id: ?[]const u8 = null,

    fn call(self: *Session, method: std.http.Method, path: []const u8, body: ?V, key: ?[]const u8) !V {
        const bytes = if (body) |v| try std.json.Stringify.valueAlloc(self.gpa, v, .{}) else null;
        var request_id: [64]u8 = undefined;
        const id = try std.fmt.bufPrint(&request_id, "it-zig-{x}", .{std.Io.Clock.now(.real, std.testing.io).nanoseconds});
        const token_ptr: *const []const u8 = &self.token;
        self.client.config.token_provider = sdk.TokenProvider.static(token_ptr);
        var segments = std.mem.splitScalar(u8, path, '?');
        const route = segments.next().?;
        var query: [1]sdk.client.QueryParam = undefined;
        var count: usize = 0;
        if (segments.next()) |pair| {
            var fields = std.mem.splitScalar(u8, pair, '=');
            query[0] = .{ .name = fields.next().?, .value = fields.next().? };
            count = 1;
        }
        var out = try self.client.send(V, .{ .method = method, .path = route, .query = query[0..count], .body = bytes, .idempotency_key = key, .read_only = std.mem.startsWith(u8, route, "/api/v1/authz/"), .options = .{ .request_id = self.request_id orelse id, .traceparent = self.traceparent } });
        defer out.deinit();
        return switch (out.result) {
            .ok => |success| encoded(self.gpa, .{ .kind = "ok", .status = success.status, .data = success.data, .meta = success.meta, .page = success.page, .request_id = success.ids.request_id, .trace_id = success.ids.trace_id, .replayed = success.replayed }),
            .api_error => |e| encoded(self.gpa, .{ .kind = "api_error", .status = e.status, .code = e.code, .violations = e.violations, .request_id = e.ids.request_id, .trace_id = e.ids.trace_id, .retryable = e.retryable, .commit_state = if (e.commit_state) |state| @tagName(state) else @as(?[]const u8, null) }),
            else => {
                std.debug.print("live SDK transport/protocol failure at {s}\n", .{path});
                return error.LiveRequestFailed;
            },
        };
    }
    fn ok(self: *Session, method: std.http.Method, path: []const u8, body: ?V, key: ?[]const u8) !V {
        const r = try self.call(method, path, body, key);
        if (!std.mem.eql(u8, text(get(r, "kind")), "ok")) {
            std.debug.print("unexpected API error at {s}: {s}\n", .{ path, text(get(r, "code")) });
            return error.LiveRequestFailed;
        }
        return r;
    }
    fn api(self: *Session, method: std.http.Method, path: []const u8, body: ?V, key: ?[]const u8, code: []const u8, status: i64) !V {
        const r = try self.call(method, path, body, key);
        try eq(get(r, "kind"), "api_error");
        try eq(get(r, "code"), code);
        try std.testing.expectEqual(status, get(r, "status").integer);
        try std.testing.expect(get(r, "request_id") == .string and get(r, "trace_id") == .string);
        return r;
    }
};

fn login(a: A, env: *std.process.Environ.Map) !*Session {
    const base = env.get("AUTHGEBRA_IT_BASE_URL") orelse {
        std.debug.print("NOT_RUN: AUTHGEBRA_IT_BASE_URL unset\n", .{});
        return error.SkipZigTest;
    };
    try std.testing.expectEqualStrings("1", env.get("AUTHGEBRA_IT_CONFIRM_ISOLATED") orelse "");
    const username = env.get("AUTHGEBRA_IT_USER") orelse return error.MissingCredentials;
    const password = env.get("AUTHGEBRA_IT_PASSWORD") orelse return error.MissingCredentials;
    const prefix = try std.fmt.allocPrint(a, "it-zig-{x}", .{std.Io.Clock.now(.real, std.testing.io).nanoseconds});
    const s = try a.create(Session);
    s.* = .{ .gpa = a, .client = sdk.Client.init(a, std.testing.io, .{ .base_url = base }), .prefix = prefix, .password = password };
    errdefer s.client.deinit();
    const r = try s.ok(.POST, "/api/v1/auth/login", try encoded(a, .{ .identifier = username, .password = password }), null);
    try eq(get(get(r, "data"), "status"), "authenticated");
    s.token = text(get(get(r, "data"), "access_token"));
    const me = try s.ok(.GET, "/api/v1/auth/me", null, null);
    s.subject = text(get(get(get(me, "data"), "user"), "id"));
    return s;
}

test "live server authorization, paging, explain, revision and idempotency" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var env = try std.testing.environ.createMap(a);
    const s = try login(a, &env);
    defer s.client.deinit();
    _ = try s.api(.POST, "/api/v1/auth/login", try encoded(a, .{ .identifier = env.get("AUTHGEBRA_IT_USER").?, .password = try std.fmt.allocPrint(a, "{s}-wrong", .{s.password}) }), null, "INVALID_CREDENTIALS", 401);
    for ([_][]const u8{ "user:read", "nope:nope" }, [_][]const u8{ "allow", "deny" }) |permission, expected| {
        var out = try sdk.AuthorizationClient.init(&s.client).check(.{ .subject_id = s.subject, .permission = permission }, .{});
        defer out.deinit();
        try std.testing.expect(out.isOk());
        try std.testing.expectEqualStrings(expected, @tagName(out.ok().?.data.decision));
        try std.testing.expect(out.ok().?.meta.?.authz_revision > 0 and out.ok().?.meta.?.snapshot_generation.? > 0);
    }
    _ = try s.ok(.POST, "/api/v1/resource-types", try encoded(a, .{ .code = s.prefix, .name = s.prefix }), null);
    const attr = try s.ok(.POST, "/api/v1/attributes", try encoded(a, .{ .code = s.prefix, .name = s.prefix, .owner_type = "resource", .value_type = "string" }), null);
    const secret = try std.fmt.allocPrint(a, "private-{s}", .{s.prefix});
    const b = .{ .resource_type_code = s.prefix, .external_id = "b" };
    const x = .{ .resource_type_code = s.prefix, .external_id = "a" };
    for ([_][]const u8{ "b", "a" }) |external| {
        _ = try s.ok(.POST, "/api/v1/resources", try encoded(a, .{
            .resource_type_code = s.prefix,
            .external_id = external,
            .name = external,
            .attributes = .{.{ .attribute_id = text(get(get(attr, "data"), "id")), .value = secret }},
        }), null);
    }
    const batch = get(try s.ok(.POST, "/api/v1/authz/batch-check", try encoded(a, .{ .subject_id = s.subject, .permission = "user:read", .resources = .{ b, .{ .resource_type_code = s.prefix, .external_id = "missing" }, x, b } }), null), "data");
    const allowed = get(batch, "allowed").array.items;
    const denied = get(batch, "denied").array.items;
    try std.testing.expectEqual(@as(usize, 2), allowed.len);
    try std.testing.expectEqual(@as(usize, 1), denied.len);
    try eq(get(get(allowed[0], "resource"), "external_id"), "b");
    try eq(get(get(allowed[1], "resource"), "external_id"), "a");
    try eq(get(get(denied[0], "resource"), "external_id"), "missing");
    const first = try s.ok(.POST, "/api/v1/authz/list-resources", try encoded(a, .{ .subject_id = s.subject, .permission = "user:read", .resource_type = s.prefix, .limit = 1 }), null);
    try std.testing.expect(get(get(first, "page"), "has_more").bool);
    try std.testing.expectEqual(@as(usize, 1), get(first, "data").array.items.len);
    const cursor = text(get(get(first, "page"), "next_cursor"));
    const second = try s.ok(.POST, "/api/v1/authz/list-resources", try encoded(a, .{ .subject_id = s.subject, .permission = "user:read", .resource_type = s.prefix, .limit = 1, .cursor = cursor }), null);
    try std.testing.expectEqual(@as(usize, 1), get(second, "data").array.items.len);
    try std.testing.expect(!get(get(second, "page"), "has_more").bool);
    try std.testing.expect(!std.mem.eql(u8, text(get(get(first, "data").array.items[0], "external_id")), text(get(get(second, "data").array.items[0], "external_id"))));
    try std.testing.expectEqual(get(get(first, "meta"), "snapshot_generation").integer, get(get(second, "meta"), "snapshot_generation").integer);
    const bad = try a.dupe(u8, cursor);
    bad[0] = if (bad[0] == 'A') 'B' else 'A';
    _ = try s.api(.POST, "/api/v1/authz/list-resources", try encoded(a, .{ .subject_id = s.subject, .permission = "user:read", .resource_type = s.prefix, .limit = 1, .cursor = bad }), null, "CURSOR_INVALID", 400);
    const explain = try s.ok(.POST, "/api/v1/authz/explain", try encoded(a, .{ .subject_id = s.subject, .permission = "user:read", .resource = b }), null);
    try eq(get(get(explain, "data"), "decision"), "allow");
    try std.testing.expect(std.mem.indexOf(u8, try std.json.Stringify.valueAlloc(a, explain, .{}), secret) == null);
    var rev = try sdk.AuthorizationClient.init(&s.client).revision(.{});
    defer rev.deinit();
    try std.testing.expect(rev.isOk());
    try std.testing.expect(rev.ok().?.data.db_revision > 0);
    try std.testing.expectEqual(rev.ok().?.data.db_revision, rev.ok().?.data.snapshot_generation);
    const body = try encoded(a, .{ .username = s.prefix, .display_name = s.prefix, .initial_password = s.password });
    const created = try s.ok(.POST, "/api/v1/users", body, s.prefix);
    const replay = try s.ok(.POST, "/api/v1/users", body, s.prefix);
    try std.testing.expectEqual(@as(i64, 201), get(created, "status").integer);
    try std.testing.expect(get(replay, "replayed").bool);
    try eq(get(get(replay, "data"), "id"), text(get(get(created, "data"), "id")));
    const conflict = try s.api(.POST, "/api/v1/users", try encoded(a, .{ .username = s.prefix, .display_name = "different", .initial_password = s.password }), s.prefix, "IDEMPOTENCY_CONFLICT", 409);
    try std.testing.expect(!get(conflict, "retryable").bool);
    const found = try s.ok(.GET, try std.fmt.allocPrint(a, "/api/v1/users?q={s}", .{s.prefix}), null, null);
    try std.testing.expectEqual(@as(usize, 1), get(found, "data").array.items.len);
    try eq(get(get(found, "data").array.items[0], "id"), text(get(get(created, "data"), "id")));
    s.traceparent = "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01";
    s.request_id = try std.fmt.allocPrint(a, "{s}-missing", .{s.prefix});
    const missing_response = try s.api(.GET, "/api/v1/users/00000000-0000-4000-8000-000000000099", null, null, "RESOURCE_NOT_FOUND", 404);
    try eq(get(missing_response, "request_id"), s.request_id.?);
    try eq(get(missing_response, "trace_id"), "1234567890abcdef1234567890abcdef");
}

test "live server malformed path and unknown field" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var env = try std.testing.environ.createMap(a);
    const s = try login(a, &env);
    defer s.client.deinit();
    const query_error = try s.api(.GET, "/api/v1/users?limit=abc", null, null, "INVALID_REQUEST", 400);
    try eq(get(get(query_error, "violations").array.items[0], "reason"), "invalid_query_parameter");
    const e = try s.api(.GET, "/api/v1/users/not-a-uuid", null, null, "INVALID_REQUEST", 400);
    try eq(get(get(e, "violations").array.items[0], "reason"), "invalid_path_parameter");
    const unknown = try s.api(.POST, "/api/v1/auth/login", try encoded(a, .{ .identifier = env.get("AUTHGEBRA_IT_USER").?, .password = s.password, .extra = true }), null, "INVALID_REQUEST", 400);
    try eq(get(get(unknown, "violations").array.items[0], "reason"), "unknown_field");
}

fn faultCommand(a: A, env: *std.process.Environ.Map, name: []const u8) !void {
    const command = env.get(name) orelse return error.MissingFaultCommand;
    const result = try std.process.run(a, std.testing.io, .{ .argv = &.{ "sh", "-c", command }, .timeout = .{ .duration = .{ .raw = .fromSeconds(15), .clock = .awake } } });
    defer a.free(result.stdout);
    defer a.free(result.stderr);
    try std.testing.expect(result.term == .exited and result.term.exited == 0);
}

test "live server first-write inflight same and different payload" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const io = std.testing.io;
    var env = try std.testing.environ.createMap(a);
    if (!std.mem.eql(u8, env.get("AUTHGEBRA_IT_INFLIGHT") orelse "", "1")) return error.SkipZigTest;
    const s = try login(a, &env);
    defer s.client.deinit();
    for ([_]bool{ false, true }) |different| {
        const prefix = try std.fmt.allocPrint(a, "it-flight-zig-{x}", .{std.Io.Clock.real.now(io).nanoseconds});
        const key = try std.fmt.allocPrint(a, "key-{s}", .{prefix});
        const body = try encoded(a, .{ .username = prefix, .display_name = prefix, .initial_password = s.password });
        const duplicate = if (different) try encoded(a, .{ .username = prefix, .display_name = "different", .initial_password = s.password }) else body;
        var first_arena = std.heap.ArenaAllocator.init(std.heap.page_allocator);
        defer first_arena.deinit();
        const first_a = first_arena.allocator();
        var first = Session{ .gpa = first_a, .client = sdk.Client.init(first_a, io, s.client.config), .prefix = prefix, .password = s.password, .subject = s.subject, .token = s.token };
        defer first.client.deinit();
        try faultCommand(a, &env, "AUTHGEBRA_IT_INFLIGHT_HOLD_CMD");
        var future = try io.concurrent(Session.ok, .{ &first, .POST, "/api/v1/users", body, key });
        defer {
            faultCommand(a, &env, "AUTHGEBRA_IT_INFLIGHT_RELEASE_CMD") catch @panic("inflight cleanup failed");
            _ = future.cancel(io) catch @panic("inflight first write failed during cleanup");
        }
        try faultCommand(a, &env, "AUTHGEBRA_IT_INFLIGHT_WAIT_CMD");
        const second = try s.call(.POST, "/api/v1/users", duplicate, key);
        try faultCommand(a, &env, "AUTHGEBRA_IT_INFLIGHT_RELEASE_CMD");
        const created = try future.await(io);
        try std.testing.expectEqual(@as(i64, 201), get(created, "status").integer);
        try eq(get(second, "kind"), "api_error");
        try eq(get(second, "code"), "IDEMPOTENCY_IN_PROGRESS");
        try std.testing.expectEqual(@as(i64, 409), get(second, "status").integer);
        try std.testing.expect(get(second, "retryable").bool);
        const replay = try s.ok(.POST, "/api/v1/users", body, key);
        const found = try s.ok(.GET, try std.fmt.allocPrint(a, "/api/v1/users?q={s}", .{prefix}), null, null);
        try std.testing.expect(get(replay, "replayed").bool);
        try eq(get(get(replay, "data"), "id"), text(get(get(created, "data"), "id")));
        const rows = get(found, "data").array.items;
        try std.testing.expectEqual(@as(usize, 1), rows.len);
        try eq(get(rows[0], "id"), text(get(get(created, "data"), "id")));
        if (different) _ = try s.api(.POST, "/api/v1/users", duplicate, key, "IDEMPOTENCY_CONFLICT", 409);
    }
}

test "live server committed unpublished recovers and replays exactly one entity" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var env = try std.testing.environ.createMap(a);
    if (!std.mem.eql(u8, env.get("AUTHGEBRA_IT_FAULT") orelse "", "1")) {
        std.debug.print("NOT_RUN: fault injection not enabled\n", .{});
        return error.SkipZigTest;
    }
    const s = try login(a, &env);
    defer s.client.deinit();
    const body = try encoded(a, .{ .username = s.prefix, .display_name = s.prefix, .initial_password = s.password });
    {
        defer faultCommand(a, &env, "AUTHGEBRA_IT_FAULT_OFF_CMD") catch @panic("publication fault cleanup failed");
        try faultCommand(a, &env, "AUTHGEBRA_IT_FAULT_ON_CMD");
        const e = try s.api(.POST, "/api/v1/users", body, s.prefix, "AUTHZ_RELOAD_PENDING", 503);
        try eq(get(e, "commit_state"), "committed_unpublished");
        try std.testing.expect(!get(e, "retryable").bool);
    }
    const deadline = std.Io.Clock.now(.awake, std.testing.io).nanoseconds + 60 * std.time.ns_per_s;
    while (true) {
        var rev = try sdk.AuthorizationClient.init(&s.client).revision(.{});
        defer rev.deinit();
        if (rev.ok()) |success| {
            if (success.data.db_revision == success.data.snapshot_generation) break;
        } else if (rev.apiError()) |pending| {
            try std.testing.expectEqualStrings("NOT_READY", pending.code);
            try std.testing.expectEqual(@as(u16, 503), pending.status);
        } else return error.UnexpectedRecoveryFailure;
        try std.testing.expect(std.Io.Clock.now(.awake, std.testing.io).nanoseconds < deadline);
        try std.Io.sleep(std.testing.io, .fromMilliseconds(200), .awake);
    }
    const replay = try s.ok(.POST, "/api/v1/users", body, s.prefix);
    try std.testing.expect(get(replay, "replayed").bool);
    const found = try s.ok(.GET, try std.fmt.allocPrint(a, "/api/v1/users?q={s}", .{s.prefix}), null, null);
    try std.testing.expectEqual(@as(usize, 1), get(found, "data").array.items.len);
    try eq(get(get(found, "data").array.items[0], "id"), text(get(get(replay, "data"), "id")));
}

test "live generated typed admin model create replay get and PATCH clear" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var env = try std.testing.environ.createMap(a);
    const s = try login(a, &env);
    defer s.client.deinit();
    const admin = sdk.AdminClient.init(&s.client);
    const body: sdk.models.CreateUserRequest = .{ .username = s.prefix, .display_name = s.prefix, .initial_password = s.password, .email = .{ .value = "sdk@example.com" } };
    const key = try std.fmt.allocPrint(a, "{s}-typed", .{s.prefix});
    var created = try admin.createUser(.{ .body = body, .idempotency_key = key, .options = .{ .request_id = try std.fmt.allocPrint(a, "{s}-create", .{s.prefix}) } });
    defer created.deinit();
    try std.testing.expect(created.isOk());
    const id = created.ok().?.data.id;
    var replay = try admin.createUser(.{ .body = body, .idempotency_key = key, .options = .{ .request_id = try std.fmt.allocPrint(a, "{s}-replay", .{s.prefix}) } });
    defer replay.deinit();
    try std.testing.expect(replay.isOk() and replay.ok().?.replayed);
    try std.testing.expectEqualStrings(id, replay.ok().?.data.id);
    var patched = try admin.patchUser(.{ .id = id, .if_match = created.ok().?.data.etag, .body = .{ .email = .null }, .options = .{ .request_id = try std.fmt.allocPrint(a, "{s}-clear", .{s.prefix}) } });
    defer patched.deinit();
    try std.testing.expect(patched.isOk());
    try std.testing.expect(patched.ok().?.data.email == .null);
    var found = try admin.getUser(.{ .id = id });
    defer found.deinit();
    try std.testing.expect(found.isOk() and found.ok().?.data.email == .null);
}
