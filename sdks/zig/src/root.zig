//! authgebra-zig — official Zig client for the Authgebra `/api/v1` HTTP API.
//!
//! The SDK is a thin, typed wrapper over HTTP: it never evaluates RBAC/ABAC/ReBAC itself.

pub const errors = @import("errors.zig");
pub const trace = @import("trace.zig");
pub const client = @import("client.zig");
pub const authorization = @import("authorization.zig");
pub const admin = @import("admin.zig");
pub const models = @import("models.zig");

pub const Client = client.Client;
pub const Config = client.Config;
pub const RetryPolicy = client.RetryPolicy;
pub const CallOptions = client.CallOptions;
pub const TokenProvider = client.TokenProvider;
pub const Outcome = errors.Outcome;
pub const ApiError = errors.ApiError;
pub const AuthorizationClient = authorization.AuthorizationClient;
pub const AdminClient = admin.AdminClient;
pub const RawAdminClient = admin.RawAdminClient;
pub const version = client.version;

test {
    _ = errors;
    _ = trace;
    _ = client;
    _ = authorization;
    _ = admin;
}
