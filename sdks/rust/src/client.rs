//! HTTP core: request pipeline, retry, response/error decoding.

use std::sync::Arc;
use std::time::Duration;

use reqwest::header::{
    ACCEPT, AUTHORIZATION, CONTENT_TYPE, HeaderMap, HeaderName, HeaderValue, IF_MATCH,
};
use reqwest::{Method, StatusCode};
use serde::Deserialize;
use serde::de::DeserializeOwned;
use url::Url;

use crate::config::{CallOptions, RetryPolicy, Secret, StaticToken, TokenProvider};
use crate::error::{
    ApiError, CommitState, Error, ErrorCode, ProtocolError, ProtocolKind, Result, TransportError,
    TransportKind, Violation,
};
use crate::trace::{TraceContext, is_valid_request_id, is_valid_trace_id, new_request_id};

const X_REQUEST_ID: &str = "x-request-id";
const X_TRACE_ID: &str = "x-trace-id";
const TRACEPARENT: &str = "traceparent";

/// Authorization version info on a successful response. Absent for admin reads.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
pub struct Meta {
    /// Committed and published authorization revision (64-bit, lossless).
    pub authz_revision: i64,
    /// Snapshot generation the decision used (decision endpoints only).
    #[serde(default)]
    pub snapshot_generation: Option<i64>,
}

/// Cursor pagination state.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Page {
    /// Opaque cursor for the next page.
    #[serde(default)]
    pub next_cursor: Option<String>,
    /// Whether another page exists.
    pub has_more: bool,
}

/// A successful response.
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct Response<T> {
    /// HTTP status (200, 201, ...).
    pub status: u16,
    /// Business payload.
    pub data: T,
    /// Version info; `None` when the endpoint does not carry it (never faked as 0).
    pub meta: Option<Meta>,
    /// Pagination state for list endpoints.
    pub page: Option<Page>,
    /// Valid `X-Request-ID`.
    pub request_id: Option<String>,
    /// Trace id from `X-Trace-ID` / `traceparent` (validated).
    pub trace_id: Option<String>,
    /// `ETag` header.
    pub etag: Option<String>,
    /// `X-Authz-Revision` header.
    pub authz_revision_header: Option<i64>,
    /// `Idempotency-Replayed: true` was present.
    pub replayed: bool,
}

impl<T> Response<T> {
    /// `meta.authz_revision`, else the `X-Authz-Revision` header, else `None`.
    #[must_use]
    pub fn authz_revision(&self) -> Option<i64> {
        self.meta
            .map(|m| m.authz_revision)
            .or(self.authz_revision_header)
    }
}

/// Whether a call may be retried under the default (read) rules.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RetryClass {
    /// Side-effect free (GET, and the authorization decision POSTs).
    Read,
    /// Mutation: never resent unless the call opts in and carries an idempotency key.
    Write,
}

/// Response body shape.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Shape {
    /// `{ "data": ..., "meta"?: ..., "page"?: ... }`
    Envelope,
    /// The whole body is the payload (health endpoints).
    Bare,
}

/// A low-level call description (used by the generated admin client).
#[derive(Debug, Clone)]
pub struct Call {
    pub(crate) method: Method,
    pub(crate) segments: Vec<String>,
    pub(crate) query: Vec<(String, String)>,
    pub(crate) body: Option<Vec<u8>>,
    pub(crate) class: RetryClass,
    pub(crate) shape: Shape,
    require_page: bool,
}

impl Call {
    /// New call; path segments are percent-encoded individually.
    #[must_use]
    pub fn new(method: Method, segments: Vec<String>) -> Self {
        let class = if method == Method::GET || method == Method::HEAD {
            RetryClass::Read
        } else {
            RetryClass::Write
        };
        Self {
            method,
            segments,
            query: Vec::new(),
            body: None,
            class,
            shape: Shape::Envelope,
            require_page: false,
        }
    }

    /// Require the pagination metadata declared by this operation's response schema.
    pub(crate) fn paged(mut self) -> Self {
        self.require_page = true;
        self
    }

    /// Add one query pair.
    #[must_use]
    pub fn query(mut self, name: &str, value: impl ToString) -> Self {
        self.query.push((name.to_owned(), value.to_string()));
        self
    }

    /// Attach a JSON body (serialized as UTF-8 exactly once; resends reuse these bytes).
    pub fn json<B: serde::Serialize + ?Sized>(mut self, body: &B) -> Result<Self> {
        self.body = Some(serde_json::to_vec(body).map_err(|e| Error::Serialize(e.to_string()))?);
        Ok(self)
    }

    /// Override the retry class (the decision POSTs are reads).
    #[must_use]
    pub fn class(mut self, class: RetryClass) -> Self {
        self.class = class;
        self
    }

    /// The body is the payload itself (no envelope).
    #[must_use]
    pub fn bare(mut self) -> Self {
        self.shape = Shape::Bare;
        self
    }
}

#[derive(Deserialize)]
struct Envelope<T> {
    data: T,
    #[serde(default)]
    meta: Option<Meta>,
    #[serde(default)]
    page: Option<Page>,
}

struct Inner {
    http: reqwest::Client,
    base: Url,
    token: Option<Arc<dyn TokenProvider>>,
    headers: HeaderMap,
    retry: RetryPolicy,
    timeout: Duration,
    max_response_bytes: usize,
}

/// The Authgebra client. Cheap to clone; share one per process (it owns the connection pool).
#[derive(Clone)]
pub struct Client {
    inner: Arc<Inner>,
}

impl std::fmt::Debug for Client {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Client")
            .field("base", &self.inner.base.as_str())
            .field("token", &self.inner.token.as_ref().map(|_| "<provider>"))
            .finish_non_exhaustive()
    }
}

/// Builder for [`Client`].
#[derive(Default)]
pub struct ClientBuilder {
    base_url: Option<String>,
    token: Option<Arc<dyn TokenProvider>>,
    http: Option<reqwest::Client>,
    connect_timeout: Option<Duration>,
    timeout: Option<Duration>,
    headers: Vec<(String, String)>,
    retry: Option<RetryPolicy>,
    max_response_bytes: Option<usize>,
    allow_insecure_http: bool,
    user_agent_suffix: Option<String>,
}

impl ClientBuilder {
    /// Server base URL, for example `https://authz.example.com`.
    #[must_use]
    pub fn base_url(mut self, url: impl Into<String>) -> Self {
        self.base_url = Some(url.into());
        self
    }

    /// Token source for the bearer credential.
    #[must_use]
    pub fn token_provider(mut self, provider: Arc<dyn TokenProvider>) -> Self {
        self.token = Some(provider);
        self
    }

    /// Convenience for a fixed token.
    #[must_use]
    pub fn bearer_token(self, token: impl Into<String>) -> Self {
        self.token_provider(Arc::new(StaticToken::new(token)))
    }

    /// Use your own `reqwest::Client` (TLS roots, proxy, pool). Per-request timeouts still apply.
    #[must_use]
    pub fn http_client(mut self, client: reqwest::Client) -> Self {
        self.http = Some(client);
        self
    }

    /// Connect timeout for the built-in client (default 5 s).
    #[must_use]
    pub fn connect_timeout(mut self, value: Duration) -> Self {
        self.connect_timeout = Some(value);
        self
    }

    /// Total per-request timeout (default 30 s). Always finite.
    #[must_use]
    pub fn timeout(mut self, value: Duration) -> Self {
        self.timeout = Some(value);
        self
    }

    /// Extra header sent on every call. Reserved names are rejected at `build()`.
    #[must_use]
    pub fn default_header(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        self.headers.push((name.into(), value.into()));
        self
    }

    /// Retry policy.
    #[must_use]
    pub fn retry_policy(mut self, policy: RetryPolicy) -> Self {
        self.retry = Some(policy);
        self
    }

    /// Response body cap in bytes (default 8 MiB).
    #[must_use]
    pub fn max_response_bytes(mut self, value: usize) -> Self {
        self.max_response_bytes = Some(value);
        self
    }

    /// Permit `http://` to a non-loopback host. Off by default: TLS is the norm.
    #[must_use]
    pub fn allow_insecure_http(mut self, allow: bool) -> Self {
        self.allow_insecure_http = allow;
        self
    }

    /// Append a product token to the User-Agent (`authgebra-client/<ver> <suffix>`).
    #[must_use]
    pub fn user_agent_suffix(mut self, suffix: impl Into<String>) -> Self {
        self.user_agent_suffix = Some(suffix.into());
        self
    }

    /// Validate the configuration and build the client.
    pub fn build(self) -> Result<Client> {
        let raw = self
            .base_url
            .ok_or_else(|| Error::Config("base_url is required".to_owned()))?;
        let base = Url::parse(&raw).map_err(|e| Error::Config(format!("invalid base_url: {e}")))?;
        if !base.username().is_empty() || base.password().is_some() {
            return Err(Error::Config(
                "base_url must not embed credentials".to_owned(),
            ));
        }
        if base.query().is_some() || base.fragment().is_some() {
            return Err(Error::Config(
                "base_url must not have a query or fragment".to_owned(),
            ));
        }
        match base.scheme() {
            "https" => {}
            "http" if self.allow_insecure_http || is_loopback(&base) => {}
            "http" => {
                return Err(Error::Config(
                    "plain http to a non-loopback host is refused; use https or allow_insecure_http(true)"
                        .to_owned(),
                ));
            }
            other => return Err(Error::Config(format!("unsupported scheme {other}"))),
        }

        let timeout = self.timeout.unwrap_or(Duration::from_secs(30));
        let mut ua = format!("authgebra-client/{}", env!("CARGO_PKG_VERSION"));
        if let Some(suffix) = &self.user_agent_suffix {
            ua.push(' ');
            ua.push_str(suffix);
        }
        let mut headers = HeaderMap::new();
        headers.insert(
            reqwest::header::USER_AGENT,
            HeaderValue::from_str(&ua)
                .map_err(|_| Error::Config("invalid user agent".to_owned()))?,
        );
        for (name, value) in self.headers {
            let lower = name.to_ascii_lowercase();
            if matches!(
                lower.as_str(),
                "authorization"
                    | "x-request-id"
                    | "traceparent"
                    | "tracestate"
                    | "idempotency-key"
                    | "if-match"
                    | "content-type"
                    | "content-length"
                    | "host"
                    | "accept"
            ) {
                return Err(Error::Config(format!(
                    "header {name} is managed by the SDK"
                )));
            }
            let name = HeaderName::from_bytes(name.as_bytes())
                .map_err(|_| Error::Config(format!("invalid header name {name:?}")))?;
            let value = HeaderValue::from_str(&value)
                .map_err(|_| Error::Config(format!("invalid value for header {name}")))?;
            headers.insert(name, value);
        }

        let http = match self.http {
            Some(client) => client,
            None => reqwest::Client::builder()
                .connect_timeout(self.connect_timeout.unwrap_or(Duration::from_secs(5)))
                .timeout(timeout)
                // Never follow redirects: that would replay the Authorization header elsewhere.
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .map_err(|e| Error::Config(format!("http client: {}", e.without_url())))?,
        };
        Ok(Client {
            inner: Arc::new(Inner {
                http,
                base,
                token: self.token,
                headers,
                retry: self.retry.unwrap_or_default(),
                timeout,
                max_response_bytes: self.max_response_bytes.unwrap_or(8 * 1024 * 1024),
            }),
        })
    }
}

fn is_loopback(url: &Url) -> bool {
    match url.host() {
        Some(url::Host::Domain(domain)) => domain.eq_ignore_ascii_case("localhost"),
        Some(url::Host::Ipv4(ip)) => ip.is_loopback(),
        Some(url::Host::Ipv6(ip)) => ip.is_loopback(),
        None => false,
    }
}

impl Client {
    /// Start a builder.
    #[must_use]
    pub fn builder() -> ClientBuilder {
        ClientBuilder::default()
    }

    /// Authorization decisions (check, batch, list, explain, revision).
    #[must_use]
    pub fn authorization(&self) -> crate::authorization::AuthorizationClient {
        crate::authorization::AuthorizationClient::new(self.clone())
    }

    /// Every other `/api/v1` operation (generated from the OpenAPI document).
    #[must_use]
    pub fn admin(&self) -> crate::admin::AdminClient {
        crate::admin::AdminClient::new(self.clone())
    }

    /// Execute a low-level call.
    pub async fn execute<T: DeserializeOwned>(
        &self,
        call: Call,
        opts: &CallOptions,
    ) -> Result<Response<T>> {
        opts.validate(call.class)?;
        let request_id = match &opts.request_id {
            Some(id) if is_valid_request_id(id) => id.clone(),
            _ => new_request_id(),
        };
        let trace = opts.trace.unwrap_or_else(TraceContext::new_root);
        let url = self.url_for(&call)?;
        let headers = self.build_headers(&call, opts, &request_id, &trace).await?;

        let writes_resend = matches!(call.class, RetryClass::Write)
            && opts.retry_writes
            && opts.idempotency_key.is_some();
        let limit = match call.class {
            RetryClass::Read => self.inner.retry.max_retries,
            RetryClass::Write if writes_resend => self.inner.retry.max_write_retries,
            RetryClass::Write => 0,
        };

        let mut retries_done = 0u32;
        loop {
            match self
                .attempt::<T>(&call, &url, &headers, retries_done + 1)
                .await
            {
                Ok(response) => return Ok(response),
                Err(mut error) => {
                    if let Error::Transport(transport) = &mut error {
                        transport.attempts = retries_done + 1;
                    }
                    let delay = if retries_done < limit {
                        self.retry_delay(&error, call.class, retries_done)
                    } else {
                        None
                    };
                    let Some(delay) = delay else {
                        return Err(error);
                    };
                    #[cfg(feature = "tracing")]
                    tracing::debug!(request_id = %request_id, retries_done, ?delay, "retrying");
                    tokio::time::sleep(delay).await;
                    retries_done += 1;
                }
            }
        }
    }

    pub(crate) fn url_for(&self, call: &Call) -> Result<Url> {
        let mut url = self.inner.base.clone();
        {
            let mut segments = url
                .path_segments_mut()
                .map_err(|()| Error::Config("base_url cannot be a base".to_owned()))?;
            segments.pop_if_empty();
            segments.extend(call.segments.iter().map(String::as_str));
        }
        if !call.query.is_empty() {
            url.query_pairs_mut()
                .extend_pairs(call.query.iter().map(|(k, v)| (k.as_str(), v.as_str())));
        }
        Ok(url)
    }

    async fn build_headers(
        &self,
        call: &Call,
        opts: &CallOptions,
        request_id: &str,
        trace: &TraceContext,
    ) -> Result<HeaderMap> {
        let mut headers = self.inner.headers.clone();
        headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
        if let Some(provider) = &self.inner.token {
            let token: Secret = provider.token().await?;
            let mut value =
                HeaderValue::from_str(&format!("Bearer {}", token.expose())).map_err(|_| {
                    Error::Auth("token contains characters not allowed in a header".to_owned())
                })?;
            value.set_sensitive(true);
            headers.insert(AUTHORIZATION, value);
        }
        headers.insert(
            HeaderName::from_static(X_REQUEST_ID),
            HeaderValue::from_str(request_id)
                .map_err(|_| Error::Config("bad request id".to_owned()))?,
        );
        headers.insert(
            HeaderName::from_static(TRACEPARENT),
            HeaderValue::from_str(&trace.traceparent())
                .map_err(|_| Error::Config("bad traceparent".to_owned()))?,
        );
        if let Some(key) = &opts.idempotency_key {
            headers.insert(
                HeaderName::from_static("idempotency-key"),
                HeaderValue::from_str(key)
                    .map_err(|_| Error::Config("invalid idempotency_key".to_owned()))?,
            );
        }
        if let Some(value) = &opts.if_match {
            headers.insert(
                IF_MATCH,
                HeaderValue::from_str(value)
                    .map_err(|_| Error::Config("invalid if_match".to_owned()))?,
            );
        }
        if call.body.is_some() {
            headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        }
        Ok(headers)
    }

    fn retry_delay(&self, error: &Error, class: RetryClass, retries_done: u32) -> Option<Duration> {
        let policy = &self.inner.retry;
        let backoff = policy.backoff(retries_done);
        match error {
            Error::Api(api) => match api.known_code? {
                ErrorCode::RateLimited => Some(api.retry_after_seconds.map_or(backoff, |s| {
                    Duration::from_secs(s).min(policy.max_retry_after)
                })),
                ErrorCode::NotReady => Some(backoff),
                ErrorCode::IdempotencyInProgress if class == RetryClass::Write => Some(backoff),
                // Everything else — notably AUTHZ_RELOAD_PENDING, IDENTITY_RELOAD_PENDING and
                // CURSOR_STALE — is never retried automatically.
                _ => None,
            },
            Error::Transport(t) => match t.kind {
                TransportKind::Timeout
                | TransportKind::ConnectionReset
                | TransportKind::Connect => Some(backoff),
                TransportKind::Tls | TransportKind::Other => None,
            },
            _ => None,
        }
    }

    async fn attempt<T: DeserializeOwned>(
        &self,
        call: &Call,
        url: &Url,
        headers: &HeaderMap,
        attempts: u32,
    ) -> Result<Response<T>> {
        let mut builder = self
            .inner
            .http
            .request(call.method.clone(), url.clone())
            .timeout(self.inner.timeout)
            .headers(headers.clone());
        if let Some(body) = &call.body {
            builder = builder.body(body.clone());
        }
        let mut response = builder
            .send()
            .await
            .map_err(|e| transport(e, call.class, attempts))?;
        let status = response.status();
        let response_headers = response.headers().clone();

        if response
            .content_length()
            .is_some_and(|length| length > self.inner.max_response_bytes as u64)
        {
            return Err(protocol(
                ProtocolKind::ResponseTooLarge,
                status,
                &response_headers,
                Some("content-length exceeds limit"),
            ));
        }
        let mut body: Vec<u8> = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|e| transport(e, call.class, attempts))?
        {
            if body.len() + chunk.len() > self.inner.max_response_bytes {
                return Err(protocol(
                    ProtocolKind::ResponseTooLarge,
                    status,
                    &response_headers,
                    Some("body exceeds limit"),
                ));
            }
            body.extend_from_slice(&chunk);
        }

        if status.is_success() {
            parse_success::<T>(
                status,
                &response_headers,
                &body,
                call.shape,
                call.require_page,
            )
        } else if status.is_client_error() || status.is_server_error() {
            Err(parse_error(status, &response_headers, &body))
        } else {
            Err(protocol(
                ProtocolKind::UnexpectedStatus,
                status,
                &response_headers,
                None,
            ))
        }
    }
}

fn transport(error: reqwest::Error, class: RetryClass, attempts: u32) -> Error {
    let connect = error.is_connect();
    let timeout = error.is_timeout();
    let error = error.without_url();
    let chain = {
        let mut text = error.to_string();
        let mut source = std::error::Error::source(&error);
        while let Some(inner) = source {
            text.push_str(": ");
            text.push_str(&inner.to_string());
            source = inner.source();
        }
        text
    };
    let lowered = chain.to_ascii_lowercase();
    // Transport-layer introspection only; API error classification never looks at text.
    let tls = lowered.contains("certificate")
        || lowered.contains("tls")
        || lowered.contains("handshake")
        || lowered.contains("received corrupt message");
    let kind = if timeout {
        TransportKind::Timeout
    } else if tls {
        TransportKind::Tls
    } else if connect {
        TransportKind::Connect
    } else if error.is_request() || error.is_body() || error.is_decode() {
        TransportKind::ConnectionReset
    } else {
        TransportKind::Other
    };
    // A write that may have reached the server has an unknown outcome. Failures before the
    // request could be processed (connect, TLS) did not commit anything.
    let commit_state =
        if matches!(class, RetryClass::Write) && !connect && kind != TransportKind::Tls {
            Some(CommitState::Unknown)
        } else {
            None
        };
    Error::Transport(TransportError {
        kind,
        commit_state,
        detail: chain,
        attempts,
    })
}

fn header_str<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    headers.get(name).and_then(|v| v.to_str().ok())
}

fn header_request_id(headers: &HeaderMap) -> Option<String> {
    header_str(headers, X_REQUEST_ID)
        .filter(|v| is_valid_request_id(v))
        .map(str::to_owned)
}

fn header_trace_id(headers: &HeaderMap) -> Option<String> {
    header_str(headers, X_TRACE_ID)
        .filter(|v| is_valid_trace_id(v))
        .map(str::to_owned)
        .or_else(|| {
            header_str(headers, TRACEPARENT)
                .and_then(TraceContext::parse)
                .map(|ctx| ctx.trace_id_hex())
        })
}

fn protocol(
    kind: ProtocolKind,
    status: StatusCode,
    headers: &HeaderMap,
    detail: Option<&str>,
) -> Error {
    Error::Protocol(ProtocolError {
        kind,
        status: status.as_u16(),
        request_id: header_request_id(headers),
        trace_id: header_trace_id(headers),
        detail: detail.map(str::to_owned),
    })
}

fn category(error: &serde_json::Error) -> String {
    // Never include serde's message: it can quote request/response values.
    format!(
        "{:?} error at line {}, column {}",
        error.classify(),
        error.line(),
        error.column()
    )
}

fn parse_success<T: DeserializeOwned>(
    status: StatusCode,
    headers: &HeaderMap,
    body: &[u8],
    shape: Shape,
    require_page: bool,
) -> Result<Response<T>> {
    if let Err(error) = serde_json::from_slice::<serde::de::IgnoredAny>(body) {
        return Err(protocol(
            ProtocolKind::NonJsonResponse,
            status,
            headers,
            Some(&category(&error)),
        ));
    }
    let (data, meta, page) = match shape {
        Shape::Envelope => match serde_json::from_slice::<Envelope<T>>(body) {
            Ok(envelope) => (envelope.data, envelope.meta, envelope.page),
            Err(error) => {
                return Err(protocol(
                    ProtocolKind::MalformedSuccessBody,
                    status,
                    headers,
                    Some(&category(&error)),
                ));
            }
        },
        Shape::Bare => match serde_json::from_slice::<T>(body) {
            Ok(data) => (data, None, None),
            Err(error) => {
                return Err(protocol(
                    ProtocolKind::MalformedSuccessBody,
                    status,
                    headers,
                    Some(&category(&error)),
                ));
            }
        },
    };
    if require_page && page.is_none() {
        return Err(protocol(
            ProtocolKind::MalformedSuccessBody,
            status,
            headers,
            Some("missing pagination metadata"),
        ));
    }
    Ok(Response {
        status: status.as_u16(),
        data,
        meta,
        page,
        request_id: header_request_id(headers),
        trace_id: header_trace_id(headers),
        etag: header_str(headers, "etag").map(str::to_owned),
        authz_revision_header: header_str(headers, "x-authz-revision").and_then(|v| v.parse().ok()),
        replayed: header_str(headers, "idempotency-replayed")
            .is_some_and(|v| v.eq_ignore_ascii_case("true")),
    })
}

fn parse_error(status: StatusCode, headers: &HeaderMap, body: &[u8]) -> Error {
    let value = match serde_json::from_slice::<serde_json::Value>(body) {
        Ok(value) => value,
        Err(error) => {
            return protocol(
                ProtocolKind::NonJsonResponse,
                status,
                headers,
                Some(&category(&error)),
            );
        }
    };
    let Some(object) = value.get("error").and_then(serde_json::Value::as_object) else {
        return protocol(
            ProtocolKind::MalformedErrorBody,
            status,
            headers,
            Some("missing error object"),
        );
    };
    let Some(code) = object.get("code").and_then(serde_json::Value::as_str) else {
        return protocol(
            ProtocolKind::MalformedErrorBody,
            status,
            headers,
            Some("missing error.code"),
        );
    };

    let header_id = header_request_id(headers);
    let body_id = object
        .get("request_id")
        .and_then(serde_json::Value::as_str)
        .filter(|v| is_valid_request_id(v))
        .map(str::to_owned);
    let mismatch = matches!((&header_id, &body_id), (Some(h), Some(b)) if h != b);
    let request_id = header_id.clone().or_else(|| body_id.clone());

    let trace_id = object
        .get("trace_id")
        .and_then(serde_json::Value::as_str)
        .filter(|v| is_valid_trace_id(v))
        .map(str::to_owned)
        .or_else(|| header_trace_id(headers));

    let violations = object
        .get("details")
        .and_then(|d| d.get("violations"))
        .and_then(serde_json::Value::as_array)
        .map(|list| {
            list.iter()
                .filter_map(|item| {
                    Some(Violation {
                        path: item
                            .get("path")
                            .and_then(serde_json::Value::as_str)
                            .map(str::to_owned),
                        reason: item.get("reason")?.as_str()?.to_owned(),
                        message: item
                            .get("message")
                            .and_then(serde_json::Value::as_str)
                            .unwrap_or_default()
                            .to_owned(),
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let retry_after_seconds = object
        .get("retry_after_seconds")
        .and_then(serde_json::Value::as_u64)
        .or_else(|| header_str(headers, "retry-after").and_then(|v| v.trim().parse().ok()));

    Error::Api(Box::new(ApiError {
        status: status.as_u16(),
        code: code.to_owned(),
        known_code: ErrorCode::parse(code),
        message: object
            .get("message")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default()
            .to_owned(),
        request_id,
        body_request_id: if mismatch { body_id } else { None },
        request_id_mismatch: mismatch,
        trace_id,
        details: object.get("details").cloned(),
        violations,
        retryable: object.get("retryable").and_then(serde_json::Value::as_bool),
        retry_after_seconds,
        commit_state: match object
            .get("commit_state")
            .and_then(serde_json::Value::as_str)
        {
            Some("committed_unpublished") => Some(CommitState::CommittedUnpublished),
            _ => None,
        },
    }))
}
