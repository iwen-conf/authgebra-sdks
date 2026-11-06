package authgebra

import (
	"context"
	"crypto/tls"
	"errors"
	"log/slog"
	"net/http"
	"time"
)

// TokenProvider supplies the bearer access token for each attempt.
type TokenProvider interface {
	Token(ctx context.Context) (string, error)
}

// TokenFunc adapts a function to TokenProvider.
type TokenFunc func(ctx context.Context) (string, error)

// Token implements TokenProvider.
func (f TokenFunc) Token(ctx context.Context) (string, error) { return f(ctx) }

type staticToken string

// StaticToken returns a TokenProvider that always yields tok.
// Its String/GoString never reveal the token.
func StaticToken(tok string) TokenProvider { return staticToken(tok) }

func (t staticToken) Token(context.Context) (string, error) { return string(t), nil }
func (staticToken) String() string                          { return "authgebra.StaticToken(<redacted>)" }
func (staticToken) GoString() string                        { return "authgebra.StaticToken(<redacted>)" }

// RetryPolicy bounds automatic retries. The zero value means "use defaults";
// retries are never unbounded.
type RetryPolicy struct {
	// MaxRetries is the number of extra attempts after the first (default 2; set
	// NoRetry to disable). Hard cap: 10.
	MaxRetries int
	// NoRetry disables all automatic retries.
	NoRetry bool
	// BaseBackoff is the first backoff step (default 100ms), doubled per attempt with jitter.
	BaseBackoff time.Duration
	// MaxBackoff caps one backoff sleep (default 2s).
	MaxBackoff time.Duration
	// MaxRetryAfter caps how long a server Retry-After is honored (default 5s).
	MaxRetryAfter time.Duration
	// RetryWrites opts in to retrying mutating requests. It only takes effect for
	// requests that carry an Idempotency-Key header (same key, same payload bytes).
	RetryWrites bool
}

const maxRetriesCap = 10

func (p RetryPolicy) normalized() RetryPolicy {
	if p.NoRetry {
		p.MaxRetries = 0
	} else if p.MaxRetries <= 0 {
		p.MaxRetries = 2
	}
	if p.MaxRetries > maxRetriesCap {
		p.MaxRetries = maxRetriesCap
	}
	if p.BaseBackoff <= 0 {
		p.BaseBackoff = 100 * time.Millisecond
	}
	if p.MaxBackoff <= 0 {
		p.MaxBackoff = 2 * time.Second
	}
	if p.MaxRetryAfter <= 0 {
		p.MaxRetryAfter = 5 * time.Second
	}
	return p
}

// Config configures a Client.
type Config struct {
	// BaseURL is the server origin, e.g. https://authgebra.example.com (required).
	BaseURL string
	// Token supplies the bearer token; nil sends no Authorization header.
	Token TokenProvider
	// HTTPClient is an optional custom client/transport. Its TLS settings are used as is.
	HTTPClient *http.Client
	// TLSConfig customizes TLS (e.g. RootCAs) for the default transport. Certificate
	// verification stays on unless you explicitly change it here. Ignored with HTTPClient.
	TLSConfig *tls.Config
	// ConnectTimeout bounds dialing for the default transport (default 5s). Ignored with HTTPClient.
	ConnectTimeout time.Duration
	// Timeout bounds each attempt, including reading the body (default 30s; negative disables).
	Timeout time.Duration
	// Headers are added to every request (never overrides SDK-managed headers).
	Headers http.Header
	// UserAgentSuffix is appended to the SDK User-Agent (authgebra-go/<version>).
	UserAgentSuffix string
	// MaxResponseBytes caps the response body (default 8 MiB).
	MaxResponseBytes int64
	// Retry bounds automatic retries.
	Retry RetryPolicy
	// Logger receives debug logs: method, path template, status, ids, attempt, duration.
	// Never tokens, headers or bodies. nil disables logging.
	Logger *slog.Logger
}

const (
	defaultTimeout        = 30 * time.Second
	defaultConnectTimeout = 5 * time.Second
	defaultMaxResponse    = 8 << 20
)

var errNoBaseURL = errors.New("authgebra: Config.BaseURL is required")
