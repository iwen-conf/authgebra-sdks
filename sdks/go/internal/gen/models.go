package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"
)

func pointerToken(s string) string {
	return strings.ReplaceAll(strings.ReplaceAll(s, "~", "~0"), "/", "~1")
}
func schemaType(s obj) string {
	if t, ok := s["type"].(string); ok {
		return t
	}
	for _, t := range asSlice(s["type"]) {
		if t != "null" {
			return t.(string)
		}
	}
	return ""
}
func nullable(s obj) bool {
	if schemaType(s) == "" && s["$ref"] == nil && s["enum"] == nil && s["properties"] == nil && s["oneOf"] == nil && s["anyOf"] == nil && s["allOf"] == nil {
		return true
	}
	if s["x-nullable"] == true {
		return true
	}
	for _, t := range asSlice(s["type"]) {
		if t == "null" {
			return true
		}
	}
	for _, k := range []string{"oneOf", "anyOf"} {
		for _, a := range asSlice(s[k]) {
			if schemaType(a.(obj)) == "null" {
				return true
			}
		}
	}
	return false
}
func shape(s obj) string {
	b, err := json.Marshal(s)
	if err != nil {
		fatalf("schema shape: %v", err)
	}
	return string(b)
}

func schemaReference(name string, s obj) obj {
	r := obj{"$ref": "#/components/schemas/" + name, "x-nullable": nullable(s)}
	for _, key := range []string{"description", "default", "readOnly", "writeOnly", "deprecated", "x-authgebra-clearable"} {
		if value, ok := s[key]; ok {
			r[key] = value
		}
	}
	return r
}
func clone(s obj) obj {
	c := obj{}
	for k, v := range s {
		c[k] = v
	}
	return c
}

func (g *gen) prepare() {
	if g.normalized {
		return
	}
	g.normalized = true
	original := g.schemas
	g.schemas = obj{}
	g.origins = map[string]string{}
	g.openFields = map[string]string{}
	g.aliases = map[string]string{}
	// Only identical contracts are reused. Similar names or field lists are not evidence of identity.
	for _, n := range sortedKeys(original) {
		key := shape(original[n].(obj))
		if _, seen := g.aliases[key]; seen {
			g.aliases[key] = ""
		} else {
			g.aliases[key] = n
		}
	}
	for _, n := range sortedKeys(original) {
		p := "#/components/schemas/" + pointerToken(n)
		g.origins[n] = p
		g.schemas[n] = g.normalize(original[n].(obj), n, p, true)
	}
}
func (g *gen) normalize(s obj, hint, pointer string, root bool) obj {
	for _, key := range []string{"not", "if", "then", "else", "dependentSchemas", "dependentRequired", "patternProperties", "unevaluatedProperties", "contains", "minContains", "maxContains", "prefixItems"} {
		if _, present := s[key]; present {
			fatalf("unsupported structural schema keyword %s at %s", key, pointer)
		}
	}
	if types, ok := s["type"].([]any); ok {
		count := 0
		for _, value := range types {
			if value != "null" {
				count++
			}
		}
		if count > 1 {
			fatalf("unsupported primitive type union at %s", pointer)
		}
	}
	if names := sub(s, "propertyNames"); names != nil && shape(names) != shape(obj{"type": "string"}) {
		fatalf("unsupported propertyNames constraint at %s", pointer)
	}
	r := clone(s)
	if ref := str(s, "$ref"); ref != "" {
		return r
	}
	structured := s["properties"] != nil || s["enum"] != nil || len(asSlice(s["oneOf"])) > 1 || len(asSlice(s["anyOf"])) > 1 || (schemaType(s) == "object" && s["additionalProperties"] != nil)
	// Nullable wrappers retain their shape; only real multi-arm unions get a distinct identity.
	for _, k := range []string{"oneOf", "anyOf"} {
		if arms := asSlice(s[k]); len(arms) > 0 {
			count := 0
			for _, a := range arms {
				if schemaType(a.(obj)) != "null" {
					count++
				}
			}
			if count == 1 {
				structured = false
			}
		}
	}
	if structured && !root {
		if n, ok := g.aliases[shape(s)]; ok && n != "" {
			return schemaReference(n, s)
		}
		n := pascal(hint)
		nonnull := clone(s)
		if types, ok := s["type"].([]any); ok {
			for _, v := range types {
				if v != "null" {
					nonnull["type"] = v
				}
			}
		}
		if old, ok := g.schemas[n]; ok && shape(old.(obj)) != shape(nonnull) {
			fatalf("inline model collision: %s at %s", n, pointer)
		}
		if _, ok := g.origins[n]; !ok {
			g.origins[n] = pointer
			g.schemas[n] = g.normalize(nonnull, n, pointer, true)
		}
		return schemaReference(n, s)
	}
	if props := sub(s, "properties"); props != nil {
		n := obj{}
		for _, p := range sortedKeys(props) {
			n[p] = g.normalize(props[p].(obj), hint+pascal(p), pointer+"/properties/"+pointerToken(p), false)
		}
		r["properties"] = n
	}
	if item := sub(s, "items"); item != nil {
		r["items"] = g.normalize(item, hint+"Item", pointer+"/items", false)
	}
	if ap, ok := s["additionalProperties"].(obj); ok {
		r["additionalProperties"] = g.normalize(ap, hint+"Value", pointer+"/additionalProperties", false)
	}
	for _, k := range []string{"oneOf", "anyOf", "allOf"} {
		if arms := asSlice(s[k]); len(arms) > 0 {
			a := make([]any, len(arms))
			for i, v := range arms {
				suffix := fmt.Sprintf("Variant%d", i+1)
				for _, key := range []string{"kind", "type"} {
					if tag := sub(sub(v.(obj), "properties"), key); tag != nil && len(asSlice(tag["enum"])) == 1 {
						suffix = pascal(asSlice(tag["enum"])[0].(string))
					}
				}
				a[i] = g.normalize(v.(obj), hint+suffix, fmt.Sprintf("%s/%s/%d", pointer, k, i), false)
			}
			r[k] = a
		}
	}
	if schemaType(s) == "" && s["$ref"] == nil && s["oneOf"] == nil && s["anyOf"] == nil && s["allOf"] == nil && s["properties"] == nil {
		g.openFields[pointer] = "OpenAPI permits any JSON value; JSONValue validates and retains its exact numeric representation"
	}
	return r
}

func (g *gen) resolve(s obj) typeInfo {
	if r := str(s, "$ref"); r != "" {
		if !strings.HasPrefix(r, "#/components/schemas/") {
			fatalf("unsupported external schema reference %s", r)
		}
		n := refName(r)
		target := sub(g.schemas, n)
		if target == nil {
			fatalf("dangling $ref %s", r)
		}
		kind := "scalar"
		switch {
		case target["properties"] != nil:
			kind = "struct"
		case schemaType(target) == "array":
			kind = "slice"
		case schemaType(target) == "object":
			kind = "map"
		case target["oneOf"] != nil || target["anyOf"] != nil:
			kind = "struct"
		}
		return typeInfo{pascal(n), nullable(s) || nullable(target), kind}
	}
	for _, k := range []string{"oneOf", "anyOf", "allOf"} {
		if arms := asSlice(s[k]); len(arms) > 0 {
			var rest []obj
			for _, a := range arms {
				if schemaType(a.(obj)) != "null" {
					rest = append(rest, a.(obj))
				}
			}
			if len(rest) != 1 {
				fatalf("unnamed union must be normalized before resolution: %v", s)
			}
			t := g.resolve(rest[0])
			t.nullable = t.nullable || nullable(s)
			return t
		}
	}
	typ, nul := schemaType(s), nullable(s)
	switch typ {
	case "string":
		return typeInfo{"string", nul, "scalar"}
	case "integer":
		switch str(s, "format") {
		case "int32":
			return typeInfo{"int32", nul, "scalar"}
		case "uint32":
			return typeInfo{"uint32", nul, "scalar"}
		case "uint64":
			return typeInfo{"uint64", nul, "scalar"}
		case "int64", "":
			return typeInfo{"int64", nul, "scalar"}
		default:
			fatalf("unsupported integer format %s", str(s, "format"))
		}
	case "number":
		return typeInfo{"float64", nul, "scalar"}
	case "boolean":
		return typeInfo{"bool", nul, "scalar"}
	case "array":
		item := sub(s, "items")
		if item == nil {
			fatalf("array has no item contract")
		}
		e := g.resolve(item)
		t := e.goType
		if e.nullable {
			t = "*" + t
		}
		return typeInfo{"[]" + t, nul, "slice"}
	case "object":
		if ap, ok := s["additionalProperties"].(obj); ok {
			e := g.resolve(ap)
			return typeInfo{"map[string]" + e.goType, nul, "map"}
		}
		if s["properties"] != nil || s["additionalProperties"] == false {
			fatalf("unnamed closed object must be normalized")
		}
		return typeInfo{"JSONObject", nul, "map"}
	case "":
		return typeInfo{"JSONValue", true, "json"}
	default:
		fatalf("unsupported schema type %q", typ)
	}
	panic("unreachable")
}

func (g *gen) generateModels() string {
	var b bytes.Buffer
	b.WriteString("// Code generated by internal/gen from sdks/contracts/openapi.json; DO NOT EDIT.\n\npackage authgebra\n\nimport (\"encoding/json\"; \"fmt\"; \"bytes\"; \"regexp\"; \"unicode/utf8\")\n\nvar _ = fmt.Errorf\nvar _ = bytes.Equal\nvar _ = json.Marshal\nvar _ = regexp.MatchString\nvar _ = utf8.RuneCountInString\n\n")
	for _, n := range sortedKeys(g.schemas) {
		if handTypes[n] {
			continue
		}
		s := sub(g.schemas, n)
		gn := pascal(n)
		b.WriteString(comment(gn+" — ", str(s, "description"), ""))
		fmt.Fprintf(&b, "// Source: %s\n", g.origins[n])
		switch {
		case s["enum"] != nil:
			g.emitEnum(&b, gn, s)
		case s["properties"] != nil || schemaType(s) == "object" && s["additionalProperties"] == false:
			g.emitStruct(&b, n, gn, s)
		case s["oneOf"] != nil || s["anyOf"] != nil:
			arms := asSlice(s["oneOf"])
			if len(arms) == 0 {
				arms = asSlice(s["anyOf"])
			}
			nonnull := 0
			for _, a := range arms {
				if schemaType(a.(obj)) != "null" {
					nonnull++
				}
			}
			if nonnull == 1 {
				t := g.resolve(s)
				fmt.Fprintf(&b, "type %s %s\n\n", gn, t.goType)
			} else {
				g.emitUnion(&b, n, gn, arms)
			}
		default:
			t := g.resolve(s)
			if s["x-go-alias"] == true {
				fmt.Fprintf(&b, "type %s = %s\n\n", gn, t.goType)
			} else {
				fmt.Fprintf(&b, "type %s %s\n\n", gn, t.goType)
			}
		}
	}
	b.WriteString(g.requestJSONValidators())
	return b.String()
}
func (g *gen) emitEnum(b *bytes.Buffer, n string, s obj) {
	fmt.Fprintf(b, "type %s string\nconst (\n", n)
	seen := map[string]bool{}
	for _, v := range asSlice(s["enum"]) {
		value, ok := v.(string)
		if !ok {
			fatalf("unsupported non-string enum %s", n)
		}
		cn := n + pascal(strings.ToLower(value))
		if seen[cn] {
			fatalf("enum collision %s", cn)
		}
		seen[cn] = true
		fmt.Fprintf(b, "%s %s = %q\n", cn, n, value)
	}
	fmt.Fprintf(b, ")\nfunc (v %s) Validate() error { switch v {\ncase ", n)
	for i, v := range asSlice(s["enum"]) {
		if i > 0 {
			b.WriteString(",")
		}
		fmt.Fprintf(b, "%q", v)
	}
	fmt.Fprintf(b, ": return nil\ndefault: return fmt.Errorf(\"invalid %s value %%q\",v)\n} }\n\n", n)
}
func (g *gen) emitStruct(b *bytes.Buffer, source, n string, s obj) {
	req := map[string]bool{}
	for _, r := range asSlice(s["required"]) {
		req[r.(string)] = true
	}
	props := sub(s, "properties")
	var optional, clears []string
	fmt.Fprintf(b, "type %s struct {\n", n)
	for _, p := range sortedKeys(props) {
		v := props[p].(obj)
		t := g.resolve(v)
		ft, tag := t.goType, p
		if !req[p] {
			optional = append(optional, p)
			tag += ",omitempty"
			if t.kind != "slice" && t.kind != "map" {
				ft = "*" + ft
			}
		} else if t.nullable && t.kind != "slice" && t.kind != "map" && t.kind != "json" {
			ft = "*" + ft
		}
		b.WriteString(comment("", str(v, "description"), ""))
		fmt.Fprintf(b, "// Source: %s/properties/%s\n%s %s `json:%q`\n", g.origins[source], pointerToken(p), pascal(p), ft, tag)
		if !req[p] {
			fmt.Fprintf(b, "// %sField preserves absent, explicit null, and zero values; it conflicts with %s.\n%sField Field[%s] `json:\"-\"`\n", pascal(p), pascal(p), pascal(p), t.goType)
		}
		if v["x-authgebra-clearable"] == true {
			if req[p] || !t.nullable || !strings.HasPrefix(n, "Patch") {
				fatalf("contradictory clearable field %s.%s", n, p)
			}
			clears = append(clears, p)
			fmt.Fprintf(b, "// Clear%s is the compatible explicit-null shorthand.\nClear%s bool `json:\"-\"`\n", pascal(p), pascal(p))
		}
	}
	b.WriteString("}\n\n")
	if len(optional) > 0 {
		fmt.Fprintf(b, "func (v %s) MarshalJSON() ([]byte,error) {\ntype plain %s\nraw,err:=marshalBody(plain(v));if err!=nil{return nil,err}\nvar fields map[string]json.RawMessage\nif err=json.Unmarshal(raw,&fields);err!=nil{return nil,err}\n", n, n)
		for _, p := range optional {
			t := g.resolve(props[p].(obj))
			clear := "false"
			for _, c := range clears {
				if c == p {
					clear = "v.Clear" + pascal(p)
				}
			}
			value := "v." + pascal(p)
			if t.kind != "slice" && t.kind != "map" {
				value = "fieldPointerValue(" + value + ")"
			}
			fmt.Fprintf(b, "if err=encodeField(fields,%q,refreshField(v.%sField,%s,v.%s!=nil),%t,%s);err!=nil{return nil,err}\n", p, pascal(p), value, pascal(p), t.nullable, clear)
		}
		b.WriteString("return marshalBody(fields)\n}\n")
	}
	validation := ""
	if strings.HasSuffix(n, "Request") {
		validation = fmt.Sprintf("if err:=validate%sRequestJSON(raw);err!=nil{return err}\n", n)
	}
	fmt.Fprintf(b, "func (v *%s) UnmarshalJSON(raw []byte) error {\n%svar fields map[string]json.RawMessage\nif err:=json.Unmarshal(raw,&fields);err!=nil{return err}\nif fields==nil{return fmt.Errorf(\"%s requires an object\")}\n", n, validation, n)
	for _, p := range sortedKeys(props) {
		t := g.resolve(props[p].(obj))
		if req[p] {
			fmt.Fprintf(b, "if _,ok:=fields[%q];!ok{return fmt.Errorf(%q)}\n", p, p+" is required")
		}
		if !t.nullable {
			fmt.Fprintf(b, "if raw,ok:=fields[%q];ok&&isJSONNull(raw){return fmt.Errorf(%q)}\n", p, p+" does not accept null")
		}
	}
	fmt.Fprintf(b, "type plain %s\nvar out plain\nif err:=json.Unmarshal(raw,&out);err!=nil{return err}\n", n)
	for _, p := range optional {
		t := g.resolve(props[p].(obj))
		expr := "out." + pascal(p)
		if t.kind != "slice" && t.kind != "map" {
			expr = "fieldPointerValue(" + expr + ")"
		}
		fmt.Fprintf(b, "out.%sField=decodedField(fields[%q],%s)\n", pascal(p), p, expr)
	}
	fmt.Fprintf(b, "*v=%s(out);return nil\n}\n", n)
	g.emitValidation(b, n, s)
}
func (g *gen) emitUnion(b *bytes.Buffer, source, n string, arms []any) {
	type variant struct{ name, typ, key, tag string }
	var vs []variant
	for _, a := range arms {
		ref := str(a.(obj), "$ref")
		if ref == "" {
			fatalf("union %s has an unnamed arm", n)
		}
		name := refName(ref)
		s := sub(g.schemas, name)
		var key, tag string
		for _, p := range sortedKeys(sub(s, "properties")) {
			value := sub(sub(s, "properties"), p)
			if r := str(value, "$ref"); r != "" {
				value = sub(g.schemas, refName(r))
			}
			if e := asSlice(value["enum"]); len(e) == 1 {
				key, tag = p, e[0].(string)
				break
			}
		}
		if key == "" {
			fatalf("union %s arm %s has no unique discriminator", n, name)
		}
		vs = append(vs, variant{pascal(tag), pascal(name), key, tag})
	}
	if len(vs) == 0 {
		fatalf("empty union %s", n)
	}
	for _, v := range vs {
		if v.key != vs[0].key {
			fatalf("union %s has inconsistent discriminators", n)
		}
	}
	fmt.Fprintf(b, "type %s struct {\n", n)
	for _, v := range vs {
		fmt.Fprintf(b, "%s *%s `json:\"-\"`\n", v.name, v.typ)
	}
	b.WriteString("}\n")
	fmt.Fprintf(b, "func (v %s) MarshalJSON() ([]byte,error) {\ncount:=0;var raw []byte;var err error\n", n)
	for _, v := range vs {
		fmt.Fprintf(b, "if v.%s!=nil {count++;if err=v.%s.Validate();err!=nil{return nil,err};raw,err=marshalBody(v.%s)}\n", v.name, v.name, v.name)
	}
	fmt.Fprintf(b, "if count!=1{return nil,fmt.Errorf(\"%s requires exactly one variant, got %%d\",count)};return raw,err\n}\n", n)
	fmt.Fprintf(b, "func (v *%s) UnmarshalJSON(raw []byte) error {\nvar tag struct { Value string `json:%q` };if err:=json.Unmarshal(raw,&tag);err!=nil{return err};var out %s\nswitch tag.Value {\n", n, vs[0].key, n)
	for _, v := range vs {
		fmt.Fprintf(b, "case %q: out.%s=new(%s);if err:=json.Unmarshal(raw,out.%s);err!=nil{return err}\n", v.tag, v.name, v.typ, v.name)
	}
	fmt.Fprintf(b, "default:return fmt.Errorf(\"unknown %s discriminator %%q\",tag.Value)\n};*v=out;return nil\n}\n\n", n)
	fmt.Fprintf(b, "func(v %s) Validate()error{_,err:=v.MarshalJSON();return err}\n", n)
}
func (g *gen) concrete(s obj) obj {
	if r := str(s, "$ref"); r != "" {
		return g.concrete(sub(g.schemas, refName(r)))
	}
	for _, k := range []string{"oneOf", "anyOf", "allOf"} {
		arms := asSlice(s[k])
		var nonnull []obj
		for _, a := range arms {
			if schemaType(a.(obj)) != "null" {
				nonnull = append(nonnull, a.(obj))
			}
		}
		if len(nonnull) == 1 {
			return g.concrete(nonnull[0])
		}
	}
	return s
}
func (g *gen) validateValue(b *bytes.Buffer, field obj, expression, path string) {
	resolved := g.concrete(field)
	base := schemaType(resolved)
	if resolved["enum"] != nil {
		fmt.Fprintf(b, "switch string(%s) { case ", expression)
		for i, v := range asSlice(resolved["enum"]) {
			if i > 0 {
				b.WriteString(",")
			}
			fmt.Fprintf(b, "%q", v)
		}
		fmt.Fprintf(b, ": default:return fmt.Errorf(%q) }\n", path+" has an invalid enum value")
	}
	switch base {
	case "string":
		for _, v := range []struct{ k, op string }{{"minLength", "<"}, {"maxLength", ">"}} {
			if limit, ok := resolved[v.k]; ok {
				fmt.Fprintf(b, "if utf8.RuneCountInString(string(%s))%s%v{return fmt.Errorf(%q)}\n", expression, v.op, limit, path+" violates "+v.k)
			}
		}
		if pat, ok := resolved["pattern"].(string); ok {
			fmt.Fprintf(b, "if ok,err:=regexp.MatchString(%q,string(%s));err!=nil{return err}else if !ok{return fmt.Errorf(%q)}\n", pat, expression, path+" violates pattern")
		}
	case "integer", "number":
		for _, v := range []struct{ k, op string }{{"minimum", "<"}, {"maximum", ">"}} {
			if limit, ok := resolved[v.k]; ok {
				if (str(resolved, "format") == "uint32" || str(resolved, "format") == "uint64") && v.k == "minimum" && fmt.Sprint(limit) == "0" {
					continue
				}
				fmt.Fprintf(b, "if (%s)%s%v{return fmt.Errorf(%q)}\n", expression, v.op, limit, path+" violates "+v.k)
			}
		}
	case "array":
		for _, v := range []struct{ k, op string }{{"minItems", "<"}, {"maxItems", ">"}} {
			if limit, ok := resolved[v.k]; ok {
				fmt.Fprintf(b, "if len(%s)%s%v{return fmt.Errorf(%q)}\n", expression, v.op, limit, path+" violates "+v.k)
			}
		}
		var inner bytes.Buffer
		if item := sub(resolved, "items"); item != nil {
			g.validateValue(&inner, item, "item", path+"[]")
		}
		if inner.Len() > 0 {
			fmt.Fprintf(b, "for _,item:=range %s {\n%s}\n", expression, inner.String())
		}
		if resolved["uniqueItems"] == true {
			fmt.Fprintf(b, "if err:=validateUnique(%s);err!=nil{return fmt.Errorf(%q,err)}\n", expression, path+": %w")
		}
	case "object":
		ref := str(field, "$ref")
		if resolved["properties"] != nil && ref != "" && !handTypes[refName(ref)] {
			fmt.Fprintf(b, "if err:=(%s).Validate();err!=nil{return fmt.Errorf(%q,err)}\n", expression, path+": %w")
		}
		if ap := sub(resolved, "additionalProperties"); ap != nil {
			var inner bytes.Buffer
			g.validateValue(&inner, ap, "item", path+".*")
			if inner.Len() > 0 {
				fmt.Fprintf(b, "for _,item:=range %s {\n%s}\n", expression, inner.String())
			}
		}
	default:
		if resolved["oneOf"] != nil || resolved["anyOf"] != nil {
			fmt.Fprintf(b, "if err:=(%s).Validate();err!=nil{return fmt.Errorf(%q,err)}\n", expression, path+": %w")
		}
	}
}
func (g *gen) emitValidation(b *bytes.Buffer, n string, s obj) {
	fmt.Fprintf(b, "// Validate checks declared machine constraints without changing the payload or applying defaults.\nfunc(v %s) Validate() error {\n", n)
	req := map[string]bool{}
	for _, r := range asSlice(s["required"]) {
		req[r.(string)] = true
	}
	for _, p := range sortedKeys(sub(s, "properties")) {
		field := sub(sub(s, "properties"), p)
		t := g.resolve(field)
		expr := "v." + pascal(p)
		if req[p] && !t.nullable && (t.kind == "slice" || t.kind == "map") {
			fmt.Fprintf(b, "if %s==nil{return fmt.Errorf(%q)}\n", expr, p+" does not accept null")
		}
		ptr := !req[p] && t.kind != "slice" && t.kind != "map" || req[p] && t.nullable && t.kind != "slice" && t.kind != "map" && t.kind != "json"
		var body bytes.Buffer
		ve := expr
		if ptr {
			ve = "*" + expr
		}
		g.validateValue(&body, field, ve, p)
		if body.Len() > 0 {
			if ptr {
				fmt.Fprintf(b, "if %s!=nil {\n%s}\n", expr, body.String())
			} else {
				b.WriteString(body.String())
			}
		}
		if !req[p] {
			if !t.nullable {
				fmt.Fprintf(b, "if v.%sField.IsNull(){return fmt.Errorf(%q)}\n", pascal(p), p+" does not accept null")
			}
			var state bytes.Buffer
			if !t.nullable && (t.kind == "slice" || t.kind == "map") {
				fmt.Fprintf(&state, "if value==nil{return fmt.Errorf(%q)}\n", p+" does not accept null")
			}
			g.validateValue(&state, field, "value", p)
			if state.Len() > 0 {
				fmt.Fprintf(b, "if value,ok:=v.%sField.Value();ok {\n%s}\n", pascal(p), state.String())
			}
		}
	}
	b.WriteString("return nil\n}\n\n")
}
