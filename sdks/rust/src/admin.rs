//! Administrative client. Every operation in the OpenAPI document has a method in
//! `generated/admin.rs` (produced by `tools/gen.py`; a test fails if one is missing).

use crate::client::Client;

/// All `/api/v1` operations: users, roles, permissions, resources, attributes, policies,
/// bindings, relations, audit, auth/MFA/sessions, setup, health, dashboard.
#[derive(Debug, Clone)]
pub struct AdminClient {
    pub(crate) client: Client,
}

impl AdminClient {
    pub(crate) fn new(client: Client) -> Self {
        Self { client }
    }
}
