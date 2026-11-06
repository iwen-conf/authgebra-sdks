//! Test-only HTTP/1.1 mock server on a loopback port. One request per connection.
//! Steps are consumed by successive connections; the last step repeats.

const std = @import("std");
const Io = std.Io;
const Allocator = std.mem.Allocator;

pub const Step = union(enum) {
    /// Complete raw HTTP response bytes.
    respond: []const u8,
    /// Accept, read the request, then never answer (until the client gives up).
    stall,
    /// Accept, read the request, then close without answering.
    drop,
};

pub const Captured = struct {
    head: []u8,
    body: []u8,
};

pub const Mock = struct {
    gpa: Allocator,
    io: Io,
    server: Io.net.Server,
    steps: []const Step,
    thread: std.Thread,
    stopping: std.atomic.Value(bool) = .init(false),
    accepted: std.atomic.Value(usize) = .init(0),
    mutex: Io.Mutex = .init,
    captured: std.ArrayList(Captured) = .empty,

    pub fn start(gpa: Allocator, io: Io, steps: []const Step) !*Mock {
        const self = try gpa.create(Mock);
        errdefer gpa.destroy(self);
        const addr = try Io.net.IpAddress.parse("127.0.0.1", 0);
        var server = try addr.listen(io, .{ .reuse_address = true });
        errdefer server.deinit(io);
        self.* = .{ .gpa = gpa, .io = io, .server = server, .steps = steps, .thread = undefined };
        self.thread = try std.Thread.spawn(.{}, serve, .{self});
        return self;
    }

    pub fn port(self: *const Mock) u16 {
        return self.server.socket.address.getPort();
    }

    pub fn baseUrl(self: *const Mock, buf: []u8) []const u8 {
        return std.fmt.bufPrint(buf, "http://127.0.0.1:{d}", .{self.port()}) catch unreachable;
    }

    pub fn connections(self: *const Mock) usize {
        return self.accepted.load(.acquire);
    }

    pub fn stop(self: *Mock) void {
        self.stopping.store(true, .release);
        // Wake the blocking accept().
        var addr = Io.net.IpAddress.parse("127.0.0.1", self.port()) catch unreachable;
        if (addr.connect(self.io, .{ .mode = .stream })) |s| s.close(self.io) else |_| {}
        self.thread.join();
        self.server.deinit(self.io);
        for (self.captured.items) |c| {
            self.gpa.free(c.head);
            self.gpa.free(c.body);
        }
        self.captured.deinit(self.gpa);
        self.gpa.destroy(self);
    }

    /// The `index`-th captured request (head text and body bytes).
    pub fn request(self: *Mock, index: usize) ?Captured {
        self.mutex.lockUncancelable(self.io);
        defer self.mutex.unlock(self.io);
        if (index >= self.captured.items.len) return null;
        return self.captured.items[index];
    }
};

fn serve(self: *Mock) void {
    const io = self.io;
    while (true) {
        const stream = self.server.accept(io) catch return;
        if (self.stopping.load(.acquire)) {
            stream.close(io);
            return;
        }
        const n = self.accepted.fetchAdd(1, .acq_rel);
        const step = self.steps[@min(n, self.steps.len - 1)];
        handle(self, stream, step) catch {};
        stream.close(io);
    }
}

fn handle(self: *Mock, stream: Io.net.Stream, step: Step) !void {
    const io = self.io;
    var rbuf: [4096]u8 = undefined;
    var reader = stream.reader(io, &rbuf);
    const r = &reader.interface;

    var head: std.ArrayList(u8) = .empty;
    errdefer head.deinit(self.gpa);
    while (!std.mem.endsWith(u8, head.items, "\r\n\r\n")) {
        if (head.items.len > 64 * 1024) return error.HeadTooLarge;
        const b = try r.takeByte();
        try head.append(self.gpa, b);
    }
    var content_length: usize = 0;
    var lines = std.mem.splitSequence(u8, head.items, "\r\n");
    while (lines.next()) |line| {
        if (std.ascii.startsWithIgnoreCase(line, "content-length:")) {
            content_length = std.fmt.parseInt(usize, std.mem.trim(u8, line["content-length:".len..], " \t"), 10) catch 0;
        }
    }
    const body = try self.gpa.alloc(u8, content_length);
    errdefer self.gpa.free(body);
    try r.readSliceAll(body);

    {
        self.mutex.lockUncancelable(io);
        defer self.mutex.unlock(io);
        try self.captured.append(self.gpa, .{ .head = try head.toOwnedSlice(self.gpa), .body = body });
    }

    switch (step) {
        .respond => |bytes| {
            var wbuf: [1024]u8 = undefined;
            var writer = stream.writer(io, &wbuf);
            try writer.interface.writeAll(bytes);
            try writer.interface.flush();
        },
        .drop => {},
        .stall => {
            // Block until the client closes the connection.
            while (true) _ = r.takeByte() catch break;
        },
    }
}
