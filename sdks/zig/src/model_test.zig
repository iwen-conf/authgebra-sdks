const std = @import("std");
const sdk = @import("authgebra");
const models = sdk.models;
const support = models.support;
const mock = @import("mock.zig");

fn requestIdHeader(head: []const u8) ?[]const u8 {
    var lines = std.mem.splitSequence(u8, head, "\r\n");
    while (lines.next()) |line| {
        const colon = std.mem.indexOfScalar(u8, line, ':') orelse continue;
        if (std.ascii.eqlIgnoreCase(line[0..colon], "x-request-id")) return std.mem.trim(u8, line[colon + 1 ..], " \t");
    }
    return null;
}

test "shared request ids preserve valid caller values and discard invalid inputs" {
    const a = std.testing.allocator;
    var arena = std.heap.ArenaAllocator.init(a);
    defer arena.deinit();
    const bytes = try std.Io.Dir.cwd().readFileAlloc(std.testing.io, "../fixtures/request-semantics.json", arena.allocator(), .limited(1 << 20));
    const fixture = try std.json.parseFromSliceLeaky(std.json.Value, arena.allocator(), bytes, .{});
    const payload = "{\"data\":{\"decision\":\"deny\",\"reason_code\":\"missing_permission\"}}";
    const response = "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\nconnection: close\r\n" ++ std.fmt.comptimePrint("content-length: {d}\r\n\r\n", .{payload.len}) ++ payload;
    const steps = [_]mock.Step{.{ .respond = response }};
    const server = try mock.Mock.start(a, std.testing.io, &steps);
    defer server.stop();
    var url_buf: [64]u8 = undefined;
    var client = sdk.Client.init(a, std.testing.io, .{ .base_url = server.baseUrl(&url_buf) });
    defer client.deinit();
    for (fixture.object.get("request_ids").?.array.items, 0..) |case, i| {
        const id = case.object.get("value").?.string;
        const valid = case.object.get("valid").?.bool;
        try std.testing.expectEqual(valid, sdk.trace.isValidRequestId(id));
        var out = try sdk.AuthorizationClient.init(&client).check(.{ .subject_id = "00000000-0000-4000-8000-000000000001", .permission = "test:read" }, .{ .request_id = id });
        defer out.deinit();
        try std.testing.expect(out.isOk());
        const head = server.request(i).?.head;
        const sent = requestIdHeader(head);
        if (valid) try std.testing.expectEqualStrings(id, sent.?) else if (sent) |replacement| {
            try std.testing.expect(sdk.trace.isValidRequestId(replacement));
            try std.testing.expect(!std.mem.eql(u8, replacement, id));
        }
    }
}

test "typed PATCH omission null and zero values remain distinct" {
    const a = std.testing.allocator;
    const cases = [_]struct { model: models.PatchRoleRequest, json: []const u8 }{
        .{ .model = .{}, .json = "{}" },
        .{ .model = .{ .description = .null }, .json = "{\"description\":null}" },
        .{ .model = .{ .description = .{ .value = "" } }, .json = "{\"description\":\"\"}" },
    };
    for (cases) |case| {
        const bytes = try std.json.Stringify.valueAlloc(a, case.model, .{});
        defer a.free(bytes);
        try std.testing.expectEqualStrings(case.json, bytes);
        const parsed = try std.json.parseFromSlice(models.PatchRoleRequest, a, bytes, .{});
        defer parsed.deinit();
        try std.testing.expectEqual(std.meta.activeTag(case.model.description), std.meta.activeTag(parsed.value.description));
    }
    const Zero = struct {
        flag: support.Optional(bool) = .absent,
        number: support.Optional(i64) = .absent,
        items: support.Optional([]const u8) = .absent,
        pub fn jsonStringify(self: @This(), out: anytype) !void {
            try support.writeObject(self, out);
        }
    };
    const zero = try std.json.Stringify.valueAlloc(a, Zero{ .flag = .{ .value = false }, .number = .{ .value = 0 }, .items = .{ .value = "" } }, .{});
    defer a.free(zero);
    try std.testing.expectEqualStrings("{\"flag\":false,\"number\":0,\"items\":\"\"}", zero);
}

test "known tagged union keeps wire discriminator and fields" {
    const a = std.testing.allocator;
    const rule: models.RewriteRuleDto = .{ .include = .{ .relation = "member" } };
    const bytes = try std.json.Stringify.valueAlloc(a, rule, .{});
    defer a.free(bytes);
    try std.testing.expectEqualStrings("{\"kind\":\"include\",\"relation\":\"member\"}", bytes);
    const decoded = try std.json.parseFromSlice(models.RewriteRuleDto, a, bytes, .{});
    defer decoded.deinit();
    try std.testing.expectEqualStrings("member", decoded.value.include.relation);
    const unsigned = try std.json.parseFromSlice(models.TypedValueDto, a, "{\"type\":\"u64\",\"value\":18446744073709551615}", .{});
    defer unsigned.deinit();
    try std.testing.expectEqual(std.math.maxInt(u64), unsigned.value.u64.value);
}

test "generated open JSON retains arrays null and int64" {
    const a = std.testing.allocator;
    const bytes = "{\"arbitrary\":[null,9223372036854775807,18446744073709551615,true]}";
    const value = try std.json.parseFromSlice(models.JsonValue, a, bytes, .{});
    defer value.deinit();
    const encoded = try std.json.Stringify.valueAlloc(a, value.value, .{});
    defer a.free(encoded);
    try std.testing.expectEqualStrings(bytes, encoded);
}

test "shared PATCH values preserve exact JSON semantics" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const fixture = try std.Io.Dir.cwd().readFileAlloc(std.testing.io, "../fixtures/request-semantics.json", a, .limited(1 << 20));
    const parsed = try std.json.parseFromSliceLeaky(std.json.Value, a, fixture, .{ .parse_numbers = false });
    for (parsed.object.get("patch_values").?.array.items) |value| {
        const request: models.PatchPolicyRequest = .{ .expression = if (value == .null) .null else .{ .value = .{ .value = value } } };
        const bytes = try std.json.Stringify.valueAlloc(a, request, .{});
        const actual = try std.json.parseFromSliceLeaky(std.json.Value, a, bytes, .{ .parse_numbers = false });
        const wanted_json = try std.json.Stringify.valueAlloc(a, value, .{});
        const actual_json = try std.json.Stringify.valueAlloc(a, actual.object.get("expression").?, .{});
        try std.testing.expectEqualStrings(wanted_json, actual_json);
    }
    for (parsed.object.get("integer_values").?.array.items) |value| {
        const named: models.JsonValue = .{ .value = value };
        const encoded = try std.json.Stringify.valueAlloc(a, named, .{});
        const expected = try std.json.Stringify.valueAlloc(a, value, .{});
        try std.testing.expectEqualStrings(expected, encoded);
    }
}

test "paged endpoints reject missing null and structurally incomplete page metadata" {
    const invalid = [_][]const u8{
        "{\"data\":[]}",
        "{\"data\":[],\"page\":{}}",
        "{\"data\":[],\"page\":null}",
    };
    const a = std.testing.allocator;
    for (invalid) |payload| {
        const response = try std.fmt.allocPrint(a, "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {d}\r\nconnection: close\r\n\r\n{s}", .{ payload.len, payload });
        defer a.free(response);
        const steps = [_]mock.Step{.{ .respond = response }};
        const server = try mock.Mock.start(a, std.testing.io, &steps);
        defer server.stop();
        var url_buf: [64]u8 = undefined;
        var client = sdk.Client.init(a, std.testing.io, .{ .base_url = server.baseUrl(&url_buf) });
        defer client.deinit();
        var admin = try sdk.AdminClient.init(&client).listUsers(.{});
        defer admin.deinit();
        try std.testing.expect(admin.result == .protocol_error);
        var authz = try sdk.AuthorizationClient.init(&client).listResources(.{ .subject_id = "11111111-1111-4111-8111-111111111111", .permission = "user:read", .resource_type = "doc" }, .{});
        defer authz.deinit();
        try std.testing.expect(authz.result == .protocol_error);
    }
}

test "typed admin sends a named body and decodes named data in its outcome arena" {
    const a = std.testing.allocator;
    const payload = "{\"data\":{\"id\":\"00000000-0000-4000-8000-000000000001\",\"username\":\"alice\",\"display_name\":\"Alice\",\"status\":\"active\",\"auth_version\":1,\"version\":1,\"etag\":\"e\",\"created_at_rfc3339\":\"2026-11-06T00:00:00Z\",\"mfa_enabled\":false,\"roles_count\":0,\"sessions_count\":0,\"future_field\":true}}";
    const response = "HTTP/1.1 201 Created\r\ncontent-type: application/json\r\nconnection: close\r\n" ++ std.fmt.comptimePrint("content-length: {d}\r\n\r\n", .{payload.len}) ++ payload;
    const steps = [_]mock.Step{.{ .respond = response }};
    const server = try mock.Mock.start(a, std.testing.io, &steps);
    defer server.stop();
    var url_buf: [64]u8 = undefined;
    var client = sdk.Client.init(a, std.testing.io, .{ .base_url = server.baseUrl(&url_buf) });
    defer client.deinit();
    var result = try sdk.AdminClient.init(&client).createUser(.{ .body = .{ .username = "alice", .display_name = "Alice", .initial_password = "a-password-123" } });
    defer result.deinit();
    try std.testing.expect(result.isOk());
    try std.testing.expectEqualStrings("alice", result.ok().?.data.username);
    try std.testing.expect(result.ok().?.data.email == .absent);
    const body = server.request(0).?.body;
    try std.testing.expectEqualStrings("{\"display_name\":\"Alice\",\"initial_password\":\"a-password-123\",\"username\":\"alice\"}", body);
}

test "IAM code bounds and UUID value uniqueness are exact" {
    try std.testing.expect(support.validIamCode("a_z.9-"));
    for ([_][]const u8{ "", "A", "0a", "a\n", "a b", "用户" }) |code| try std.testing.expect(!support.validIamCode(code));
    const at_limit: [64]u8 = @splat('a');
    const over_limit: [65]u8 = @splat('a');
    try std.testing.expect(support.validIamCode(&at_limit));
    try std.testing.expect(!support.validIamCode(&over_limit));
    try std.testing.expect(!support.uniqueUuids(&.{ "abcdefab-1234-4123-8123-123456789abc", "abcdefab-1234-4123-8123-123456789abc" }));
    try std.testing.expect(support.uniqueUuids(&.{ "abcdefab-1234-4123-8123-123456789abc", "ABCDEFAB-1234-4123-8123-123456789ABC" }));
}

test "IAM generated models enforce source input bounds" {
    for ([_][]const u8{ "A", "a\n", "a b" }) |code| {
        const role: models.CreateRoleRequest = .{ .code = code, .name = "role" };
        try std.testing.expectError(error.InvalidValue, role.validate());
    }
    const empty_name: models.CreateRoleRequest = .{ .code = "role", .name = "" };
    try std.testing.expectError(error.InvalidValue, empty_name.validate());
    const duplicate: models.ReplaceUserRolesRequest = .{ .role_ids = &.{ "11111111-1111-4111-8111-111111111111", "11111111-1111-4111-8111-111111111111" } };
    try std.testing.expectError(error.InvalidValue, duplicate.validate());
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const ids = try arena.allocator().alloc([]const u8, 513);
    for (ids, 0..) |*id, i| id.* = try std.fmt.allocPrint(arena.allocator(), "00000000-0000-4000-8000-{d:0>12}", .{i});
    const over_limit: models.ReplaceUserRolesRequest = .{ .role_ids = ids };
    try std.testing.expectError(error.InvalidValue, over_limit.validate());
    const at_limit: models.ReplaceUserRolesRequest = .{ .role_ids = ids[0..512] };
    try at_limit.validate();
}
