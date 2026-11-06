//! Cross-language conformance: every `sdks/fixtures/cases/*.json` is replayed against a real
//! loopback HTTP server (or a fault) and the outcome is compared with the fixture's `expect`.
//! An `expect` key this harness does not understand fails the test — nothing passes silently.

const std = @import("std");
const Io = std.Io;
const Allocator = std.mem.Allocator;
const build_options = @import("build_options");
const sdk = @import("authgebra");
const errors = sdk.errors;
const mock = @import("mock.zig");

const Value = std.json.Value;

const Observed = struct {
    kind: []const u8,
    status: ?u16 = null,
    code: ?[]const u8 = null,
    known_code: ?bool = null,
    request_id: ?[]const u8 = null,
    trace_id: ?[]const u8 = null,
    retryable: ?bool = null,
    retry_after_seconds: ?u64 = null,
    commit_state: ?[]const u8 = null,
    violations: []const errors.Violation = &.{},
    error_name: ?[]const u8 = null,
    request_id_header_valid: ?bool = null,
    body_request_id: ?[]const u8 = null,
    request_id_mismatch: ?bool = null,
    decision: ?[]const u8 = null,
    authz_revision: ?i64 = null,
    snapshot_generation: ?i64 = null,
    has_more: ?bool = null,
    echo_username: ?[]const u8 = null,
};

fn commitName(c: ?errors.CommitState) ?[]const u8 {
    return if (c) |v| @tagName(v) else null;
}

fn dupeOpt(a: Allocator, v: ?[]const u8) !?[]const u8 {
    return if (v) |x| try a.dupe(u8, x) else null;
}

/// The outcome's arena is freed by the caller, so everything observed is copied into `a`.
fn observe(a: Allocator, outcome: anytype) !Observed {
    switch (outcome.result) {
        .ok => |ok| {
            var o: Observed = .{ .kind = "ok", .status = ok.status, .request_id = try dupeOpt(a, ok.ids.request_id), .trace_id = try dupeOpt(a, ok.ids.trace_id) };
            if (ok.meta) |m| {
                o.authz_revision = m.authz_revision;
                o.snapshot_generation = m.snapshot_generation;
            }
            if (ok.page) |p| o.has_more = p.has_more;
            const T = @TypeOf(ok.data);
            if (@typeInfo(T) == .@"struct" and @hasField(T, "decision")) o.decision = @tagName(ok.data.decision); // static name
            if (T == Value) {
                switch (ok.data) {
                    .object => |obj| if (obj.get("username")) |u| switch (u) {
                        .string => |s| o.echo_username = try a.dupe(u8, s),
                        else => {},
                    },
                    else => {},
                }
            }
            return o;
        },
        .api_error => |e| {
            const vs = try a.alloc(errors.Violation, e.violations.len);
            for (e.violations, vs) |src, *dst| dst.* = .{
                .path = try dupeOpt(a, src.path),
                .reason = try a.dupe(u8, src.reason),
                .message = try a.dupe(u8, src.message),
            };
            return .{
                .kind = "api_error",
                .status = e.status,
                .code = try a.dupe(u8, e.code),
                .known_code = e.known_code,
                .request_id = try dupeOpt(a, e.ids.request_id),
                .trace_id = try dupeOpt(a, e.ids.trace_id),
                .retryable = e.retryable,
                .retry_after_seconds = e.retry_after_seconds,
                .commit_state = commitName(e.commit_state),
                .violations = vs,
                .request_id_header_valid = e.request_id_header_valid,
                .body_request_id = try dupeOpt(a, e.body_request_id),
                .request_id_mismatch = e.request_id_mismatch,
            };
        },
        .transport_error => |t| return .{
            .kind = "transport_error",
            .error_name = @tagName(t.kind),
            .commit_state = commitName(t.commit_state),
        },
        .protocol_error => |p| return .{
            .kind = "protocol_error",
            .status = p.status,
            .error_name = @tagName(p.kind),
            .request_id = try dupeOpt(a, p.ids.request_id),
            .trace_id = try dupeOpt(a, p.ids.trace_id),
        },
    }
}

fn fieldOf(v: Value, key: []const u8) ?Value {
    return switch (v) {
        .object => |o| o.get(key),
        else => null,
    };
}

fn str(v: ?Value) ?[]const u8 {
    const x = v orelse return null;
    return switch (x) {
        .string => |s| s,
        else => null,
    };
}

fn buildResponse(a: Allocator, resp: Value) ![]u8 {
    const status: u16 = @intCast(fieldOf(resp, "status").?.integer);
    var out: std.ArrayList(u8) = .empty;
    const phrase = (@as(std.http.Status, @fromBackingInt(@intCast(status)))).phrase() orelse "Status";
    try out.print(a, "HTTP/1.1 {d} {s}\r\n", .{ status, phrase });
    var has_ct = false;
    if (fieldOf(resp, "headers")) |h| switch (h) {
        .object => |obj| {
            var it = obj.iterator();
            while (it.next()) |e| {
                if (std.ascii.eqlIgnoreCase(e.key_ptr.*, "content-type")) has_ct = true;
                try out.print(a, "{s}: {s}\r\n", .{ e.key_ptr.*, e.value_ptr.string });
            }
        },
        else => {},
    };
    if (!has_ct) try out.appendSlice(a, "content-type: application/json\r\n");
    const body: []const u8 = if (str(fieldOf(resp, "body_raw"))) |raw| raw else if (fieldOf(resp, "body")) |b|
        try std.json.Stringify.valueAlloc(a, b, .{})
    else
        "";
    try out.print(a, "content-length: {d}\r\nconnection: close\r\n\r\n", .{body.len});
    try out.appendSlice(a, body);
    return out.items;
}

const subject = "00000000-0000-4000-8000-000000000001";
const entity_id = "11111111-1111-4111-8111-111111111111";

fn empty_object(a: Allocator) !Value {
    return .{ .object = try std.json.ObjectMap.init(a, &.{}, &.{}) };
}

fn dispatch(client: *sdk.Client, a: Allocator, fx: Value) !Observed {
    const op = str(fieldOf(fx, "operation")).?;
    const req = fieldOf(fx, "request") orelse Value{ .null = {} };
    const key: ?[]const u8 = str(fieldOf(req, "idempotency_key"));
    const body: Value = fieldOf(req, "body") orelse try empty_object(a);
    const authz = sdk.AuthorizationClient.init(client);
    const admin = sdk.RawAdminClient.init(client);

    if (std.mem.eql(u8, op, "authz.check")) {
        var out = try authz.check(.{ .subject_id = subject, .permission = "document:read" }, .{});
        defer out.deinit();
        return observe(a, out);
    } else if (std.mem.eql(u8, op, "authz.list_resources")) {
        var out = try authz.listResources(.{ .subject_id = subject, .permission = "document:read", .resource_type = "document" }, .{});
        defer out.deinit();
        return observe(a, out);
    } else if (std.mem.eql(u8, op, "users.create")) {
        var out = try admin.createUser(.{ .idempotency_key = key, .body = body });
        defer out.deinit();
        return observe(a, out);
    } else if (std.mem.eql(u8, op, "users.update")) {
        var out = try admin.patchUser(.{ .id = entity_id, .if_match = "\"1\"", .idempotency_key = key, .body = body });
        defer out.deinit();
        return observe(a, out);
    } else if (std.mem.eql(u8, op, "users.list")) {
        var out = try admin.listUsers(.{});
        defer out.deinit();
        return observe(a, out);
    } else if (std.mem.eql(u8, op, "auth.change_password")) {
        var out = try admin.changePassword(.{ .body = body });
        defer out.deinit();
        return observe(a, out);
    }
    std.debug.print("unknown fixture operation: {s}\n", .{op});
    return error.UnknownFixtureOperation;
}

fn expectJsonEqOpt(id: []const u8, key: []const u8, want: Value, got: anytype) !void {
    const T = @TypeOf(got);
    switch (want) {
        .null => if (got != null) {
            std.debug.print("[{s}] {s}: expected absent, got {any}\n", .{ id, key, got });
            return error.FixtureMismatch;
        },
        .bool => |b| {
            if (T == ?bool and got == b) return;
            std.debug.print("[{s}] {s}: expected {}, got {any}\n", .{ id, key, b, got });
            return error.FixtureMismatch;
        },
        .integer => |i| {
            const g: ?i64 = if (T == ?u16 or T == ?u64 or T == ?i64) (if (got) |x| @as(i64, @intCast(x)) else null) else null;
            if (g != null and g.? == i) return;
            std.debug.print("[{s}] {s}: expected {d}, got {any}\n", .{ id, key, i, got });
            return error.FixtureMismatch;
        },
        .string => |s| {
            if (T == ?[]const u8) {
                if (got) |g| if (std.mem.eql(u8, g, s)) return;
            }
            // Large integers are written as decimal strings in fixtures.
            if (T == ?i64) {
                if (got) |g| {
                    var buf: [32]u8 = undefined;
                    const text = std.fmt.bufPrint(&buf, "{d}", .{g}) catch unreachable;
                    if (std.mem.eql(u8, text, s)) return;
                }
            }
            std.debug.print("[{s}] {s}: expected \"{s}\", got {any}\n", .{ id, key, s, got });
            return error.FixtureMismatch;
        },
        else => return error.FixtureMismatch,
    }
}

fn compare(id: []const u8, expect: Value, observed: Observed, srv: *mock.Mock, steps_resend_forbidden: *bool) !void {
    var it = expect.object.iterator();
    while (it.next()) |e| {
        const key = e.key_ptr.*;
        const want = e.value_ptr.*;
        if (std.mem.eql(u8, key, "kind")) {
            try std.testing.expectEqualStrings(want.string, observed.kind);
        } else if (std.mem.eql(u8, key, "status")) {
            try expectJsonEqOpt(id, key, want, observed.status);
        } else if (std.mem.eql(u8, key, "code")) {
            try expectJsonEqOpt(id, key, want, observed.code);
        } else if (std.mem.eql(u8, key, "known_code")) {
            try expectJsonEqOpt(id, key, want, observed.known_code);
        } else if (std.mem.eql(u8, key, "request_id")) {
            try expectJsonEqOpt(id, key, want, observed.request_id);
        } else if (std.mem.eql(u8, key, "trace_id")) {
            try expectJsonEqOpt(id, key, want, observed.trace_id);
        } else if (std.mem.eql(u8, key, "retryable")) {
            try expectJsonEqOpt(id, key, want, observed.retryable);
        } else if (std.mem.eql(u8, key, "retry_after_seconds")) {
            try expectJsonEqOpt(id, key, want, observed.retry_after_seconds);
        } else if (std.mem.eql(u8, key, "commit_state")) {
            try expectJsonEqOpt(id, key, want, observed.commit_state);
        } else if (std.mem.eql(u8, key, "error")) {
            try expectJsonEqOpt(id, key, want, observed.error_name);
        } else if (std.mem.eql(u8, key, "request_id_header_valid")) {
            try expectJsonEqOpt(id, key, want, observed.request_id_header_valid);
        } else if (std.mem.eql(u8, key, "body_request_id")) {
            try expectJsonEqOpt(id, key, want, observed.body_request_id);
        } else if (std.mem.eql(u8, key, "request_id_mismatch")) {
            try expectJsonEqOpt(id, key, want, observed.request_id_mismatch);
        } else if (std.mem.eql(u8, key, "decision")) {
            try expectJsonEqOpt(id, key, want, observed.decision);
        } else if (std.mem.eql(u8, key, "authz_revision")) {
            try expectJsonEqOpt(id, key, want, observed.authz_revision);
        } else if (std.mem.eql(u8, key, "snapshot_generation")) {
            try expectJsonEqOpt(id, key, want, observed.snapshot_generation);
        } else if (std.mem.eql(u8, key, "has_more")) {
            try expectJsonEqOpt(id, key, want, observed.has_more);
        } else if (std.mem.eql(u8, key, "echo_username")) {
            try expectJsonEqOpt(id, key, want, observed.echo_username);
        } else if (std.mem.eql(u8, key, "violations")) {
            const arr = want.array.items;
            try std.testing.expectEqual(arr.len, observed.violations.len);
            for (arr, observed.violations) |w, g| {
                try expectJsonEqOpt(id, "violations.path", fieldOf(w, "path").?, g.path);
                try std.testing.expectEqualStrings(str(fieldOf(w, "reason")).?, g.reason);
            }
        } else if (std.mem.eql(u8, key, "auto_resent")) {
            // false => the SDK must not have re-sent the request: exactly one connection.
            steps_resend_forbidden.* = !want.bool;
            if (!want.bool) try std.testing.expectEqual(@as(usize, 1), srv.connections());
        } else if (std.mem.eql(u8, key, "request_body_utf8_exact")) {
            const cap = srv.request(0) orelse return error.FixtureMismatch;
            const needle = "\"username\":\"用户-é-😀\"";
            try std.testing.expect(std.mem.indexOf(u8, cap.body, needle) != null);
        } else {
            std.debug.print("[{s}] unhandled expect key: {s}\n", .{ id, key });
            return error.UnhandledExpectKey;
        }
    }
}

fn runFixture(gpa: Allocator, io: Io, name: []const u8, text: []const u8) !void {
    var arena_state = std.heap.ArenaAllocator.init(gpa);
    defer arena_state.deinit();
    const a = arena_state.allocator();

    const fx = try std.json.parseFromSliceLeaky(Value, a, text, .{ .allocate = .alloc_always });
    const expect = fieldOf(fx, "expect").?;

    var steps: [1]mock.Step = undefined;
    var timeout_ms: u32 = 5000;
    if (str(fieldOf(fx, "fault"))) |fault| {
        if (std.mem.eql(u8, fault, "timeout")) {
            steps[0] = .stall;
            timeout_ms = 300;
        } else if (std.mem.eql(u8, fault, "reset")) {
            steps[0] = .drop;
        } else return error.UnknownFault;
    } else {
        steps[0] = .{ .respond = try buildResponse(a, fieldOf(fx, "response").?) };
    }

    const srv = try mock.Mock.start(gpa, io, &steps);
    defer srv.stop();
    var url_buf: [64]u8 = undefined;

    var client = sdk.Client.init(gpa, io, .{
        .base_url = srv.baseUrl(&url_buf),
        .total_timeout_ms = timeout_ms,
        // Writes would be retried up to 3 times *if* they carried a key and the failure were
        // retryable — which is what makes the "no auto resend" assertions meaningful.
        .retry = .{ .read_max_retries = 0, .write_max_retries = 3, .base_delay_ms = 1, .max_delay_ms = 5 },
    });
    defer client.deinit();

    const observed = try dispatch(&client, a, fx);
    var forbid = false;
    compare(name, expect, observed, srv, &forbid) catch |err| {
        std.debug.print("fixture {s} FAILED: {s} (kind={s})\n", .{ name, @errorName(err), observed.kind });
        return err;
    };
}

test "all shared fixtures replay identically" {
    const gpa = std.testing.allocator;
    const io = std.testing.io;

    var dir = try Io.Dir.cwd().openDir(io, build_options.fixtures_dir, .{ .iterate = true });
    defer dir.close(io);

    var names: std.ArrayList([]u8) = .empty;
    defer {
        for (names.items) |n| gpa.free(n);
        names.deinit(gpa);
    }
    var it = dir.iterate();
    while (try it.next(io)) |entry| {
        if (entry.kind != .file or !std.mem.endsWith(u8, entry.name, ".json")) continue;
        try names.append(gpa, try gpa.dupe(u8, entry.name));
    }
    std.mem.sort([]u8, names.items, {}, struct {
        fn lt(_: void, x: []u8, y: []u8) bool {
            return std.mem.lessThan(u8, x, y);
        }
    }.lt);
    try std.testing.expect(names.items.len >= 25);

    var passed: usize = 0;
    for (names.items) |name| {
        const text = try dir.readFileAlloc(io, name, gpa, .limited(1 << 20));
        defer gpa.free(text);
        try runFixture(gpa, io, name, text);
        passed += 1;
    }
    try std.testing.expectEqual(names.items.len, passed);
}
