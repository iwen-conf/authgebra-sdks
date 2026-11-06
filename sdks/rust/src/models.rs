//! Hand-written, typed models for the authorization decision endpoints.
//! Revisions are `i64` end to end (parsed from the raw bytes, never via `f64`).

use std::fmt;

use serde::{Deserialize, Deserializer, Serialize, Serializer};

/// `allow` / `deny`. A value this SDK does not know is preserved, never coerced.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum Decision {
    /// Permitted.
    Allow,
    /// Not permitted. This is a **successful** answer, not an error.
    Deny,
    /// A value from a newer server.
    Other(String),
}

impl Decision {
    /// Wire string.
    #[must_use]
    pub fn as_str(&self) -> &str {
        match self {
            Self::Allow => "allow",
            Self::Deny => "deny",
            Self::Other(value) => value,
        }
    }

    /// `true` only for an explicit `allow`.
    #[must_use]
    pub fn is_allow(&self) -> bool {
        matches!(self, Self::Allow)
    }
}

impl fmt::Display for Decision {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl Serialize for Decision {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for Decision {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        let raw = String::deserialize(d)?;
        Ok(match raw.as_str() {
            "allow" => Self::Allow,
            "deny" => Self::Deny,
            _ => Self::Other(raw),
        })
    }
}

/// Publication state reported by the revision endpoint.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum RevisionStatus {
    /// Snapshot matches the database revision.
    InSync,
    /// A newer revision is being published.
    Rebuilding,
    /// The snapshot is behind.
    Stale,
    /// A value from a newer server.
    Other(String),
}

impl<'de> Deserialize<'de> for RevisionStatus {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        let raw = String::deserialize(d)?;
        Ok(match raw.as_str() {
            "in_sync" => Self::InSync,
            "rebuilding" => Self::Rebuilding,
            "stale" => Self::Stale,
            _ => Self::Other(raw),
        })
    }
}

/// A resource reference.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResourceRef {
    /// Resource type code.
    pub resource_type_code: String,
    /// External id of the resource.
    pub external_id: String,
}

/// A matched policy.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PolicyRef {
    /// Policy code.
    pub code: String,
    /// Policy version.
    pub version: i64,
}

/// Typed context values. Absent, `null` and `{}` are equivalent on the server.
pub type Context = serde_json::Map<String, serde_json::Value>;

/// `POST /api/v1/authz/check`.
#[derive(Debug, Clone, Serialize)]
pub struct CheckRequest {
    /// Subject UUID.
    pub subject_id: String,
    /// Permission code.
    pub permission: String,
    /// Resource; omit for a route-level check.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resource: Option<ResourceRef>,
    /// Context attributes.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<Context>,
}

/// `check` result.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct CheckResponse {
    /// The decision.
    pub decision: Decision,
    /// Reason code (open string; new reasons may appear).
    pub reason_code: String,
    /// Matched policy, if any.
    #[serde(default)]
    pub matched_policy: Option<PolicyRef>,
}

/// `POST /api/v1/authz/batch-check`. `resources` must have 1..=10000 entries.
#[derive(Debug, Clone, Serialize)]
pub struct BatchCheckRequest {
    /// Subject UUID.
    pub subject_id: String,
    /// Permission code.
    pub permission: String,
    /// Resources to decide.
    pub resources: Vec<ResourceRef>,
    /// Context attributes.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<Context>,
}

/// One decided resource in a batch.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct BatchDecision {
    /// The resource.
    pub resource: ResourceRef,
    /// Reason code.
    pub reason_code: String,
    /// Matched policy, if any.
    #[serde(default)]
    pub matched_policy: Option<PolicyRef>,
}

/// `batch_check` result, in the server's order.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct BatchCheckResponse {
    /// Allowed resources.
    pub allowed: Vec<BatchDecision>,
    /// Denied resources.
    pub denied: Vec<BatchDecision>,
}

/// Explain trace options.
#[derive(Debug, Clone, Default, Serialize)]
pub struct ExplainTraceOptions {
    /// Step cap.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_steps: Option<i32>,
}

/// `POST /api/v1/authz/explain`.
#[derive(Debug, Clone, Serialize)]
pub struct ExplainRequest {
    /// Subject UUID.
    pub subject_id: String,
    /// Permission code.
    pub permission: String,
    /// Resource; omit for a route-level check.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resource: Option<ResourceRef>,
    /// Context attributes.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<Context>,
    /// Trace options.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trace: Option<ExplainTraceOptions>,
}

/// One node of an explanation tree.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct ExplainNode {
    /// Node id.
    pub node_id: i32,
    /// Node kind.
    pub kind: String,
    /// Node result.
    pub result: String,
    /// Detail text (the server keeps attribute values out of it).
    pub detail: String,
    /// Path through the expression, if present.
    #[serde(default)]
    pub path: Option<Vec<serde_json::Value>>,
}

/// A policy and its evaluation nodes.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct PolicyExplanation {
    /// The policy.
    pub policy: PolicyRef,
    /// Evaluation nodes.
    pub nodes: Vec<ExplainNode>,
}

/// `explain` result.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct ExplainResponse {
    /// The decision.
    pub decision: Decision,
    /// Reason code.
    pub reason_code: String,
    /// Matched policy, if any.
    #[serde(default)]
    pub matched_policy: Option<PolicyRef>,
    /// Per-policy explanation.
    pub policies: Vec<PolicyExplanation>,
    /// Whether the trace was truncated.
    pub truncated: bool,
    /// Total evaluation steps.
    pub total_steps: i64,
}

/// `GET /api/v1/authz/revision` result.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct RevisionResponse {
    /// Database revision.
    pub db_revision: i64,
    /// Published snapshot generation.
    pub snapshot_generation: i64,
    /// Publication status.
    pub status: RevisionStatus,
}

/// `POST /api/v1/authz/list-resources`.
#[derive(Debug, Clone, Serialize)]
pub struct ListResourcesRequest {
    /// Subject UUID.
    pub subject_id: String,
    /// Permission code.
    pub permission: String,
    /// Resource type to list.
    pub resource_type: String,
    /// Context attributes.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<Context>,
    /// Opaque cursor from a previous page (bound to the query and generation).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cursor: Option<String>,
    /// Page size.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub limit: Option<i32>,
}
