package main

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

func reviewedGenerator(t *testing.T) *gen {
	t.Helper()
	raw, err := os.ReadFile("../../../contracts/openapi.json")
	if err != nil {
		t.Fatal(err)
	}
	var doc obj
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	return &gen{schemas: sub(sub(doc, "components"), "schemas")}
}

func TestKnownModelsNeverLoseSchemaTypes(t *testing.T) {
	source := reviewedGenerator(t).models()
	if regexp.MustCompile(`(?m)^\s*(?:type [A-Z]\w*|[A-Z]\w+\s+[^\n]*)json\.RawMessage`).MatchString(source) {
		t.Fatal("known schemas must not silently become json.RawMessage")
	}
	for _, expected := range []string{"type RewriteRuleDto struct", "type TypedValueDto struct", "#/components/schemas/CheckRequest/properties/subject_id"} {
		if !strings.Contains(source, expected) {
			t.Fatalf("missing typed model or source pointer %s", expected)
		}
	}
}

func TestPatchCapabilityUsesMachineMetadata(t *testing.T) {
	g := reviewedGenerator(t)
	field := sub(sub(sub(g.schemas, "PatchUserRequest"), "properties"), "email")
	if field["x-authgebra-clearable"] != true {
		t.Fatal("reviewed clearable contract is missing its machine marker")
	}
	field["description"] = "Replacement words do not control clearability."
	source := g.models()
	if !strings.Contains(source, "ClearEmail bool") {
		t.Fatal("description edit removed PATCH capability")
	}
	g = reviewedGenerator(t)
	field = sub(sub(sub(g.schemas, "PatchUserRequest"), "properties"), "email")
	delete(field, "x-authgebra-clearable")
	source = g.models()
	if strings.Contains(source, "ClearEmail bool") {
		t.Fatal("removing the machine marker did not change the generated capability")
	}
}

func TestGeneratorCheckDetectsEveryArtifactMutation(t *testing.T) {
	dir := t.TempDir()
	run := func(args ...string) ([]byte, error) {
		t.Helper()
		return exec.Command("go", append([]string{"run", ".", "-spec", "../../../contracts/openapi.json", "-out", dir}, args...)...).CombinedOutput()
	}
	if out, err := run(); err != nil {
		t.Fatalf("generate fixture: %s %v", out, err)
	}
	if out, err := run("--check"); err != nil {
		t.Fatalf("baseline check: %s %v", out, err)
	}
	models := filepath.Join(dir, "models_gen.go")
	original, err := os.ReadFile(models)
	if err != nil {
		t.Fatal(err)
	}
	mutations := []struct{ name, from, to string }{
		{"delete-field", "SubjectID string `json:\"subject_id\"`", ""},
		{"json-tag", "json:\"subject_id\"", "json:\"subject_typo\""},
		{"nullable", "Resource *AuthzResourceRef", "Resource AuthzResourceRef"},
		{"known-object-downgrade", "Resource *AuthzResourceRef", "Resource *JSONValue"},
		{"enum", `= "direct"`, `= "wrong"`},
	}
	for _, mutation := range mutations {
		t.Run(mutation.name, func(t *testing.T) {
			changed := bytes.Replace(original, []byte(mutation.from), []byte(mutation.to), 1)
			if bytes.Equal(changed, original) {
				t.Fatalf("mutation did not target actual code: %s", mutation.from)
			}
			if err := os.WriteFile(models, changed, 0600); err != nil {
				t.Fatal(err)
			}
			if out, err := run("--check"); err == nil {
				t.Fatalf("mutation passed generated check: %s", out)
			}
			after, err := os.ReadFile(models)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(after, changed) {
				t.Fatal("--check rewrote the malformed artifact")
			}
			if err := os.WriteFile(models, original, 0600); err != nil {
				t.Fatal(err)
			}
		})
	}
	operations := filepath.Join(dir, "ops_gen.go")
	opOriginal, err := os.ReadFile(operations)
	if err != nil {
		t.Fatal(err)
	}
	changed := bytes.Replace(opOriginal, []byte("body CreateUserRequest"), []byte("body CreateRoleRequest"), 1)
	if bytes.Equal(changed, opOriginal) {
		t.Fatal("operation-type mutation did not target actual code")
	}
	if err := os.WriteFile(operations, changed, 0600); err != nil {
		t.Fatal(err)
	}
	if out, err := run("--check"); err == nil {
		t.Fatalf("operation mutation passed: %s", out)
	}
	if err := os.WriteFile(operations, opOriginal, 0600); err != nil {
		t.Fatal(err)
	}
	if out, err := run("--check"); err != nil {
		t.Fatalf("restored baseline failed: %s %v", out, err)
	}
}

func TestGeneratorRejectsUnresolvedReferences(t *testing.T) {
	raw, err := os.ReadFile("../../../contracts/openapi.json")
	if err != nil {
		t.Fatal(err)
	}
	var doc obj
	if err = json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	schemas := sub(sub(doc, "components"), "schemas")
	sub(sub(schemas, "CheckRequest"), "properties")["resource"] = obj{"$ref": "#/components/schemas/DoesNotExist"}
	raw, err = json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "bad.json")
	if err = os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command("go", "run", ".", "-spec", path, "-out", dir).CombinedOutput()
	if err == nil || !bytes.Contains(out, []byte("dangling $ref")) {
		t.Fatalf("unresolved reference not rejected: %s %v", out, err)
	}
}

func TestClearableMarkerContradictionsFail(t *testing.T) {
	raw, err := os.ReadFile("../../../contracts/openapi.json")
	if err != nil {
		t.Fatal(err)
	}
	var doc obj
	if err = json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	field := sub(sub(sub(sub(sub(doc, "components"), "schemas"), "PatchUserRequest"), "properties"), "email")
	field["type"] = "string"
	raw, err = json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "bad.json")
	if err = os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command("go", "run", ".", "-spec", path, "-out", dir).CombinedOutput()
	if err == nil || !bytes.Contains(out, []byte("contradictory clearable")) {
		t.Fatalf("clearable non-nullable contract accepted: %s %v", out, err)
	}
}

func TestOpenSchemaNullabilityIsPreserved(t *testing.T) {
	if !nullable(obj{}) || !nullable(obj{"description": "Any JSON"}) {
		t.Fatal("unconstrained JSON schema accepts null")
	}
	if nullable(obj{"type": "object"}) || nullable(obj{"type": "string"}) {
		t.Fatal("non-nullable known types acquired null")
	}
}

func TestUnsupportedShapesFailInsteadOfBecomingOpenJSON(t *testing.T) {
	for name, field := range map[string]obj{
		"conditional":            {"if": obj{"type": "string"}, "then": obj{"minLength": 1}},
		"negation":               {"not": obj{"type": "string"}},
		"unknown-integer-format": {"type": "integer", "format": "unknown_integer"},
		"missing-array-items":    {"type": "array"},
		"primitive-union":        {"type": []any{"string", "integer"}},
	} {
		t.Run(name, func(t *testing.T) {
			raw, err := os.ReadFile("../../../contracts/openapi.json")
			if err != nil {
				t.Fatal(err)
			}
			var doc obj
			if err = json.Unmarshal(raw, &doc); err != nil {
				t.Fatal(err)
			}
			sub(sub(sub(sub(doc, "components"), "schemas"), "CheckRequest"), "properties")["resource"] = field
			raw, err = json.Marshal(doc)
			if err != nil {
				t.Fatal(err)
			}
			dir := t.TempDir()
			path := filepath.Join(dir, "bad.json")
			if err = os.WriteFile(path, raw, 0600); err != nil {
				t.Fatal(err)
			}
			out, err := exec.Command("go", "run", ".", "-spec", path, "-out", dir).CombinedOutput()
			if err == nil {
				t.Fatalf("unsupported shape silently generated: %s", out)
			}
		})
	}
}
