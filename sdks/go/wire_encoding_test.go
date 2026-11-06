package authgebra

import (
	"context"
	"encoding/json"
	"testing"
)

func TestTypedClientPreservesUnescapedHTMLWire(t *testing.T) {
	const text = "&<>中文"
	for _, request := range []PatchRoleRequest{{Description: &[]string{text}[0]}, {DescriptionField: SetField(text)}} {
		c, wire, _ := newTestClient(t, respond(204, nil, ``), nil)
		// roundTrip isolates request serialization from a deliberately bodyless response.
		if _, err := c.roundTrip(context.Background(), opPatchRole, callArgs{body: request, path: map[string]string{"id": testSubject}}, nil); err != nil {
			t.Fatal(err)
		}
		if got := string(wire.last().body); got != `{"description":"`+text+`"}` {
			t.Fatalf("request bytes = %s", got)
		}
	}
	union := TypedValueDto{String: &TypedValueDtoString{Type: TypedValueDtoStringTypeString, Value: text}}
	raw, err := marshalBody(union)
	if err != nil || string(raw) != `{"type":"string","value":"`+text+`"}` {
		t.Fatalf("union bytes = %s, %v", raw, err)
	}
	value, err := ParseJSONValue([]byte(`{"text":"` + text + `"}`))
	if err != nil {
		t.Fatal(err)
	}
	fields := map[string]json.RawMessage{}
	if err := encodeField(fields, "value", SetField(value), true, false); err != nil {
		t.Fatal(err)
	}
	raw, err = marshalBody(fields)
	if err != nil || string(raw) != `{"value":{"text":"`+text+`"}}` {
		t.Fatalf("open JSON bytes = %s, %v", raw, err)
	}
}
