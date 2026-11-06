//! Behavioural guarantees from sdks/README.md that the fixtures do not pin by themselves.

mod common;

use std::time::Duration;

use authgebra_client::models::{CheckRequest, ListResourcesRequest};
use authgebra_client::{
    Call, CallOptions, Client, CommitState, Error, ErrorCode, Method, ProtocolKind, RetryPolicy,
    Secret, TraceContext,
};
use common::{Mock, Script};
use serde_json::{Value, json};

const SUBJECT: &str = "00000000-0000-7000-8000-000000000001";

fn err_body(code: &str, extra: Value) -> Value {
    let mut error = json!({"code": code, "message": "m", "request_id": "req-abcdefgh"});
    error
        .as_object_mut()
        .unwrap()
        .extend(extra.as_object().unwrap().clone());
    json!({ "error": error })
}

fn create_user(
    client: &Client,
    opts: &CallOptions,
) -> impl std::future::Future<Output = authgebra_client::Result<authgebra_client::Response<Value>>>
{
    let call = Call::new(
        Method::POST,
        vec!["api".into(), "v1".into(), "users".into()],
    )
    .json(&json!({"username": "alice"}))
    .unwrap();
    let client = client.clone();
    let opts = opts.clone();
    async move { client.execute::<Value>(call, &opts).await }
}

fn check_req() -> CheckRequest {
    CheckRequest {
        subject_id: SUBJECT.into(),
        permission: "p:r".into(),
        resource: None,
        context: None,
    }
}

#[tokio::test]
async fn revisions_are_lossless_at_i64_extremes() {
    for value in [i64::MAX, i64::MIN, 9_007_199_254_740_993, 0, -1] {
        let body = format!(
            r#"{{"data":{{"decision":"allow","reason_code":"allow"}},"meta":{{"authz_revision":{value},"snapshot_generation":{value}}}}}"#
        );
        let mock = Mock::start(Script::Respond {
            status: 200,
            headers: vec![],
            body: body.into_bytes(),
        })
        .await;
        let r = common::client(&mock.url)
            .authorization()
            .check(&check_req())
            .await
            .unwrap();
        assert_eq!(r.meta.unwrap().authz_revision, value);
        assert_eq!(r.meta.unwrap().snapshot_generation, Some(value));
    }
}

#[tokio::test]
async fn a_write_is_never_resent_without_opt_in() {
    let mock = Mock::start(Script::json(503, &err_body("NOT_READY", json!({})))).await;
    let client = common::client(&mock.url);
    let err = create_user(&client, &CallOptions::new().idempotency_key("idem-0001"))
        .await
        .unwrap_err();
    assert_eq!(err.code(), Some(ErrorCode::NotReady));
    assert_eq!(mock.count(), 1, "default: exactly one attempt for a write");
}

#[tokio::test]
async fn opted_in_write_resends_same_key_and_same_bytes() {
    let ok = json!({"data": {"username": "alice"}});
    let mock = Mock::start(Script::Seq(vec![
        Script::json(503, &err_body("NOT_READY", json!({}))),
        Script::json(201, &ok),
    ]))
    .await;
    let client = common::client(&mock.url);
    let opts = CallOptions::new()
        .idempotency_key("idem-0002")
        .retry_writes(true);
    create_user(&client, &opts).await.unwrap();
    let requests = mock.requests();
    assert_eq!(requests.len(), 2);
    assert_eq!(requests[0].header("idempotency-key"), Some("idem-0002"));
    assert_eq!(requests[1].header("idempotency-key"), Some("idem-0002"));
    assert_eq!(requests[0].body, requests[1].body);
    assert_eq!(
        requests[0].header("x-request-id"),
        requests[1].header("x-request-id")
    );
}

#[tokio::test]
async fn retry_writes_without_a_key_is_rejected_before_sending() {
    let mock = Mock::start(Script::Hang).await;
    let client = common::client(&mock.url);
    let err = create_user(&client, &CallOptions::new().retry_writes(true))
        .await
        .unwrap_err();
    assert!(matches!(err, Error::Config(_)), "{err}");
    assert_eq!(mock.count(), 0);
}

#[tokio::test]
async fn committed_unpublished_is_never_resent_even_when_opted_in() {
    for code in ["AUTHZ_RELOAD_PENDING", "IDENTITY_RELOAD_PENDING"] {
        let body = err_body(
            code,
            json!({"retryable": false, "commit_state": "committed_unpublished"}),
        );
        let mock = Mock::start(Script::json(503, &body)).await;
        let client = common::client(&mock.url);
        let opts = CallOptions::new()
            .idempotency_key("idem-0003")
            .retry_writes(true);
        let err = create_user(&client, &opts).await.unwrap_err();
        assert_eq!(err.commit_state(), Some(CommitState::CommittedUnpublished));
        assert_eq!(mock.count(), 1, "{code} must not be resent");
    }
}

#[tokio::test]
async fn reads_retry_a_bounded_number_of_times() {
    let mock = Mock::start(Script::json(503, &err_body("NOT_READY", json!({})))).await;
    let err = common::client(&mock.url)
        .authorization()
        .check(&check_req())
        .await
        .unwrap_err();
    assert_eq!(err.code(), Some(ErrorCode::NotReady));
    assert_eq!(mock.count(), 3, "1 try + max_retries(2)");
    let none = Mock::start(Script::json(503, &err_body("NOT_READY", json!({})))).await;
    let client = Client::builder()
        .base_url(&none.url)
        .retry_policy(RetryPolicy::none())
        .build()
        .unwrap();
    client
        .authorization()
        .check(&check_req())
        .await
        .unwrap_err();
    assert_eq!(none.count(), 1);
}

#[tokio::test]
async fn forbidden_and_stale_cursor_are_never_retried() {
    for (status, code) in [
        (403, "FORBIDDEN"),
        (409, "CURSOR_STALE"),
        (401, "TOKEN_EXPIRED"),
    ] {
        let mock = Mock::start(Script::json(status, &err_body(code, json!({})))).await;
        common::client(&mock.url)
            .authorization()
            .check(&check_req())
            .await
            .unwrap_err();
        assert_eq!(mock.count(), 1, "{code}");
    }
}

fn page(items: &[&str], cursor: Option<&str>, generation: i64) -> Value {
    json!({
        "data": items.iter().map(|id| json!({"resource_type_code":"document","external_id":id})).collect::<Vec<_>>(),
        "page": {"next_cursor": cursor, "has_more": cursor.is_some()},
        "meta": {"authz_revision": generation, "snapshot_generation": generation}
    })
}

fn list_req() -> ListResourcesRequest {
    ListResourcesRequest {
        subject_id: SUBJECT.into(),
        permission: "p:r".into(),
        resource_type: "document".into(),
        context: None,
        cursor: None,
        limit: Some(2),
    }
}

#[tokio::test]
async fn pager_walks_pages_and_forwards_the_opaque_cursor() {
    let mock = Mock::start(Script::Seq(vec![
        Script::json(200, &page(&["a", "b"], Some("cursor-1"), 7)),
        Script::json(200, &page(&["c"], None, 7)),
    ]))
    .await;
    let mut pager = common::client(&mock.url)
        .authorization()
        .list_resources_pager(list_req());
    let mut seen = Vec::new();
    while let Some(p) = pager.next_page().await.unwrap() {
        seen.extend(p.data.into_iter().map(|r| r.external_id));
    }
    assert_eq!(seen, ["a", "b", "c"]);
    let second: Value = serde_json::from_slice(&mock.requests()[1].body).unwrap();
    assert_eq!(second["cursor"], "cursor-1");
    assert!(pager.next_page().await.unwrap().is_none());
}

#[tokio::test]
async fn pager_surfaces_cursor_stale_and_does_not_restart() {
    let mock = Mock::start(Script::Seq(vec![
        Script::json(200, &page(&["a"], Some("cursor-1"), 7)),
        Script::json(409, &err_body("CURSOR_STALE", json!({"retryable": false}))),
    ]))
    .await;
    let mut pager = common::client(&mock.url)
        .authorization()
        .list_resources_pager(list_req());
    pager.next_page().await.unwrap();
    let err = pager.next_page().await.unwrap_err();
    assert_eq!(err.code(), Some(ErrorCode::CursorStale));
    assert_eq!(mock.count(), 2, "no silent restart from the first page");
}

#[tokio::test]
async fn pager_refuses_to_merge_generations() {
    let mock = Mock::start(Script::Seq(vec![
        Script::json(200, &page(&["a"], Some("cursor-1"), 7)),
        Script::json(200, &page(&["b"], None, 8)),
    ]))
    .await;
    let mut pager = common::client(&mock.url)
        .authorization()
        .list_resources_pager(list_req());
    pager.next_page().await.unwrap();
    let err = pager.next_page().await.unwrap_err();
    assert!(
        matches!(err, Error::GenerationChanged { first: 7, now: 8 }),
        "{err}"
    );
}

#[tokio::test]
async fn pager_has_a_hard_page_cap() {
    let mock = Mock::start(Script::json(200, &page(&["a"], Some("again"), 7))).await;
    let mut pager = common::client(&mock.url)
        .authorization()
        .list_resources_pager(list_req())
        .max_pages(3);
    for _ in 0..3 {
        pager.next_page().await.unwrap();
    }
    assert!(matches!(
        pager.next_page().await.unwrap_err(),
        Error::PageLimit { max_pages: 3 }
    ));
    assert_eq!(mock.count(), 3);
}

#[tokio::test]
async fn token_never_leaks_into_debug_display_or_errors() {
    let mock = Mock::start(Script::json(403, &err_body("FORBIDDEN", json!({})))).await;
    let client = common::client(&mock.url);
    assert!(!format!("{client:?}").contains("test-token-xyz"));
    assert!(!format!("{:?}", Secret::new("test-token-xyz")).contains("test-token-xyz"));
    let err = client
        .authorization()
        .check(&check_req())
        .await
        .unwrap_err();
    assert!(!format!("{err} {err:?}").contains("test-token-xyz"));
    let sent = &mock.requests()[0];
    assert_eq!(sent.header("authorization"), Some("Bearer test-token-xyz"));
}

#[tokio::test]
async fn request_id_and_traceparent_are_injected_and_valid() {
    let ok = json!({"data": {"decision": "deny", "reason_code": "default_deny"}});
    let mock = Mock::start(Script::json(200, &ok)).await;
    let client = common::client(&mock.url);
    client.authorization().check(&check_req()).await.unwrap();
    let first = &mock.requests()[0];
    assert!(authgebra_client::trace::is_valid_request_id(
        first.header("x-request-id").unwrap()
    ));
    let tp = TraceContext::parse(first.header("traceparent").unwrap()).expect("valid traceparent");

    let joined = TraceContext::new_root();
    let opts = CallOptions::new()
        .request_id("caller-supplied-0001")
        .trace(joined.child());
    client
        .authorization()
        .check_with(&check_req(), &opts)
        .await
        .unwrap();
    let second = &mock.requests()[1];
    assert_eq!(second.header("x-request-id"), Some("caller-supplied-0001"));
    let sent = TraceContext::parse(second.header("traceparent").unwrap()).unwrap();
    assert_eq!(
        sent.trace_id_hex(),
        joined.trace_id_hex(),
        "joined the caller's trace"
    );
    assert_ne!(tp.trace_id_hex(), sent.trace_id_hex());

    let bad = CallOptions::new().request_id("bad id");
    client
        .authorization()
        .check_with(&check_req(), &bad)
        .await
        .unwrap();
    assert_eq!(
        mock.count(),
        3,
        "an invalid caller id is discarded; the request still runs"
    );
    assert!(authgebra_client::trace::is_valid_request_id(
        mock.requests()[2].header("x-request-id").unwrap()
    ));
}

#[tokio::test]
async fn request_id_inputs_follow_the_shared_contract_vectors() {
    let vectors: Value = serde_json::from_slice(
        &std::fs::read(common::fixtures_dir().join("request-semantics.json")).unwrap(),
    )
    .unwrap();
    let mock = Mock::start(Script::json(
        200,
        &json!({"data":{"decision":"deny","reason_code":"no_applicable_allow"}}),
    ))
    .await;
    let client = common::client(&mock.url);
    for row in vectors["request_ids"].as_array().unwrap() {
        let supplied = row["value"].as_str().unwrap();
        client
            .authorization()
            .check_with(&check_req(), &CallOptions::new().request_id(supplied))
            .await
            .unwrap();
        let requests = mock.requests();
        let sent = requests.last().unwrap().header("x-request-id").unwrap();
        if row["valid"].as_bool().unwrap() {
            assert_eq!(sent, supplied);
        } else {
            assert_ne!(sent, supplied);
            assert!(authgebra_client::trace::is_valid_request_id(sent));
        }
    }
}

#[test]
fn builder_refuses_insecure_or_reserved_configuration() {
    assert!(
        Client::builder()
            .base_url("http://authz.example.com")
            .build()
            .is_err()
    );
    assert!(
        Client::builder()
            .base_url("http://authz.example.com")
            .allow_insecure_http(true)
            .build()
            .is_ok()
    );
    assert!(
        Client::builder()
            .base_url("https://user:pw@authz.example.com")
            .build()
            .is_err()
    );
    assert!(
        Client::builder()
            .base_url("https://authz.example.com")
            .default_header("Authorization", "x")
            .build()
            .is_err()
    );
    assert!(
        Client::builder()
            .base_url("https://authz.example.com")
            .default_header("X-Request-ID", "x")
            .build()
            .is_err()
    );
    assert!(Client::builder().build().is_err());
}

#[tokio::test]
async fn oversized_bodies_and_redirects_are_protocol_errors() {
    let big = Mock::start(Script::Respond {
        status: 200,
        headers: vec![],
        body: vec![b'x'; 4096],
    })
    .await;
    let client = Client::builder()
        .base_url(&big.url)
        .max_response_bytes(1024)
        .build()
        .unwrap();
    match client.authorization().revision().await.unwrap_err() {
        Error::Protocol(p) => assert_eq!(p.kind, ProtocolKind::ResponseTooLarge),
        other => panic!("{other}"),
    }
    let redirect = Mock::start(Script::Respond {
        status: 302,
        headers: vec![("location".into(), "http://127.0.0.1:1/elsewhere".into())],
        body: vec![],
    })
    .await;
    match common::client(&redirect.url)
        .authorization()
        .revision()
        .await
        .unwrap_err()
    {
        Error::Protocol(p) => assert_eq!(p.kind, ProtocolKind::UnexpectedStatus),
        other => panic!("{other}"),
    }
    assert_eq!(
        redirect.count(),
        1,
        "redirects are not followed (would replay Authorization)"
    );
}

#[tokio::test]
async fn connection_refused_is_a_connect_error_with_no_commit_claim() {
    let port = {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        l.local_addr().unwrap().port()
    };
    let client = Client::builder()
        .base_url(format!("http://127.0.0.1:{port}"))
        .timeout(Duration::from_millis(300))
        .retry_policy(RetryPolicy::none())
        .build()
        .unwrap();
    match create_user(&client, &CallOptions::new()).await.unwrap_err() {
        Error::Transport(t) => {
            assert_eq!(t.kind.as_str(), "connect");
            assert_eq!(
                t.commit_state, None,
                "nothing was sent, so no unknown-commit claim"
            );
        }
        other => panic!("{other}"),
    }
}

#[test]
fn three_state_fields_distinguish_absent_null_and_value() {
    #[derive(serde::Deserialize, serde::Serialize)]
    struct Patch {
        #[serde(
            default,
            skip_serializing_if = "Option::is_none",
            deserialize_with = "authgebra_client::serde_helpers::double_option"
        )]
        note: Option<Option<String>>,
    }
    let absent: Patch = serde_json::from_str("{}").unwrap();
    let null: Patch = serde_json::from_str(r#"{"note":null}"#).unwrap();
    let set: Patch = serde_json::from_str(r#"{"note":"x"}"#).unwrap();
    assert_eq!(absent.note, None);
    assert_eq!(null.note, Some(None));
    assert_eq!(set.note, Some(Some("x".to_owned())));
    assert_eq!(serde_json::to_string(&absent).unwrap(), "{}");
    assert_eq!(serde_json::to_string(&null).unwrap(), r#"{"note":null}"#);
}

#[tokio::test]
async fn generated_paged_responses_require_page_metadata() {
    use authgebra_client::generated::admin::ListUsersArgs;
    for page in [
        None,
        Some(json!(null)),
        Some(json!({})),
        Some(json!({"has_more":null})),
    ] {
        let mut body = json!({"data": []});
        if let Some(page) = page {
            body["page"] = page;
        }
        let mock = Mock::start(Script::json(200, &body)).await;
        let result = common::client(&mock.url)
            .admin()
            .list_users(ListUsersArgs::default(), &CallOptions::default())
            .await;
        assert!(
            matches!(result, Err(Error::Protocol(_))),
            "malformed page accepted: {body}"
        );
    }
}
