//! Request-ID / W3C Trace Context helpers. Three identifiers are never mixed:
//! `request_id` (one HTTP request), `trace_id` (a call chain), `span_id` (one execution span).

const std = @import("std");
const Io = std.Io;

/// `^[A-Za-z0-9._-]{8,128}$` — same rule the server applies to `X-Request-ID`.
pub fn isValidRequestId(value: []const u8) bool {
    if (value.len < 8 or value.len > 128) return false;
    for (value) |c| {
        const ok = std.ascii.isAlphanumeric(c) or c == '.' or c == '_' or c == '-';
        if (!ok) return false;
    }
    return true;
}

/// Idempotency keys: 1–128 chars of `[A-Za-z0-9._-]`.
pub fn isValidIdempotencyKey(value: []const u8) bool {
    if (value.len == 0 or value.len > 128) return false;
    for (value) |c| {
        const ok = std.ascii.isAlphanumeric(c) or c == '.' or c == '_' or c == '-';
        if (!ok) return false;
    }
    return true;
}

fn isLowerHex(value: []const u8) bool {
    for (value) |c| {
        if (!((c >= '0' and c <= '9') or (c >= 'a' and c <= 'f'))) return false;
    }
    return true;
}

fn allZero(value: []const u8) bool {
    for (value) |c| if (c != '0') return false;
    return true;
}

/// A trace id is 32 lowercase hex characters and not all zero.
pub fn isValidTraceId(value: []const u8) bool {
    return value.len == 32 and isLowerHex(value) and !allZero(value);
}

/// Strict `traceparent` validation (version `00`: exactly 55 bytes; `ff` is reserved).
pub fn isValidTraceparent(value: []const u8) bool {
    if (value.len < 55) return false;
    if (value[2] != '-' or value[35] != '-' or value[52] != '-') return false;
    const version = value[0..2];
    if (!isLowerHex(version) or std.mem.eql(u8, version, "ff")) return false;
    if (std.mem.eql(u8, version, "00")) {
        if (value.len != 55) return false;
    } else if (value.len > 55 and value[55] != '-') return false;
    const trace = value[3..35];
    const parent = value[36..52];
    return isValidTraceId(trace) and isLowerHex(parent) and !allZero(parent) and isLowerHex(value[53..55]);
}

/// Extract the trace id from a valid `traceparent`.
pub fn traceIdOf(traceparent: []const u8) ?[]const u8 {
    if (!isValidTraceparent(traceparent)) return null;
    return traceparent[3..35];
}

pub const traceparent_len = 55;

/// Write a fresh `traceparent` (new trace id + new span id, sampled) into `out`.
pub fn newTraceparent(io: Io, out: *[traceparent_len]u8) void {
    var trace: [16]u8 = undefined;
    var span: [8]u8 = undefined;
    while (true) {
        io.random(&trace);
        if (!allZeroBytes(&trace)) break;
    }
    while (true) {
        io.random(&span);
        if (!allZeroBytes(&span)) break;
    }
    writeTraceparent(out, trace, span);
}

/// New span id under an existing trace id (used per retry attempt).
pub fn childTraceparent(io: Io, trace_id: []const u8, out: *[traceparent_len]u8) void {
    var span: [8]u8 = undefined;
    while (true) {
        io.random(&span);
        if (!allZeroBytes(&span)) break;
    }
    var trace: [16]u8 = undefined;
    _ = std.fmt.hexToBytes(&trace, trace_id) catch unreachable;
    writeTraceparent(out, trace, span);
}

fn allZeroBytes(bytes: []const u8) bool {
    for (bytes) |b| if (b != 0) return false;
    return true;
}

fn writeTraceparent(out: *[traceparent_len]u8, trace: [16]u8, span: [8]u8) void {
    const hex = "0123456789abcdef";
    out[0] = '0';
    out[1] = '0';
    out[2] = '-';
    for (trace, 0..) |b, i| {
        out[3 + i * 2] = hex[b >> 4];
        out[3 + i * 2 + 1] = hex[b & 0xf];
    }
    out[35] = '-';
    for (span, 0..) |b, i| {
        out[36 + i * 2] = hex[b >> 4];
        out[36 + i * 2 + 1] = hex[b & 0xf];
    }
    out[52] = '-';
    out[53] = '0';
    out[54] = '1';
}

test "traceparent validation" {
    const ok = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
    try std.testing.expect(isValidTraceparent(ok));
    try std.testing.expectEqualStrings("4bf92f3577b34da6a3ce929d0e0e4736", traceIdOf(ok).?);
    const bad = [_][]const u8{
        "",
        "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
        "00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01",
        "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01-x",
    };
    for (bad) |b| try std.testing.expect(!isValidTraceparent(b));
}

test "request id and idempotency key rules" {
    try std.testing.expect(isValidRequestId("req-abcdefgh"));
    try std.testing.expect(!isValidRequestId("short"));
    try std.testing.expect(!isValidRequestId("bad id\twith ctrl"));
    try std.testing.expect(isValidIdempotencyKey("idem-0001"));
    try std.testing.expect(!isValidIdempotencyKey(""));
    try std.testing.expect(!isValidIdempotencyKey("a b"));
}

test "generated traceparent is valid and child keeps the trace id" {
    var buf: [traceparent_len]u8 = undefined;
    newTraceparent(std.testing.io, &buf);
    try std.testing.expect(isValidTraceparent(&buf));
    var child: [traceparent_len]u8 = undefined;
    childTraceparent(std.testing.io, buf[3..35], &child);
    try std.testing.expect(isValidTraceparent(&child));
    try std.testing.expectEqualStrings(buf[3..35], child[3..35]);
}
