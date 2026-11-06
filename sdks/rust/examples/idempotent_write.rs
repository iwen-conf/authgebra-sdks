//! A write with an idempotency key, handling `AUTHZ_RELOAD_PENDING` and unknown outcomes.

use authgebra_client::{CallOptions, Client, CommitState, Error};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let client = Client::builder()
        .base_url(std::env::var("AUTHGEBRA_URL")?)
        .bearer_token(std::env::var("AUTHGEBRA_TOKEN")?)
        .build()?;

    // Generate the key once per logical operation and persist it with your own record, so a
    // retry after a crash reuses it.
    let key = "create-alice-2026-11-04";
    let opts = CallOptions::new().idempotency_key(key); // add .retry_writes(true) to opt in to bounded resends

    let args = authgebra_client::generated::admin::CreateUserArgs {
        body: authgebra_client::generated::models::CreateUserRequest {
            username: "alice".into(),
            display_name: "Alice".into(),
            initial_password: std::env::var("NEW_USER_PASSWORD")?,
            email: None,
            role_ids: None,
        },
    };
    match client.admin().create_user(args, &opts).await {
        Ok(created) => println!(
            "created (replayed={}), revision={:?}",
            created.replayed,
            created.authz_revision()
        ),
        Err(error) => match error.commit_state() {
            // Committed but not yet published: the user EXISTS. Do not resend; wait for the
            // revision to be published (poll `revision()`), then report success.
            Some(CommitState::CommittedUnpublished) => {
                eprintln!("committed, publication pending: {error}")
            }
            // Timeout/connection loss: unknown. Reconcile with the same key or by reading the user.
            Some(CommitState::Unknown) => {
                eprintln!("outcome unknown, reconcile with key {key}: {error}")
            }
            _ => {
                return Err(match error {
                    Error::Api(a) => (*a).into(),
                    other => other.into(),
                });
            }
        },
    }
    Ok(())
}
