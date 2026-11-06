package authgebra

import (
	"net/http"
)

// Result is a successful response. A deny decision is a successful Result.
type Result[T any] struct {
	// Data is the business payload.
	Data T
	// Meta is nil when the endpoint returns none (e.g. admin reads). It is never
	// filled with a fake revision 0.
	Meta *Meta
	// Page is set for list endpoints.
	Page *Page
	// Status is the HTTP status.
	Status int
	// RequestID is the response X-Request-ID (valid values only).
	RequestID string
	// TraceID is the W3C trace id from X-Trace-ID / traceparent (valid values only).
	TraceID string
	// ETag is the response ETag header, if any.
	ETag string
	// Replayed is true when the server replayed a stored idempotent response.
	Replayed bool
	// AuthzRevisionHeader is X-Authz-Revision when present.
	AuthzRevisionHeader *int64
	// Header is the raw response header (may contain Set-Cookie; never logged by the SDK).
	Header http.Header
}

// Decision values of CheckResponse.Decision.
const (
	DecisionAllow = "allow"
	DecisionDeny  = "deny"
)

// CallOption customizes a single call.
type CallOption func(*callConfig)

type callConfig struct {
	requestID   string
	traceparent string
	tracestate  string
	noAuth      bool
	header      http.Header
	retryWrites *bool
}

// WithRequestID sets X-Request-ID. Invalid values are discarded and replaced with a generated ID.
func WithRequestID(id string) CallOption { return func(c *callConfig) { c.requestID = id } }

// WithTraceparent propagates an existing W3C trace context (and optional tracestate).
// An invalid traceparent makes the call fail before sending.
func WithTraceparent(traceparent, tracestate string) CallOption {
	return func(c *callConfig) { c.traceparent, c.tracestate = traceparent, tracestate }
}

// WithoutAuth omits the Authorization header (login, refresh, health, setup).
func WithoutAuth() CallOption { return func(c *callConfig) { c.noAuth = true } }

// WithHeader adds a header to this call only (SDK-managed headers cannot be overridden).
func WithHeader(key, value string) CallOption {
	return func(c *callConfig) {
		if c.header == nil {
			c.header = http.Header{}
		}
		c.header.Add(key, value)
	}
}

// WithRetryWrites overrides RetryPolicy.RetryWrites for this call. It still only
// applies when the request carries an Idempotency-Key.
func WithRetryWrites(enabled bool) CallOption {
	return func(c *callConfig) { c.retryWrites = &enabled }
}

type ctxKey int

const traceparentKey ctxKey = 1
