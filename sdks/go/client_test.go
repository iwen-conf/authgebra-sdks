package authgebra

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func check(c *Client, opts ...CallOption) error {
	_, err := c.Authorization.Check(context.Background(), CheckRequest{SubjectID: testSubject, Permission: "p:r"}, opts...)
	return err
}

func createUser(c *Client, key string, opts ...CallOption) error {
	_, err := c.Admin.Users.CreateUser(context.Background(), CreateUserParams{IdempotencyKey: key},
		CreateUserRequest{Username: "alice", DisplayName: "A", InitialPassword: "p"}, opts...)
	return err
}

func TestReadRetriesAreBounded(t *testing.T) {
	c, rec, _ := newTestClient(t, respond(503, nil, errBody("NOT_READY", `"retryable":true`)), nil)
	err := check(c)
	var ae *APIError
	if !errors.As(err, &ae) || ae.Code != CodeNotReady {
		t.Fatalf("got %v", err)
	}
	if rec.count() != 3 {
		t.Fatalf("default is 1 try + 2 retries, got %d requests", rec.count())
	}
}

func TestRetriesCanBeDisabledAndAreCapped(t *testing.T) {
	c, rec, _ := newTestClient(t, respond(503, nil, errBody("NOT_READY", "")), func(c *Config) { c.Retry.NoRetry = true })
	_ = check(c)
	if rec.count() != 1 {
		t.Fatalf("NoRetry: got %d requests", rec.count())
	}
	c2, rec2, _ := newTestClient(t, respond(503, nil, errBody("NOT_READY", "")), func(c *Config) { c.Retry.MaxRetries = 1000 })
	_ = check(c2)
	if rec2.count() != 1+maxRetriesCap {
		t.Fatalf("retries must be capped at %d, got %d requests", maxRetriesCap, rec2.count()-1)
	}
}

func TestNonRetryableReadErrorsAreNotRetried(t *testing.T) {
	for _, code := range []string{"CURSOR_STALE", "FORBIDDEN", "AUTH_REQUIRED", "INTERNAL_ERROR", "VALIDATION_ERROR", "AUTHZ_RELOAD_PENDING"} {
		c, rec, _ := newTestClient(t, respond(409, nil, errBody(code, "")), nil)
		_ = check(c)
		if rec.count() != 1 {
			t.Errorf("%s was retried (%d requests)", code, rec.count())
		}
	}
}

func TestRateLimitHonorsRetryAfterWithCap(t *testing.T) {
	c, _, sleeps := newTestClient(t, respond(429, map[string]string{"Retry-After": "7"}, errBody("RATE_LIMITED", `"retry_after_seconds":7`)),
		func(c *Config) { c.Retry.MaxRetryAfter = 3 * time.Second })
	_ = check(c)
	if len(*sleeps) != 2 {
		t.Fatalf("sleeps = %v", *sleeps)
	}
	for _, d := range *sleeps {
		if d != 3*time.Second {
			t.Fatalf("Retry-After 7s must be capped to 3s, slept %v", d)
		}
	}
}

func TestWritesAreNeverAutoRetriedByDefault(t *testing.T) {
	for _, status := range []struct {
		code string
		http int
	}{{"NOT_READY", 503}, {"RATE_LIMITED", 429}, {"IDEMPOTENCY_IN_PROGRESS", 409}, {"INTERNAL_ERROR", 500}} {
		c, rec, _ := newTestClient(t, respond(status.http, nil, errBody(status.code, "")), nil)
		_ = createUser(c, "idem-key-0001")
		if rec.count() != 1 {
			t.Errorf("%s: write sent %d times without opt-in", status.code, rec.count())
		}
	}
}

func TestWriteRetryNeedsOptInAndIdempotencyKey(t *testing.T) {
	var n atomic.Int32
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(503)
		_, _ = w.Write([]byte(errBody("NOT_READY", "")))
		n.Add(1)
	})
	c, rec, _ := newTestClient(t, h, func(c *Config) { c.Retry.RetryWrites = true })
	_ = createUser(c, "") // opted in, but no key: must not replay
	if rec.count() != 1 {
		t.Fatalf("no Idempotency-Key: sent %d times", rec.count())
	}
	c2, rec2, _ := newTestClient(t, h, func(c *Config) { c.Retry.RetryWrites = true })
	_ = createUser(c2, "idem-key-0002")
	if rec2.count() != 3 {
		t.Fatalf("opt-in + key: got %d requests", rec2.count())
	}
	for i := range rec2.reqs {
		if rec2.reqs[i].header.Get("Idempotency-Key") != "idem-key-0002" || !bytes.Equal(rec2.reqs[i].body, rec2.reqs[0].body) {
			t.Fatal("replays must carry the same key and identical bytes")
		}
	}
	c3, rec3, _ := newTestClient(t, h, nil)
	_ = createUser(c3, "idem-key-0003", WithRetryWrites(true)) // per-call opt-in
	if rec3.count() != 3 {
		t.Fatalf("per-call opt-in: got %d", rec3.count())
	}
}

func TestReloadPendingIsNeverReplayedEvenWithOptIn(t *testing.T) {
	for _, code := range []string{"AUTHZ_RELOAD_PENDING", "IDENTITY_RELOAD_PENDING"} {
		c, rec, _ := newTestClient(t, respond(503, nil, errBody(code, `"retryable":false,"commit_state":"committed_unpublished"`)),
			func(c *Config) { c.Retry.RetryWrites = true })
		err := createUser(c, "idem-key-0004")
		var ae *APIError
		if !errors.As(err, &ae) || !ae.CommittedUnpublished() {
			t.Fatalf("%s: %v", code, err)
		}
		if rec.count() != 1 {
			t.Errorf("%s was replayed (%d requests): the mutation is already committed", code, rec.count())
		}
		if ae.Retryable == nil || *ae.Retryable {
			t.Errorf("%s must surface retryable=false", code)
		}
	}
}

func TestRealTimeoutIsClassifiedAndWriteCommitStateUnknown(t *testing.T) {
	slow := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-time.After(2 * time.Second):
		}
	})
	c, _, _ := newTestClient(t, slow, func(c *Config) { c.Timeout = 80 * time.Millisecond; c.Retry.NoRetry = true })
	var te *TransportError
	if err := check(c); !errors.As(err, &te) || te.Kind != TransportTimeout || te.CommitState != CommitStateNone {
		t.Fatalf("read timeout: %v", err)
	}
	if err := createUser(c, "idem-key-0005"); !errors.As(err, &te) || te.Kind != TransportTimeout || te.CommitState != CommitStateUnknown {
		t.Fatalf("write timeout must leave commit_state unknown: %v", err)
	}
}

func TestRealConnectionResetIsClassified(t *testing.T) {
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, _, _ := w.(http.Hijacker).Hijack()
		_ = conn.Close()
	})
	c, _, _ := newTestClient(t, h, func(c *Config) { c.Retry.NoRetry = true })
	var te *TransportError
	if err := createUser(c, "idem-key-0006"); !errors.As(err, &te) || te.Kind != TransportConnectionReset || te.CommitState != CommitStateUnknown {
		t.Fatalf("got %v", err)
	}
}

func TestTLSVerificationIsOnByDefault(t *testing.T) {
	srv := httptest.NewTLSServer(respond(200, nil, okCheckBody()))
	defer srv.Close()
	c, err := New(Config{BaseURL: srv.URL})
	if err != nil {
		t.Fatal(err)
	}
	var te *TransportError
	if err := check(c); !errors.As(err, &te) || te.Kind != TransportTLS {
		t.Fatalf("untrusted certificate must fail as TLS error, got %v", err)
	}
	trusted, err := New(Config{BaseURL: srv.URL, HTTPClient: srv.Client()})
	if err != nil {
		t.Fatal(err)
	}
	if err := check(trusted); err != nil {
		t.Fatalf("trusted client must work: %v", err)
	}
}

func TestCallerCancellationIsNotARetry(t *testing.T) {
	var n atomic.Int32
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { n.Add(1); <-r.Context().Done() })
	c, _, _ := newTestClient(t, h, nil)
	ctx, cancel := context.WithCancel(context.Background())
	go func() { time.Sleep(50 * time.Millisecond); cancel() }()
	_, err := c.Authorization.Check(ctx, CheckRequest{SubjectID: testSubject, Permission: "p:r"})
	var te *TransportError
	if !errors.As(err, &te) || te.Kind != TransportCanceled || !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	if n.Load() != 1 {
		t.Fatalf("cancellation retried: %d requests", n.Load())
	}
}

func TestResponseSizeIsLimited(t *testing.T) {
	c, _, _ := newTestClient(t, respond(200, nil, `{"data":"`+strings.Repeat("x", 4096)+`"}`), func(c *Config) { c.MaxResponseBytes = 1024 })
	var pe *ProtocolError
	if err := check(c); !errors.As(err, &pe) || pe.Kind != ProtocolResponseTooLarge {
		t.Fatalf("got %v", err)
	}
}

func TestRequestHeaders(t *testing.T) {
	c, rec, _ := newTestClient(t, respond(200, nil, okCheckBody()), func(c *Config) {
		c.Headers = http.Header{"X-Custom": {"v"}, "User-Agent": {"evil"}, "X-Request-Id": {"hijack"}}
		c.UserAgentSuffix = "myapp/1"
	})
	if err := check(c); err != nil {
		t.Fatal(err)
	}
	h := rec.last().header
	if h.Get("Authorization") != "Bearer secret-token-value" || h.Get("X-Custom") != "v" || h.Get("Accept") != "application/json" {
		t.Fatalf("headers: %v", h)
	}
	if ua := h.Get("User-Agent"); ua != "authgebra-go/"+Version+" myapp/1" {
		t.Fatalf("user agent %q", ua)
	}
	if !ValidRequestID(h.Get("X-Request-Id")) || h.Get("X-Request-Id") == "hijack" || !ValidTraceparent(h.Get("Traceparent")) {
		t.Fatalf("SDK-managed headers must win: %v", h)
	}
}

func TestTraceAndRequestIDInjection(t *testing.T) {
	c, rec, _ := newTestClient(t, respond(200, nil, okCheckBody()), nil)
	tp := "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
	if err := check(c, WithRequestID("my-request-0001"), WithTraceparent(tp, "vendor=1")); err != nil {
		t.Fatal(err)
	}
	h := rec.last().header
	if h.Get("X-Request-Id") != "my-request-0001" || h.Get("Traceparent") != tp || h.Get("Tracestate") != "vendor=1" {
		t.Fatalf("explicit ids not propagated: %v", h)
	}
	ctx := ContextWithTraceparent(context.Background(), tp, "")
	if _, err := c.Authorization.Check(ctx, CheckRequest{SubjectID: testSubject, Permission: "p:r"}); err != nil {
		t.Fatal(err)
	}
	if rec.last().header.Get("Traceparent") != tp {
		t.Fatal("context trace not propagated")
	}
	before := rec.count()
	if err := check(c, WithRequestID("bad id")); err != nil || rec.count() != before+1 {
		t.Fatal("invalid request id must be discarded without failing the call")
	}
	before = rec.count()
	if err := check(c, WithTraceparent("00-zz-zz-01", "")); err == nil || rec.count() != before {
		t.Fatal("invalid traceparent must fail before sending")
	}
	// retries keep the same ids so the attempts correlate
	c2, rec2, _ := newTestClient(t, respond(503, nil, errBody("NOT_READY", "")), nil)
	_ = check(c2)
	if rec2.reqs[0].header.Get("X-Request-Id") != rec2.reqs[2].header.Get("X-Request-Id") ||
		rec2.reqs[0].header.Get("Traceparent") != rec2.reqs[2].header.Get("Traceparent") {
		t.Fatal("retries must reuse request id and trace context")
	}
}

func TestWithoutAuthOmitsAuthorization(t *testing.T) {
	c, rec, _ := newTestClient(t, respond(200, nil, okCheckBody()), nil)
	if err := check(c, WithoutAuth()); err != nil {
		t.Fatal(err)
	}
	if rec.last().header.Get("Authorization") != "" {
		t.Fatal("Authorization sent despite WithoutAuth")
	}
}

func TestTokenNeverLeaksIntoLogsOrErrors(t *testing.T) {
	var logs bytes.Buffer
	logger := slog.New(slog.NewTextHandler(&logs, &slog.HandlerOptions{Level: slog.LevelDebug}))
	c, _, _ := newTestClient(t, respond(403, nil, errBody("FORBIDDEN", "")), func(c *Config) { c.Logger = logger })
	err := check(c)
	if logs.Len() == 0 {
		t.Fatal("expected debug logs")
	}
	if strings.Contains(logs.String(), "secret-token-value") || strings.Contains(err.Error(), "secret-token-value") {
		t.Fatalf("token leaked:\nlogs=%s\nerr=%v", logs.String(), err)
	}
	if strings.Contains(StaticToken("secret-token-value").(interface{ String() string }).String(), "secret-token-value") {
		t.Fatal("StaticToken.String leaks")
	}
	badProvider := TokenFunc(func(context.Context) (string, error) { return "", errors.New("vault down") })
	c2, _, _ := newTestClient(t, respond(200, nil, okCheckBody()), func(c *Config) { c.Token = badProvider })
	if err := check(c2); err == nil || !strings.Contains(err.Error(), "token provider failed") {
		t.Fatalf("got %v", err)
	}
}

func TestAPIErrorIsMatchesByCode(t *testing.T) {
	c, _, _ := newTestClient(t, respond(409, nil, errBody("CURSOR_STALE", "")), nil)
	err := check(c)
	if !errors.Is(err, &APIError{Code: CodeCursorStale}) || errors.Is(err, &APIError{Code: CodeForbidden}) {
		t.Fatalf("errors.Is by code failed: %v", err)
	}
}

func TestAdminReadHasNoFakeRevision(t *testing.T) {
	c, _, _ := newTestClient(t, respond(200, nil, `{"data":[],"page":{"next_cursor":null,"has_more":false}}`), nil)
	r, err := c.Admin.Users.ListUsers(context.Background(), ListUsersParams{})
	if err != nil || r.Meta != nil {
		t.Fatalf("admin read must expose no meta (never a fake 0): %v %+v", err, r)
	}
}

func TestConfigValidation(t *testing.T) {
	for _, bad := range []string{"", "  ", "ftp://x", "not a url", "/relative"} {
		if _, err := New(Config{BaseURL: bad}); err == nil {
			t.Errorf("BaseURL %q accepted", bad)
		}
	}
}

func TestPathParametersAreEscaped(t *testing.T) {
	c, rec, _ := newTestClient(t, respond(200, nil, `{"data":{}}`), nil)
	_, _ = c.Admin.Users.GetUser(context.Background(), "a/b?c")
	if got := rec.last(); got.path != "/api/v1/users/a/b?c" || got.rawQuery != "" {
		t.Fatalf("path parameter must stay one escaped segment: path=%q query=%q", got.path, got.rawQuery)
	}
	if _, err := c.Admin.Users.GetUser(context.Background(), ""); err == nil {
		t.Fatal("empty path parameter must be rejected before sending")
	}
}
