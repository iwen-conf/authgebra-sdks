package authgebra

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func readSpec(t *testing.T) (raw []byte, doc map[string]any) {
	t.Helper()
	raw, err := os.ReadFile("../contracts/openapi.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	return raw, doc
}

// Every OpenAPI operationId must have a real SDK method (no fake methods either).
func TestEveryOperationHasAMethod(t *testing.T) {
	_, doc := readSpec(t)
	c, _, _ := newTestClient(t, respond(200, nil, "{}"), nil)
	ids := map[string]bool{}
	for _, item := range doc["paths"].(map[string]any) {
		for _, op := range item.(map[string]any) {
			ids[op.(map[string]any)["operationId"].(string)] = true
		}
	}

	for id := range ids {
		target, ok := OperationMethods[id]
		if !ok {
			t.Errorf("operationId %q has no SDK method", id)
			continue
		}
		group, method, _ := strings.Cut(target, ".")
		var holder reflect.Value
		if group == "Authz" {
			holder = reflect.ValueOf(c.Authz)
		} else {
			f := reflect.ValueOf(c.Admin).Elem().FieldByName(group)
			if !f.IsValid() {
				t.Errorf("%s: Admin has no group %s", id, group)
				continue
			}
			holder = f
		}
		if !holder.MethodByName(method).IsValid() {
			t.Errorf("%s: %s.%s does not exist", id, group, method)
		}
	}
	for id := range OperationMethods {
		if !ids[id] {
			t.Errorf("SDK exposes %q which is not in the OpenAPI (fake method)", id)
		}
	}
}

// Generated code must come from exactly the reviewed OpenAPI export.
func TestGeneratedCodeMatchesReviewedContract(t *testing.T) {
	raw, _ := readSpec(t)
	sum := sha256.Sum256(raw)
	got := hex.EncodeToString(sum[:])
	if got != OpenAPISHA256 {
		t.Fatalf("generated code is stale: spec sha256 %s, generated from %s — run go generate", got, OpenAPISHA256)
	}
	pinned, err := os.ReadFile("../contracts/openapi.sha256")
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(string(pinned)) != got {
		t.Fatalf("../contracts/openapi.sha256 (%s) does not match openapi.json (%s)", strings.TrimSpace(string(pinned)), got)
	}
}

// The hand-written code list must equal the server enum.
func TestKnownCodesMatchOpenAPIEnum(t *testing.T) {
	_, doc := readSpec(t)
	enum := doc["components"].(map[string]any)["schemas"].(map[string]any)["ApiErrorCode"].(map[string]any)["enum"].([]any)
	want := map[string]bool{}
	for _, e := range enum {
		want[e.(string)] = true
	}
	if len(KnownCodes) != len(want) {
		t.Errorf("KnownCodes has %d entries, OpenAPI has %d", len(KnownCodes), len(want))
	}
	for _, k := range KnownCodes {
		if !want[string(k)] {
			t.Errorf("%s is not in the OpenAPI ApiErrorCode enum", k)
		}
	}
}

// Meta must keep 64-bit precision.
func TestMetaPrecisionIsLossless(t *testing.T) {
	var m Meta
	if err := json.Unmarshal([]byte(`{"authz_revision":9223372036854775807,"snapshot_generation":9007199254740993}`), &m); err != nil {
		t.Fatal(err)
	}
	if m.AuthzRevision != 9223372036854775807 || *m.SnapshotGeneration != 9007199254740993 {
		t.Fatalf("precision lost: %+v", m)
	}
	var neg Meta
	if err := json.Unmarshal([]byte(`{"authz_revision":-9223372036854775808}`), &neg); err != nil || neg.AuthzRevision != -9223372036854775808 {
		t.Fatalf("min int64 lost: %v %+v", err, neg)
	}
}
