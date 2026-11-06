package authgebra

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"strings"
	"sync"
	"testing"
	"time"
)

// This test owns only uniquely prefixed facts on the explicitly confirmed isolated surface.
// call is the same HTTP pipeline used by generated methods; authorization checks use the public facade.
func TestRealServerContract(t *testing.T) {
	base := os.Getenv("AUTHGEBRA_IT_BASE_URL")
	if base == "" {
		t.Skip("NOT_RUN: AUTHGEBRA_IT_BASE_URL unset")
	}
	if os.Getenv("AUTHGEBRA_IT_CONFIRM_ISOLATED") != "1" {
		t.Fatal("confirm disposable SDK surface")
	}
	username, password := os.Getenv("AUTHGEBRA_IT_USER"), os.Getenv("AUTHGEBRA_IT_PASSWORD")
	if username == "" || password == "" {
		t.Fatal("missing integration credentials")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	token := ""
	c, err := New(Config{BaseURL: base, Token: TokenFunc(func(context.Context) (string, error) { return token, nil })})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	prefix := fmt.Sprintf("it-go-%x", time.Now().UnixNano())
	request := func(method, path string, body any, key string, q url.Values) (*Result[json.RawMessage], error) {
		h := http.Header{}
		if key != "" {
			h.Set("Idempotency-Key", key)
		}
		return call[json.RawMessage](ctx, c, opSpec{Method: method, Path: path, Mutating: method != "GET" && !strings.HasPrefix(path, "/api/v1/authz/"), Env: envData}, callArgs{body: body, header: h, query: q}, []CallOption{WithRequestID(NewRequestID())})
	}
	ok := func(method, path string, body any, key string, q url.Values) *Result[json.RawMessage] {
		t.Helper()
		r, e := request(method, path, body, key, q)
		if e != nil {
			t.Fatalf("%s %s: %v", method, path, e)
		}
		return r
	}
	field := func(raw json.RawMessage, key string) string {
		t.Helper()
		var v map[string]json.RawMessage
		if e := json.Unmarshal(raw, &v); e != nil {
			t.Fatal(e)
		}
		var s string
		if e := json.Unmarshal(v[key], &s); e != nil {
			t.Fatal(e)
		}
		return s
	}
	api := func(t *testing.T, method, path string, body any, key string, q url.Values, code string, status int) *APIError {
		t.Helper()
		_, e := request(method, path, body, key, q)
		var a *APIError
		if !errors.As(e, &a) || a.Code != ErrorCode(code) || a.Status != status {
			t.Fatalf("%s: want %d %s, got %v", path, status, code, e)
		}
		if a.RequestID == "" || a.TraceID == "" {
			t.Fatal("missing response correlation")
		}
		return a
	}
	api(t, "POST", "/api/v1/auth/login", map[string]string{"identifier": username, "password": password + "-wrong"}, "", nil, "INVALID_CREDENTIALS", 401)
	login := ok("POST", "/api/v1/auth/login", map[string]string{"identifier": username, "password": password}, "", nil)
	token = field(login.Data, "access_token")
	if token == "" {
		t.Fatal("empty access token")
	}
	me := ok("GET", "/api/v1/auth/me", nil, "", nil)
	var m struct {
		User struct {
			ID string `json:"id"`
		} `json:"user"`
	}
	if e := json.Unmarshal(me.Data, &m); e != nil {
		t.Fatal(e)
	}
	subject := m.User.ID
	for permission, want := range map[string]string{"user:read": "allow", "nope:nope": "deny"} {
		r, e := c.Authorization.Check(ctx, CheckRequest{SubjectID: subject, Permission: permission})
		if e != nil {
			t.Fatal(e)
		}
		if r.Data.Decision != want || r.Meta == nil || r.Meta.AuthzRevision <= 0 || r.Meta.SnapshotGeneration == nil {
			t.Fatal("decision/revision contract")
		}
	}
	t.Log("IT-1/2/3 PASS")
	t.Run("typed-role-crud", func(t *testing.T) {
		description := "typed &<> 中文"
		body := CreateRoleRequest{Code: prefix + "-typed-role", Name: "Typed role 中文", Description: &description}
		params := CreateRoleParams{IdempotencyKey: NewRequestID()}
		created, err := c.Admin.Roles.CreateRole(ctx, params, body)
		if err != nil {
			t.Fatal(err)
		}
		id := created.Data.ID
		if id == "" {
			t.Fatal("typed role response has no ID")
		}
		deleted := false
		defer func() {
			if deleted {
				return
			}
			current, err := c.Admin.Roles.GetRole(ctx, id)
			if err != nil {
				t.Errorf("typed role cleanup read: %v", err)
				return
			}
			if _, err := c.Admin.Roles.DeleteRole(ctx, id, DeleteRoleParams{IfMatch: current.Data.Etag}); err != nil {
				t.Errorf("typed role cleanup delete: %v", err)
			}
		}()
		assertRole := func(role RoleDto) {
			t.Helper()
			if role.ID != id || role.Code != body.Code || role.Name != body.Name || role.Etag == "" || role.Version < 1 || role.IsSystem || !role.Enabled || role.UsersCount != 0 || role.PermissionsCount != 0 {
				t.Fatal("typed role fields differ from persisted input")
			}
		}
		assertRole(created.Data)
		if created.Status != 201 || created.Data.Description == nil || *created.Data.Description != description {
			t.Fatal("typed create response fields")
		}
		replay, err := c.Admin.Roles.CreateRole(ctx, params, body)
		if err != nil {
			t.Fatal(err)
		}
		assertRole(replay.Data)
		if !replay.Replayed || replay.Data.Description == nil || *replay.Data.Description != description {
			t.Fatal("typed create idempotent replay")
		}
		fetched, err := c.Admin.Roles.GetRole(ctx, id)
		if err != nil {
			t.Fatal(err)
		}
		assertRole(fetched.Data)
		if fetched.Data.Description == nil || *fetched.Data.Description != description {
			t.Fatal("typed get description")
		}
		patched, err := c.Admin.Roles.PatchRole(ctx, id, PatchRoleParams{IfMatch: fetched.Data.Etag}, PatchRoleRequest{DescriptionField: NullField[string]()})
		if err != nil {
			t.Fatal(err)
		}
		assertRole(patched.Data)
		if patched.Data.Description != nil || patched.Data.Version <= fetched.Data.Version {
			t.Fatal("typed PATCH did not clear description or advance version")
		}
		cleared, err := c.Admin.Roles.GetRole(ctx, id)
		if err != nil {
			t.Fatal(err)
		}
		assertRole(cleared.Data)
		if cleared.Data.Description != nil {
			t.Fatal("typed PATCH clear was not persisted")
		}
		if _, err := c.Admin.Roles.DeleteRole(ctx, id, DeleteRoleParams{IfMatch: cleared.Data.Etag}); err != nil {
			t.Fatal(err)
		}
		deleted = true
		_, err = c.Admin.Roles.GetRole(ctx, id)
		var missing *APIError
		if !errors.As(err, &missing) || missing.Status != 404 || missing.Code != CodeResourceNotFound {
			t.Fatalf("deleted typed role remains readable: %v", err)
		}
		t.Log("IT-TYPED-ROLE PASS")
	})
	ok("POST", "/api/v1/resource-types", map[string]string{"code": prefix, "name": prefix}, "", nil)
	attr := ok("POST", "/api/v1/attributes", map[string]string{"code": prefix, "name": prefix, "owner_type": "resource", "value_type": "string"}, "", nil)
	secret := NewRequestID()
	refs := []map[string]string{{"resource_type_code": prefix, "external_id": "b"}, {"resource_type_code": prefix, "external_id": "a"}}
	for _, ref := range refs {
		ok("POST", "/api/v1/resources", map[string]any{"resource_type_code": prefix, "external_id": ref["external_id"], "name": ref["external_id"], "attributes": []any{map[string]string{"attribute_id": field(attr.Data, "id"), "value": secret}}}, "", nil)
	}
	missing := map[string]string{"resource_type_code": prefix, "external_id": "missing"}
	batch := ok("POST", "/api/v1/authz/batch-check", map[string]any{"subject_id": subject, "permission": "user:read", "resources": []any{refs[0], missing, refs[1], refs[0]}}, "", nil)
	var b struct {
		Allowed, Denied []struct {
			Resource struct {
				ExternalID string `json:"external_id"`
			} `json:"resource"`
		}
	}
	if e := json.Unmarshal(batch.Data, &b); e != nil {
		t.Fatal(e)
	}
	if len(b.Allowed) != 2 || len(b.Denied) != 1 || b.Allowed[0].Resource.ExternalID != "b" || b.Allowed[1].Resource.ExternalID != "a" || b.Denied[0].Resource.ExternalID != "missing" {
		t.Fatal("batch partition/order/dedup")
	}
	body := map[string]any{"subject_id": subject, "permission": "user:read", "resource_type": prefix, "limit": 1}
	first := ok("POST", "/api/v1/authz/list-resources", body, "", nil)
	if first.Page == nil || !first.Page.HasMore || first.Page.NextCursor == nil {
		t.Fatal("first page")
	}
	cursor := *first.Page.NextCursor
	body["cursor"] = cursor
	second := ok("POST", "/api/v1/authz/list-resources", body, "", nil)
	if second.Page == nil || second.Page.HasMore || string(first.Data) == string(second.Data) || first.Meta == nil || second.Meta == nil || *first.Meta.SnapshotGeneration != *second.Meta.SnapshotGeneration {
		t.Fatal("page continuation")
	}
	replacement := "A"
	if strings.HasPrefix(cursor, "A") {
		replacement = "B"
	}
	body["cursor"] = replacement + cursor[1:]
	api(t, "POST", "/api/v1/authz/list-resources", body, "", nil, "CURSOR_INVALID", 400)
	explain := ok("POST", "/api/v1/authz/explain", map[string]any{"subject_id": subject, "permission": "user:read", "resource": refs[0]}, "", nil)
	if strings.Contains(string(explain.Data), secret) || field(explain.Data, "decision") != "allow" {
		t.Fatal("explain leaks attribute or wrong decision")
	}
	rev, e := c.Authorization.Revision(ctx)
	if e != nil {
		t.Fatal(e)
	}
	if rev.Data.DBRevision <= 0 || rev.Data.DBRevision != rev.Data.SnapshotGeneration || rev.Data.Status != "in_sync" {
		t.Fatal("revision not in sync")
	}
	t.Log("IT-4/5/6/7 PASS")
	user := map[string]string{"username": prefix, "display_name": prefix, "initial_password": password}
	key := NewRequestID()
	created := ok("POST", "/api/v1/users", user, key, nil)
	replay := ok("POST", "/api/v1/users", user, key, nil)
	if created.Status != 201 || !replay.Replayed || field(created.Data, "id") != field(replay.Data, "id") {
		t.Fatal("idempotent replay")
	}
	changed := map[string]string{"username": prefix, "display_name": "different", "initial_password": password}
	conflict := api(t, "POST", "/api/v1/users", changed, key, nil, "IDEMPOTENCY_CONFLICT", 409)
	if conflict.Retryable == nil || *conflict.Retryable {
		t.Fatal("conflict retry hint")
	}
	// Concurrent duplicates of an already committed key must replay the same entity.
	var wg sync.WaitGroup
	errs := make(chan error, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r, e := request("POST", "/api/v1/users", user, key, nil)
			if e == nil && (!r.Replayed || field(r.Data, "id") != field(created.Data, "id")) {
				e = fmt.Errorf("duplicate entity")
			}
			errs <- e
		}()
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		if e != nil {
			t.Fatal(e)
		}
	}
	found := ok("GET", "/api/v1/users", nil, "", url.Values{"q": {prefix}})
	var users []json.RawMessage
	if e := json.Unmarshal(found.Data, &users); e != nil {
		t.Fatal(e)
	}
	if len(users) != 1 || field(users[0], "id") != field(created.Data, "id") {
		t.Fatal("durable user count")
	}
	t.Log("IT-8 PASS")
	// Finish independent errors even when another subcase fails; preserve the first real divergence.
	t.Run("IT-9", func(t *testing.T) {
		trace := "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
		_, e := call[json.RawMessage](ctx, c, opSpec{Method: "GET", Path: "/api/v1/users/00000000-0000-4000-8000-000000000099"}, callArgs{}, []CallOption{WithTraceparent(trace, ""), WithRequestID(prefix + "-missing")})
		var a *APIError
		if !errors.As(e, &a) || a.Code != CodeResourceNotFound || a.Status != 404 || a.TraceID != "1234567890abcdef1234567890abcdef" || a.RequestID != prefix+"-missing" {
			t.Fatal("not-found correlation")
		}
	})
	t.Run("IT-10-query", func(t *testing.T) {
		e := api(t, "GET", "/api/v1/users", nil, "", url.Values{"limit": {"abc"}}, "INVALID_REQUEST", 400)
		if len(e.Violations) == 0 || e.Violations[0].Reason != "invalid_query_parameter" {
			t.Fatal("query violation")
		}
	})
	t.Run("IT-10-path", func(t *testing.T) {
		e := api(t, "GET", "/api/v1/users/not-a-uuid", nil, "", nil, "INVALID_REQUEST", 400)
		if len(e.Violations) == 0 || e.Violations[0].Reason != "invalid_path_parameter" {
			t.Fatal("path violation")
		}
	})
	t.Run("IT-12", func(t *testing.T) {
		e := api(t, "POST", "/api/v1/auth/login", map[string]any{"identifier": username, "password": password, "extra": true}, "", nil, "INVALID_REQUEST", 400)
		if len(e.Violations) == 0 || e.Violations[0].Reason != "unknown_field" {
			t.Fatal("unknown-field violation")
		}
	})
	t.Run("IT-11", func(t *testing.T) {
		if os.Getenv("AUTHGEBRA_IT_FAULT") != "1" {
			t.Skip("NOT_RUN: fault injection not enabled")
		}
		on, off := os.Getenv("AUTHGEBRA_IT_FAULT_ON_CMD"), os.Getenv("AUTHGEBRA_IT_FAULT_OFF_CMD")
		if on == "" || off == "" {
			t.Fatal("missing fault commands")
		}
		run := func(cmd string) {
			t.Helper()
			cx, stop := context.WithTimeout(ctx, 15*time.Second)
			defer stop()
			if e := exec.CommandContext(cx, "sh", "-c", cmd).Run(); e != nil {
				t.Fatal("fault command failed")
			}
		}
		faultUser := map[string]string{"username": prefix + "-f", "display_name": prefix, "initial_password": password}
		faultKey := NewRequestID()
		func() {
			defer run(off)
			run(on)
			e := api(t, "POST", "/api/v1/users", faultUser, faultKey, nil, "AUTHZ_RELOAD_PENDING", 503)
			if e.CommitState != CommitStateCommittedUnpublished || e.Retryable == nil || *e.Retryable {
				t.Fatal("committed-unpublished contract")
			}
		}()
		deadline := time.Now().Add(60 * time.Second)
		for {
			r, e := c.Authorization.Revision(ctx)
			if e != nil {
				var pending *APIError
				if !errors.As(e, &pending) || pending.Code != "NOT_READY" || pending.Status != 503 {
					t.Fatal(e)
				}
			} else if r.Data.DBRevision == r.Data.SnapshotGeneration {
				break
			}
			if time.Now().After(deadline) {
				t.Fatal("recovery deadline")
			}
			timer := time.NewTimer(200 * time.Millisecond)
			select {
			case <-ctx.Done():
				timer.Stop()
				t.Fatal(ctx.Err())
			case <-timer.C:
			}
		}
		r := ok("POST", "/api/v1/users", faultUser, faultKey, nil)
		f := ok("GET", "/api/v1/users", nil, "", url.Values{"q": {prefix + "-f"}})
		var list []json.RawMessage
		if e := json.Unmarshal(f.Data, &list); e != nil {
			t.Fatal(e)
		}
		if !r.Replayed || len(list) != 1 || field(list[0], "id") != field(r.Data, "id") {
			t.Fatal("fault recovery duplicated/lost write")
		}
	})
}
