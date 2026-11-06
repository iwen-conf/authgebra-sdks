// Command gen generates models_gen.go and ops_gen.go from the reviewed OpenAPI
// export (sdks/contracts/openapi.json). Run: go run ./internal/gen -spec ../contracts/openapi.json -out .
//
// It is deterministic (sorted keys, no timestamps) and fails loudly on shapes it
// does not understand instead of guessing.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"go/format"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

type obj = map[string]any

var initialisms = map[string]string{"id": "ID", "url": "URL", "uuid": "UUID", "ip": "IP", "api": "API",
	"mfa": "MFA", "totp": "TOTP", "http": "HTTP", "json": "JSON", "abac": "ABAC", "rbac": "RBAC",
	"rfc3339": "RFC3339", "sso": "SSO", "ui": "UI", "db": "DB", "sql": "SQL", "uri": "URI"}

// Hand-written types that generated code must not redefine.
var handTypes = map[string]bool{"CommitState": true}

// POST operations that do not mutate state: they are treated as reads (safe to
// retry, no commit_state on transport failure).
var readOnlyPost = map[string]bool{"check": true, "batch_check": true, "explain": true, "list_authorized_resources": true}

func fatalf(f string, a ...any) { fmt.Fprintf(os.Stderr, "gen: "+f+"\n", a...); os.Exit(1) }

func words(s string) []string {
	return regexp.MustCompile(`[A-Za-z0-9]+`).FindAllString(s, -1)
}

func pascal(s string) string {
	if !strings.ContainsAny(s, "_- ") && s != "" && s[0] >= 'A' && s[0] <= 'Z' {
		return s
	}
	var b strings.Builder
	for _, w := range words(s) {
		lw := strings.ToLower(w)
		if up, ok := initialisms[lw]; ok {
			b.WriteString(up)
			continue
		}
		b.WriteString(strings.ToUpper(w[:1]) + w[1:])
	}
	return b.String()
}

func str(m obj, k string) string { s, _ := m[k].(string); return s }
func sub(m obj, k string) obj    { o, _ := m[k].(obj); return o }

func comment(prefix, desc string, indent string) string {
	d := strings.TrimSpace(strings.SplitN(desc, "\n\n", 2)[0])
	d = strings.Join(strings.Fields(d), " ")
	d = strings.ReplaceAll(d, "*/", "* /")
	if d == "" {
		return ""
	}
	r := []rune(d)
	if len(r) > 180 {
		d = string(r[:180]) + "…"
	}
	return indent + "// " + prefix + d + "\n"
}

type typeInfo struct {
	goType   string
	nullable bool
	kind     string // scalar | struct | slice | map | raw
}

type gen struct {
	schemas    obj
	normalized bool
	origins    map[string]string
	openFields map[string]string
	aliases    map[string]string
}

func refName(r string) string {
	name := r[strings.LastIndex(r, "/")+1:]
	return strings.ReplaceAll(strings.ReplaceAll(name, "~1", "/"), "~0", "~")
}

func (g *gen) typeOf(s obj) typeInfo {
	return g.resolve(s)
}

func sortedKeys(m obj) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}

func (g *gen) models() string {
	g.prepare()
	return g.generateModels()
}

func asSlice(v any) []any { s, _ := v.([]any); return s }

type param struct {
	name, in, goName, goType, kind string
	required                       bool
}

type operation struct {
	id, method, path, tag, summary string
	pathParams                     []param
	params                         []param
	bodyType                       string
	bodyRequired                   bool
	respType                       string
	env                            string
	mutating                       bool
}

func (g *gen) dataType(wrapper string) (string, string) {
	ref := refName(wrapper)
	for _, pre := range []string{"PagedResponse_", "ApiResponse_"} {
		if !strings.HasPrefix(ref, pre) {
			continue
		}
		x := strings.TrimPrefix(ref, pre)
		env := "envData"
		if pre == "PagedResponse_" {
			env = "envPaged"
		}
		var t string
		switch {
		case x == "Value":
			t = "JSONValue"
		case strings.HasPrefix(x, "Vec_"):
			if sub(g.schemas, strings.TrimPrefix(x, "Vec_")) == nil {
				fatalf("unknown element schema for %s", ref)
			}
			t = "[]" + pascal(strings.TrimPrefix(x, "Vec_"))
		default:
			if sub(g.schemas, x) == nil {
				fatalf("unknown data schema for %s", ref)
			}
			t = pascal(x)
		}
		if env == "envPaged" {
			if t == "JSONValue" {
				t = "[]JSONValue"
			} else {
				t = "[]" + t
			}
		}
		if strings.HasPrefix(t, "[]") {
			name := pascal(ref) + "Data"
			data := sub(sub(sub(g.schemas, ref), "properties"), "data")
			if data == nil {
				fatalf("array response %s has no data schema", ref)
			}
			alias := clone(data)
			alias["x-go-alias"] = true
			if _, exists := g.schemas[name]; !exists {
				g.schemas[name] = alias
				g.origins[name] = g.origins[ref] + "/properties/data"
			}
			t = name
		}
		return t, env
	}
	if sub(g.schemas, ref) == nil {
		fatalf("unknown response schema %s", ref)
	}
	return pascal(ref), "envBare"
}

func (g *gen) operations(paths obj) []operation {
	g.prepare()
	var ops []operation
	for _, p := range sortedKeys(paths) {
		item := paths[p].(obj)
		for _, m := range sortedKeys(item) {
			o := item[m].(obj)
			op := operation{id: str(o, "operationId"), method: strings.ToUpper(m), path: p, summary: str(o, "summary")}
			if tags := asSlice(o["tags"]); len(tags) > 0 {
				op.tag = tags[0].(string)
			} else {
				fatalf("%s has no tag", op.id)
			}
			op.mutating = op.method != "GET" && op.method != "HEAD" && !readOnlyPost[op.id]
			for _, pr := range asSlice(o["parameters"]) {
				pm := pr.(obj)
				ti := g.typeOf(sub(pm, "schema"))
				if ti.kind != "scalar" {
					fatalf("%s: unsupported parameter %v", op.id, pm["name"])
				}
				pp := param{kind: schemaType(sub(pm, "schema")), name: str(pm, "name"), in: str(pm, "in"), goName: pascal(str(pm, "name")), goType: ti.goType, required: pm["required"] == true}
				if pp.in == "path" {
					if ti.goType != "string" {
						fatalf("%s: non-string path parameter", op.id)
					}
					op.pathParams = append(op.pathParams, pp)
				} else {
					op.params = append(op.params, pp)
				}
			}
			// path params in template order
			order := regexp.MustCompile(`\{([^}]+)\}`).FindAllStringSubmatch(p, -1)
			var ordered []param
			for _, o2 := range order {
				for _, pp := range op.pathParams {
					if pp.name == o2[1] {
						ordered = append(ordered, pp)
					}
				}
			}
			if len(ordered) != len(op.pathParams) {
				fatalf("%s: path parameter mismatch", op.id)
			}
			op.pathParams = ordered
			if rb := sub(o, "requestBody"); rb != nil {
				sc := sub(sub(sub(rb, "content"), "application/json"), "schema")
				r := str(sc, "$ref")
				if r == "" {
					fatalf("%s: inline request body", op.id)
				}
				op.bodyType, op.bodyRequired = pascal(refName(r)), rb["required"] == true
			}
			var succ []string
			for st := range sub(o, "responses") {
				if strings.HasPrefix(st, "2") {
					succ = append(succ, st)
				}
			}
			sort.Strings(succ)
			if len(succ) == 0 {
				fatalf("%s: no 2xx response", op.id)
			}
			sc := sub(sub(sub(sub(sub(o, "responses"), succ[0]), "content"), "application/json"), "schema")
			if str(sc, "$ref") == "" {
				fatalf("%s: unsupported success schema", op.id)
			}
			op.respType, op.env = g.dataType(str(sc, "$ref"))
			ops = append(ops, op)
		}
	}
	sort.Slice(ops, func(i, j int) bool { return ops[i].id < ops[j].id })
	return ops
}

func (g *gen) ops(ops []operation, digest string) string {
	var b bytes.Buffer
	b.WriteString("// Code generated by internal/gen from sdks/contracts/openapi.json; DO NOT EDIT.\n\npackage authgebra\n\nimport (\n\t\"context\"\n\t\"net/http\"\n\t\"net/url\"\n\t\"strconv\"\n)\n\n")
	b.WriteString("var (\n\t_ = http.Header(nil)\n\t_ = url.Values(nil)\n\t_ = strconv.Itoa\n)\n\n")
	fmt.Fprintf(&b, "// OpenAPISHA256 is the digest of the OpenAPI export this code was generated from.\nconst OpenAPISHA256 = %q\n\n", digest)

	tags := map[string]bool{}
	for _, op := range ops {
		tags[op.tag] = true
	}
	tagNames := make([]string, 0)
	for t := range tags {
		tagNames = append(tagNames, t)
	}
	sort.Strings(tagNames)

	for _, t := range tagNames {
		fmt.Fprintf(&b, "// %sAPI groups the %q operations.\ntype %sAPI struct{ c *Client }\n\n", pascal(t), t, pascal(t))
	}
	b.WriteString("// AdminClient groups every management operation by area.\ntype AdminClient struct {\n")
	for _, t := range tagNames {
		if t != "authz" {
			fmt.Fprintf(&b, "\t%s *%sAPI\n", pascal(t), pascal(t))
		}
	}
	b.WriteString("}\n\nfunc newAdminClient(c *Client) *AdminClient {\n\treturn &AdminClient{\n")
	for _, t := range tagNames {
		if t != "authz" {
			fmt.Fprintf(&b, "\t\t%s: &%sAPI{c: c},\n", pascal(t), pascal(t))
		}
	}
	b.WriteString("\t}\n}\n\n")

	b.WriteString("// OperationMethods maps every OpenAPI operationId to its SDK method (Group.Method).\nvar OperationMethods = map[string]string{\n")
	for _, op := range ops {
		grp := pascal(op.tag)
		fmt.Fprintf(&b, "\t%q: %q,\n", op.id, grp+"."+pascal(op.id))
	}
	b.WriteString("}\n\n")

	for _, op := range ops {
		name, grp := pascal(op.id), pascal(op.tag)
		kind := map[string]string{"envData": "envData", "envPaged": "envPaged", "envBare": "envBare"}[op.env]
		fmt.Fprintf(&b, "var op%s = opSpec{ID: %q, Method: %q, Path: %q, Mutating: %v, Env: %s}\n\n", name, op.id, op.method, op.path, op.mutating, kind)
		if len(op.params) > 0 {
			fmt.Fprintf(&b, "// %sParams are the query/header parameters of %s.\ntype %sParams struct {\n", name, op.id, name)
			for _, p := range op.params {
				t := p.goType
				if !p.required && t != "string" {
					t = "*" + t
				}
				fmt.Fprintf(&b, "\t// %s %s parameter %q.\n\t%s %s\n", p.goName, p.in, p.name, p.goName, t)
			}
			b.WriteString("}\n\n")
			fmt.Fprintf(&b, "func (p %sParams) apply(a *callArgs) {\n", name)
			for _, p := range op.params {
				target := "a.query.Set"
				if p.in == "header" {
					target = "a.header.Set"
				}
				var val, cond string
				switch {
				case p.kind == "string":
					cond, val = fmt.Sprintf("p.%s != \"\"", p.goName), "string(p."+p.goName+")"
				case p.goType == "bool":
					if p.required {
						cond, val = "true", fmt.Sprintf("strconv.FormatBool(p.%s)", p.goName)
					} else {
						cond, val = fmt.Sprintf("p.%s != nil", p.goName), fmt.Sprintf("strconv.FormatBool(*p.%s)", p.goName)
					}
				default:
					if p.required {
						cond, val = "true", fmt.Sprintf("strconv.FormatInt(int64(p.%s), 10)", p.goName)
					} else {
						cond, val = fmt.Sprintf("p.%s != nil", p.goName), fmt.Sprintf("strconv.FormatInt(int64(*p.%s), 10)", p.goName)
					}
				}
				fmt.Fprintf(&b, "\tif %s {\n\t\t%s(%q, %s)\n\t}\n", cond, target, p.name, val)
			}
			b.WriteString("}\n\n")
		}
		// signature
		sig := []string{"ctx context.Context"}
		for _, p := range op.pathParams {
			sig = append(sig, lowerFirst(pascal(p.name))+" string")
		}
		if len(op.params) > 0 {
			sig = append(sig, "params "+name+"Params")
		}
		if op.bodyType != "" {
			if op.bodyRequired {
				sig = append(sig, "body "+op.bodyType)
			} else {
				sig = append(sig, "body *"+op.bodyType)
			}
		}
		sig = append(sig, "opts ...CallOption")
		b.WriteString(comment(name+" — ", op.summary, ""))
		fmt.Fprintf(&b, "// %s %s (operationId %s).\n", op.method, op.path, op.id)
		fmt.Fprintf(&b, "// %sResult preserves the concrete payload and the existing Result API.\ntype %sResult = Result[%s]\n\n", name, name, op.respType)
		fmt.Fprintf(&b, "func (g *%sAPI) %s(%s) (*%sResult, error) {\n", grp, name, strings.Join(sig, ", "), name)
		b.WriteString("\ta := callArgs{query: url.Values{}, header: http.Header{}")
		if len(op.pathParams) > 0 {
			b.WriteString(", path: map[string]string{")
			for i, p := range op.pathParams {
				if i > 0 {
					b.WriteString(", ")
				}
				fmt.Fprintf(&b, "%q: %s", p.name, lowerFirst(pascal(p.name)))
			}
			b.WriteString("}")
		}
		b.WriteString("}\n")
		if op.bodyType != "" {
			if op.bodyRequired {
				b.WriteString("\ta.body = body\n")
			} else {
				b.WriteString("\tif body != nil {\n\t\ta.body = body\n\t}\n")
			}
		}
		if len(op.params) > 0 {
			b.WriteString("\tparams.apply(&a)\n")
		}
		fmt.Fprintf(&b, "\treturn call[%s](ctx, g.c, op%s, a, opts)\n}\n\n", op.respType, name)
	}
	return b.String()
}

func lowerFirst(s string) string {
	if s == "" {
		return s
	}
	if strings.ToUpper(s) == s { // ID → id
		return strings.ToLower(s)
	}
	return strings.ToLower(s[:1]) + s[1:]
}

func write(dir, name, src string, check bool) {
	out, err := format.Source([]byte(src))
	if err != nil {
		fatalf("gofmt %s: %v", name, err)
	}
	writeBytes(dir, name, out, check)
}
func compareArtifact(dir, name string, out []byte) error {
	temporary, err := os.MkdirTemp("", "authgebra-go-generated-")
	if err != nil {
		return fmt.Errorf("create generated candidate directory: %w", err)
	}
	defer os.RemoveAll(temporary)
	candidate := filepath.Join(temporary, name)
	if err = os.WriteFile(candidate, out, 0600); err != nil {
		return fmt.Errorf("write generated candidate: %w", err)
	}
	regenerated, err := os.ReadFile(candidate)
	if err != nil {
		return fmt.Errorf("read generated candidate: %w", err)
	}
	existing, err := os.ReadFile(filepath.Join(dir, name))
	if err != nil || !bytes.Equal(existing, regenerated) {
		return fmt.Errorf("generated artifact differs: %s", name)
	}
	return nil
}
func writeBytes(dir, name string, out []byte, check bool) {
	if check {
		if err := compareArtifact(dir, name, out); err != nil {
			fatalf("%v", err)
		}
		return
	}
	if err := os.WriteFile(filepath.Join(dir, name), out, 0644); err != nil {
		fatalf("%v", err)
	}
}

func main() {
	specPath := flag.String("spec", "../contracts/openapi.json", "OpenAPI JSON")
	outDir := flag.String("out", ".", "output directory")
	check := flag.Bool("check", false, "compare regenerated artifacts without writing")
	flag.Parse()
	raw, err := os.ReadFile(*specPath)
	if err != nil {
		fatalf("%v", err)
	}
	sum := sha256.Sum256(raw)
	var doc obj
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&doc); err != nil {
		fatalf("parse spec: %v", err)
	}
	g := &gen{schemas: sub(sub(doc, "components"), "schemas")}
	ops := g.operations(sub(doc, "paths"))
	write(*outDir, "models_gen.go", g.models(), *check)
	write(*outDir, "ops_gen.go", g.ops(ops, hex.EncodeToString(sum[:])), *check)
	writeBytes(*outDir, "models.provenance.json", g.provenance(), *check)
	write(*outDir, "models_serialization_gen_test.go", g.serializationTests(), *check)
	fmt.Printf("generated %d operations, %d schemas\n", len(ops), len(g.schemas))
}
