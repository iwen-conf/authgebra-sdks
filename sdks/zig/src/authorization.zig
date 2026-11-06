//! AuthorizationClient: Check, BatchCheck, ListResources, Explain, Revision.
//!
//! The SDK only calls the server. `decision = deny` is a normal `ok` result; a 503 NOT_READY is
//! an `api_error` and is never converted into a Deny.

const std = @import("std");
const Allocator = std.mem.Allocator;
const client_mod = @import("client.zig");
const errors = @import("errors.zig");
const models = @import("models.zig");

pub const Client = client_mod.Client;

pub const ResourceRef = struct {
    resource_type_code: []const u8,
    external_id: []const u8,
};

pub const Decision = enum { allow, deny };

pub const PolicyRef = struct { code: []const u8, version: i64 };
pub const Context = models.CheckRequestContext;

pub const CheckRequest = struct {
    subject_id: []const u8,
    permission: []const u8,
    resource: ?ResourceRef = null,
    /// Typed context values (object). Omitted when null.
    context: ?Context = null,
};

pub const CheckResponse = struct {
    decision: Decision,
    reason_code: []const u8,
    matched_policy: ?PolicyRef = null,
};

pub const BatchCheckRequest = struct {
    subject_id: []const u8,
    permission: []const u8,
    /// 1..=10000. An empty array is a 400 on the server, not an empty result.
    resources: []const ResourceRef,
    context: ?Context = null,
};

pub const BatchDecision = struct {
    resource: ResourceRef,
    reason_code: []const u8,
    matched_policy: ?PolicyRef = null,
};

pub const BatchCheckResponse = struct {
    allowed: []const BatchDecision,
    denied: []const BatchDecision,
};

pub const ExplainTraceOptions = struct { max_steps: ?u32 = null };

pub const ExplainRequest = struct {
    subject_id: []const u8,
    permission: []const u8,
    resource: ?ResourceRef = null,
    context: ?Context = null,
    trace: ?ExplainTraceOptions = null,
};

pub const ExplainResponse = struct {
    decision: Decision,
    reason_code: []const u8,
    matched_policy: ?PolicyRef = null,
    policies: []const models.AuthzPolicyExplanation,
    truncated: bool,
    total_steps: i64,
};

pub const RevisionStatus = enum { in_sync, rebuilding, stale };

pub const RevisionResponse = struct {
    db_revision: i64,
    snapshot_generation: i64,
    status: RevisionStatus,
};

pub const ListResourcesRequest = struct {
    subject_id: []const u8,
    permission: []const u8,
    resource_type: []const u8,
    context: ?Context = null,
    /// Opaque; copy it verbatim from `page.next_cursor`.
    cursor: ?[]const u8 = null,
    limit: ?u32 = null,
};

fn encode(gpa: Allocator, value: anytype) ![]u8 {
    return std.json.Stringify.valueAlloc(gpa, value, .{ .emit_null_optional_fields = false });
}

pub const AuthorizationClient = struct {
    client: *Client,

    pub fn init(client: *Client) AuthorizationClient {
        return .{ .client = client };
    }

    fn post(self: AuthorizationClient, comptime T: type, path: []const u8, body_value: anytype, opts: client_mod.CallOptions, paged: bool) !errors.Outcome(T) {
        const body = try encode(self.client.gpa, body_value);
        defer self.client.gpa.free(body);
        return self.client.send(T, .{ .method = .POST, .path = path, .body = body, .read_only = true, .paged_response = paged, .options = opts });
    }

    pub fn check(self: AuthorizationClient, req: CheckRequest, opts: client_mod.CallOptions) !errors.Outcome(CheckResponse) {
        return self.post(CheckResponse, "/api/v1/authz/check", req, opts, false);
    }

    pub fn batchCheck(self: AuthorizationClient, req: BatchCheckRequest, opts: client_mod.CallOptions) !errors.Outcome(BatchCheckResponse) {
        return self.post(BatchCheckResponse, "/api/v1/authz/batch-check", req, opts, false);
    }

    pub fn explain(self: AuthorizationClient, req: ExplainRequest, opts: client_mod.CallOptions) !errors.Outcome(ExplainResponse) {
        return self.post(ExplainResponse, "/api/v1/authz/explain", req, opts, false);
    }

    /// One page. `page.next_cursor` / `page.has_more` are on the `ok` result.
    pub fn listResources(self: AuthorizationClient, req: ListResourcesRequest, opts: client_mod.CallOptions) !errors.Outcome([]const ResourceRef) {
        return self.post([]const ResourceRef, "/api/v1/authz/list-resources", req, opts, true);
    }

    pub fn revision(self: AuthorizationClient, opts: client_mod.CallOptions) !errors.Outcome(RevisionResponse) {
        return self.client.send(RevisionResponse, .{ .method = .GET, .path = "/api/v1/authz/revision", .options = opts });
    }

    pub fn pager(self: AuthorizationClient, req: ListResourcesRequest, opts: PagerOptions) ResourcePager {
        return .{ .authz = self, .request = req, .options = opts, .cursor = null, .pages = 0, .done = false };
    }
};

pub const PagerOptions = struct {
    /// Hard stop against runaway iteration.
    max_pages: u32 = 1000,
    call: client_mod.CallOptions = .{},
};

pub const PagerError = error{PageLimitExceeded} || client_mod.SendError || std.json.Stringify.Error;

/// Page iterator. It never restarts on its own: a `CURSOR_STALE` (or any other error) is
/// returned to the caller as an `api_error` outcome and iteration ends. Restarting from page one
/// is the caller's explicit decision (create a new pager). Cancellation: cancel the calling task
/// through `std.Io`; each page call honours the client's deadline.
pub const ResourcePager = struct {
    authz: AuthorizationClient,
    request: ListResourcesRequest,
    options: PagerOptions,
    /// Owned copy of the last `next_cursor`.
    cursor: ?[]u8,
    pages: u32,
    done: bool,

    pub fn deinit(self: *ResourcePager) void {
        if (self.cursor) |c| self.authz.client.gpa.free(c);
        self.cursor = null;
    }

    /// Next page, or null when exhausted. The caller owns (and must `deinit`) the outcome.
    pub fn next(self: *ResourcePager) !?errors.Outcome([]const ResourceRef) {
        if (self.done) return null;
        if (self.pages >= self.options.max_pages) return error.PageLimitExceeded;
        var req = self.request;
        if (self.cursor) |c| req.cursor = c;
        var outcome = try self.authz.listResources(req, self.options.call);
        errdefer outcome.deinit();
        self.pages += 1;
        switch (outcome.result) {
            .ok => |ok| {
                const more = if (ok.page) |p| p.has_more and p.next_cursor != null else false;
                const copy: ?[]u8 = if (more) try self.authz.client.gpa.dupe(u8, ok.page.?.next_cursor.?) else null;
                if (self.cursor) |c| self.authz.client.gpa.free(c);
                self.cursor = copy;
                if (!more) self.done = true;
            },
            else => self.done = true,
        }
        return outcome;
    }
};
