package authgebra

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
)

// ErrorCode is the server's stable machine error code. It is kept as the raw
// string: codes this SDK does not know are preserved (see APIError.KnownCode).
type ErrorCode string

// Known server error codes (mirrors the OpenAPI ApiErrorCode enum; a test keeps them in sync).
const (
	CodeInvalidRequest         ErrorCode = "INVALID_REQUEST"
	CodeCursorInvalid          ErrorCode = "CURSOR_INVALID"
	CodeAuthRequired           ErrorCode = "AUTH_REQUIRED"
	CodeInvalidCredentials     ErrorCode = "INVALID_CREDENTIALS"
	CodeTokenExpired           ErrorCode = "TOKEN_EXPIRED"
	CodeForbidden              ErrorCode = "FORBIDDEN"
	CodeResourceNotFound       ErrorCode = "RESOURCE_NOT_FOUND"
	CodeRevisionConflict       ErrorCode = "REVISION_CONFLICT"
	CodeStateConflict          ErrorCode = "STATE_CONFLICT"
	CodeIdempotencyConflict    ErrorCode = "IDEMPOTENCY_CONFLICT"
	CodeIdempotencyInProgress  ErrorCode = "IDEMPOTENCY_IN_PROGRESS"
	CodeCursorStale            ErrorCode = "CURSOR_STALE"
	CodeResourceAliasCollision ErrorCode = "RESOURCE_ALIAS_COLLISION"
	CodePreconditionFailed     ErrorCode = "PRECONDITION_FAILED"
	CodePayloadTooLarge        ErrorCode = "PAYLOAD_TOO_LARGE"
	CodeValidationError        ErrorCode = "VALIDATION_ERROR"
	CodePolicyInvalid          ErrorCode = "POLICY_INVALID"
	CodePolicyTooComplex       ErrorCode = "POLICY_TOO_COMPLEX"
	CodeRateLimited            ErrorCode = "RATE_LIMITED"
	CodeInternalError          ErrorCode = "INTERNAL_ERROR"
	CodeNotReady               ErrorCode = "NOT_READY"
	CodeAuthzReloadPending     ErrorCode = "AUTHZ_RELOAD_PENDING"
	CodeIdentityReloadPending  ErrorCode = "IDENTITY_RELOAD_PENDING"
)

// KnownCodes lists every code this SDK version knows.
var KnownCodes = []ErrorCode{
	CodeInvalidRequest, CodeCursorInvalid, CodeAuthRequired, CodeInvalidCredentials,
	CodeTokenExpired, CodeForbidden, CodeResourceNotFound, CodeRevisionConflict,
	CodeStateConflict, CodeIdempotencyConflict, CodeIdempotencyInProgress, CodeCursorStale,
	CodeResourceAliasCollision, CodePreconditionFailed, CodePayloadTooLarge,
	CodeValidationError, CodePolicyInvalid, CodePolicyTooComplex, CodeRateLimited,
	CodeInternalError, CodeNotReady, CodeAuthzReloadPending, CodeIdentityReloadPending,
}

// Known reports whether the code is in KnownCodes.
func (c ErrorCode) Known() bool {
	for _, k := range KnownCodes {
		if k == c {
			return true
		}
	}
	return false
}

// CommitState tells whether a mutation reached the database.
//
// CommitStateNone (empty) means "no statement" — NOT "not committed".
// Source: #/components/schemas/CommitState
type CommitState string

const (
	// CommitStateNone: nothing is known or declared.
	CommitStateNone CommitState = ""
	// CommitStateCommittedUnpublished: declared by the server — the transaction
	// committed but the snapshot is not published yet. The mutation HAS happened;
	// never resend it.
	CommitStateCommittedUnpublished CommitState = "committed_unpublished"
	// CommitStateUnknown: inferred by the SDK for a mutating request that failed
	// in transit (timeout/reset). It may or may not have been applied.
	CommitStateUnknown CommitState = "unknown"
)

// APIError is a well-formed error response from the server.
type APIError struct {
	Status  int
	Code    ErrorCode
	Message string
	// KnownCode is false for codes unknown to this SDK; Code still holds the raw string.
	KnownCode bool
	// RequestID prefers the X-Request-ID response header when valid, else the body value.
	RequestID string
	// RequestIDHeaderValid is true when the response carried a valid X-Request-ID header.
	RequestIDHeaderValid bool
	// RequestIDMismatch is true when header and body request ids are both valid but differ.
	RequestIDMismatch bool
	// BodyRequestID is error.request_id from the body.
	BodyRequestID string
	// TraceID is the W3C trace id (32 lowercase hex), "" when absent/invalid.
	TraceID string
	// Details is the raw error.details object (may be nil).
	Details json.RawMessage
	// Violations is error.details.violations in server order.
	Violations []Violation
	// Retryable is the server hint: nil = no statement. It is not permission to
	// replay a write.
	Retryable *bool
	// RetryAfterSeconds is the server's wait hint (body field or Retry-After header).
	RetryAfterSeconds *int64
	// CommitState is the server-declared commit state, "" when not declared.
	CommitState CommitState
}

// Error implements error. The text never includes request payloads or credentials.
func (e *APIError) Error() string {
	s := fmt.Sprintf("authgebra: HTTP %d %s: %s", e.Status, e.Code, e.Message)
	if e.RequestID != "" {
		s += " (request_id=" + e.RequestID
		if e.TraceID != "" {
			s += " trace_id=" + e.TraceID
		}
		s += ")"
	}
	return s
}

// Is lets errors.Is match an *APIError by code: errors.Is(err, &APIError{Code: CodeCursorStale}).
func (e *APIError) Is(target error) bool {
	t, ok := target.(*APIError)
	return ok && t.Code != "" && t.Code == e.Code && (t.Status == 0 || t.Status == e.Status)
}

// IsCode reports whether the error has the given code.
func (e *APIError) IsCode(c ErrorCode) bool { return e.Code == c }

// CommittedUnpublished reports that the mutation was committed but is not yet
// published. Do not resend; confirm via reads before telling callers it is effective.
func (e *APIError) CommittedUnpublished() bool {
	return e.CommitState == CommitStateCommittedUnpublished
}

// TransportErrorKind classifies a failure before a complete response was received.
type TransportErrorKind string

const (
	TransportTimeout         TransportErrorKind = "timeout"
	TransportConnectionReset TransportErrorKind = "connection_reset"
	TransportTLS             TransportErrorKind = "tls"
	TransportCanceled        TransportErrorKind = "canceled"
	TransportNetwork         TransportErrorKind = "network"
)

// TransportError means no complete HTTP response was obtained.
type TransportError struct {
	Kind   TransportErrorKind
	Method string
	// Path is the path template (no ids, no query), safe to log.
	Path string
	Err  error
	// CommitState is CommitStateUnknown for mutating requests (the server may have
	// applied the change), CommitStateNone for reads.
	CommitState CommitState
	// Attempts is how many attempts were made.
	Attempts int
}

// Error implements error.
func (e *TransportError) Error() string {
	return fmt.Sprintf("authgebra: %s %s: %s: %v", e.Method, e.Path, e.Kind, e.Err)
}

// Unwrap exposes the underlying error (context.DeadlineExceeded, net.Error, ...).
func (e *TransportError) Unwrap() error { return e.Err }

// Timeout reports whether the failure was a timeout.
func (e *TransportError) Timeout() bool { return e.Kind == TransportTimeout }

// ProtocolErrorKind classifies a response that cannot be understood.
type ProtocolErrorKind string

const (
	ProtocolNonJSON              ProtocolErrorKind = "non_json_response"
	ProtocolMalformedErrorBody   ProtocolErrorKind = "malformed_error_body"
	ProtocolMalformedSuccessBody ProtocolErrorKind = "malformed_success_body"
	ProtocolResponseTooLarge     ProtocolErrorKind = "response_too_large"
)

// ProtocolError means a response arrived but does not match the contract.
type ProtocolError struct {
	Kind      ProtocolErrorKind
	Status    int
	RequestID string
	TraceID   string
	// BodySnippet is up to 256 printable-ASCII bytes of a non-JSON body (for diagnosis only).
	BodySnippet string
	Err         error
}

// Error implements error.
func (e *ProtocolError) Error() string {
	s := fmt.Sprintf("authgebra: HTTP %d: %s", e.Status, e.Kind)
	if e.Err != nil {
		s += ": " + e.Err.Error()
	}
	return s
}

// Unwrap exposes the decode error, if any.
func (e *ProtocolError) Unwrap() error { return e.Err }

// ErrMaxPages is returned by page iterators that hit PageOptions.MaxPages.
var ErrMaxPages = errors.New("authgebra: page limit reached")

// ErrGenerationChanged is returned by the authorization list iterator when the
// snapshot generation changes between pages; results of different generations
// are never merged silently.
var ErrGenerationChanged = errors.New("authgebra: snapshot generation changed between pages")

type wireError struct {
	Error *struct {
		Code              *string         `json:"code"`
		Message           string          `json:"message"`
		RequestID         string          `json:"request_id"`
		TraceID           string          `json:"trace_id"`
		Details           json.RawMessage `json:"details"`
		Retryable         *bool           `json:"retryable"`
		RetryAfterSeconds *int64          `json:"retry_after_seconds"`
		CommitState       string          `json:"commit_state"`
	} `json:"error"`
}

func printableSnippet(b []byte) string {
	if len(b) > 256 {
		b = b[:256]
	}
	out := make([]byte, 0, len(b))
	for _, c := range b {
		if c >= 0x20 && c < 0x7f {
			out = append(out, c)
		} else if c == '\n' || c == '\t' {
			out = append(out, ' ')
		}
	}
	return string(out)
}

func requestIDFromHeader(h http.Header) (string, bool) {
	v := h.Get("X-Request-ID")
	if v != "" && ValidRequestID(v) {
		return v, true
	}
	return "", false
}

func traceIDFromHeaders(h http.Header) string {
	if v := h.Get("X-Trace-ID"); ValidTraceID(v) {
		return v
	}
	if id, ok := TraceIDFromTraceparent(h.Get("Traceparent")); ok {
		return id
	}
	return ""
}

// decodeError turns a non-2xx response into *APIError or *ProtocolError.
func decodeError(status int, h http.Header, body []byte) error {
	hdrID, hdrOK := requestIDFromHeader(h)
	if !json.Valid(body) {
		return &ProtocolError{Kind: ProtocolNonJSON, Status: status, RequestID: hdrID,
			TraceID: traceIDFromHeaders(h), BodySnippet: printableSnippet(body)}
	}
	var w wireError
	if err := json.Unmarshal(body, &w); err != nil || w.Error == nil || w.Error.Code == nil || *w.Error.Code == "" {
		return &ProtocolError{Kind: ProtocolMalformedErrorBody, Status: status, RequestID: hdrID,
			TraceID: traceIDFromHeaders(h), Err: err}
	}
	we := w.Error
	e := &APIError{
		Status:               status,
		Code:                 ErrorCode(*we.Code),
		Message:              we.Message,
		RequestIDHeaderValid: hdrOK,
		Details:              we.Details,
		Retryable:            we.Retryable,
		RetryAfterSeconds:    we.RetryAfterSeconds,
		CommitState:          CommitState(we.CommitState),
	}
	e.KnownCode = e.Code.Known()
	if ValidRequestID(we.RequestID) {
		e.BodyRequestID = we.RequestID
	}
	switch {
	case hdrOK:
		e.RequestID = hdrID
		e.RequestIDMismatch = e.BodyRequestID != "" && e.BodyRequestID != hdrID
	default:
		e.RequestID = e.BodyRequestID
	}
	if ValidTraceID(we.TraceID) {
		e.TraceID = we.TraceID
	} else {
		e.TraceID = traceIDFromHeaders(h)
	}
	if e.RetryAfterSeconds == nil {
		if n, err := strconv.ParseInt(h.Get("Retry-After"), 10, 64); err == nil && n >= 0 {
			e.RetryAfterSeconds = &n
		}
	}
	if len(we.Details) > 0 {
		var d struct {
			Violations []Violation `json:"violations"`
		}
		if json.Unmarshal(we.Details, &d) == nil {
			e.Violations = d.Violations
		}
	}
	return e
}
