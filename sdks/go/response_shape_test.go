package authgebra

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
)

func TestKnownResponsePresenceAndNull(t *testing.T) {
	for _, raw := range []string{`null`, `{}`, `{"decision":null,"reason_code":"missing_permission"}`, `{"decision":"deny","reason_code":null}`} {
		var value CheckResponse
		if err := json.Unmarshal([]byte(raw), &value); err == nil {
			t.Fatalf("accepted invalid CheckResponse: %s", raw)
		}
	}
	var shared AuthzResourceRef
	for _, raw := range []string{`{}`, `{"resource_type_code":"document","external_id":null}`} {
		if err := json.Unmarshal([]byte(raw), &shared); err == nil {
			t.Fatalf("accepted invalid shared response: %s", raw)
		}
	}
	if err := json.Unmarshal([]byte(`{"resource_type_code":"document","external_id":"1","future":true}`), &shared); err != nil {
		t.Fatal(err)
	}
	var v CheckResponse
	if err := json.Unmarshal([]byte(`{"decision":"future_decision","reason_code":"future_reason","future":true}`), &v); err != nil {
		t.Fatal(err)
	}
}

func TestMalformedTypedSuccessIsProtocolError(t *testing.T) {
	for _, body := range []string{`{"data":null}`, `{"data":{}}`, `{"data":{"decision":"deny","reason_code":"missing_permission"},"meta":{}}`, `{"data":{"decision":"deny","reason_code":"missing_permission"},"meta":{"authz_revision":null}}`} {
		t.Run(body, func(t *testing.T) {
			c, _, _ := newTestClient(t, respond(200, nil, body), nil)
			_, err := c.Authorization.Check(context.Background(), CheckRequest{SubjectID: testSubject, Permission: "document:read"})
			var protocol *ProtocolError
			if !errors.As(err, &protocol) || protocol.Kind != ProtocolMalformedSuccessBody {
				t.Fatalf("expected malformed success, got %v", err)
			}
		})
	}
}

func TestAdminPagedSuccessRequiresPageAndHasMore(t *testing.T) {
	for _, body := range []string{`{"data":[]}`, `{"data":[],"page":null}`, `{"data":[],"page":{}}`, `{"data":[],"page":{"has_more":null}}`} {
		t.Run(body, func(t *testing.T) {
			c, _, _ := newTestClient(t, respond(200, nil, body), nil)
			_, err := c.Admin.Users.ListUsers(context.Background(), ListUsersParams{})
			var protocol *ProtocolError
			if !errors.As(err, &protocol) || protocol.Kind != ProtocolMalformedSuccessBody {
				t.Fatalf("expected malformed paged success, got %v", err)
			}
		})
	}
	c, _, _ := newTestClient(t, respond(200, nil, `{"data":[],"page":{"has_more":false,"future":true}}`), nil)
	if _, err := c.Admin.Users.ListUsers(context.Background(), ListUsersParams{}); err != nil {
		t.Fatal(err)
	}
}
