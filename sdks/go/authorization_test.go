package authgebra

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"sync/atomic"
	"testing"
)

func pageBody(gen int, next string) string {
	cursor := "null"
	more := "false"
	if next != "" {
		cursor, more = `"`+next+`"`, "true"
	}
	return fmt.Sprintf(`{"data":[{"resource_type_code":"doc","external_id":"d-%s"}],"page":{"next_cursor":%s,"has_more":%s},"meta":{"authz_revision":%d,"snapshot_generation":%d}}`, next, cursor, more, gen, gen)
}

func listReq() ListResourcesRequest {
	return ListResourcesRequest{SubjectID: testSubject, Permission: "p:r", ResourceType: "doc"}
}

func TestIteratorWalksPagesAndStops(t *testing.T) {
	var n atomic.Int32
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		i := n.Add(1)
		next := ""
		if i < 3 {
			next = fmt.Sprintf("c%d", i)
		}
		_, _ = w.Write([]byte(pageBody(7, next)))
	})
	c, rec, _ := newTestClient(t, h, nil)
	it := c.Authorization.ListResourcesPages(listReq(), PageOptions{})
	pages := 0
	for it.Next(context.Background()) {
		pages++
	}
	if it.Err() != nil || pages != 3 || rec.count() != 3 {
		t.Fatalf("pages=%d requests=%d err=%v", pages, rec.count(), it.Err())
	}
	if string(rec.reqs[1].body) == string(rec.reqs[0].body) {
		t.Fatal("second request must carry the cursor")
	}
}

func TestIteratorSurfacesCursorStaleWithoutRestarting(t *testing.T) {
	var n atomic.Int32
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if n.Add(1) == 1 {
			_, _ = w.Write([]byte(pageBody(7, "c1")))
			return
		}
		w.WriteHeader(409)
		_, _ = w.Write([]byte(errBody("CURSOR_STALE", `"retryable":false`)))
	})
	c, rec, _ := newTestClient(t, h, nil)
	it := c.Authorization.ListResourcesPages(listReq(), PageOptions{})
	for it.Next(context.Background()) {
	}
	var ae *APIError
	if !errors.As(it.Err(), &ae) || ae.Code != CodeCursorStale {
		t.Fatalf("got %v", it.Err())
	}
	if rec.count() != 2 {
		t.Fatalf("stale cursor must not trigger a silent restart or retry: %d requests", rec.count())
	}
	if it.Next(context.Background()) {
		t.Fatal("iterator must stay stopped after an error")
	}
}

func TestIteratorRefusesToMixGenerations(t *testing.T) {
	var n atomic.Int32
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		i := n.Add(1)
		_, _ = w.Write([]byte(pageBody(int(i)+10, "more")))
	})
	c, _, _ := newTestClient(t, h, nil)
	it := c.Authorization.ListResourcesPages(listReq(), PageOptions{})
	got := 0
	for it.Next(context.Background()) {
		got++
	}
	if got != 1 || !errors.Is(it.Err(), ErrGenerationChanged) {
		t.Fatalf("pages=%d err=%v", got, it.Err())
	}
}

func TestIteratorIsBoundedAndCancellable(t *testing.T) {
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(pageBody(7, "again"))) })
	c, rec, _ := newTestClient(t, h, nil)
	it := c.Authorization.ListResourcesPages(listReq(), PageOptions{MaxPages: 4})
	for it.Next(context.Background()) {
	}
	if !errors.Is(it.Err(), ErrMaxPages) || rec.count() != 4 {
		t.Fatalf("err=%v requests=%d", it.Err(), rec.count())
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	it2 := c.Authorization.ListResourcesPages(listReq(), PageOptions{})
	if it2.Next(ctx) || it2.Err() == nil {
		t.Fatal("cancelled context must stop the iterator with an error")
	}
}

func TestDenyIsASuccessfulResultAndNotReadyIsNeverADeny(t *testing.T) {
	c, _, _ := newTestClient(t, respond(200, nil, `{"data":{"decision":"deny","reason_code":"default_deny"},"meta":{"authz_revision":3,"snapshot_generation":3}}`), nil)
	r, err := c.Authorization.Check(context.Background(), CheckRequest{SubjectID: testSubject, Permission: "p:r"})
	if err != nil || r.Data.Allowed() || r.Data.Decision != DecisionDeny {
		t.Fatalf("deny must be a normal result: %v %+v", err, r)
	}
	c2, _, _ := newTestClient(t, respond(503, nil, errBody("NOT_READY", "")), func(c *Config) { c.Retry.NoRetry = true })
	r2, err := c2.Authorization.Check(context.Background(), CheckRequest{SubjectID: testSubject, Permission: "p:r"})
	if err == nil || r2 != nil {
		t.Fatal("503 NOT_READY must be an error with no Result (never a computed deny)")
	}
}
