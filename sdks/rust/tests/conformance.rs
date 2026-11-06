//! Runs every shared fixture in `sdks/fixtures/cases/*.json` against the real client.

mod common;

use authgebra_client::models::{CheckRequest, ListResourcesRequest};
use authgebra_client::{Call, CallOptions, Error, Method};
use common::{Mock, Script};
use serde_json::Value;

const SUBJECT: &str = "00000000-0000-7000-8000-000000000001";

enum Got {
    Ok(Ok_),
    Err(Box<Error>),
}

#[derive(Default)]
struct Ok_ {
    decision: Option<String>,
    authz_revision: Option<String>,
    snapshot_generation: Option<String>,
    trace_id: Option<String>,
    request_id: Option<String>,
    has_more: Option<bool>,
    echo_username: Option<String>,
}

fn is_read(operation: &str) -> bool {
    operation.starts_with("authz.") || operation == "users.list"
}

async fn call(operation: &str, request: &Value, url: &str) -> Got {
    let client = common::client(url);
    let opts = CallOptions {
        idempotency_key: request
            .get("idempotency_key")
            .and_then(Value::as_str)
            .map(str::to_owned),
        ..CallOptions::default()
    };
    match operation {
        "authz.check" => {
            let req = CheckRequest {
                subject_id: SUBJECT.to_owned(),
                permission: "document:read".to_owned(),
                resource: None,
                context: None,
            };
            match client.authorization().check_with(&req, &opts).await {
                Ok(r) => Got::Ok(Ok_ {
                    decision: Some(r.data.decision.to_string()),
                    authz_revision: r.meta.map(|m| m.authz_revision.to_string()),
                    snapshot_generation: r
                        .meta
                        .and_then(|m| m.snapshot_generation)
                        .map(|v| v.to_string()),
                    trace_id: r.trace_id,
                    request_id: r.request_id,
                    ..Ok_::default()
                }),
                Err(e) => Got::Err(Box::new(e)),
            }
        }
        "authz.list_resources" => {
            let req = ListResourcesRequest {
                subject_id: SUBJECT.to_owned(),
                permission: "document:read".to_owned(),
                resource_type: "document".to_owned(),
                context: None,
                cursor: Some("opaque".to_owned()),
                limit: None,
            };
            match client
                .authorization()
                .list_resources_with(&req, &opts)
                .await
            {
                Ok(r) => Got::Ok(Ok_ {
                    has_more: r.page.map(|p| p.has_more),
                    ..Ok_::default()
                }),
                Err(e) => Got::Err(Box::new(e)),
            }
        }
        other => {
            let (method, segments): (Method, Vec<&str>) = match other {
                "users.create" => (Method::POST, vec!["api", "v1", "users"]),
                "users.update" => (Method::PATCH, vec!["api", "v1", "users", SUBJECT]),
                "users.list" => (Method::GET, vec!["api", "v1", "users"]),
                "auth.change_password" => {
                    (Method::POST, vec!["api", "v1", "auth", "change-password"])
                }
                unknown => panic!("fixture operation {unknown} has no mapping"),
            };
            let mut c = Call::new(method, segments.into_iter().map(str::to_owned).collect());
            if let Some(body) = request.get("body").filter(|b| !b.is_null()) {
                c = c.json(body).unwrap();
            }
            match client.execute::<Value>(c, &opts).await {
                Ok(r) => Got::Ok(Ok_ {
                    authz_revision: r.authz_revision().map(|v| v.to_string()),
                    has_more: r.page.map(|p| p.has_more),
                    echo_username: r
                        .data
                        .get("username")
                        .and_then(Value::as_str)
                        .map(str::to_owned),
                    trace_id: r.trace_id,
                    request_id: r.request_id,
                    ..Ok_::default()
                }),
                Err(e) => Got::Err(Box::new(e)),
            }
        }
    }
}

/// `Some(None)` = key present and null (must be absent); `None` = key not asserted.
fn want(expect: &Value, key: &str) -> Option<Option<String>> {
    expect.get(key).map(|v| match v {
        Value::Null => None,
        Value::String(s) => Some(s.clone()),
        other => Some(other.to_string()),
    })
}

fn check<T: PartialEq + std::fmt::Debug>(failures: &mut Vec<String>, what: &str, got: T, want: T) {
    if got != want {
        failures.push(format!("{what}: got {got:?}, want {want:?}"));
    }
}

async fn run(case: &Value) -> Vec<String> {
    let id = case["id"].as_str().unwrap();
    let operation = case["operation"].as_str().unwrap();
    let request = &case["request"];
    let expect = &case["expect"];
    let script = match case.get("fault").and_then(Value::as_str) {
        Some("timeout") => Script::Hang,
        Some("reset") => Script::Reset,
        _ => {
            let response = &case["response"];
            let headers = response["headers"]
                .as_object()
                .map(|h| {
                    h.iter()
                        .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_owned()))
                        .collect()
                })
                .unwrap_or_default();
            let body = match response.get("body_raw").and_then(Value::as_str) {
                Some(raw) => raw.as_bytes().to_vec(),
                None => serde_json::to_vec(&response["body"]).unwrap(),
            };
            Script::Respond {
                status: response["status"].as_u64().unwrap() as u16,
                headers,
                body,
            }
        }
    };
    let mock = Mock::start(script).await;
    let got = call(operation, request, &mock.url).await;
    let mut f: Vec<String> = Vec::new();
    let mut expected_attempts = 1usize;

    match (expect["kind"].as_str().unwrap(), got) {
        ("ok", Got::Ok(ok)) => {
            for (key, value) in [
                ("decision", &ok.decision),
                ("authz_revision", &ok.authz_revision),
                ("snapshot_generation", &ok.snapshot_generation),
                ("trace_id", &ok.trace_id),
                ("request_id", &ok.request_id),
                ("echo_username", &ok.echo_username),
            ] {
                if let Some(w) = want(expect, key) {
                    check(&mut f, key, value.clone(), w);
                }
            }
            if let Some(w) = expect.get("has_more").and_then(Value::as_bool) {
                check(&mut f, "has_more", ok.has_more, Some(w));
            }
            if expect
                .get("request_body_utf8_exact")
                .and_then(Value::as_bool)
                == Some(true)
            {
                let literal = expect["echo_username"].as_str().unwrap();
                let sent = &mock.requests()[0].body;
                let text = std::str::from_utf8(sent).expect("request body must be UTF-8");
                if !text.contains(literal) {
                    f.push(format!(
                        "request body does not carry {literal:?} verbatim: {text}"
                    ));
                }
            }
        }
        ("api_error", Got::Err(e)) => match *e {
            Error::Api(api) => {
                check(
                    &mut f,
                    "status",
                    u64::from(api.status),
                    expect["status"].as_u64().unwrap(),
                );
                check(
                    &mut f,
                    "code",
                    api.code.as_str(),
                    expect["code"].as_str().unwrap(),
                );
                check(
                    &mut f,
                    "known_code",
                    api.known_code.is_some(),
                    expect["known_code"].as_bool().unwrap(),
                );
                for (key, value) in [
                    ("request_id", api.request_id.clone()),
                    ("trace_id", api.trace_id.clone()),
                    ("body_request_id", api.body_request_id.clone()),
                    (
                        "commit_state",
                        api.commit_state.map(|c| c.as_str().to_owned()),
                    ),
                    (
                        "retry_after_seconds",
                        api.retry_after_seconds.map(|v| v.to_string()),
                    ),
                    ("retryable", api.retryable.map(|v| v.to_string())),
                ] {
                    if let Some(w) = want(expect, key) {
                        check(&mut f, key, value, w);
                    }
                }
                if let Some(w) = expect.get("request_id_mismatch").and_then(Value::as_bool) {
                    check(&mut f, "request_id_mismatch", api.request_id_mismatch, w);
                }
                if let Some(list) = expect.get("violations").and_then(Value::as_array) {
                    let want: Vec<(Option<String>, String)> = list
                        .iter()
                        .map(|v| {
                            (
                                v["path"].as_str().map(str::to_owned),
                                v["reason"].as_str().unwrap().to_owned(),
                            )
                        })
                        .collect();
                    let got: Vec<_> = api
                        .violations
                        .iter()
                        .map(|v| (v.path.clone(), v.reason.clone()))
                        .collect();
                    check(&mut f, "violations", got, want);
                }
                if is_read(operation) && matches!(api.code.as_str(), "RATE_LIMITED" | "NOT_READY") {
                    expected_attempts = 3;
                }
            }
            other => f.push(format!("want api_error, got {other}")),
        },
        ("transport_error", Got::Err(e)) => match *e {
            Error::Transport(t) => {
                check(
                    &mut f,
                    "transport kind",
                    t.kind.as_str(),
                    expect["error"].as_str().unwrap(),
                );
                check(
                    &mut f,
                    "commit_state",
                    t.commit_state.map(|c| c.as_str().to_owned()),
                    want(expect, "commit_state").flatten(),
                );
                if is_read(operation) && t.kind.as_str() == "timeout" {
                    expected_attempts = 3;
                }
                check(
                    &mut f,
                    "attempts reported",
                    t.attempts as usize,
                    expected_attempts,
                );
            }
            other => f.push(format!("want transport_error, got {other}")),
        },
        ("protocol_error", Got::Err(e)) => match *e {
            Error::Protocol(p) => {
                check(
                    &mut f,
                    "protocol kind",
                    p.kind.as_str(),
                    expect["error"].as_str().unwrap(),
                );
                check(
                    &mut f,
                    "status",
                    u64::from(p.status),
                    expect["status"].as_u64().unwrap(),
                );
            }
            other => f.push(format!("want protocol_error, got {other}")),
        },
        (kind, Got::Ok(_)) => f.push(format!("want {kind}, got success")),
        (kind, Got::Err(e)) => f.push(format!("want {kind}, got error {e}")),
    }
    if expect.get("auto_resent").and_then(Value::as_bool) == Some(false) {
        expected_attempts = 1;
    }
    check(&mut f, "request attempts", mock.count(), expected_attempts);
    f.into_iter().map(|m| format!("[{id}] {m}")).collect()
}

#[tokio::test(flavor = "multi_thread")]
async fn every_shared_fixture_passes() {
    let dir = common::fixtures_dir().join("cases");
    let mut paths: Vec<_> = std::fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("cannot read fixtures at {}: {e}", dir.display()))
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    paths.sort();
    assert!(
        paths.len() >= 25,
        "expected the 25 shared fixtures, found {}",
        paths.len()
    );
    let mut failures = Vec::new();
    for path in &paths {
        let case: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        failures.extend(run(&case).await);
    }
    println!(
        "fixtures: {}/{} passed",
        paths.len()
            - failures
                .iter()
                .map(|f| &f[..f.find(']').unwrap()])
                .collect::<std::collections::BTreeSet<_>>()
                .len(),
        paths.len()
    );
    assert!(
        failures.is_empty(),
        "fixture failures:\n{}",
        failures.join("\n")
    );
}
