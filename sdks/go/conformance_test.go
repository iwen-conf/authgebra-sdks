package authgebra

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
)

// fixture mirrors sdks/fixtures/cases/*.json (see sdks/README.md).
type fixture struct {
	ID        string `json:"id"`
	Operation string `json:"operation"`
	Request   struct {
		Method         string          `json:"method"`
		Path           string          `json:"path"`
		IdempotencyKey *string         `json:"idempotency_key"`
		Body           json.RawMessage `json:"body"`
	} `json:"request"`
	Response *struct {
		Status  int               `json:"status"`
		Headers map[string]string `json:"headers"`
		Body    json.RawMessage   `json:"body"`
		BodyRaw *string           `json:"body_raw"`
	} `json:"response"`
	Fault  string         `json:"fault"`
	Expect map[string]any `json:"expect"`
}

// outcome is what a typed SDK call yields, normalized for comparison.
type outcome struct {
	decision      string
	revision      *int64
	generation    *int64
	traceID       string
	requestID     string
	hasMore       *bool
	username      string
	metaPresent   bool
	headerRevSeen bool
}

func loadFixtures(t *testing.T) []fixture {
	t.Helper()
	files, err := filepath.Glob("../fixtures/cases/*.json")
	if err != nil || len(files) == 0 {
		t.Fatalf("no fixtures found: %v", err)
	}
	var out []fixture
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var fx fixture
		if err := json.Unmarshal(raw, &fx); err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		out = append(out, fx)
	}
	return out
}

func fixtureBody(fx fixture) string {
	if fx.Response.BodyRaw != nil {
		return *fx.Response.BodyRaw
	}
	return string(fx.Response.Body)
}

// invoke runs the typed SDK method that corresponds to the fixture operation.
func invoke(ctx context.Context, c *Client, fx fixture) (outcome, error) {
	var key string
	if fx.Request.IdempotencyKey != nil {
		key = *fx.Request.IdempotencyKey
	}
	switch fx.Operation {
	case "authz.check":
		r, err := c.Authorization.Check(ctx, CheckRequest{SubjectID: testSubject, Permission: "document:read"})
		if err != nil {
			return outcome{}, err
		}
		return outcome{decision: r.Data.Decision, revision: revOf(r.Meta), generation: genOf(r.Meta),
			traceID: r.TraceID, requestID: r.RequestID, metaPresent: r.Meta != nil}, nil
	case "authz.list_resources":
		_, err := c.Authorization.ListResources(ctx, ListResourcesRequest{SubjectID: testSubject, Permission: "p:r", ResourceType: "doc"})
		return outcome{}, err
	case "users.create":
		var b struct {
			Username string `json:"username"`
		}
		_ = json.Unmarshal(fx.Request.Body, &b)
		// These shared transport fixtures deliberately carry partial domain payloads.
		r, err := call[json.RawMessage](ctx, c, opCreateUser, callArgs{body: CreateUserRequest{Username: b.Username, DisplayName: "d", InitialPassword: "p"}, header: http.Header{"Idempotency-Key": []string{key}}}, nil)
		if err != nil {
			return outcome{}, err
		}
		var user struct {
			Username string `json:"username"`
		}
		if err := json.Unmarshal(r.Data, &user); err != nil {
			return outcome{}, err
		}
		return outcome{username: user.Username, revision: revOf(r.Meta)}, nil
	case "users.update":
		_, err := c.Admin.Users.PatchUser(ctx, testSubject, PatchUserParams{IfMatch: `"1"`}, PatchUserRequest{})
		return outcome{}, err
	case "users.list":
		r, err := c.Admin.Users.ListUsers(ctx, ListUsersParams{})
		if err != nil {
			return outcome{}, err
		}
		hm := r.Page.HasMore
		return outcome{revision: revOf(r.Meta), hasMore: &hm, metaPresent: r.Meta != nil}, nil
	case "auth.change_password":
		_, err := c.Admin.Auth.ChangePassword(ctx, ChangePasswordRequest{})
		return outcome{}, err
	}
	panic("fixture operation not mapped: " + fx.Operation)
}

func revOf(m *Meta) *int64 {
	if m == nil {
		return nil
	}
	return &m.AuthzRevision
}
func genOf(m *Meta) *int64 {
	if m == nil {
		return nil
	}
	return m.SnapshotGeneration
}

// faultTransport simulates network faults without a real network.
type faultTransport struct{ kind string }

func (f faultTransport) RoundTrip(*http.Request) (*http.Response, error) {
	switch f.kind {
	case "timeout":
		return nil, context.DeadlineExceeded
	case "reset":
		return nil, &net.OpError{Op: "read", Net: "tcp", Err: os.NewSyscallError("read", syscall.ECONNRESET)}
	}
	return nil, errors.New("unknown fault " + f.kind)
}

func TestFixtureConformance(t *testing.T) {
	fixtures := loadFixtures(t)
	if len(fixtures) != 25 {
		t.Fatalf("expected 25 fixtures, found %d (update this harness deliberately)", len(fixtures))
	}
	for _, fx := range fixtures {
		t.Run(fx.ID, func(t *testing.T) {
			var c *Client
			var rec *recorder
			if fx.Fault != "" {
				var err error
				c, err = New(Config{BaseURL: "http://authgebra.invalid", HTTPClient: &http.Client{Transport: faultTransport{fx.Fault}}})
				if err != nil {
					t.Fatal(err)
				}
				rec = &recorder{}
			}
			if fx.Fault == "" {
				c, rec, _ = newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
					for k, v := range fx.Response.Headers {
						w.Header().Set(k, v)
					}
					w.WriteHeader(fx.Response.Status)
					_, _ = w.Write([]byte(fixtureBody(fx)))
				}), nil)
			} else {
				c.sleep = noSleep
			}
			out, err := invoke(context.Background(), c, fx)
			assertExpect(t, fx, out, err, rec)
		})
	}
}

func assertExpect(t *testing.T, fx fixture, out outcome, err error, rec *recorder) {
	t.Helper()
	ex := fx.Expect
	str := func(k string) string { s, _ := ex[k].(string); return s }
	switch ex["kind"] {
	case "ok":
		if err != nil {
			t.Fatalf("expected success, got %v", err)
		}
		if v, ok := ex["decision"]; ok && out.decision != v {
			t.Errorf("decision = %q, want %v", out.decision, v)
		}
		if v, present := ex["authz_revision"]; present {
			if v == nil {
				if out.revision != nil || out.metaPresent {
					t.Errorf("authz_revision must be absent (no fake 0), got %v meta=%v", out.revision, out.metaPresent)
				}
			} else if out.revision == nil || strconv.FormatInt(*out.revision, 10) != fmt.Sprint(jsonNumber(v)) {
				t.Errorf("authz_revision = %v, want %v", out.revision, v)
			}
		}
		if v, ok := ex["snapshot_generation"]; ok && (out.generation == nil || strconv.FormatInt(*out.generation, 10) != fmt.Sprint(jsonNumber(v))) {
			t.Errorf("snapshot_generation = %v, want %v", out.generation, v)
		}
		if v, ok := ex["trace_id"]; ok && out.traceID != v {
			t.Errorf("trace_id = %q, want %v", out.traceID, v)
		}
		if v, ok := ex["request_id"]; ok && out.requestID != v {
			t.Errorf("request_id = %q, want %v", out.requestID, v)
		}
		if v, ok := ex["has_more"]; ok && (out.hasMore == nil || *out.hasMore != v) {
			t.Errorf("has_more = %v, want %v", out.hasMore, v)
		}
		if v, ok := ex["echo_username"]; ok && out.username != v {
			t.Errorf("username = %q, want %v", out.username, v)
		}
		if ex["request_body_utf8_exact"] == true {
			body := string(rec.last().body)
			if !strings.Contains(body, "用户-é-😀") || strings.Contains(body, `\u`) {
				t.Errorf("request body must carry UTF-8 verbatim, got %s", body)
			}
		}
	case "api_error":
		var ae *APIError
		if !errors.As(err, &ae) {
			t.Fatalf("expected *APIError, got %T %v", err, err)
		}
		eq := func(name string, got, want any) {
			if fmt.Sprint(got) != fmt.Sprint(want) {
				t.Errorf("%s = %v, want %v", name, got, want)
			}
		}
		eq("status", ae.Status, jsonNumber(ex["status"]))
		eq("code", ae.Code, ex["code"])
		eq("known_code", ae.KnownCode, ex["known_code"])
		eq("request_id", ae.RequestID, ex["request_id"])
		eq("trace_id", ae.TraceID, str("trace_id"))
		eq("commit_state", ae.CommitState, str("commit_state"))
		if v, ok := ex["retryable"]; ok {
			switch {
			case v == nil && ae.Retryable != nil:
				t.Errorf("retryable must be undeclared, got %v", *ae.Retryable)
			case v != nil && (ae.Retryable == nil || *ae.Retryable != v):
				t.Errorf("retryable = %v, want %v", ae.Retryable, v)
			}
		}
		if v, ok := ex["retry_after_seconds"]; ok {
			switch {
			case v == nil && ae.RetryAfterSeconds != nil:
				t.Errorf("retry_after_seconds must be absent, got %d", *ae.RetryAfterSeconds)
			case v != nil && (ae.RetryAfterSeconds == nil || float64(*ae.RetryAfterSeconds) != v):
				t.Errorf("retry_after_seconds = %v, want %v", ae.RetryAfterSeconds, v)
			}
		}
		if v, ok := ex["request_id_mismatch"]; ok {
			eq("request_id_mismatch", ae.RequestIDMismatch, v)
		} else if ae.RequestIDMismatch {
			t.Errorf("unexpected request_id mismatch")
		}
		if v, ok := ex["body_request_id"]; ok {
			eq("body_request_id", ae.BodyRequestID, v)
		}
		if v, ok := ex["request_id_header_valid"]; ok {
			eq("request_id_header_valid", ae.RequestIDHeaderValid, v)
		}
		if want, ok := ex["violations"].([]any); ok {
			if len(ae.Violations) != len(want) {
				t.Fatalf("violations = %d, want %d", len(ae.Violations), len(want))
			}
			for i, w := range want {
				wm := w.(map[string]any)
				got := ae.Violations[i]
				if got.Reason != wm["reason"] {
					t.Errorf("violation[%d].reason = %q, want %v", i, got.Reason, wm["reason"])
				}
				switch p := wm["path"].(type) {
				case nil:
					if got.Path != nil {
						t.Errorf("violation[%d].path must be absent, got %q", i, *got.Path)
					}
				case string:
					if got.Path == nil || *got.Path != p {
						t.Errorf("violation[%d].path = %v, want %q", i, got.Path, p)
					}
				}
			}
		}
	case "transport_error":
		var te *TransportError
		if !errors.As(err, &te) {
			t.Fatalf("expected *TransportError, got %T %v", err, err)
		}
		if string(te.Kind) != ex["error"] {
			t.Errorf("kind = %q, want %v", te.Kind, ex["error"])
		}
		if string(te.CommitState) != str("commit_state") {
			t.Errorf("commit_state = %q, want %q", te.CommitState, str("commit_state"))
		}
		if ex["auto_resent"] == false && te.Attempts != 1 {
			t.Errorf("a write without safe-replay conditions was sent %d times", te.Attempts)
		}
	case "protocol_error":
		var pe *ProtocolError
		if !errors.As(err, &pe) {
			t.Fatalf("expected *ProtocolError, got %T %v", err, err)
		}
		if string(pe.Kind) != ex["error"] {
			t.Errorf("kind = %q, want %v", pe.Kind, ex["error"])
		}
		if pe.Status != jsonNumber(ex["status"]) {
			t.Errorf("status = %d, want %v", pe.Status, ex["status"])
		}
	default:
		t.Fatalf("unknown expect.kind %v", ex["kind"])
	}
}

// jsonNumber normalizes json numbers decoded as float64 to int for comparison;
// strings (used for >2^53 values) pass through untouched.
func jsonNumber(v any) any {
	if f, ok := v.(float64); ok {
		return int(f)
	}
	return v
}

var _ = exec.Command
var _ = httptest.NewServer
var _ = url.Values{}
