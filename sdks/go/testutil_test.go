package authgebra

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

const testSubject = "11111111-1111-4111-8111-111111111111"

// recorder captures what a test server received.
type recorder struct {
	mu   sync.Mutex
	reqs []recorded
}

type recorded struct {
	method, path, rawQuery string
	header                 http.Header
	body                   []byte
}

func (r *recorder) add(rr recorded) { r.mu.Lock(); r.reqs = append(r.reqs, rr); r.mu.Unlock() }
func (r *recorder) count() int      { r.mu.Lock(); defer r.mu.Unlock(); return len(r.reqs) }
func (r *recorder) last() recorded  { r.mu.Lock(); defer r.mu.Unlock(); return r.reqs[len(r.reqs)-1] }

// newTestClient builds a client against h with instant retry sleeps.
func newTestClient(t *testing.T, h http.Handler, mut func(*Config)) (*Client, *recorder, *[]time.Duration) {
	t.Helper()
	rec := &recorder{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		buf := make([]byte, 0, 512)
		tmp := make([]byte, 512)
		for {
			n, err := r.Body.Read(tmp)
			buf = append(buf, tmp[:n]...)
			if err != nil {
				break
			}
		}
		rec.add(recorded{r.Method, r.URL.Path, r.URL.RawQuery, r.Header.Clone(), buf})
		h.ServeHTTP(w, r)
	}))
	t.Cleanup(srv.Close)
	cfg := Config{BaseURL: srv.URL, Token: StaticToken("secret-token-value")}
	if mut != nil {
		mut(&cfg)
	}
	c, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	sleeps := &[]time.Duration{}
	c.sleep = func(ctx context.Context, d time.Duration) error {
		mu.Lock()
		*sleeps = append(*sleeps, d)
		mu.Unlock()
		return ctx.Err()
	}
	return c, rec, sleeps
}

func respond(status int, headers map[string]string, body string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		for k, v := range headers {
			w.Header().Set(k, v)
		}
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	})
}

func okCheckBody() string {
	return `{"data":{"decision":"allow","reason_code":"allow"},"meta":{"authz_revision":5,"snapshot_generation":5}}`
}

func errBody(code, extra string) string {
	if extra != "" {
		extra = "," + extra
	}
	return `{"error":{"code":"` + code + `","message":"m","request_id":"req-abcdefgh"` + extra + `}}`
}

func noSleep(ctx context.Context, _ time.Duration) error { return ctx.Err() }
