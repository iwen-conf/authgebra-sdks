//! One HTTP exchange over `std.http.Client` with a hard total deadline.
//!
//! `std.http.Client.fetch` drops response headers, so this uses `request()`/`receiveHead()`
//! directly. The deadline races the exchange against a sleep with `std.Io.Select`; the loser is
//! cancelled and any response it produced is freed.

const std = @import("std");
const Io = std.Io;
const http = std.http;
const Allocator = std.mem.Allocator;
const errors = @import("errors.zig");

pub const Header = struct { name: []u8, value: []u8 };

/// A complete response. Memory comes from the allocator passed to `execute`.
pub const RawResponse = struct {
    status: u16,
    headers: []Header,
    body: []u8,

    pub fn deinit(self: *RawResponse, gpa: Allocator) void {
        for (self.headers) |h| {
            gpa.free(h.name);
            gpa.free(h.value);
        }
        gpa.free(self.headers);
        gpa.free(self.body);
        self.* = undefined;
    }

    pub fn header(self: *const RawResponse, name: []const u8) ?[]const u8 {
        for (self.headers) |h| {
            if (std.ascii.eqlIgnoreCase(h.name, name)) return h.value;
        }
        return null;
    }
};

pub const Failure = struct {
    kind: errors.TransportKind,
    detail: []const u8,
};

pub const Exchange = union(enum) {
    response: RawResponse,
    failure: Failure,

    pub fn deinit(self: *Exchange, gpa: Allocator) void {
        switch (self.*) {
            .response => |*r| r.deinit(gpa),
            .failure => {},
        }
    }
};

pub const Prepared = struct {
    method: http.Method,
    url: []const u8,
    /// Extra request headers (already validated: no CR/LF).
    headers: []const http.Header,
    authorization: ?[]const u8 = null,
    user_agent: []const u8,
    payload: ?[]const u8 = null,
    max_response_bytes: usize,
};

fn fail(kind: errors.TransportKind, err: anyerror) Exchange {
    return .{ .failure = .{ .kind = kind, .detail = @errorName(err) } };
}

fn exchangeTask(client: *http.Client, gpa: Allocator, prepared: Prepared) Exchange {
    const uri = std.Uri.parse(prepared.url) catch |err| return fail(.invalid_url, err);

    var req = client.request(prepared.method, uri, .{
        .redirect_behavior = .unhandled,
        .keep_alive = true,
        .headers = .{
            .user_agent = .{ .override = prepared.user_agent },
            .accept_encoding = .omit,
            .authorization = if (prepared.authorization) |a| .{ .override = a } else .default,
            .content_type = if (prepared.payload != null) .{ .override = "application/json" } else .default,
        },
        .extra_headers = prepared.headers,
    }) catch |err| return switch (err) {
        error.Timeout => fail(.timeout, err),
        error.Canceled => fail(.canceled, err),
        error.TlsInitializationFailed, error.CertificateBundleLoadFailure => fail(.tls, err),
        error.UnsupportedUriScheme, error.UriMissingHost, error.InvalidHostName => fail(.invalid_url, err),
        else => fail(.connection_failed, err),
    };
    defer req.deinit();

    // Everything below happens after a connection exists: a failure may mean the server saw
    // (part of) the request, so it is reported as connection_reset, never as "not sent".
    send: {
        if (prepared.payload != null or prepared.method.requestHasBody()) {
            const payload = prepared.payload orelse "";
            req.transfer_encoding = .{ .content_length = payload.len };
            var body = req.sendBodyUnflushed(&.{}) catch |err| return fail(.connection_reset, err);
            body.writer.writeAll(payload) catch |err| return fail(.connection_reset, err);
            body.end() catch |err| return fail(.connection_reset, err);
            req.connection.?.flush() catch |err| return fail(.connection_reset, err);
        } else {
            req.sendBodiless() catch |err| return fail(.connection_reset, err);
        }
        break :send;
    }

    var response = req.receiveHead(&.{}) catch |err| return switch (err) {
        error.Canceled => fail(.canceled, err),
        else => fail(.connection_reset, err),
    };

    const status: u16 = @backingInt(response.head.status);

    var headers: std.ArrayList(Header) = .empty;
    errdefer freeHeaders(gpa, &headers);
    var it = response.head.iterateHeaders();
    while (it.next()) |h| {
        const name = gpa.dupe(u8, h.name) catch return fail(.other, error.OutOfMemory);
        const value = gpa.dupe(u8, h.value) catch {
            gpa.free(name);
            return fail(.other, error.OutOfMemory);
        };
        headers.append(gpa, .{ .name = name, .value = value }) catch {
            gpa.free(name);
            gpa.free(value);
            return fail(.other, error.OutOfMemory);
        };
    }

    var transfer_buffer: [256]u8 = undefined;
    const reader = response.reader(&transfer_buffer);
    const body = reader.allocRemaining(gpa, .limited(prepared.max_response_bytes)) catch |err| {
        freeHeaders(gpa, &headers);
        return switch (err) {
            error.StreamTooLong => fail(.response_too_large, err),
            error.OutOfMemory => fail(.other, err),
            error.ReadFailed => fail(.connection_reset, response.bodyErr() orelse err),
        };
    };

    const owned = headers.toOwnedSlice(gpa) catch {
        freeHeaders(gpa, &headers);
        gpa.free(body);
        return fail(.other, error.OutOfMemory);
    };
    return .{ .response = .{ .status = status, .headers = owned, .body = body } };
}

fn freeHeaders(gpa: Allocator, headers: *std.ArrayList(Header)) void {
    for (headers.items) |h| {
        gpa.free(h.name);
        gpa.free(h.value);
    }
    headers.deinit(gpa);
}

const Race = union(enum) {
    done: Exchange,
    deadline: void,
};

fn deadlineTask(io: Io, timeout_ms: u32) void {
    io.sleep(.fromMilliseconds(timeout_ms), .awake) catch {};
}

/// Run one exchange with a hard total deadline of `timeout_ms` (connect + TLS + send + receive).
pub fn execute(
    client: *http.Client,
    io: Io,
    gpa: Allocator,
    prepared: Prepared,
    timeout_ms: u32,
) Exchange {
    var buffer: [2]Race = undefined;
    var select: Io.Select(Race) = .init(io, &buffer);

    select.concurrent(.done, exchangeTask, .{ client, gpa, prepared }) catch {
        // No concurrency available: the deadline could not be enforced, so refuse to run the
        // exchange unbounded.
        return fail(.other, error.ConcurrencyUnavailable);
    };
    select.concurrent(.deadline, deadlineTask, .{ io, timeout_ms }) catch {
        // Could not start the timer: refuse to run unbounded.
        while (select.cancel()) |leftover| switch (leftover) {
            .done => |ex| {
                var e = ex;
                e.deinit(gpa);
            },
            .deadline => {},
        };
        return fail(.other, error.ConcurrencyUnavailable);
    };

    const first = select.await() catch {
        drain(&select, gpa);
        return fail(.canceled, error.Canceled);
    };
    switch (first) {
        .done => |ex| {
            drain(&select, gpa);
            return ex;
        },
        .deadline => {
            drain(&select, gpa);
            return fail(.timeout, error.Timeout);
        },
    }
}

fn drain(select: *Io.Select(Race), gpa: Allocator) void {
    while (select.cancel()) |leftover| switch (leftover) {
        .done => |ex| {
            var e = ex;
            e.deinit(gpa);
        },
        .deadline => {},
    };
}
