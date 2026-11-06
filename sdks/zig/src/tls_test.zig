//! Real sockets and certificates; run with integration/tls.py.
const std = @import("std");
const sdk = @import("authgebra");

test "real TLS certificate hostname and handshake verification" {
    const a = std.testing.allocator;
    const io = std.testing.io;
    var env = try std.testing.environ.createMap(a);
    defer env.deinit();
    if (env.get("SDK_TLS_VALID_URL") == null) return error.SkipZigTest;
    const ca = env.get("SDK_TLS_CA") orelse return error.MissingTlsCa;
    const origins = [_][]const u8{ "SDK_TLS_VALID_URL", "SDK_TLS_MISMATCH_URL", "SDK_TLS_PLAIN_URL", "SDK_TLS_VALID_URL" };
    for (origins, 0..) |origin, index| {
        const base = env.get(origin) orelse return error.MissingTlsOrigin;
        var client = sdk.Client.init(a, io, .{ .base_url = base, .retry = .{ .read_max_retries = 0 } });
        defer client.deinit();
        if (index != 0) {
            const now = std.Io.Clock.real.now(io);
            try client.http_client.ca_bundle.addCertsFromFilePathAbsolute(a, io, now, ca);
            client.http_client.now = now;
        }
        var out = try client.send(std.json.Value, .{ .method = .GET, .path = "/tls-test" });
        defer out.deinit();
        if (index == 3) {
            try std.testing.expect(out.isOk());
            try std.testing.expect(out.ok().?.data.object.get("tls").?.bool);
        } else {
            switch (out.result) {
                .transport_error => |failure| try std.testing.expectEqual(sdk.errors.TransportKind.tls, failure.kind),
                else => return error.InvalidTlsConnectionAccepted,
            }
        }
    }
}
