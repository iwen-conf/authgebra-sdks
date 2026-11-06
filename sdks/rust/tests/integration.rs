//! Live-server integration. No environment means explicit NOT_RUN, never live evidence.
use authgebra_client::{
    ApiError, Call, CallOptions, Client, Error, Method, Response, RetryClass, TraceContext,
};
use serde_json::{Value, json};
use std::time::{Duration, Instant};

struct Session {
    client: Client,
    prefix: String,
    password: String,
    subject: String,
}

#[tokio::test]
#[ignore = "requires the explicitly isolated real SDK server"]
async fn real_server_generated_admin_models() {
    use authgebra_client::generated::{admin::*, models::*};
    let session = Session::login().await;
    let name = format!("typed_{}", &TraceContext::new_root().trace_id_hex()[..12]);
    let options = CallOptions::new().idempotency_key(TraceContext::new_root().trace_id_hex());
    let admin = session.client.admin();
    let args = CreateRoleArgs {
        body: CreateRoleRequest {
            code: name.clone(),
            name: name.clone(),
            description: Some(Some("typed description".to_owned())),
        },
    };
    let created = admin.create_role(args.clone(), &options).await.unwrap();
    let replay = admin.create_role(args, &options).await.unwrap();
    assert!(replay.replayed);
    assert_eq!(created.data.id, replay.data.id);
    let id = created.data.id;
    let loaded = admin
        .get_role(GetRoleArgs { id: id.clone() }, &CallOptions::default())
        .await
        .unwrap();
    assert_eq!(
        loaded.data.description,
        Some(Some("typed description".to_owned()))
    );
    let cleared = admin
        .patch_role(
            PatchRoleArgs {
                id: id.clone(),
                body: PatchRoleRequest {
                    name: None,
                    description: Some(None),
                },
            },
            &CallOptions::new().if_match(&loaded.data.etag),
        )
        .await
        .unwrap();
    assert_eq!(cleared.data.description, Some(None));
    admin
        .delete_role(
            DeleteRoleArgs { id },
            &CallOptions::new().if_match(&cleared.data.etag),
        )
        .await
        .unwrap();
}

fn inflight_control(operation: &str) {
    let command = std::env::var(format!("AUTHGEBRA_IT_INFLIGHT_{operation}_CMD")).unwrap();
    assert!(
        std::process::Command::new("timeout")
            .args(["15", "sh", "-c", &command])
            .status()
            .unwrap()
            .success()
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires an explicitly enabled isolated first-write database barrier"]
async fn real_server_inflight() {
    assert_eq!(std::env::var("AUTHGEBRA_IT_INFLIGHT").as_deref(), Ok("1"));
    let s = Session::login().await;
    for different in [false, true] {
        let prefix = format!(
            "it-flight-rs-{}",
            &TraceContext::new_root().trace_id_hex()[..12]
        );
        let body = json!({"username":prefix,"display_name":prefix,"initial_password":s.password});
        let key = TraceContext::new_root().trace_id_hex();
        inflight_control("HOLD");
        struct Release;
        impl Drop for Release {
            fn drop(&mut self) {
                inflight_control("RELEASE");
            }
        }
        let release = Release;
        let call = Call::new(
            Method::POST,
            vec!["api".into(), "v1".into(), "users".into()],
        )
        .json(&body)
        .unwrap();
        let options = CallOptions::new().idempotency_key(&key).request_id(format!(
            "it-flight-rs-{}",
            TraceContext::new_root().trace_id_hex()
        ));
        let client = s.client.clone();
        let first = tokio::spawn(async move { client.execute::<Value>(call, &options).await });
        inflight_control("WAIT");
        let mut duplicate = body.clone();
        if different {
            duplicate["display_name"] = json!("different");
        }
        let second = s
            .request(
                Method::POST,
                "/api/v1/users",
                Some(duplicate.clone()),
                Some(&key),
            )
            .await;
        drop(release);
        let created = first.await.unwrap().unwrap();
        assert_eq!(created.status, 201);
        match second {
            Err(Error::Api(error)) => {
                assert_eq!(
                    (error.status, error.code.as_str(), error.retryable),
                    (409, "IDEMPOTENCY_IN_PROGRESS", Some(true))
                );
            }
            other => panic!("expected in-flight conflict: {other:?}"),
        }
        let replay = s
            .ok(Method::POST, "/api/v1/users", Some(body), Some(&key))
            .await;
        let found = s
            .ok(
                Method::GET,
                &format!("/api/v1/users?q={prefix}"),
                None,
                None,
            )
            .await;
        assert!(replay.replayed);
        assert_eq!(replay.data["id"], created.data["id"]);
        assert_eq!(found.data.as_array().unwrap().len(), 1);
        assert_eq!(found.data[0]["id"], created.data["id"]);
        if different {
            let error = s
                .error(
                    Method::POST,
                    "/api/v1/users",
                    Some(duplicate),
                    Some(&key),
                    "IDEMPOTENCY_CONFLICT",
                    409,
                )
                .await;
            assert_eq!(error.retryable, Some(false));
        }
    }
    println!("first-write inflight PASS");
}

impl Session {
    async fn login() -> Self {
        let base = std::env::var("AUTHGEBRA_IT_BASE_URL")
            .expect("NOT_RUN: explicitly selected live tests require AUTHGEBRA_IT_BASE_URL");
        assert_eq!(std::env::var("AUTHGEBRA_IT_CONFIRM_ISOLATED").unwrap(), "1");
        let username = std::env::var("AUTHGEBRA_IT_USER").unwrap();
        let password = std::env::var("AUTHGEBRA_IT_PASSWORD").unwrap();
        let prefix = format!("it-rs-{}", &TraceContext::new_root().trace_id_hex()[..12]);
        let client = Client::builder().base_url(&base).build().unwrap();
        let mut s = Self {
            client,
            prefix,
            password,
            subject: String::new(),
        };
        let login = s
            .ok(
                Method::POST,
                "/api/v1/auth/login",
                Some(json!({"identifier":username,"password":s.password})),
                None,
            )
            .await;
        assert_eq!(login.data["status"], "authenticated");
        s.client = Client::builder()
            .base_url(&base)
            .bearer_token(login.data["access_token"].as_str().unwrap())
            .build()
            .unwrap();
        let me = s.ok(Method::GET, "/api/v1/auth/me", None, None).await;
        s.subject = me.data["user"]["id"].as_str().unwrap().to_owned();
        s
    }
    async fn request(
        &self,
        method: Method,
        path: &str,
        body: Option<Value>,
        key: Option<&str>,
    ) -> Result<Response<Value>, Error> {
        let (path, query) = path.split_once('?').unwrap_or((path, ""));
        let mut call = Call::new(
            method,
            path.trim_start_matches('/')
                .split('/')
                .map(str::to_owned)
                .collect(),
        );
        if path.starts_with("/api/v1/authz/") {
            call = call.class(RetryClass::Read);
        }
        for (k, v) in url::form_urlencoded::parse(query.as_bytes()) {
            call = call.query(&k, &v);
        }
        if let Some(b) = body {
            call = call.json(&b)?;
        }
        let mut opts = CallOptions::new()
            .request_id(format!("it-rs-{}", TraceContext::new_root().trace_id_hex()));
        if let Some(k) = key {
            opts = opts.idempotency_key(k);
        }
        self.client.execute(call, &opts).await
    }
    async fn ok(&self, m: Method, p: &str, b: Option<Value>, k: Option<&str>) -> Response<Value> {
        self.request(m, p, b, k)
            .await
            .unwrap_or_else(|e| panic!("{p}: {e}"))
    }
    async fn error(
        &self,
        m: Method,
        p: &str,
        b: Option<Value>,
        k: Option<&str>,
        code: &str,
        status: u16,
    ) -> ApiError {
        match self.request(m, p, b, k).await {
            Err(Error::Api(e)) => {
                assert_eq!(e.code, code);
                assert_eq!(e.status, status);
                assert!(e.request_id.is_some() && e.trace_id.is_some());
                *e
            }
            other => panic!("expected API error {code}, got {other:?}"),
        }
    }
}
#[tokio::test]
#[ignore = "NOT_RUN unless explicitly selected with isolated live-server environment"]
async fn real_server_contract() {
    let s = Session::login().await;
    s.error(Method::POST,"/api/v1/auth/login",Some(json!({"identifier":std::env::var("AUTHGEBRA_IT_USER").unwrap(),"password":format!("{}-wrong",s.password)})),None,"INVALID_CREDENTIALS",401).await;
    for (permission, expected) in [("user:read", "allow"), ("nope:nope", "deny")] {
        let r = s
            .client
            .authorization()
            .check(&authgebra_client::models::CheckRequest {
                subject_id: s.subject.clone(),
                permission: permission.into(),
                resource: None,
                context: None,
            })
            .await
            .unwrap();
        assert_eq!(r.data.decision.as_str(), expected);
        assert!(r.meta.unwrap().authz_revision > 0);
        assert!(r.meta.unwrap().snapshot_generation.unwrap() > 0);
    }
    s.ok(
        Method::POST,
        "/api/v1/resource-types",
        Some(json!({"code":s.prefix,"name":s.prefix})),
        None,
    )
    .await;
    let attr=s.ok(Method::POST,"/api/v1/attributes",Some(json!({"code":s.prefix,"name":s.prefix,"owner_type":"resource","value_type":"string"})),None).await;
    let secret = TraceContext::new_root().trace_id_hex();
    let refs = [
        json!({"resource_type_code":s.prefix,"external_id":"b"}),
        json!({"resource_type_code":s.prefix,"external_id":"a"}),
    ];
    for r in &refs {
        s.ok(Method::POST,"/api/v1/resources",Some(json!({"resource_type_code":s.prefix,"external_id":r["external_id"],"name":r["external_id"],"attributes":[{"attribute_id":attr.data["id"],"value":secret}]})),None).await;
    }
    let batch=s.ok(Method::POST,"/api/v1/authz/batch-check",Some(json!({"subject_id":s.subject,"permission":"user:read","resources":[refs[0],{"resource_type_code":s.prefix,"external_id":"missing"},refs[1],refs[0]]})),None).await;
    assert_eq!(batch.data["allowed"].as_array().unwrap().len(), 2);
    assert_eq!(batch.data["denied"].as_array().unwrap().len(), 1);
    assert_eq!(batch.data["allowed"][0]["resource"]["external_id"], "b");
    assert_eq!(batch.data["allowed"][1]["resource"]["external_id"], "a");
    assert_eq!(
        batch.data["denied"][0]["resource"]["external_id"],
        "missing"
    );
    let mut body =
        json!({"subject_id":s.subject,"permission":"user:read","resource_type":s.prefix,"limit":1});
    let first = s
        .ok(
            Method::POST,
            "/api/v1/authz/list-resources",
            Some(body.clone()),
            None,
        )
        .await;
    assert_eq!(first.data.as_array().unwrap().len(), 1);
    assert!(first.page.as_ref().unwrap().has_more);
    let cursor = first.page.as_ref().unwrap().next_cursor.as_ref().unwrap();
    body["cursor"] = json!(cursor);
    let second = s
        .ok(
            Method::POST,
            "/api/v1/authz/list-resources",
            Some(body.clone()),
            None,
        )
        .await;
    assert_eq!(second.data.as_array().unwrap().len(), 1);
    assert_ne!(first.data, second.data);
    assert_eq!(first.meta, second.meta);
    assert!(!second.page.unwrap().has_more);
    body["cursor"] = json!(format!(
        "{}{}",
        if cursor.starts_with('A') { "B" } else { "A" },
        &cursor[1..]
    ));
    s.error(
        Method::POST,
        "/api/v1/authz/list-resources",
        Some(body),
        None,
        "CURSOR_INVALID",
        400,
    )
    .await;
    let explain = s
        .ok(
            Method::POST,
            "/api/v1/authz/explain",
            Some(json!({"subject_id":s.subject,"permission":"user:read","resource":refs[0]})),
            None,
        )
        .await;
    assert_eq!(explain.data["decision"], "allow");
    assert!(!explain.data.to_string().contains(&secret));
    let rev = s.client.authorization().revision().await.unwrap().data;
    assert!(rev.db_revision > 0);
    assert_eq!(rev.db_revision, rev.snapshot_generation);
    assert_eq!(rev.status, authgebra_client::models::RevisionStatus::InSync);
    let body = json!({"username":s.prefix,"display_name":s.prefix,"initial_password":s.password});
    let key = TraceContext::new_root().trace_id_hex();
    let created = s
        .ok(
            Method::POST,
            "/api/v1/users",
            Some(body.clone()),
            Some(&key),
        )
        .await;
    let replay = s
        .ok(
            Method::POST,
            "/api/v1/users",
            Some(body.clone()),
            Some(&key),
        )
        .await;
    assert_eq!(created.status, 201);
    assert!(replay.replayed);
    assert_eq!(created.data["id"], replay.data["id"]);
    let (left, right) = tokio::join!(
        s.request(
            Method::POST,
            "/api/v1/users",
            Some(body.clone()),
            Some(&key)
        ),
        s.request(Method::POST, "/api/v1/users", Some(body), Some(&key))
    );
    for r in [left.unwrap(), right.unwrap()] {
        assert!(r.replayed);
        assert_eq!(r.data["id"], created.data["id"]);
    }
    let conflict=s.error(Method::POST,"/api/v1/users",Some(json!({"username":s.prefix,"display_name":"different","initial_password":s.password})),Some(&key),"IDEMPOTENCY_CONFLICT",409).await;
    assert_eq!(conflict.retryable, Some(false));
    let found = s
        .ok(
            Method::GET,
            &format!("/api/v1/users?q={}", s.prefix),
            None,
            None,
        )
        .await;
    assert_eq!(found.data.as_array().unwrap().len(), 1);
    assert_eq!(found.data[0]["id"], created.data["id"]);
    let trace = TraceContext::new_root();
    let request_id = format!("{}-missing", s.prefix);
    let call = Call::new(
        Method::GET,
        vec![
            "api".into(),
            "v1".into(),
            "users".into(),
            "00000000-0000-4000-8000-000000000099".into(),
        ],
    );
    let e = s
        .client
        .execute::<Value>(
            call,
            &CallOptions::new().request_id(&request_id).trace(trace),
        )
        .await
        .unwrap_err();
    if let Error::Api(e) = e {
        assert_eq!(e.code, "RESOURCE_NOT_FOUND");
        assert_eq!(e.status, 404);
        assert_eq!(e.trace_id, Some(trace.trace_id_hex()));
        assert_eq!(e.request_id, Some(request_id));
    } else {
        panic!("not-found not an API error")
    }
    println!("IT-1..9 PASS");
}
#[tokio::test]
#[ignore = "requires isolated live server"]
async fn real_server_invalid_input() {
    let s = Session::login().await;
    // Each assertion retains its real-server divergence rather than accepting the observed output.
    for (path, reason) in [
        ("/api/v1/users?limit=abc", "invalid_query_parameter"),
        ("/api/v1/users/not-a-uuid", "invalid_path_parameter"),
    ] {
        let e = s
            .error(Method::GET, path, None, None, "INVALID_REQUEST", 400)
            .await;
        assert_eq!(e.violations[0].reason, reason);
    }
    let e=s.error(Method::POST,"/api/v1/auth/login",Some(json!({"identifier":std::env::var("AUTHGEBRA_IT_USER").unwrap(),"password":s.password,"extra":true})),None,"INVALID_REQUEST",400).await;
    assert_eq!(e.violations[0].reason, "unknown_field");
    assert!(e.violations[0].path.is_some());
}
#[tokio::test]
#[ignore = "requires explicitly enabled isolated publication fault injection"]
async fn real_server_committed_unpublished() {
    assert_eq!(
        std::env::var("AUTHGEBRA_IT_FAULT").as_deref(),
        Ok("1"),
        "explicitly selected fault test requires AUTHGEBRA_IT_FAULT=1"
    );
    let s = Session::login().await;
    let on = std::env::var("AUTHGEBRA_IT_FAULT_ON_CMD").unwrap();
    let off = std::env::var("AUTHGEBRA_IT_FAULT_OFF_CMD").unwrap();
    struct Reset(String);
    impl Drop for Reset {
        fn drop(&mut self) {
            let status = std::process::Command::new("timeout")
                .args(["15", "sh", "-c", &self.0])
                .status()
                .expect("fault cleanup");
            assert!(status.success(), "fault cleanup failed");
        }
    }
    let body = json!({"username":s.prefix,"display_name":s.prefix,"initial_password":s.password});
    let key = TraceContext::new_root().trace_id_hex();
    {
        let _reset = Reset(off);
        assert!(
            std::process::Command::new("timeout")
                .args(["15", "sh", "-c", &on])
                .status()
                .unwrap()
                .success()
        );
        let e = s
            .error(
                Method::POST,
                "/api/v1/users",
                Some(body.clone()),
                Some(&key),
                "AUTHZ_RELOAD_PENDING",
                503,
            )
            .await;
        assert_eq!(
            e.commit_state,
            Some(authgebra_client::CommitState::CommittedUnpublished)
        );
        assert_eq!(e.retryable, Some(false));
    }
    let deadline = Instant::now() + Duration::from_secs(60);
    loop {
        match s.client.authorization().revision().await {
            Ok(rev) if rev.data.db_revision == rev.data.snapshot_generation => break,
            Ok(_) => {}
            Err(Error::Api(e)) if e.code == "NOT_READY" && e.status == 503 => {}
            Err(e) => panic!("unexpected recovery error: {e}"),
        }
        assert!(Instant::now() < deadline, "recovery deadline");
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    let replay = s
        .ok(Method::POST, "/api/v1/users", Some(body), Some(&key))
        .await;
    let found = s
        .ok(
            Method::GET,
            &format!("/api/v1/users?q={}", s.prefix),
            None,
            None,
        )
        .await;
    assert!(replay.replayed);
    assert_eq!(found.data.as_array().unwrap().len(), 1);
    assert_eq!(replay.data["id"], found.data[0]["id"]);
    println!("IT-11 PASS");
}
