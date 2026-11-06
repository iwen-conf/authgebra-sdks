//! `AUTHGEBRA_URL=https://authz.example.com AUTHGEBRA_TOKEN=... cargo run --example check`
//!
//! Shows the three outcomes: allow/deny are both `Ok`; `NOT_READY` is an `Err` that you must
//! handle with your own safe-deny policy; the trace id lets you find the call server-side.

use authgebra_client::models::{CheckRequest, ResourceRef};
use authgebra_client::{CallOptions, Client, Error, ErrorCode, TraceContext};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let client = Client::builder()
        .base_url(std::env::var("AUTHGEBRA_URL")?)
        .bearer_token(std::env::var("AUTHGEBRA_TOKEN")?)
        .build()?;

    let request = CheckRequest {
        subject_id: std::env::var("AUTHGEBRA_SUBJECT")?,
        permission: "document:read".into(),
        resource: Some(ResourceRef {
            resource_type_code: "document".into(),
            external_id: "doc-123".into(),
        }),
        context: None,
    };
    // Join your own trace so the call shows up in your distributed trace.
    let opts = CallOptions::new().trace(TraceContext::new_root());

    match client.authorization().check_with(&request, &opts).await {
        Ok(response) => {
            println!(
                "decision={} revision={:?} generation={:?} trace_id={:?}",
                response.data.decision,
                response.meta.map(|m| m.authz_revision),
                response.meta.and_then(|m| m.snapshot_generation),
                response.trace_id,
            );
        }
        Err(Error::Api(api)) if api.known_code == Some(ErrorCode::NotReady) => {
            // The system could not reach a trustworthy decision. This is NOT a deny from the
            // policy; apply your own fail-closed rule and keep the evidence for diagnosis.
            eprintln!(
                "authorization unavailable (trace_id={:?}): refusing the action",
                api.trace_id
            );
        }
        Err(other) => return Err(other.into()),
    }
    Ok(())
}
