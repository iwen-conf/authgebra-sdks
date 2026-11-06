//! Client configuration, token providers, retry policy and per-call options.

use std::fmt;
use std::future::Future;
use std::pin::Pin;
use std::time::Duration;

use crate::error::{Error, Result};
use crate::trace::TraceContext;

/// A credential. `Debug` never prints the value; there is no `Display`.
#[derive(Clone)]
pub struct Secret(String);

impl Secret {
    /// Wrap a credential.
    #[must_use]
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    /// The raw value. Only the transport layer should call this.
    #[must_use]
    pub fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Secret(<redacted, {} bytes>)", self.0.len())
    }
}

/// Supplies the bearer access token for each call (so callers can refresh tokens).
pub trait TokenProvider: Send + Sync + 'static {
    /// Return the current access token.
    fn token(&self) -> Pin<Box<dyn Future<Output = Result<Secret>> + Send + '_>>;
}

/// A fixed token (tests, scripts). Production callers should implement [`TokenProvider`].
#[derive(Debug, Clone)]
pub struct StaticToken(Secret);

impl StaticToken {
    /// Wrap a token.
    #[must_use]
    pub fn new(token: impl Into<String>) -> Self {
        Self(Secret::new(token))
    }
}

impl TokenProvider for StaticToken {
    fn token(&self) -> Pin<Box<dyn Future<Output = Result<Secret>> + Send + '_>> {
        let secret = self.0.clone();
        Box::pin(async move { Ok(secret) })
    }
}

/// Bounded retry policy. There is no "unlimited" setting.
#[derive(Debug, Clone)]
pub struct RetryPolicy {
    /// Extra attempts for reads (default 2, so at most 3 tries).
    pub max_retries: u32,
    /// Extra attempts for writes; only used when the call opts in and carries an
    /// `Idempotency-Key` (default 2).
    pub max_write_retries: u32,
    /// First backoff (default 100 ms); doubles each retry.
    pub base_backoff: Duration,
    /// Backoff ceiling (default 2 s).
    pub max_backoff: Duration,
    /// Cap applied to a server `Retry-After` (default 5 s).
    pub max_retry_after: Duration,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_retries: 2,
            max_write_retries: 2,
            base_backoff: Duration::from_millis(100),
            max_backoff: Duration::from_secs(2),
            max_retry_after: Duration::from_secs(5),
        }
    }
}

impl RetryPolicy {
    /// Never retry anything.
    #[must_use]
    pub fn none() -> Self {
        Self {
            max_retries: 0,
            max_write_retries: 0,
            ..Self::default()
        }
    }

    pub(crate) fn backoff(&self, retries_done: u32) -> Duration {
        let exp = self
            .base_backoff
            .saturating_mul(1u32 << retries_done.min(16));
        let capped = exp.min(self.max_backoff);
        // Equal jitter in [0.5, 1.0] of the capped value.
        let jitter = 0.5 + (crate::trace::random_u64() % 1000) as f64 / 2000.0;
        capped.mul_f64(jitter)
    }
}

/// Per-call options. `Default` sends nothing extra and never retries a write.
#[derive(Debug, Clone, Default)]
pub struct CallOptions {
    /// Caller-supplied `X-Request-ID` (must match `^[A-Za-z0-9._-]{8,128}$`); generated if absent.
    pub request_id: Option<String>,
    /// Join an existing trace; a new root trace is created if absent.
    pub trace: Option<TraceContext>,
    /// `Idempotency-Key` for writes.
    pub idempotency_key: Option<String>,
    /// `If-Match` (entity ETag/version) for conditional writes.
    pub if_match: Option<String>,
    /// Allow bounded automatic retry of a **write**. Requires `idempotency_key`; the same key
    /// and the same bytes are resent. Off by default.
    pub retry_writes: bool,
}

impl CallOptions {
    /// Empty options.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Set the idempotency key.
    #[must_use]
    pub fn idempotency_key(mut self, key: impl Into<String>) -> Self {
        self.idempotency_key = Some(key.into());
        self
    }

    /// Set `If-Match`.
    #[must_use]
    pub fn if_match(mut self, value: impl Into<String>) -> Self {
        self.if_match = Some(value.into());
        self
    }

    /// Set the request id.
    #[must_use]
    pub fn request_id(mut self, id: impl Into<String>) -> Self {
        self.request_id = Some(id.into());
        self
    }

    /// Join a trace.
    #[must_use]
    pub fn trace(mut self, trace: TraceContext) -> Self {
        self.trace = Some(trace);
        self
    }

    /// Opt in to bounded write retries (requires an idempotency key).
    #[must_use]
    pub fn retry_writes(mut self, enabled: bool) -> Self {
        self.retry_writes = enabled;
        self
    }

    pub(crate) fn validate(&self, class: crate::client::RetryClass) -> Result<()> {
        if self.retry_writes
            && matches!(class, crate::client::RetryClass::Write)
            && self.idempotency_key.is_none()
        {
            return Err(Error::Config(
                "retry_writes requires an idempotency_key (a write is only resent under the same key)"
                    .to_owned(),
            ));
        }
        Ok(())
    }
}
