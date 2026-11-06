//! Admin surface (users, roles, permissions, resources, attributes, policies, bindings,
//! relation definitions/tuples, audit, auth/session/MFA, dashboard, setup, health).
//! Generated from the reviewed OpenAPI export — see `admin_ops.zig`.

const ops = @import("admin_ops.zig");

pub const AdminClient = ops.AdminClient;
pub const RawAdminClient = ops.RawAdminClient;
pub const models = @import("models.zig");
pub const Operation = ops.Operation;
pub const operations = ops.operations;
pub const Json = ops.Json;
