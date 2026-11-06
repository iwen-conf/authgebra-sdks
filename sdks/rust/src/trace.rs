//! W3C Trace Context helpers and identifier validation shared by request and response paths.
//!
//! `request_id` (one HTTP request), `trace_id` (one distributed call chain) and `span_id`
//! (one execution slice) are three different things and are never mixed.

use std::fmt;

/// A W3C `traceparent` value (version `00`).
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct TraceContext {
    trace_id: [u8; 16],
    span_id: [u8; 8],
    sampled: bool,
}

impl TraceContext {
    /// A fresh root context (random non-zero ids, not sampled).
    #[must_use]
    pub fn new_root() -> Self {
        Self {
            trace_id: random_nonzero::<16>(),
            span_id: random_nonzero::<8>(),
            sampled: false,
        }
    }

    /// Same trace, new span: use for each outgoing call made inside an existing trace.
    #[must_use]
    pub fn child(&self) -> Self {
        Self {
            trace_id: self.trace_id,
            span_id: random_nonzero::<8>(),
            sampled: self.sampled,
        }
    }

    /// Set the sampled flag.
    #[must_use]
    pub fn with_sampled(mut self, sampled: bool) -> Self {
        self.sampled = sampled;
        self
    }

    /// Strictly parse a `traceparent` header value; `None` if invalid.
    #[must_use]
    pub fn parse(raw: &str) -> Option<Self> {
        let bytes = raw.as_bytes();
        if bytes.len() < 55 || bytes[2] != b'-' || bytes[35] != b'-' || bytes[52] != b'-' {
            return None;
        }
        let version = decode::<1>(&raw[0..2])?[0];
        if version == 0xff {
            return None;
        }
        if version == 0 {
            if bytes.len() != 55 {
                return None;
            }
        } else if bytes.len() > 55 && bytes[55] != b'-' {
            return None;
        }
        let trace_id = decode::<16>(&raw[3..35])?;
        let span_id = decode::<8>(&raw[36..52])?;
        let flags = decode::<1>(&raw[53..55])?[0];
        if trace_id == [0; 16] || span_id == [0; 8] {
            return None;
        }
        Some(Self {
            trace_id,
            span_id,
            sampled: flags & 1 == 1,
        })
    }

    /// The `traceparent` header value.
    #[must_use]
    pub fn traceparent(&self) -> String {
        format!(
            "00-{}-{}-{:02x}",
            self.trace_id_hex(),
            hex(&self.span_id),
            u8::from(self.sampled)
        )
    }

    /// 32 lowercase hex characters.
    #[must_use]
    pub fn trace_id_hex(&self) -> String {
        hex(&self.trace_id)
    }

    /// 16 lowercase hex characters.
    #[must_use]
    pub fn span_id_hex(&self) -> String {
        hex(&self.span_id)
    }
}

impl fmt::Debug for TraceContext {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "TraceContext({})", self.traceparent())
    }
}

/// `true` for 32 lowercase hex characters that are not all zero.
#[must_use]
pub fn is_valid_trace_id(value: &str) -> bool {
    value.len() == 32 && decode::<16>(value).is_some_and(|bytes| bytes != [0; 16])
}

/// `^[A-Za-z0-9._-]{8,128}$`.
#[must_use]
pub fn is_valid_request_id(value: &str) -> bool {
    (8..=128).contains(&value.len())
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

/// A fresh request id (`sdk-` + 32 hex).
#[must_use]
pub fn new_request_id() -> String {
    format!("sdk-{}", hex(&random_nonzero::<16>()))
}

pub(crate) fn random_u64() -> u64 {
    u64::from_le_bytes(random_nonzero::<8>())
}

fn random_nonzero<const N: usize>() -> [u8; N] {
    loop {
        let mut bytes = [0u8; N];
        // The OS RNG failing is an environment fault, not a recoverable condition; falling
        // back to predictable ids would silently weaken correlation, so fail loudly.
        getrandom::fill(&mut bytes).expect("operating system random source unavailable");
        if bytes != [0u8; N] {
            return bytes;
        }
    }
}

fn hex(bytes: &[u8]) -> String {
    use std::fmt::Write as _;
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        let _ = write!(out, "{byte:02x}");
    }
    out
}

fn decode<const N: usize>(text: &str) -> Option<[u8; N]> {
    let bytes = text.as_bytes();
    if bytes.len() != N * 2 {
        return None;
    }
    let nibble = |b: u8| match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        _ => None,
    };
    let mut out = [0u8; N];
    for (index, slot) in out.iter_mut().enumerate() {
        *slot = (nibble(bytes[index * 2])? << 4) | nibble(bytes[index * 2 + 1])?;
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    const VALID: &str = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";

    #[test]
    fn parses_and_round_trips() {
        let ctx = TraceContext::parse(VALID).expect("valid");
        assert_eq!(ctx.traceparent(), VALID);
        assert_eq!(
            ctx.child().trace_id_hex(),
            "4bf92f3577b34da6a3ce929d0e0e4736"
        );
        assert_ne!(ctx.child().span_id_hex(), "00f067aa0ba902b7");
    }

    #[test]
    fn rejects_what_the_server_rejects() {
        let upper = VALID.to_uppercase();
        for bad in [
            "",
            upper.as_str(),
            "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01-x",
        ] {
            assert!(TraceContext::parse(bad).is_none(), "{bad:?}");
        }
    }

    #[test]
    fn id_validators() {
        assert!(is_valid_trace_id("4bf92f3577b34da6a3ce929d0e0e4736"));
        assert!(!is_valid_trace_id("NOT-HEX"));
        assert!(!is_valid_trace_id(&"0".repeat(32)));
        assert!(is_valid_request_id("req-abcdefgh"));
        assert!(!is_valid_request_id("bad id\twith ctrl"));
        assert!(!is_valid_request_id("short"));
        assert!(is_valid_request_id(&new_request_id()));
    }
}
