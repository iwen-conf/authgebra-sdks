//! Client: configuration, retry policy, request building, response classification.
//!
//! The SDK performs no authorization logic and never logs: tokens only ever live in the
//! `Authorization` header of the request being sent (and are zeroed afterwards).

const std = @import("std");
const Io = std.Io;
const http = std.http;
const Allocator = std.mem.Allocator;
const errors = @import("errors.zig");
const trace = @import("trace.zig");
const transport = @import("transport.zig");
const model_support = @import("model_support.zig");

pub const version = "0.1.0";

/// Supplies the bearer token for each attempt (so it can be refreshed). The returned slice is
/// allocated with `gpa`; the SDK zeroes and frees it after use.
pub const TokenProvider = struct {
    context: ?*anyopaque = null,
    getFn: *const fn (context: ?*anyopaque, gpa: Allocator) anyerror![]u8,

    pub fn static(token: *const []const u8) TokenProvider {
        return .{ .context = @ptrCast(@constCast(token)), .getFn = staticGet };
    }

    fn staticGet(context: ?*anyopaque, gpa: Allocator) anyerror![]u8 {
        const token: *const []const u8 = @ptrCast(@alignCast(context.?));
        return gpa.dupe(u8, token.*);
    }
};

/// Retries are bounded and conservative.
///   * reads (GET/HEAD): up to `read_max_retries` on RATE_LIMITED, NOT_READY, timeout, reset.
///   * writes: `write_max_retries` is 0 by default. When raised it only applies to requests that
///     carry an Idempotency-Key, and never to AUTHZ_RELOAD_PENDING / IDENTITY_RELOAD_PENDING.
pub const RetryPolicy = struct {
    read_max_retries: u8 = 2,
    write_max_retries: u8 = 0,
    base_delay_ms: u32 = 200,
    max_delay_ms: u32 = 5000,

    pub const hard_cap_reads: u8 = 10;
    pub const hard_cap_writes: u8 = 5;
};

pub const Config = struct {
    /// e.g. `https://authz.example.com` (no trailing path; `/api/v1/...` is appended).
    base_url: []const u8,
    token_provider: ?TokenProvider = null,
    /// Hard deadline for one attempt, covering connect, TLS, send and receive.
    /// (std.http in Zig 0.17 exposes no separate connect-phase timeout for TLS.)
    total_timeout_ms: u32 = 30_000,
    max_response_bytes: usize = 8 * 1024 * 1024,
    retry: RetryPolicy = .{},
    /// Added to every request. Names/values must not contain CR/LF.
    extra_headers: []const http.Header = &.{},
    /// Generate a W3C `traceparent` for calls that don't supply one.
    propagate_trace: bool = true,
    // TLS certificate verification is always on; there is deliberately no switch to disable it.
};

pub const CallOptions = struct {
    /// Sent as `X-Request-ID` when it passes `trace.isValidRequestId`.
    request_id: ?[]const u8 = null,
    /// Valid `traceparent` to continue an existing trace.
    traceparent: ?[]const u8 = null,
    tracestate: ?[]const u8 = null,
};

pub const QueryParam = struct { name: []const u8, value: []const u8 };

pub const Request = struct {
    method: http.Method,
    /// Path with already percent-encoded segments, e.g. `/api/v1/users/123`.
    path: []const u8,
    query: []const QueryParam = &.{},
    /// JSON bytes (serialised by the caller from typed values).
    body: ?[]const u8 = null,
    if_match: ?[]const u8 = null,
    idempotency_key: ?[]const u8 = null,
    /// POST endpoints that never mutate state (authorization decisions) are treated as reads:
    /// retried like reads, and a timeout does not imply an unknown commit state.
    read_only: bool = false,
    /// Setup/health schemas are bare JSON, rather than an envelope with a `data` member.
    bare_response: bool = false,
    /// A paged operation cannot succeed without a valid `page.has_more` boolean.
    paged_response: bool = false,
    options: CallOptions = .{},
};

pub const SendError = error{
    OutOfMemory,
    InvalidHeaderValue,
    InvalidIdempotencyKey,
    InvalidTraceparent,
    InvalidBaseUrl,
};

pub const Client = struct {
    gpa: Allocator,
    io: Io,
    config: Config,
    http_client: http.Client,

    pub fn init(gpa: Allocator, io: Io, config: Config) Client {
        return .{
            .gpa = gpa,
            .io = io,
            .config = config,
            .http_client = .{ .allocator = gpa, .io = io },
        };
    }

    pub fn deinit(self: *Client) void {
        self.http_client.deinit();
    }

    /// Execute `req` and classify the result. `T` is the type of the response `data`.
    pub fn send(self: *Client, comptime T: type, req: Request) SendError!errors.Outcome(T) {
        try validateRequest(req, self.config);
        const is_write = !req.read_only and switch (req.method) {
            .GET, .HEAD => false,
            else => true,
        };
        const has_key = req.idempotency_key != null;
        const max_retries: u8 = if (!is_write)
            @min(self.config.retry.read_max_retries, RetryPolicy.hard_cap_reads)
        else if (has_key)
            @min(self.config.retry.write_max_retries, RetryPolicy.hard_cap_writes)
        else
            0;

        var outcome = errors.newOutcome(T, self.gpa);
        errdefer outcome.arena.deinit();
        const url = try buildUrl(outcome.arena.allocator(), self.config.base_url, req);

        var trace_buf: [trace.traceparent_len]u8 = undefined;
        var attempt: u8 = 0;
        while (true) : (attempt += 1) {
            // A fresh span id per attempt; the trace id stays the same across retries.
            var trace_value: ?[]const u8 = null;
            if (req.options.traceparent) |tp| {
                trace_value = tp;
            } else if (self.config.propagate_trace) {
                if (attempt == 0) {
                    trace.newTraceparent(self.io, &trace_buf);
                } else {
                    var next: [trace.traceparent_len]u8 = undefined;
                    trace.childTraceparent(self.io, trace_buf[3..35], &next);
                    trace_buf = next;
                }
                trace_value = &trace_buf;
            }

            var raw = try self.performAttempt(url, req, trace_value);
            defer raw.deinit(self.gpa);

            var attempt_outcome = errors.newOutcome(T, self.gpa);
            errdefer attempt_outcome.arena.deinit();
            try classify(T, &attempt_outcome, &raw, is_write, req.bare_response, req.paged_response);

            if (attempt < max_retries and shouldRetry(&attempt_outcome.result)) {
                const delay = retryDelayMs(self.config.retry, attempt, &attempt_outcome.result);
                attempt_outcome.deinit();
                self.io.sleep(.fromMilliseconds(delay), .awake) catch {
                    outcome.arena.deinit();
                    var canceled = errors.newOutcome(T, self.gpa);
                    canceled.result = .{ .transport_error = .{ .kind = .canceled, .detail = "Canceled", .commit_state = if (is_write) .unknown else null } };
                    return canceled;
                };
                continue;
            }
            outcome.arena.deinit();
            return attempt_outcome;
        }
    }

    fn performAttempt(self: *Client, url: []const u8, req: Request, traceparent: ?[]const u8) SendError!AttemptResult {
        var arena = std.heap.ArenaAllocator.init(self.gpa);
        defer arena.deinit();
        const a = arena.allocator();

        var headers: std.ArrayList(http.Header) = .empty;
        try headers.append(a, .{ .name = "accept", .value = "application/json" });
        if (req.if_match) |v| try headers.append(a, .{ .name = "if-match", .value = v });
        if (req.idempotency_key) |v| try headers.append(a, .{ .name = "idempotency-key", .value = v });
        if (req.options.request_id) |v| {
            if (trace.isValidRequestId(v)) try headers.append(a, .{ .name = "x-request-id", .value = v });
        }
        if (traceparent) |v| try headers.append(a, .{ .name = "traceparent", .value = v });
        if (req.options.tracestate) |v| {
            if (traceparent != null and noCtl(v)) try headers.append(a, .{ .name = "tracestate", .value = v });
        }
        for (self.config.extra_headers) |h| try headers.append(a, h);

        var token: ?[]u8 = null;
        defer if (token) |t| {
            std.crypto.secureZero(u8, t);
            self.gpa.free(t);
        };
        var auth: ?[]const u8 = null;
        if (self.config.token_provider) |provider| {
            const t = provider.getFn(provider.context, self.gpa) catch return error.OutOfMemory;
            token = t;
            if (!noCtl(t)) return error.InvalidHeaderValue;
            auth = std.fmt.allocPrint(a, "Bearer {s}", .{t}) catch return error.OutOfMemory;
        }
        defer if (auth) |v| std.crypto.secureZero(u8, @constCast(v));

        const ua = std.fmt.allocPrint(a, "authgebra-zig/{s}", .{version}) catch return error.OutOfMemory;
        const ex = transport.execute(&self.http_client, self.io, self.gpa, .{
            .method = req.method,
            .url = url,
            .headers = headers.items,
            .authorization = auth,
            .user_agent = ua,
            .payload = req.body,
            .max_response_bytes = self.config.max_response_bytes,
        }, self.config.total_timeout_ms);
        return .{ .exchange = ex };
    }
};

const AttemptResult = struct {
    exchange: transport.Exchange,
    fn deinit(self: *AttemptResult, gpa: Allocator) void {
        self.exchange.deinit(gpa);
    }
};

fn noCtl(value: []const u8) bool {
    for (value) |c| if (c < 0x20 and c != '\t' or c == 0x7f) return false;
    return true;
}

fn validateRequest(req: Request, config: Config) SendError!void {
    if (req.if_match) |v| if (!noCtl(v) or v.len == 0) return error.InvalidHeaderValue;
    if (req.idempotency_key) |v| if (!trace.isValidIdempotencyKey(v)) return error.InvalidIdempotencyKey;
    if (req.options.traceparent) |v| if (!trace.isValidTraceparent(v)) return error.InvalidTraceparent;
    for (config.extra_headers) |h| {
        if (h.name.len == 0 or !noCtl(h.name) or !noCtl(h.value) or std.mem.indexOfScalar(u8, h.name, ':') != null)
            return error.InvalidHeaderValue;
    }
}

fn buildUrl(a: Allocator, base: []const u8, req: Request) SendError![]u8 {
    var base_trim = base;
    while (base_trim.len > 0 and base_trim[base_trim.len - 1] == '/') base_trim = base_trim[0 .. base_trim.len - 1];
    if (base_trim.len == 0) return error.InvalidBaseUrl;
    var out: std.ArrayList(u8) = .empty;
    try out.appendSlice(a, base_trim);
    try out.appendSlice(a, req.path);
    var sep: u8 = '?';
    for (req.query) |q| {
        try out.append(a, sep);
        sep = '&';
        try percentEncode(a, &out, q.name);
        try out.append(a, '=');
        try percentEncode(a, &out, q.value);
    }
    return out.toOwnedSlice(a);
}

/// Percent-encode everything except RFC 3986 unreserved characters.
pub fn percentEncode(a: Allocator, out: *std.ArrayList(u8), value: []const u8) Allocator.Error!void {
    const hex = "0123456789ABCDEF";
    for (value) |c| {
        if (std.ascii.isAlphanumeric(c) or c == '-' or c == '.' or c == '_' or c == '~') {
            try out.append(a, c);
        } else {
            try out.append(a, '%');
            try out.append(a, hex[c >> 4]);
            try out.append(a, hex[c & 0xf]);
        }
    }
}

fn shouldRetry(result: anytype) bool {
    return switch (result.*) {
        .ok, .protocol_error => false,
        .transport_error => |t| switch (t.kind) {
            .timeout, .connection_reset, .connection_failed => true,
            else => false,
        },
        .api_error => |e| blk: {
            // Committed-but-unpublished writes must never be resent.
            if (e.commit_state != null) break :blk false;
            const retryable_codes = [_][]const u8{ "RATE_LIMITED", "NOT_READY", "IDEMPOTENCY_IN_PROGRESS" };
            for (retryable_codes) |c| if (std.mem.eql(u8, e.code, c)) break :blk true;
            break :blk false;
        },
    };
}

fn retryDelayMs(policy: RetryPolicy, attempt: u8, result: anytype) u32 {
    const shift: u5 = @intCast(@min(attempt, 16));
    var delay: u64 = @as(u64, policy.base_delay_ms) << shift;
    switch (result.*) {
        .api_error => |e| if (e.retry_after_seconds) |s| {
            delay = @max(delay, s *| 1000);
        },
        else => {},
    }
    return @intCast(@min(delay, policy.max_delay_ms));
}

// ---------------------------------------------------------------------------------------------
// Classification
// ---------------------------------------------------------------------------------------------

fn classify(comptime T: type, out: *errors.Outcome(T), attempt_result: *AttemptResult, is_write: bool, bare_response: bool, paged_response: bool) SendError!void {
    const a = out.arena.allocator();
    switch (attempt_result.exchange) {
        .failure => |f| {
            const maybe_sent = f.kind == .timeout or f.kind == .connection_reset;
            out.result = .{ .transport_error = .{
                .kind = f.kind,
                .detail = f.detail,
                .commit_state = if (is_write and maybe_sent) .unknown else null,
            } };
        },
        .response => |*raw| try classifyResponse(T, out, a, raw, bare_response, paged_response),
    }
}

fn parseU64(text: []const u8) ?u64 {
    return std.fmt.parseInt(u64, std.mem.trim(u8, text, " \t"), 10) catch null;
}

fn jsonObject(value: std.json.Value) ?std.json.ObjectMap {
    return switch (value) {
        .object => |o| o,
        else => null,
    };
}

fn jsonString(value: ?std.json.Value) ?[]const u8 {
    const v = value orelse return null;
    return switch (v) {
        .string => |s| s,
        else => null,
    };
}

fn classifyResponse(comptime T: type, out: *errors.Outcome(T), a: Allocator, raw: *const transport.RawResponse, bare_response: bool, paged_response: bool) SendError!void {
    const status = raw.status;
    var ids: errors.Ids = .{};

    // Header values are validated; invalid ones are ignored, never trusted.
    var header_request_id: ?[]const u8 = null;
    var request_id_header_valid = true;
    if (raw.header("x-request-id")) |v| {
        if (trace.isValidRequestId(v)) {
            header_request_id = try a.dupe(u8, v);
        } else request_id_header_valid = false;
    }
    var header_trace_id: ?[]const u8 = null;
    if (raw.header("x-trace-id")) |v| {
        if (trace.isValidTraceId(v)) header_trace_id = try a.dupe(u8, v);
    }

    if (status >= 300 and status < 400) {
        out.result = .{ .protocol_error = .{ .kind = .unexpected_redirect, .status = status, .ids = .{ .request_id = header_request_id, .trace_id = header_trace_id } } };
        return;
    }

    var parsed = std.json.parseFromSliceLeaky(std.json.Value, a, raw.body, .{ .allocate = .alloc_always, .parse_numbers = false }) catch {
        ids = .{ .request_id = header_request_id, .trace_id = header_trace_id };
        out.result = .{ .protocol_error = .{ .kind = .non_json_response, .status = status, .ids = ids } };
        return;
    };
    model_support.preserveIntegers(&parsed);
    const root = jsonObject(parsed);

    if (status >= 200 and status < 300) {
        ids = .{ .request_id = header_request_id, .trace_id = header_trace_id };
        const obj = root orelse return malformed(T, out, .malformed_success_body, status, ids);
        const data_value = if (bare_response) parsed else obj.get("data") orelse return malformed(T, out, .malformed_success_body, status, ids);
        const data = std.json.parseFromValueLeaky(T, a, data_value, .{ .ignore_unknown_fields = true }) catch
            return malformed(T, out, .malformed_success_body, status, ids);
        var meta: ?errors.Meta = null;
        if (obj.get("meta")) |m| switch (m) {
            .null => {},
            else => {
                meta = std.json.parseFromValueLeaky(errors.Meta, a, m, .{ .ignore_unknown_fields = true }) catch
                    return malformed(T, out, .malformed_success_body, status, ids);
            },
        };
        var page: ?errors.Page = null;
        if (obj.get("page")) |p| switch (p) {
            .null => {},
            else => {
                page = std.json.parseFromValueLeaky(errors.Page, a, p, .{ .ignore_unknown_fields = true }) catch
                    return malformed(T, out, .malformed_success_body, status, ids);
            },
        };
        if (paged_response and page == null) return malformed(T, out, .malformed_success_body, status, ids);
        const replayed = if (raw.header("idempotency-replayed")) |value| std.mem.eql(u8, value, "true") else false;
        out.result = .{ .ok = .{ .status = status, .data = data, .meta = meta, .page = page, .ids = ids, .replayed = replayed } };
        return;
    }

    // Error envelope.
    ids = .{ .request_id = header_request_id, .trace_id = header_trace_id };
    const envelope = root orelse return malformed(T, out, .malformed_error_body, status, ids);
    const err_obj = jsonObject(envelope.get("error") orelse return malformed(T, out, .malformed_error_body, status, ids)) orelse
        return malformed(T, out, .malformed_error_body, status, ids);
    const code = jsonString(err_obj.get("code")) orelse return malformed(T, out, .malformed_error_body, status, ids);
    const message = jsonString(err_obj.get("message")) orelse "";

    var body_request_id: ?[]const u8 = null;
    if (jsonString(err_obj.get("request_id"))) |v| body_request_id = v;
    var final_request_id = header_request_id;
    var mismatch = false;
    var shown_body_id: ?[]const u8 = null;
    if (header_request_id) |h| {
        if (body_request_id) |b| if (!std.mem.eql(u8, h, b)) {
            mismatch = true;
            shown_body_id = b;
        };
    } else final_request_id = body_request_id;

    var trace_id: ?[]const u8 = null;
    if (jsonString(err_obj.get("trace_id"))) |v| {
        if (trace.isValidTraceId(v)) trace_id = v;
    }
    if (trace_id == null) trace_id = header_trace_id;

    var retryable: ?bool = null;
    if (err_obj.get("retryable")) |v| switch (v) {
        .bool => |b| retryable = b,
        else => {},
    };

    var retry_after: ?u64 = null;
    if (err_obj.get("retry_after_seconds")) |v| switch (v) {
        .integer => |i| if (i >= 0) {
            retry_after = @intCast(i);
        },
        else => {},
    };
    if (retry_after == null) {
        if (raw.header("retry-after")) |v| retry_after = parseU64(v);
    }

    var commit_state: ?errors.CommitState = null;
    if (err_obj.get("commit_state")) |v| switch (v) {
        .string => |s| commit_state = if (std.mem.eql(u8, s, "committed_unpublished")) .committed_unpublished else .unknown,
        else => {},
    };

    var violations: []const errors.Violation = &.{};
    var details: ?std.json.Value = null;
    if (err_obj.get("details")) |d| {
        details = d;
        if (jsonObject(d)) |dobj| if (dobj.get("violations")) |vs| switch (vs) {
            .array => |arr| {
                var list: std.ArrayList(errors.Violation) = .empty;
                for (arr.items) |item| {
                    const o = jsonObject(item) orelse continue;
                    const reason = jsonString(o.get("reason")) orelse continue;
                    try list.append(a, .{
                        .path = jsonString(o.get("path")),
                        .reason = reason,
                        .message = jsonString(o.get("message")) orelse "",
                    });
                }
                violations = try list.toOwnedSlice(a);
            },
            else => {},
        };
    }

    out.result = .{ .api_error = .{
        .status = status,
        .code = code,
        .known_code = errors.isKnownCode(code),
        .message = message,
        .ids = .{ .request_id = final_request_id, .trace_id = trace_id },
        .body_request_id = shown_body_id,
        .request_id_mismatch = mismatch,
        .request_id_header_valid = request_id_header_valid,
        .retryable = retryable,
        .retry_after_seconds = retry_after,
        .commit_state = commit_state,
        .violations = violations,
        .details = details,
    } };
}

fn malformed(comptime T: type, out: *errors.Outcome(T), kind: errors.ProtocolKind, status: u16, ids: errors.Ids) void {
    out.result = .{ .protocol_error = .{ .kind = kind, .status = status, .ids = ids } };
}
