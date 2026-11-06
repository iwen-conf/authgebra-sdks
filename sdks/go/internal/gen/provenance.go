package main

import "encoding/json"

var constraintKeys = []string{"type", "format", "default", "enum", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems", "additionalProperties", "readOnly", "writeOnly", "deprecated", "oneOf", "anyOf", "allOf", "x-authgebra-clearable", "x-authgebra-normalized-value"}

func (g *gen) provenance() []byte {
	g.prepare()
	var models []obj
	for _, name := range sortedKeys(g.schemas) {
		s := sub(g.schemas, name)
		file := "models_gen.go"
		if name == "CommitState" {
			file = "errors.go"
		}
		required := map[string]bool{}
		for _, r := range asSlice(s["required"]) {
			required[r.(string)] = true
		}
		fields := []obj{}
		for _, field := range sortedKeys(sub(s, "properties")) {
			schema := sub(sub(s, "properties"), field)
			constraints := obj{}
			for _, key := range constraintKeys {
				if v, ok := g.concrete(schema)[key]; ok {
					constraints[key] = v
				}
			}
			info := g.resolve(schema)
			fields = append(fields, obj{"schema_pointer": g.origins[name] + "/properties/" + pointerToken(field), "json_name": field, "go_field": pascal(field), "go_type": info.goType, "required": required[field], "nullable": info.nullable, "constraints": constraints})
		}
		models = append(models, obj{"schema_pointer": g.origins[name], "go_type": pascal(name), "file": file, "fields": fields})
	}
	exceptions := []obj{}
	for _, pointer := range sortedStringKeys(g.openFields) {
		exceptions = append(exceptions, obj{"schema_pointer": pointer, "go_type": "JSONValue", "reason": g.openFields[pointer], "test": "TestOpenJSONValuePreservesAllJSONKinds"})
	}
	raw, err := json.MarshalIndent(obj{"models": models, "dynamic_exceptions": exceptions}, "", "  ")
	if err != nil {
		fatalf("encode provenance: %v", err)
	}
	return append(raw, '\n')
}
func sortedStringKeys(values map[string]string) []string {
	keys := obj{}
	for key := range values {
		keys[key] = true
	}
	return sortedKeys(keys)
}
