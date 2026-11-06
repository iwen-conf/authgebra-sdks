//! A tiny hand-rolled HTTP/1.1 server for tests (no extra dependencies).
#![allow(dead_code)]

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

#[derive(Clone, Debug)]
pub struct Recorded {
    pub method: String,
    pub path: String,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

impl Recorded {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
    }
}

#[derive(Clone, Debug)]
pub enum Script {
    Respond {
        status: u16,
        headers: Vec<(String, String)>,
        body: Vec<u8>,
    },
    /// Accept, read the request, then never answer.
    Hang,
    /// Accept, read the request, then close without answering.
    Reset,
    /// One script per request; the last repeats.
    Seq(Vec<Script>),
}

impl Script {
    pub fn json(status: u16, body: &serde_json::Value) -> Self {
        Self::Respond {
            status,
            headers: vec![],
            body: serde_json::to_vec(body).unwrap(),
        }
    }
}

pub struct Mock {
    pub url: String,
    pub recorded: Arc<Mutex<Vec<Recorded>>>,
}

impl Mock {
    pub async fn start(script: Script) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let recorded: Arc<Mutex<Vec<Recorded>>> = Arc::default();
        let log = recorded.clone();
        tokio::spawn(async move {
            loop {
                let Ok((mut stream, _)) = listener.accept().await else {
                    return;
                };
                let log = log.clone();
                let script = script.clone();
                tokio::spawn(async move {
                    let mut buf = Vec::new();
                    let mut chunk = [0u8; 4096];
                    let header_end = loop {
                        let n = stream.read(&mut chunk).await.unwrap_or(0);
                        if n == 0 {
                            return;
                        }
                        buf.extend_from_slice(&chunk[..n]);
                        if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                            break pos + 4;
                        }
                    };
                    let head = String::from_utf8_lossy(&buf[..header_end]).into_owned();
                    let mut lines = head.split("\r\n");
                    let mut first = lines.next().unwrap_or_default().split(' ');
                    let method = first.next().unwrap_or_default().to_owned();
                    let path = first.next().unwrap_or_default().to_owned();
                    let headers: Vec<(String, String)> = lines
                        .filter(|l| !l.is_empty())
                        .filter_map(|l| l.split_once(':'))
                        .map(|(k, v)| (k.trim().to_owned(), v.trim().to_owned()))
                        .collect();
                    let length: usize = headers
                        .iter()
                        .find(|(k, _)| k.eq_ignore_ascii_case("content-length"))
                        .and_then(|(_, v)| v.parse().ok())
                        .unwrap_or(0);
                    while buf.len() < header_end + length {
                        let n = stream.read(&mut chunk).await.unwrap_or(0);
                        if n == 0 {
                            break;
                        }
                        buf.extend_from_slice(&chunk[..n]);
                    }
                    let body = buf[header_end..].to_vec();
                    let index = {
                        let mut guard = log.lock().unwrap();
                        guard.push(Recorded {
                            method,
                            path,
                            headers,
                            body,
                        });
                        guard.len() - 1
                    };
                    let mut current = &script;
                    while let Script::Seq(list) = current {
                        current = &list[index.min(list.len() - 1)];
                    }
                    match current {
                        Script::Respond {
                            status,
                            headers,
                            body,
                        } => {
                            let mut out = format!("HTTP/1.1 {status} X\r\n").into_bytes();
                            if !headers
                                .iter()
                                .any(|(k, _)| k.eq_ignore_ascii_case("content-type"))
                            {
                                out.extend_from_slice(b"content-type: application/json\r\n");
                            }
                            for (k, v) in headers {
                                out.extend_from_slice(format!("{k}: {v}\r\n").as_bytes());
                            }
                            out.extend_from_slice(
                                format!(
                                    "content-length: {}\r\nconnection: close\r\n\r\n",
                                    body.len()
                                )
                                .as_bytes(),
                            );
                            out.extend_from_slice(body);
                            let _ = stream.write_all(&out).await;
                            let _ = stream.shutdown().await;
                        }
                        Script::Hang => tokio::time::sleep(Duration::from_secs(30)).await,
                        Script::Reset | Script::Seq(_) => drop(stream),
                    }
                });
            }
        });
        Self { url, recorded }
    }

    pub fn count(&self) -> usize {
        self.recorded.lock().unwrap().len()
    }

    pub fn requests(&self) -> Vec<Recorded> {
        self.recorded.lock().unwrap().clone()
    }
}

fn dir(env: &str, default: &str) -> PathBuf {
    std::env::var_os(env).map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(default),
        PathBuf::from,
    )
}

/// Directory holding `cases/*.json` (override with `AUTHGEBRA_SDK_FIXTURES`).
pub fn fixtures_dir() -> PathBuf {
    dir("AUTHGEBRA_SDK_FIXTURES", "../fixtures")
}

/// Directory holding `openapi.json` / `openapi.sha256` (override with `AUTHGEBRA_SDK_CONTRACTS`).
pub fn contracts_dir() -> PathBuf {
    dir("AUTHGEBRA_SDK_CONTRACTS", "../contracts")
}

pub fn fast_retry() -> authgebra_client::RetryPolicy {
    authgebra_client::RetryPolicy {
        max_retries: 2,
        max_write_retries: 2,
        base_backoff: Duration::from_millis(1),
        max_backoff: Duration::from_millis(5),
        max_retry_after: Duration::from_millis(20),
    }
}

pub fn client(url: &str) -> authgebra_client::Client {
    authgebra_client::Client::builder()
        .base_url(url)
        .bearer_token("test-token-xyz")
        .timeout(Duration::from_millis(300))
        .retry_policy(fast_retry())
        .build()
        .unwrap()
}
