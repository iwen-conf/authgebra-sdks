//! Official Rust client for the Authgebra `/api/v1` HTTP API.
//!
//! This crate is a thin, typed wrapper. It contains **no authorization logic**: decisions are
//! computed by the server. It honestly separates three outcomes:
//!
//! * `Ok(Response<_>)` — the server answered successfully. A `decision = deny` is `Ok`.
//! * `Err(Error::Api(_))` — the server answered with the error envelope (`NOT_READY`,
//!   `AUTHZ_RELOAD_PENDING`, ...). Never converted into a deny.
//! * `Err(Error::Transport(_))` / `Err(Error::Protocol(_))` — no usable answer.
//!
//! See `README.md` for retry, idempotency and trace rules.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod admin;
pub mod authorization;
pub mod client;
pub mod config;
pub mod error;
#[allow(missing_docs, clippy::all, clippy::pedantic, clippy::nursery)]
pub mod generated;
pub mod models;
pub mod serde_helpers;
pub mod trace;

pub use admin::AdminClient;
pub use authorization::{AuthorizationClient, ListResourcesPager};
pub use client::{Call, Client, ClientBuilder, Meta, Page, Response, RetryClass, Shape};
pub use config::{CallOptions, RetryPolicy, Secret, StaticToken, TokenProvider};
pub use error::{
    ApiError, CommitState, Error, ErrorCode, ProtocolError, ProtocolKind, Result, TransportError,
    TransportKind, Violation,
};
pub use reqwest::Method;
pub use trace::TraceContext;

/// SDK version.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
