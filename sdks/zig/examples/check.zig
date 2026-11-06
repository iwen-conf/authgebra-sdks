//! Check example. Run: AUTHGEBRA_URL=https://authz.example.com AUTHGEBRA_TOKEN=... \
//!   AUTHGEBRA_SUBJECT=<uuid> zig build run-example

const std = @import("std");
const authgebra = @import("authgebra");

var token_storage: []const u8 = "";

fn tokenProvider(_: ?*anyopaque, gpa: std.mem.Allocator) anyerror![]u8 {
    return gpa.dupe(u8, token_storage);
}

pub fn main(init: std.process.Init) !void {
    const gpa = init.gpa;
    const env = init.environ_map;
    const url = env.get("AUTHGEBRA_URL") orelse return fail("set AUTHGEBRA_URL");
    token_storage = env.get("AUTHGEBRA_TOKEN") orelse return fail("set AUTHGEBRA_TOKEN");
    const subject = env.get("AUTHGEBRA_SUBJECT") orelse return fail("set AUTHGEBRA_SUBJECT (user uuid)");

    var client = authgebra.Client.init(gpa, init.io, .{
        .base_url = url,
        .token_provider = .{ .getFn = tokenProvider },
        .total_timeout_ms = 10_000,
    });
    defer client.deinit();
    const authz = authgebra.AuthorizationClient.init(&client);

    var outcome = try authz.check(.{
        .subject_id = subject,
        .permission = "document:read",
        .resource = .{ .resource_type_code = "document", .external_id = "doc-123" },
    }, .{});
    defer outcome.deinit();

    switch (outcome.result) {
        // `deny` is a normal, successful answer.
        .ok => |ok| std.debug.print("decision={s} reason={s} revision={?d} trace_id={?s}\n", .{
            @tagName(ok.data.decision),
            ok.data.reason_code,
            if (ok.meta) |m| m.authz_revision else null,
            ok.ids.trace_id,
        }),
        // The system could not make a trusted decision (e.g. NOT_READY). Do NOT treat this as
        // Deny inside the SDK; apply your own safe default and keep the error for diagnosis.
        .api_error => |e| std.debug.print("api error {d} {s} request_id={?s} trace_id={?s}\n", .{ e.status, e.code, e.ids.request_id, e.ids.trace_id }),
        .transport_error => |t| std.debug.print("transport error {s} ({s})\n", .{ @tagName(t.kind), t.detail }),
        .protocol_error => |p| std.debug.print("protocol error {s} (HTTP {d})\n", .{ @tagName(p.kind), p.status }),
    }
}

fn fail(msg: []const u8) error{MissingConfig} {
    std.debug.print("{s}\n", .{msg});
    return error.MissingConfig;
}
