package authgebra

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"
)

func setPatchClear(t *testing.T, request any, field string) {
	t.Helper()
	clear := reflect.ValueOf(request).Elem().FieldByName("Clear" + field)
	if !clear.IsValid() {
		t.Fatalf("typed PATCH has no explicit null for %s", field)
	}
	clear.SetBool(true)
}

func TestTypedPatchDistinguishesAbsentNullAndValue(t *testing.T) {
	for _, request := range []any{
		&PatchUserRequest{}, &PatchRoleRequest{}, &PatchPolicyRequest{}, &PatchResourceTypeRequest{},
	} {
		t.Run(reflect.TypeOf(request).Elem().Name(), func(t *testing.T) {
			field, key, value := "Description", "description", "replacement"
			if _, ok := request.(*PatchUserRequest); ok {
				field, key = "Email", "email"
			}
			assertJSON := func(want string) {
				t.Helper()
				got, err := json.Marshal(request)
				if err != nil || string(got) != want {
					t.Fatalf("PATCH JSON = %s, %v; want %s", got, err, want)
				}
			}
			assertJSON(`{}`)
			reflect.ValueOf(request).Elem().FieldByName(field).Set(reflect.ValueOf(&value))
			assertJSON(`{"` + key + `":"replacement"}`)
			reflect.ValueOf(request).Elem().FieldByName(field).Set(reflect.Zero(reflect.TypeOf(&value)))
			setPatchClear(t, request, field)
			assertJSON(`{"` + key + `":null}`)
			reflect.ValueOf(request).Elem().FieldByName(field).Set(reflect.ValueOf(&value))
			if _, err := json.Marshal(request); err == nil {
				t.Fatal("conflicting PATCH value and explicit null must fail")
			}
		})
	}
}

func TestTypedPatchNullReachesHTTP(t *testing.T) {
	c, wire, _ := newTestClient(t, respond(200, nil, `{"data":{"id":"`+testSubject+`","username":"user","display_name":"User","status":"active","auth_version":1,"version":1,"etag":"1","created_at_rfc3339":"2026-11-06T00:00:00Z","mfa_enabled":false,"roles_count":0,"sessions_count":0}}`), nil)
	request := PatchUserRequest{}
	setPatchClear(t, &request, "Email")
	_, err := c.Admin.Users.PatchUser(context.Background(), testSubject, PatchUserParams{IfMatch: `W/"1"`}, request)
	if err != nil {
		t.Fatal(err)
	}
	if got := string(wire.last().body); got != `{"email":null}` {
		t.Fatalf("wire PATCH = %s; want explicit email null", got)
	}
}
