//! Real certificate, hostname and TLS handshake matrix; run via integration/tls.py.
use authgebra_client::{Call, CallOptions, Client, Error, Method, RetryPolicy, TransportKind};
use serde_json::Value;

#[tokio::test]
#[ignore = "NOT_RUN unless selected with the temporary TLS fixture"]
async fn real_tls_verification() {
    let ca = std::fs::read(std::env::var("SDK_TLS_CA").expect("TLS fixture CA")).unwrap();
    for (origin, trusted, succeeds) in [
        ("SDK_TLS_VALID_URL", false, false),
        ("SDK_TLS_MISMATCH_URL", true, false),
        ("SDK_TLS_PLAIN_URL", true, false),
        ("SDK_TLS_VALID_URL", true, true),
    ] {
        let mut builder = Client::builder()
            .base_url(std::env::var(origin).expect("TLS fixture origin"))
            .retry_policy(RetryPolicy::none());
        if trusted {
            let http = reqwest::Client::builder()
                .add_root_certificate(reqwest::Certificate::from_pem(&ca).unwrap())
                .build()
                .unwrap();
            builder = builder.http_client(http);
        }
        let client = builder.build().unwrap();
        let result = client
            .execute::<Value>(
                Call::new(Method::GET, vec!["tls-test".into()]),
                &CallOptions::new(),
            )
            .await;
        if succeeds {
            assert_eq!(result.unwrap().data["tls"], true);
        } else {
            match result {
                Err(Error::Transport(error)) => {
                    assert_eq!(error.kind, TransportKind::Tls, "{origin}: {error}");
                    assert_eq!(error.attempts, 1);
                    assert_eq!(error.commit_state, None);
                }
                other => panic!("{origin} accepted an invalid TLS connection: {other:?}"),
            }
        }
    }
}
