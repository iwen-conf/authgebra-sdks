package authgebra

import (
	"encoding/json"
	"testing"
)

func TestNonNullableFieldValueCannotSerializeTypedNil(t *testing.T) {
	request := CreateRelationDefinitionRequest{
		ObjectType: "document", Relation: "viewer", SubjectTypes: []string{"user"},
		RewritesField: SetField([]RewriteRuleDto(nil)),
	}
	if _, err := json.Marshal(request); err == nil {
		t.Fatal("a typed nil slice is JSON null, which rewrites does not accept")
	}
	fields := map[string]json.RawMessage{}
	if err := encodeField(fields, "dictionary", SetField(map[string]string(nil)), false, false); err == nil {
		t.Fatal("a typed nil map must not bypass a non-nullable field")
	}
	if err := request.Validate(); err == nil {
		t.Fatal("Validate accepted a typed nil slice")
	}
	request.RewritesField = SetField([]RewriteRuleDto{})
	if _, err := json.Marshal(request); err != nil {
		t.Fatalf("an explicit empty array must remain valid: %v", err)
	}
}
