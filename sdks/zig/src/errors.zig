//! Result and error model shared by every operation.
//!
//! A call returns `Outcome(T)`, a tagged result — never a bare string error:
//!   * `ok`              — HTTP 2xx. `decision=deny` is a *successful* result.
//!   * `api_error`       — the server answered with the `{"error":{...}}` envelope.
//!   * `transport_error` — no usable HTTP response (timeout, connection reset, TLS, ...).
//!   * `protocol_error`  — a response arrived but cannot be understood (non-JSON, missing fields).
//!
//! Ownership: every variant owns an arena; call `deinit()` exactly once.

const std = @import("std");
const Allocator = std.mem.Allocator;

/// The 23 frozen server error codes. The test-suite compares this list with
/// `sdks/contracts/openapi.json` so it cannot drift silently.
pub const known_codes = [_][]const u8{
    "INVALID_REQUEST",          "CURSOR_INVALID",          "AUTH_REQUIRED",
    "INVALID_CREDENTIALS",      "TOKEN_EXPIRED",           "FORBIDDEN",
    "RESOURCE_NOT_FOUND",       "REVISION_CONFLICT",       "STATE_CONFLICT",
    "IDEMPOTENCY_CONFLICT",     "IDEMPOTENCY_IN_PROGRESS", "CURSOR_STALE",
    "RESOURCE_ALIAS_COLLISION", "PRECONDITION_FAILED",     "PAYLOAD_TOO_LARGE",
    "VALIDATION_ERROR",         "POLICY_INVALID",          "POLICY_TOO_COMPLEX",
    "RATE_LIMITED",             "INTERNAL_ERROR",          "NOT_READY",
    "AUTHZ_RELOAD_PENDING",     "IDENTITY_RELOAD_PENDING",
};

pub fn isKnownCode(code: []const u8) bool {
    for (known_codes) |known| if (std.mem.eql(u8, known, code)) return true;
    return false;
}

/// What the SDK knows about whether a write was committed.
///   * `committed_unpublished` — declared by the server: the mutation ran, the snapshot is not yet
///     published. Never resend.
///   * `unknown` — the SDK could not tell (write timed out / connection dropped). Never assume
///     "not committed"; reconcile with the same Idempotency-Key or an entity read.
/// `null` (absent) means nothing is claimed.
pub const CommitState = enum { committed_unpublished, unknown };

pub const Meta = struct {
    authz_revision: i64,
    snapshot_generation: ?i64 = null,
};

pub const Page = struct {
    next_cursor: ?[]const u8 = null,
    has_more: bool,
};

pub const Violation = struct {
    /// JSON Pointer; null when the server could not locate the field.
    path: ?[]const u8 = null,
    reason: []const u8,
    message: []const u8 = "",
};

/// Identifiers shared by every outcome that carries an HTTP response.
pub const Ids = struct {
    /// `X-Request-ID` response header when valid, else the body value.
    request_id: ?[]const u8 = null,
    /// Valid W3C trace id from the body (`error.trace_id`) or the `X-Trace-ID` header.
    trace_id: ?[]const u8 = null,
};

pub fn Success(comptime T: type) type {
    return struct {
        status: u16,
        data: T,
        /// Absent on admin reads — never filled with 0.
        meta: ?Meta = null,
        page: ?Page = null,
        ids: Ids = .{},
        /// True only when the server returned `Idempotency-Replayed: true`.
        replayed: bool = false,
    };
}

pub const ApiError = struct {
    status: u16,
    /// Raw server code, kept verbatim even when unknown.
    code: []const u8,
    known_code: bool,
    message: []const u8,
    ids: Ids = .{},
    /// Body `request_id` when it differs from the (valid) header value.
    body_request_id: ?[]const u8 = null,
    request_id_mismatch: bool = false,
    /// False when the response carried an `X-Request-ID` header that failed validation.
    request_id_header_valid: bool = true,
    /// Tri-state: true / false / not declared.
    retryable: ?bool = null,
    retry_after_seconds: ?u64 = null,
    commit_state: ?CommitState = null,
    violations: []const Violation = &.{},
    /// `error.details` as sent (sanitised by the server).
    details: ?std.json.Value = null,
};

pub const TransportKind = enum {
    timeout,
    connection_reset,
    connection_failed,
    tls,
    canceled,
    response_too_large,
    invalid_url,
    other,
};

pub const TransportError = struct {
    kind: TransportKind,
    /// `@errorName` of the underlying Zig error — safe to log.
    detail: []const u8,
    /// `unknown` for writes that may have been committed; null when provably nothing was sent.
    commit_state: ?CommitState = null,
};

pub const ProtocolKind = enum {
    non_json_response,
    malformed_error_body,
    malformed_success_body,
    unexpected_redirect,
};

pub const ProtocolError = struct {
    kind: ProtocolKind,
    status: u16,
    ids: Ids = .{},
};

pub fn Outcome(comptime T: type) type {
    return struct {
        arena: std.heap.ArenaAllocator,
        result: Result,

        const Self = @This();

        pub const Result = union(enum) {
            ok: Success(T),
            api_error: ApiError,
            transport_error: TransportError,
            protocol_error: ProtocolError,
        };

        pub fn deinit(self: *Self) void {
            self.arena.deinit();
            self.* = undefined;
        }

        pub fn isOk(self: *const Self) bool {
            return self.result == .ok;
        }

        pub fn ok(self: *const Self) ?*const Success(T) {
            return switch (self.result) {
                .ok => |*s| s,
                else => null,
            };
        }

        pub fn apiError(self: *const Self) ?*const ApiError {
            return switch (self.result) {
                .api_error => |*e| e,
                else => null,
            };
        }
    };
}

pub fn newOutcome(comptime T: type, gpa: Allocator) Outcome(T) {
    return .{ .arena = std.heap.ArenaAllocator.init(gpa), .result = undefined };
}
