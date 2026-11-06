//! Page through `list-resources`. A stale cursor is returned to you, never retried silently.

use authgebra_client::models::ListResourcesRequest;
use authgebra_client::{Client, Error, ErrorCode};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let client = Client::builder()
        .base_url(std::env::var("AUTHGEBRA_URL")?)
        .bearer_token(std::env::var("AUTHGEBRA_TOKEN")?)
        .build()?;
    let request = ListResourcesRequest {
        subject_id: std::env::var("AUTHGEBRA_SUBJECT")?,
        permission: "document:read".into(),
        resource_type: "document".into(),
        context: None,
        cursor: None,
        limit: Some(100),
    };

    let mut pager = client
        .authorization()
        .list_resources_pager(request)
        .max_pages(50);
    let mut total = 0usize;
    loop {
        match pager.next_page().await {
            Ok(Some(page)) => total += page.data.len(),
            Ok(None) => break,
            Err(Error::Api(api)) if api.known_code == Some(ErrorCode::CursorStale) => {
                // The authorization snapshot moved. Pages from different generations must not
                // be mixed: restarting from page one is your explicit decision.
                eprintln!(
                    "cursor stale after {total} items; restart if a fresh listing is acceptable"
                );
                return Ok(());
            }
            Err(other) => return Err(other.into()),
        }
    }
    println!("{total} resources");
    Ok(())
}
