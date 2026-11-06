package authgebra

import (
	"bytes"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"reflect"
	"strings"
	"testing"
)

type sharedRequestSemantics struct {
	RequestIDs []struct {
		Value string `json:"value"`
		Valid bool   `json:"valid"`
	} `json:"request_ids"`
	PatchValues   []json.RawMessage `json:"patch_values"`
	IntegerValues []json.RawMessage `json:"integer_values"`
}

func readRequestSemantics(t *testing.T) sharedRequestSemantics {
	t.Helper()
	raw, err := os.ReadFile("../fixtures/request-semantics.json")
	if err != nil {
		t.Fatal(err)
	}
	var values sharedRequestSemantics
	if err = json.Unmarshal(raw, &values); err != nil {
		t.Fatal(err)
	}
	return values
}
func TestOpenJSONValuePreservesAllJSONKinds(t *testing.T) {
	shared := readRequestSemantics(t)
	var provenance struct {
		Exceptions []struct {
			Pointer string `json:"schema_pointer"`
		} `json:"dynamic_exceptions"`
	}
	raw, err := os.ReadFile("models.provenance.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &provenance); err != nil {
		t.Fatal(err)
	}
	vectors := append(append([]json.RawMessage{}, shared.PatchValues...), shared.IntegerValues...)
	for _, exception := range provenance.Exceptions {
		t.Run(exception.Pointer, func(t *testing.T) {
			for _, input := range vectors {
				value, err := ParseJSONValue(input)
				if err != nil {
					t.Fatal(err)
				}
				encoded, err := value.MarshalJSON()
				if err != nil || !bytes.Equal(input, encoded) {
					t.Fatalf("open JSON numeric/shape loss: %s -> %s (%v)", input, encoded, err)
				}
				var decoded JSONValue
				if err = json.Unmarshal(input, &decoded); err != nil {
					t.Fatal(err)
				}
				encoded, err = decoded.MarshalJSON()
				if err != nil || !bytes.Equal(input, encoded) {
					t.Fatalf("roundtrip loss: %s -> %s", input, encoded)
				}
			}
		})
	}
	if _, err := ParseJSONValue([]byte(`{"bad":`)); err == nil {
		t.Fatal("invalid JSON must be rejected")
	}
}
func assertFieldWire[T any](t *testing.T, field Field[T], want string, nullable bool) {
	t.Helper()
	fields := map[string]json.RawMessage{}
	if err := encodeField(fields, "value", field, nullable, false); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(fields)
	if err != nil || string(raw) != want {
		t.Fatalf("got %s (%v), want %s", raw, err, want)
	}
}
func TestFieldsPreserveAbsenceNullAndTypedZeroValues(t *testing.T) {
	assertFieldWire(t, Field[string]{}, `{}`, true)
	assertFieldWire(t, NullField[string](), `{"value":null}`, true)
	assertFieldWire(t, NullField[bool](), `{"value":null}`, true)
	assertFieldWire(t, NullField[int64](), `{"value":null}`, true)
	assertFieldWire(t, NullField[[]string](), `{"value":null}`, true)
	assertFieldWire(t, NullField[AuthzResourceRef](), `{"value":null}`, true)
	assertFieldWire(t, SetField(""), `{"value":""}`, true)
	assertFieldWire(t, SetField(false), `{"value":false}`, true)
	assertFieldWire(t, SetField(int64(0)), `{"value":0}`, true)
	assertFieldWire(t, SetField([]string{}), `{"value":[]}`, true)
	assertFieldWire(t, SetField(AuthzResourceRef{ResourceTypeCode: "doc", ExternalID: "a"}), `{"value":{"external_id":"a","resource_type_code":"doc"}}`, true)
	if err := encodeField(map[string]json.RawMessage{}, "value", NullField[bool](), false, false); err == nil {
		t.Fatal("non-nullable field accepted null")
	}
	request := FactSpecDto{SubjectRolesField: SetField([]string{})}
	raw, err := json.Marshal(request)
	if err != nil || string(raw) != `{"subject_roles":[]}` {
		t.Fatalf("generated model lost explicit empty array: %s, %v", raw, err)
	}
	var decoded FactSpecDto
	if err = json.Unmarshal(raw, &decoded); err != nil {
		t.Fatal(err)
	}
	values, ok := decoded.SubjectRolesField.Value()
	if !ok || values == nil || len(values) != 0 {
		t.Fatal("decoded field lost empty array presence")
	}
}
func TestTypedUnionsRetainVariantsAndRejectAmbiguity(t *testing.T) {
	for _, raw := range []string{`{"kind":"direct"}`, `{"kind":"include","relation":"viewer"}`, `{"kind":"inherit","parent_object_type":"folder","parent_relation":"parent","relation":"viewer"}`} {
		var rule RewriteRuleDto
		if err := json.Unmarshal([]byte(raw), &rule); err != nil {
			t.Fatal(err)
		}
		output, err := json.Marshal(rule)
		if err != nil {
			t.Fatal(err)
		}
		var before, after map[string]string
		if err = json.Unmarshal([]byte(raw), &before); err != nil {
			t.Fatal(err)
		}
		if err = json.Unmarshal(output, &after); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(before, after) {
			t.Fatalf("union changed wire: %s -> %s", raw, output)
		}
	}
	var unknown RewriteRuleDto
	if err := json.Unmarshal([]byte(`{"kind":"unknown"}`), &unknown); err == nil {
		t.Fatal("unknown closed variant accepted")
	}
	if _, err := json.Marshal(RewriteRuleDto{}); err == nil {
		t.Fatal("empty union accepted")
	}
	var direct, include RewriteRuleDto
	if err := json.Unmarshal([]byte(`{"kind":"direct"}`), &direct); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(`{"kind":"include","relation":"viewer"}`), &include); err != nil {
		t.Fatal(err)
	}
	direct.Include = include.Include
	if _, err := json.Marshal(direct); err == nil {
		t.Fatal("ambiguous union accepted")
	}
}
func TestGeneratedFieldsNeverExposeDynamicKnownTypes(t *testing.T) {
	file, err := parser.ParseFile(token.NewFileSet(), "models_gen.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	for _, declaration := range file.Decls {
		group, ok := declaration.(*ast.GenDecl)
		if !ok {
			continue
		}
		for _, declaration := range group.Specs {
			spec, ok := declaration.(*ast.TypeSpec)
			if !ok {
				continue
			}
			model, ok := spec.Type.(*ast.StructType)
			if !ok {
				continue
			}
			for _, field := range model.Fields.List {
				if field.Tag == nil || !strings.Contains(field.Tag.Value, "json:") {
					continue
				}
				ast.Inspect(field.Type, func(node ast.Node) bool {
					forbidden := false
					switch value := node.(type) {
					case *ast.InterfaceType:
						forbidden = true
					case *ast.Ident:
						forbidden = value.Name == "any"
					case *ast.SelectorExpr:
						if owner, ok := value.X.(*ast.Ident); ok {
							forbidden = owner.Name == "json" && value.Sel.Name == "RawMessage"
						}
					}
					if forbidden {
						t.Errorf("public model %s loses known field type", spec.Name.Name)
					}
					return true
				})
			}
		}
	}
}

func TestUnsignedTypedValuePreservesMaximum(t *testing.T) {
	input := []byte(`{"type":"u64","value":18446744073709551615}`)
	var value TypedValueDto
	if err := json.Unmarshal(input, &value); err != nil {
		t.Fatal(err)
	}
	if value.U64 == nil || value.U64.Value != ^uint64(0) {
		t.Fatal("unsigned 64-bit maximum lost")
	}
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(raw, []byte("18446744073709551615")) {
		t.Fatalf("unsigned integer changed: %s", raw)
	}
}
func TestDeclaredRequestConstraints(t *testing.T) {
	for _, limit := range []int32{0, 101} {
		if err := (ListResourcesRequest{Limit: &limit}).Validate(); err == nil {
			t.Fatal("out-of-bound list limit accepted")
		}
	}
	for _, limit := range []int32{1, 100} {
		if err := (ListResourcesRequest{Limit: &limit}).Validate(); err != nil {
			t.Fatal(err)
		}
	}
	if err := (BatchCheckRequest{}).Validate(); err == nil {
		t.Fatal("empty batch accepted")
	}
	for _, count := range []int{1, 10000} {
		if err := (BatchCheckRequest{Resources: make([]AuthzResourceRef, count)}).Validate(); err != nil {
			t.Fatal(err)
		}
	}
	if err := (BatchCheckRequest{Resources: make([]AuthzResourceRef, 10001)}).Validate(); err == nil {
		t.Fatal("oversize batch accepted")
	}
}

func TestPatchTypedStateRetainsLegacyCompatibility(t *testing.T) {
	for _, request := range []PatchUserRequest{{EmailField: NullField[string]()}, {ClearEmail: true}} {
		raw, err := json.Marshal(request)
		if err != nil || string(raw) != `{"email":null}` {
			t.Fatalf("clear state lost: %s %v", raw, err)
		}
	}
	value := ""
	raw, err := json.Marshal(PatchUserRequest{EmailField: SetField(value)})
	if err != nil || string(raw) != `{"email":""}` {
		t.Fatalf("typed zero lost: %s %v", raw, err)
	}
	for _, request := range []PatchUserRequest{{Email: &value, EmailField: SetField(value)}, {EmailField: NullField[string](), ClearEmail: true}} {
		if _, err = json.Marshal(request); err == nil {
			t.Fatal("conflicting field state accepted")
		}
	}
}

func TestDecodedFieldsFollowLegacyEditsWithoutStaleValues(t *testing.T) {
	var request FactSpecDto
	if err := json.Unmarshal([]byte(`{"subject_roles":["old"]}`), &request); err != nil {
		t.Fatal(err)
	}
	request.SubjectRoles = []string{}
	raw, err := json.Marshal(request)
	if err != nil || string(raw) != `{"subject_roles":[]}` {
		t.Fatalf("legacy edit restored a stale array: %s %v", raw, err)
	}
	request.SubjectRoles = nil
	raw, err = json.Marshal(request)
	if err != nil || string(raw) != `{}` {
		t.Fatalf("legacy omission restored stale data: %s %v", raw, err)
	}
	var patch PatchUserRequest
	if err = json.Unmarshal([]byte(`{"email":"old@example.test"}`), &patch); err != nil {
		t.Fatal(err)
	}
	patch.Email = nil
	raw, err = json.Marshal(patch)
	if err != nil || string(raw) != `{}` {
		t.Fatalf("legacy nil restored a stale value: %s %v", raw, err)
	}
}

func TestUnsignedPolicyVersionPreservesMaximum(t *testing.T) {
	input := []byte(`{"code":"policy","version":4294967295}`)
	var reference AuthzPolicyRef
	if err := json.Unmarshal(input, &reference); err != nil {
		t.Fatal(err)
	}
	if reference.Version != ^uint32(0) {
		t.Fatal("uint32 maximum lost")
	}
	if err := reference.Validate(); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(`{"code":"policy","version":4294967296}`), &reference); err == nil {
		t.Fatal("uint32 overflow accepted")
	}
}

func TestRequestJSONIsStrictWithoutBreakingSharedResponseModels(t *testing.T) {
	for _, input := range []string{
		`{"subject_id":"` + testSubject + `","permission":"doc:read","unknown":true}`,
		`{"subject_id":"` + testSubject + `","permission":"doc:read","resource":{"resource_type_code":"doc","external_id":"a","unknown":true}}`,
		`{"permission":"doc:read"}`,
		`{"subject_id":null,"permission":"doc:read"}`,
	} {
		var request CheckRequest
		if err := json.Unmarshal([]byte(input), &request); err == nil {
			t.Fatalf("invalid request accepted: %s", input)
		}
	}
	var request CheckRequest
	if err := json.Unmarshal([]byte(`{"subject_id":"`+testSubject+`","permission":"doc:read","resource":null}`), &request); err != nil {
		t.Fatal(err)
	}
	var response AuthzResourceRef
	if err := json.Unmarshal([]byte(`{"resource_type_code":"doc","external_id":"a","future_field":true}`), &response); err != nil {
		t.Fatal("shared response must accept new fields", err)
	}
}
