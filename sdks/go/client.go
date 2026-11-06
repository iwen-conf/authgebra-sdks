package authgebra

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// Client is the Authgebra API client. It is safe for concurrent use; create one
// and reuse it so the connection pool lives as long as the client.
type Client struct {
	cfg     Config
	base    *url.URL
	http    *http.Client
	retry   RetryPolicy
	ua      string
	sleep   func(ctx context.Context, d time.Duration) error // replaceable in tests
	maxBody int64

	// Authorization is the decision API (Check, BatchCheck, ListResources, Explain, Revision).
	Authorization *AuthorizationClient
	// Admin groups all management operations by area.
	Admin *AdminClient
	// Authz exposes the generated low-level decision operations.
	Authz *AuthzAPI
}

// New validates cfg and builds a Client.
func New(cfg Config) (*Client, error) {
	if strings.TrimSpace(cfg.BaseURL) == "" {
		return nil, errNoBaseURL
	}
	u, err := url.Parse(cfg.BaseURL)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
		return nil, fmt.Errorf("authgebra: invalid BaseURL %q", cfg.BaseURL)
	}
	c := &Client{cfg: cfg, base: u, retry: cfg.Retry.normalized(), sleep: sleepCtx, maxBody: cfg.MaxResponseBytes}
	if c.maxBody <= 0 {
		c.maxBody = defaultMaxResponse
	}
	c.ua = "authgebra-go/" + Version
	if cfg.UserAgentSuffix != "" {
		c.ua += " " + cfg.UserAgentSuffix
	}
	c.http = cfg.HTTPClient
	if c.http == nil {
		ct := cfg.ConnectTimeout
		if ct <= 0 {
			ct = defaultConnectTimeout
		}
		tr := http.DefaultTransport.(*http.Transport).Clone()
		tr.DialContext = (&net.Dialer{Timeout: ct, KeepAlive: 30 * time.Second}).DialContext
		if cfg.TLSConfig != nil {
			tr.TLSClientConfig = cfg.TLSConfig.Clone()
		}
		c.http = &http.Client{Transport: tr}
	}
	c.Authorization = &AuthorizationClient{c: c}
	c.Authz = &AuthzAPI{c: c}
	c.Admin = newAdminClient(c)
	return c, nil
}

// Close releases idle connections of the default transport.
func (c *Client) Close() { c.http.CloseIdleConnections() }

// ContextWithTraceparent carries a W3C trace context in ctx; calls made with it
// propagate it (a per-call WithTraceparent wins).
func ContextWithTraceparent(ctx context.Context, traceparent, tracestate string) context.Context {
	return context.WithValue(ctx, traceparentKey, [2]string{traceparent, tracestate})
}

func sleepCtx(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return ctx.Err()
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

type envelopeKind int

const (
	envData  envelopeKind = iota // {"data":…,"meta"?}
	envPaged                     // {"data":[…],"page":…,"meta"?}
	envBare                      // the body itself is the DTO
)

type opSpec struct {
	ID       string
	Method   string
	Path     string // template, e.g. /api/v1/users/{id}
	Mutating bool
	Env      envelopeKind
}

type callArgs struct {
	path   map[string]string
	query  url.Values
	header http.Header
	body   any
}

type rawResponse struct {
	status int
	header http.Header
	body   []byte
}

func (c *Client) buildURL(op opSpec, a callArgs) (string, error) {
	p := op.Path
	for k, v := range a.path {
		if v == "" {
			return "", fmt.Errorf("authgebra: empty path parameter %q for %s", k, op.ID)
		}
		p = strings.ReplaceAll(p, "{"+k+"}", url.PathEscape(v))
	}
	if strings.Contains(p, "{") {
		return "", fmt.Errorf("authgebra: unresolved path parameter in %s", op.Path)
	}
	u := *c.base
	// p already holds escaped path-parameter segments; keep them escaped on the wire
	// (RawPath) and give net/url the decoded form (Path) so nothing is escaped twice.
	escaped := strings.TrimRight(c.base.EscapedPath(), "/") + p
	decoded, err := url.PathUnescape(escaped)
	if err != nil {
		return "", fmt.Errorf("authgebra: bad path for %s: %w", op.ID, err)
	}
	u.Path, u.RawPath = decoded, escaped
	u.RawQuery = a.query.Encode()
	return u.String(), nil
}

func marshalBody(v any) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false) // keep UTF-8 payloads byte-exact
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimRight(buf.Bytes(), "\n"), nil
}

// roundTrip performs the request with bounded retries and returns the 2xx response,
// or *APIError / *ProtocolError / *TransportError.
func (c *Client) roundTrip(ctx context.Context, op opSpec, a callArgs, opts []CallOption) (*rawResponse, error) {
	var cc callConfig
	for _, o := range opts {
		o(&cc)
	}
	target, err := c.buildURL(op, a)
	if err != nil {
		return nil, err
	}
	var payload []byte
	if a.body != nil {
		if payload, err = marshalBody(a.body); err != nil {
			return nil, fmt.Errorf("authgebra: encode request for %s: %w", op.ID, err)
		}
	}
	requestID := cc.requestID
	if !ValidRequestID(requestID) {
		requestID = NewRequestID()
	}
	traceparent, tracestate := cc.traceparent, cc.tracestate
	if traceparent == "" {
		if v, ok := ctx.Value(traceparentKey).([2]string); ok {
			traceparent, tracestate = v[0], v[1]
		}
	}
	if traceparent == "" {
		traceparent = NewTraceparent()
	} else if !ValidTraceparent(traceparent) {
		return nil, errors.New("authgebra: invalid traceparent")
	}

	maxRetries := c.retry.MaxRetries
	canRetry := !op.Mutating
	if op.Mutating {
		allow := c.retry.RetryWrites
		if cc.retryWrites != nil {
			allow = *cc.retryWrites
		}
		// A mutation is only ever replayed with the same Idempotency-Key and identical bytes.
		canRetry = allow && a.header.Get("Idempotency-Key") != ""
	}
	if !canRetry {
		maxRetries = 0
	}

	for attempt := 1; ; attempt++ {
		raw, err := c.attempt(ctx, op, target, payload, a, &cc, requestID, traceparent, tracestate, attempt)
		if err == nil {
			return raw, nil
		}
		wait, retry := c.retryDecision(op, err)
		if !retry || attempt > maxRetries || ctx.Err() != nil {
			var te *TransportError
			if errors.As(err, &te) {
				te.Attempts = attempt
			}
			return nil, err
		}
		if serr := c.sleep(ctx, wait(attempt)); serr != nil {
			return nil, err
		}
	}
}

// retryDecision says whether err is retryable at all and how long to wait before attempt n+1.
func (c *Client) retryDecision(op opSpec, err error) (func(int) time.Duration, bool) {
	backoff := func(n int) time.Duration {
		d := c.retry.BaseBackoff << (n - 1)
		if d <= 0 || d > c.retry.MaxBackoff {
			d = c.retry.MaxBackoff
		}
		return d/2 + time.Duration(rand.Int64N(int64(d/2)+1))
	}
	var ae *APIError
	if errors.As(err, &ae) {
		switch ae.Code {
		case CodeRateLimited:
			return func(n int) time.Duration {
				d := backoff(n)
				if ae.RetryAfterSeconds != nil {
					d = time.Duration(*ae.RetryAfterSeconds) * time.Second
				}
				if d > c.retry.MaxRetryAfter {
					d = c.retry.MaxRetryAfter
				}
				return d
			}, true
		case CodeNotReady:
			return backoff, true
		case CodeIdempotencyInProgress:
			return backoff, op.Mutating // only reachable for opted-in idempotent writes
		}
		// AUTHZ/IDENTITY_RELOAD_PENDING, CURSOR_STALE, conflicts, 4xx: never retried.
		return nil, false
	}
	var te *TransportError
	if errors.As(err, &te) {
		switch te.Kind {
		case TransportTimeout, TransportConnectionReset, TransportNetwork:
			return backoff, true
		}
	}
	return nil, false
}

func (c *Client) attempt(ctx context.Context, op opSpec, target string, payload []byte, a callArgs,
	cc *callConfig, requestID, traceparent, tracestate string, attempt int) (*rawResponse, error) {

	start := time.Now()
	actx := ctx
	if c.cfg.Timeout >= 0 {
		to := c.cfg.Timeout
		if to == 0 {
			to = defaultTimeout
		}
		var cancel context.CancelFunc
		actx, cancel = context.WithTimeout(ctx, to)
		defer cancel()
	}
	var rdr io.Reader
	if payload != nil {
		rdr = bytes.NewReader(payload)
	}
	req, err := http.NewRequestWithContext(actx, op.Method, target, rdr)
	if err != nil {
		return nil, fmt.Errorf("authgebra: build request: %w", err)
	}
	for k, vs := range c.cfg.Headers {
		for _, v := range vs {
			req.Header.Add(k, v)
		}
	}
	for k, vs := range cc.header {
		for _, v := range vs {
			req.Header.Add(k, v)
		}
	}
	for k, vs := range a.header {
		for _, v := range vs {
			req.Header.Set(k, v)
		}
	}
	// SDK-managed headers are set last so callers cannot override them.
	req.Header.Set("Accept", "application/json")
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("User-Agent", c.ua)
	req.Header.Set("X-Request-ID", requestID)
	req.Header.Set("Traceparent", traceparent)
	if tracestate != "" {
		req.Header.Set("Tracestate", tracestate)
	}
	req.Header.Del("Authorization")
	if c.cfg.Token != nil && !cc.noAuth {
		tok, terr := c.cfg.Token.Token(ctx)
		if terr != nil {
			return nil, fmt.Errorf("authgebra: token provider failed: %w", terr)
		}
		if tok != "" {
			req.Header.Set("Authorization", "Bearer "+tok)
		}
	}

	resp, err := c.http.Do(req)
	if err != nil {
		te := c.classify(ctx, op, err)
		c.logf(op, 0, requestID, "", attempt, start, string(te.Kind))
		return nil, te
	}
	defer resp.Body.Close()
	body, rerr := io.ReadAll(io.LimitReader(resp.Body, c.maxBody+1))
	if rerr != nil {
		te := c.classify(ctx, op, rerr)
		c.logf(op, resp.StatusCode, requestID, "", attempt, start, string(te.Kind))
		return nil, te
	}
	if int64(len(body)) > c.maxBody {
		id, _ := requestIDFromHeader(resp.Header)
		return nil, &ProtocolError{Kind: ProtocolResponseTooLarge, Status: resp.StatusCode, RequestID: id,
			Err: fmt.Errorf("response exceeds %d bytes", c.maxBody)}
	}
	trace := traceIDFromHeaders(resp.Header)
	id, _ := requestIDFromHeader(resp.Header)
	c.logf(op, resp.StatusCode, id, trace, attempt, start, "")
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return &rawResponse{status: resp.StatusCode, header: resp.Header, body: body}, nil
	}
	return nil, decodeError(resp.StatusCode, resp.Header, body)
}

func (c *Client) logf(op opSpec, status int, requestID, traceID string, attempt int, start time.Time, failure string) {
	if c.cfg.Logger == nil {
		return
	}
	c.cfg.Logger.Debug("authgebra request",
		"operation", op.ID, "method", op.Method, "path", op.Path, "status", status,
		"request_id", requestID, "trace_id", traceID, "attempt", attempt,
		"duration", time.Since(start), "failure", failure)
}

// classify maps a low-level error to *TransportError.
func (c *Client) classify(ctx context.Context, op opSpec, err error) *TransportError {
	te := &TransportError{Method: op.Method, Path: op.Path, Err: err}
	var x509a x509.UnknownAuthorityError
	var x509h x509.HostnameError
	var x509c x509.CertificateInvalidError
	var tlsr *tls.CertificateVerificationError
	var tlsHeader tls.RecordHeaderError
	var netErr net.Error
	switch {
	case errors.Is(ctx.Err(), context.Canceled):
		te.Kind = TransportCanceled
	case errors.As(err, &tlsHeader), errors.As(err, &tlsr), errors.As(err, &x509a), errors.As(err, &x509h), errors.As(err, &x509c), errors.Is(err, http.ErrSchemeMismatch):
		te.Kind = TransportTLS
	case errors.Is(err, context.DeadlineExceeded), errors.As(err, &netErr) && netErr.Timeout():
		te.Kind = TransportTimeout
	case errors.Is(err, syscall.ECONNRESET), errors.Is(err, syscall.EPIPE),
		errors.Is(err, io.EOF), errors.Is(err, io.ErrUnexpectedEOF), errors.Is(err, net.ErrClosed):
		te.Kind = TransportConnectionReset
	default:
		te.Kind = TransportNetwork
	}
	if op.Mutating && te.Kind != TransportCanceled {
		te.CommitState = CommitStateUnknown
	}
	if te.Kind == TransportCanceled && op.Mutating {
		te.CommitState = CommitStateUnknown
	}
	return te
}

// call executes op and decodes the success body into Result[T].
func call[T any](ctx context.Context, c *Client, op opSpec, a callArgs, opts []CallOption) (*Result[T], error) {
	raw, err := c.roundTrip(ctx, op, a, opts)
	if err != nil {
		return nil, err
	}
	reqID, _ := requestIDFromHeader(raw.header)
	res := &Result[T]{
		Status: raw.status, RequestID: reqID, TraceID: traceIDFromHeaders(raw.header),
		ETag: raw.header.Get("ETag"), Replayed: raw.header.Get("Idempotency-Replayed") == "true", Header: raw.header,
	}
	if v := raw.header.Get("X-Authz-Revision"); v != "" {
		if n, perr := strconv.ParseInt(v, 10, 64); perr == nil {
			res.AuthzRevisionHeader = &n
		}
	}
	protoErr := func(kind ProtocolErrorKind, e error) error {
		pe := &ProtocolError{Kind: kind, Status: raw.status, RequestID: res.RequestID, TraceID: res.TraceID, Err: e}
		if kind == ProtocolNonJSON {
			pe.BodySnippet = printableSnippet(raw.body)
		}
		return pe
	}
	if !json.Valid(raw.body) {
		return nil, protoErr(ProtocolNonJSON, nil)
	}
	if op.Env == envBare {
		if err := json.Unmarshal(raw.body, &res.Data); err != nil {
			return nil, protoErr(ProtocolMalformedSuccessBody, err)
		}
		return res, nil
	}
	var env struct {
		Data json.RawMessage `json:"data"`
		Meta *Meta           `json:"meta"`
		Page *Page           `json:"page"`
	}
	if err := json.Unmarshal(raw.body, &env); err != nil {
		return nil, protoErr(ProtocolMalformedSuccessBody, err)
	}
	if env.Data == nil || isJSONNull(env.Data) || (op.Env == envPaged && env.Page == nil) {
		return nil, protoErr(ProtocolMalformedSuccessBody, errors.New("missing required field"))
	}
	if err := json.Unmarshal(env.Data, &res.Data); err != nil {
		return nil, protoErr(ProtocolMalformedSuccessBody, err)
	}
	res.Meta, res.Page = env.Meta, env.Page
	return res, nil
}
