//! Error model. Three disjoint worlds: [`ApiError`] (the server answered with the
//! `{"error":{..}}` envelope), [`TransportError`] (no usable answer arrived) and
//! [`ProtocolError`] (an answer arrived but is not understandable).
//!
//! Callers must branch on [`ApiError::code`] / [`ApiError::known_code`], never on message text.

use std::fmt;

/// Error codes known to this SDK version (server contract §2). The server may add codes;
/// an unknown code is preserved verbatim in [`ApiError::code`] with `known_code == None`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum ErrorCode {
    /// 400: malformed request.
    InvalidRequest,
    /// 400: cursor unparsable or bound to a different query.
    CursorInvalid,
    /// 401: not authenticated.
    AuthRequired,
    /// 401: wrong credentials.
    InvalidCredentials,
    /// 401: access token expired.
    TokenExpired,
    /// 403: authenticated but not permitted.
    Forbidden,
    /// 404: not found (or hidden).
    ResourceNotFound,
    /// 409: `If-Match` version mismatch.
    RevisionConflict,
    /// 409: not allowed in the current state.
    StateConflict,
    /// 409: same key, different payload.
    IdempotencyConflict,
    /// 409: same key is in flight.
    IdempotencyInProgress,
    /// 409: cursor from an older snapshot generation; restart pagination explicitly.
    CursorStale,
    /// 409: resource key collides with a subject alias.
    ResourceAliasCollision,
    /// 412: missing or malformed `If-Match`.
    PreconditionFailed,
    /// 413: body too large.
    PayloadTooLarge,
    /// 422: semantic validation failed.
    ValidationError,
    /// 422: policy AST invalid.
    PolicyInvalid,
    /// 422: policy exceeds complexity limits.
    PolicyTooComplex,
    /// 429: rate limited.
    RateLimited,
    /// 500: internal error.
    InternalError,
    /// 503: service cannot give a trustworthy decision yet (never a deny).
    NotReady,
    /// 503: committed, snapshot not yet published; never resend.
    AuthzReloadPending,
    /// 503: committed, identity snapshot not yet published; never resend.
    IdentityReloadPending,
}

impl ErrorCode {
    /// Every known code.
    pub const ALL: [Self; 23] = [
        Self::InvalidRequest,
        Self::CursorInvalid,
        Self::AuthRequired,
        Self::InvalidCredentials,
        Self::TokenExpired,
        Self::Forbidden,
        Self::ResourceNotFound,
        Self::RevisionConflict,
        Self::StateConflict,
        Self::IdempotencyConflict,
        Self::IdempotencyInProgress,
        Self::CursorStale,
        Self::ResourceAliasCollision,
        Self::PreconditionFailed,
        Self::PayloadTooLarge,
        Self::ValidationError,
        Self::PolicyInvalid,
        Self::PolicyTooComplex,
        Self::RateLimited,
        Self::InternalError,
        Self::NotReady,
        Self::AuthzReloadPending,
        Self::IdentityReloadPending,
    ];

    /// Wire string.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::InvalidRequest => "INVALID_REQUEST",
            Self::CursorInvalid => "CURSOR_INVALID",
            Self::AuthRequired => "AUTH_REQUIRED",
            Self::InvalidCredentials => "INVALID_CREDENTIALS",
            Self::TokenExpired => "TOKEN_EXPIRED",
            Self::Forbidden => "FORBIDDEN",
            Self::ResourceNotFound => "RESOURCE_NOT_FOUND",
            Self::RevisionConflict => "REVISION_CONFLICT",
            Self::StateConflict => "STATE_CONFLICT",
            Self::IdempotencyConflict => "IDEMPOTENCY_CONFLICT",
            Self::IdempotencyInProgress => "IDEMPOTENCY_IN_PROGRESS",
            Self::CursorStale => "CURSOR_STALE",
            Self::ResourceAliasCollision => "RESOURCE_ALIAS_COLLISION",
            Self::PreconditionFailed => "PRECONDITION_FAILED",
            Self::PayloadTooLarge => "PAYLOAD_TOO_LARGE",
            Self::ValidationError => "VALIDATION_ERROR",
            Self::PolicyInvalid => "POLICY_INVALID",
            Self::PolicyTooComplex => "POLICY_TOO_COMPLEX",
            Self::RateLimited => "RATE_LIMITED",
            Self::InternalError => "INTERNAL_ERROR",
            Self::NotReady => "NOT_READY",
            Self::AuthzReloadPending => "AUTHZ_RELOAD_PENDING",
            Self::IdentityReloadPending => "IDENTITY_RELOAD_PENDING",
        }
    }

    /// Parse a wire string; `None` for codes this SDK does not know.
    #[must_use]
    pub fn parse(code: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|known| known.as_str() == code)
    }
}

impl fmt::Display for ErrorCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Commit state of a write, **only when known**.
///
/// `None` on an error means "nothing is claimed", which is **not** "not committed".
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum CommitState {
    /// Server-declared: the transaction committed but the snapshot is not yet published.
    /// The mutation **has been executed**; never resend it.
    CommittedUnpublished,
    /// SDK-inferred: a write timed out or the connection broke after the request may have
    /// been sent. It may or may not have been applied. Reconcile with the same
    /// `Idempotency-Key` or by reading the entity; never assume it was not committed.
    Unknown,
}

impl CommitState {
    /// Stable lowercase name.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::CommittedUnpublished => "committed_unpublished",
            Self::Unknown => "unknown",
        }
    }
}

/// One field-level violation from `error.details.violations[]`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Violation {
    /// JSON Pointer; `None` when the server could not locate the field reliably.
    pub path: Option<String>,
    /// Stable machine reason (snake_case).
    pub reason: String,
    /// Human text; never use it for control flow.
    pub message: String,
}

/// The server answered with the error envelope.
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct ApiError {
    /// HTTP status.
    pub status: u16,
    /// Raw wire code (always preserved, even when unknown to this SDK).
    pub code: String,
    /// `Some` only when `code` is a code this SDK version knows.
    pub known_code: Option<ErrorCode>,
    /// Server message (safe to log).
    pub message: String,
    /// Request id: the `X-Request-ID` header when valid, else the body value.
    pub request_id: Option<String>,
    /// Request id carried in the body when it differs from the (valid) header.
    pub body_request_id: Option<String>,
    /// Header and body request ids both present, valid and different.
    pub request_id_mismatch: bool,
    /// W3C trace id (32 lowercase hex) from the body, else `X-Trace-ID`/`traceparent`.
    pub trace_id: Option<String>,
    /// Raw `error.details`, if any.
    pub details: Option<serde_json::Value>,
    /// `details.violations[]` in server order.
    pub violations: Vec<Violation>,
    /// Server hint: `Some(true)`/`Some(false)`; `None` = not declared. Not a permission to
    /// resend a write.
    pub retryable: Option<bool>,
    /// Suggested wait (body `retry_after_seconds`, else `Retry-After` header).
    pub retry_after_seconds: Option<u64>,
    /// Server-declared commit state.
    pub commit_state: Option<CommitState>,
}

impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} {}: {}", self.status, self.code, self.message)?;
        if let Some(id) = &self.request_id {
            write!(f, " (request_id={id})")?;
        }
        Ok(())
    }
}

impl std::error::Error for ApiError {}

/// Why no usable answer arrived.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum TransportKind {
    /// Could not connect (nothing was sent).
    Connect,
    /// Deadline exceeded after connecting.
    Timeout,
    /// Connection closed/reset before a complete response.
    ConnectionReset,
    /// TLS handshake or certificate failure (nothing was processed).
    Tls,
    /// Anything else.
    Other,
}

impl TransportKind {
    /// Stable name used by the cross-language fixtures.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Connect => "connect",
            Self::Timeout => "timeout",
            Self::ConnectionReset => "connection_reset",
            Self::Tls => "tls",
            Self::Other => "other",
        }
    }
}

/// No usable HTTP answer.
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct TransportError {
    /// Classification.
    pub kind: TransportKind,
    /// `Some(Unknown)` for writes that may have reached the server; `None` otherwise.
    pub commit_state: Option<CommitState>,
    /// Underlying error text with the URL stripped.
    pub detail: String,
    /// Attempts made (1 = no retry happened).
    pub attempts: u32,
}

impl fmt::Display for TransportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} after {} attempt(s): {}",
            self.kind.as_str(),
            self.attempts,
            self.detail
        )?;
        if let Some(state) = self.commit_state {
            write!(f, " [commit_state={}]", state.as_str())?;
        }
        Ok(())
    }
}

impl std::error::Error for TransportError {}

/// What was wrong with an answer that did arrive.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum ProtocolKind {
    /// Body is not JSON.
    NonJsonResponse,
    /// Non-2xx JSON without `error.code`.
    MalformedErrorBody,
    /// 2xx JSON that does not match the success contract (for example no `data`).
    MalformedSuccessBody,
    /// Body larger than the configured limit.
    ResponseTooLarge,
    /// 1xx/3xx status the SDK does not follow.
    UnexpectedStatus,
}

impl ProtocolKind {
    /// Stable name used by the cross-language fixtures.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::NonJsonResponse => "non_json_response",
            Self::MalformedErrorBody => "malformed_error_body",
            Self::MalformedSuccessBody => "malformed_success_body",
            Self::ResponseTooLarge => "response_too_large",
            Self::UnexpectedStatus => "unexpected_status",
        }
    }
}

/// An answer that could not be understood. The body text is deliberately not retained.
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct ProtocolError {
    /// Classification.
    pub kind: ProtocolKind,
    /// HTTP status.
    pub status: u16,
    /// Valid `X-Request-ID`, if any.
    pub request_id: Option<String>,
    /// Valid trace id, if any.
    pub trace_id: Option<String>,
    /// Structural detail (never body content).
    pub detail: Option<String>,
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} (HTTP {})", self.kind.as_str(), self.status)?;
        if let Some(detail) = &self.detail {
            write!(f, ": {detail}")?;
        }
        Ok(())
    }
}

impl std::error::Error for ProtocolError {}

/// Every failure a call can produce.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum Error {
    /// The server returned the error envelope.
    #[error("{0}")]
    Api(Box<ApiError>),
    /// No usable answer.
    #[error("transport error: {0}")]
    Transport(TransportError),
    /// Unintelligible answer.
    #[error("protocol error: {0}")]
    Protocol(ProtocolError),
    /// Invalid client configuration or call options (nothing was sent).
    #[error("invalid configuration: {0}")]
    Config(String),
    /// The request body could not be serialized (nothing was sent).
    #[error("request serialization failed: {0}")]
    Serialize(String),
    /// The token provider failed (nothing was sent).
    #[error("token provider failed: {0}")]
    Auth(String),
    /// A pager stopped at its page cap.
    #[error("pagination stopped after {max_pages} pages")]
    PageLimit {
        /// Configured cap.
        max_pages: usize,
    },
    /// The authorization generation changed between pages; the SDK never merges generations.
    #[error("snapshot generation changed during pagination ({first} -> {now}); restart explicitly")]
    GenerationChanged {
        /// Generation of the first page.
        first: i64,
        /// Generation of the page that disagreed.
        now: i64,
    },
}

impl Error {
    /// The API error, if this is one.
    #[must_use]
    pub fn api(&self) -> Option<&ApiError> {
        match self {
            Self::Api(error) => Some(error),
            _ => None,
        }
    }

    /// Known code, if this is an API error with a code this SDK knows.
    #[must_use]
    pub fn code(&self) -> Option<ErrorCode> {
        self.api().and_then(|error| error.known_code)
    }

    /// Commit state of a write, when known or inferred.
    #[must_use]
    pub fn commit_state(&self) -> Option<CommitState> {
        match self {
            Self::Api(error) => error.commit_state,
            Self::Transport(error) => error.commit_state,
            _ => None,
        }
    }
}

/// Crate result alias.
pub type Result<T> = std::result::Result<T, Error>;
