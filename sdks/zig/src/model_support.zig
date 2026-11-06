//! JSON value ownership follows the surrounding Outcome arena. Request values remain caller-owned.
const std = @import("std");

pub fn preserveIntegers(value: *std.json.Value) void {
    switch (value.*) {
        .number_string => |text| if (std.fmt.parseInt(i64, text, 10)) |integer| {
            value.* = .{ .integer = integer };
        } else |_| {},
        .object => |*object| {
            var iter = object.iterator();
            while (iter.next()) |item| preserveIntegers(item.value_ptr);
        },
        .array => |*array| for (array.items) |*item| preserveIntegers(item),
        else => {},
    }
}

pub fn parseAny(a: std.mem.Allocator, source: anytype, options: std.json.ParseOptions) !std.json.Value {
    var exact = options;
    exact.parse_numbers = false;
    var value = try std.json.innerParse(std.json.Value, a, source, exact);
    preserveIntegers(&value);
    return value;
}

/// Arbitrary JSON only where the reviewed schema leaves its value unconstrained.
pub const JsonValue = struct {
    value: std.json.Value,

    pub fn jsonStringify(self: @This(), out: anytype) !void {
        try out.write(self.value);
    }

    pub fn jsonParse(a: std.mem.Allocator, source: anytype, options: std.json.ParseOptions) !@This() {
        return jsonParseFromValue(a, try parseAny(a, source, options), options);
    }

    pub fn jsonParseFromValue(a: std.mem.Allocator, value: std.json.Value, options: std.json.ParseOptions) !@This() {
        return .{ .value = try std.json.parseFromValueLeaky(std.json.Value, a, value, options) };
    }
};

/// Optional fields distinguish omission, explicit null and a value, including false/0/empty arrays.
pub fn Optional(comptime T: type) type {
    return union(enum) {
        absent,
        null,
        value: T,

        pub const is_optional_field = true;

        pub fn jsonStringify(self: @This(), out: anytype) !void {
            switch (self) {
                .absent => return error.WriteFailed,
                .null => try out.write(null),
                .value => |v| try out.write(v),
            }
        }

        pub fn jsonParse(a: std.mem.Allocator, source: anytype, options: std.json.ParseOptions) !@This() {
            return jsonParseFromValue(a, try parseAny(a, source, options), options);
        }

        pub fn jsonParseFromValue(a: std.mem.Allocator, value: std.json.Value, options: std.json.ParseOptions) !@This() {
            if (value == .null) return .null;
            return .{ .value = try std.json.parseFromValueLeaky(T, a, value, options) };
        }
    };
}

pub fn isOptional(comptime T: type) bool {
    return switch (@typeInfo(T)) {
        .@"union" => @hasDecl(T, "is_optional_field"),
        else => false,
    };
}

pub fn validate(value: anytype) anyerror!void {
    const T = @TypeOf(value);
    switch (@typeInfo(T)) {
        .@"struct", .@"union" => if (@hasDecl(T, "validate")) try value.validate(),
        .optional => if (value) |v| try validate(v),
        .pointer => |info| if (info.size == .slice and info.child != u8) {
            for (value) |v| try validate(v);
        },
        else => {},
    }
}

pub fn writeObject(value: anytype, out: anytype) !void {
    try out.beginObject();
    const info = @typeInfo(@TypeOf(value)).@"struct";
    inline for (info.field_names, info.field_types) |name, T| {
        const v = @field(value, name);
        if (comptime isOptional(T)) {
            if (v != .absent) {
                try out.objectField(name);
                try out.write(v);
            }
        } else {
            try out.objectField(name);
            try out.write(v);
        }
    }
    try out.endObject();
}

/// JSON objects with dynamic keys and a schema-defined element type.
pub fn Object(comptime T: type) type {
    return struct {
        entries: []const Entry,
        pub const Entry = struct { key: []const u8, value: T };

        pub fn jsonStringify(self: @This(), out: anytype) !void {
            try out.beginObject();
            for (self.entries) |entry| {
                try out.objectField(entry.key);
                try out.write(entry.value);
            }
            try out.endObject();
        }

        pub fn jsonParse(a: std.mem.Allocator, source: anytype, options: std.json.ParseOptions) !@This() {
            return jsonParseFromValue(a, try parseAny(a, source, options), options);
        }

        pub fn jsonParseFromValue(a: std.mem.Allocator, value: std.json.Value, options: std.json.ParseOptions) !@This() {
            if (value != .object) return error.UnexpectedToken;
            const entries = try a.alloc(Entry, value.object.count());
            var iter = value.object.iterator();
            var i: usize = 0;
            while (iter.next()) |entry| : (i += 1) {
                entries[i] = .{ .key = try a.dupe(u8, entry.key_ptr.*), .value = try std.json.parseFromValueLeaky(T, a, entry.value_ptr.*, options) };
            }
            return .{ .entries = entries };
        }
    };
}

/// Exact ASCII language of ^[a-z][a-z0-9_.-]{0,63}$; no newline suffix accepted.
pub fn validIamCode(value: []const u8) bool {
    if (value.len == 0 or value.len > 64 or value[0] < 'a' or value[0] > 'z') return false;
    for (value[1..]) |char| {
        if (!(char >= 'a' and char <= 'z') and !(char >= '0' and char <= '9') and char != '_' and char != '.' and char != '-') return false;
    }
    return true;
}

/// JSON Schema string equality is byte/value equality, not UUID normalization.
pub fn uniqueUuids(values: []const []const u8) bool {
    for (values, 0..) |left, i| {
        for (values[i + 1 ..]) |right| {
            if (std.mem.eql(u8, left, right)) return false;
        }
    }
    return true;
}
