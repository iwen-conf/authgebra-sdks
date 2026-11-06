package main

import (
	"bytes"
	"fmt"
	"strings"
)

// Request JSON checks stay separate from shared response models so newly added
// response properties remain readable. No business fields are copied or flattened.
func (g *gen) requestJSONValidators() string {
	reached := map[string]bool{}
	var visit func(obj)
	visit = func(s obj) {
		if r := str(s, "$ref"); r != "" {
			n := refName(r)
			if !reached[n] {
				reached[n] = true
				visit(sub(g.schemas, n))
			}
			return
		}
		for _, p := range sub(s, "properties") {
			visit(p.(obj))
		}
		if item := sub(s, "items"); item != nil {
			visit(item)
		}
		if value := sub(s, "additionalProperties"); value != nil {
			visit(value)
		}
		for _, key := range []string{"oneOf", "anyOf", "allOf"} {
			for _, v := range asSlice(s[key]) {
				visit(v.(obj))
			}
		}
	}
	for _, name := range sortedKeys(g.schemas) {
		if strings.HasSuffix(name, "Request") {
			reached[name] = true
			visit(sub(g.schemas, name))
		}
	}
	var b bytes.Buffer
	names := obj{}
	for name := range reached {
		names[name] = true
	}
	for _, name := range sortedKeys(names) {
		s := sub(g.schemas, name)
		fmt.Fprintf(&b, "func validate%sRequestJSON(raw []byte) error {\n", pascal(name))
		switch {
		case s["enum"] != nil:
			b.WriteString("var value string;if err:=json.Unmarshal(raw,&value);err!=nil{return err}\nswitch value {case ")
			for i, v := range asSlice(s["enum"]) {
				if i > 0 {
					b.WriteString(",")
				}
				fmt.Fprintf(&b, "%q", v)
			}
			fmt.Fprintf(&b, ":return nil;default:return fmt.Errorf(%q)}\n", name+" has an invalid enum value")
		case s["properties"] != nil:
			b.WriteString("var fields map[string]json.RawMessage;if err:=json.Unmarshal(raw,&fields);err!=nil{return err};if fields==nil{return fmt.Errorf(\"request object must not be null\")}\n")
			props := sub(s, "properties")
			if s["additionalProperties"] == false {
				b.WriteString("for key:=range fields {switch key {case ")
				if len(props) == 0 {
					b.WriteString("\"\":return fmt.Errorf(\"unknown request property\");")
				} else {
					for i, p := range sortedKeys(props) {
						if i > 0 {
							b.WriteString(",")
						}
						fmt.Fprintf(&b, "%q", p)
					}
					b.WriteString(":")
				}
				b.WriteString("default:return fmt.Errorf(\"unknown request property\")}}\n")
			}
			required := map[string]bool{}
			for _, v := range asSlice(s["required"]) {
				required[v.(string)] = true
			}
			for _, p := range sortedKeys(props) {
				field := props[p].(obj)
				if required[p] {
					fmt.Fprintf(&b, "if _,ok:=fields[%q];!ok{return fmt.Errorf(%q)}\n", p, p+" is required")
				}
				fmt.Fprintf(&b, "if value,ok:=fields[%q];ok {\n", p)
				if !g.resolve(field).nullable {
					fmt.Fprintf(&b, "if bytes.Equal(bytes.TrimSpace(value),[]byte(\"null\")){return fmt.Errorf(%q)}\n", p+" does not accept null")
				}
				var body bytes.Buffer
				g.checkRequestJSONValue(&body, field, "value", 0)
				if body.Len() > 0 {
					b.WriteString(body.String())
				} else {
					b.WriteString("_ = value\n")
				}
				b.WriteString("}\n")
			}
			b.WriteString("return nil\n")
		case s["oneOf"] != nil || s["anyOf"] != nil:
			arms := asSlice(s["oneOf"])
			if len(arms) == 0 {
				arms = asSlice(s["anyOf"])
			}
			tagKey := ""
			var choices []struct{ tag, target string }
			for _, a := range arms {
				if schemaType(a.(obj)) == "null" {
					continue
				}
				target := refName(str(a.(obj), "$ref"))
				v := sub(g.schemas, target)
				for _, key := range []string{"kind", "type"} {
					tag := sub(sub(v, "properties"), key)
					if r := str(tag, "$ref"); r != "" {
						tag = sub(g.schemas, refName(r))
					}
					if values := asSlice(tag["enum"]); len(values) == 1 {
						tagKey = key
						choices = append(choices, struct{ tag, target string }{values[0].(string), target})
						break
					}
				}
			}
			if tagKey == "" {
				g.checkRequestJSONValue(&b, s, "raw", 0)
				b.WriteString("return nil\n")
			} else {
				fmt.Fprintf(&b, "var tag struct{Value string `json:%q`};if err:=json.Unmarshal(raw,&tag);err!=nil{return err};switch tag.Value {\n", tagKey)
				for _, choice := range choices {
					fmt.Fprintf(&b, "case %q:return validate%sRequestJSON(raw)\n", choice.tag, pascal(choice.target))
				}
				b.WriteString("default:return fmt.Errorf(\"unknown request union variant\")}\n")
			}
		default:
			g.checkRequestJSONValue(&b, s, "raw", 0)
			b.WriteString("return nil\n")
		}
		b.WriteString("}\n\n")
	}
	return b.String()
}
func (g *gen) checkRequestJSONValue(b *bytes.Buffer, s obj, expression string, depth int) {
	var body bytes.Buffer
	g.checkNonNullRequestJSONValue(&body, s, expression, depth)
	if body.Len() == 0 {
		return
	}
	if g.resolve(s).nullable {
		fmt.Fprintf(b, "if !bytes.Equal(bytes.TrimSpace(%s),[]byte(\"null\")){\n%s}\n", expression, body.String())
	} else {
		b.WriteString(body.String())
	}
}
func (g *gen) checkNonNullRequestJSONValue(b *bytes.Buffer, s obj, expression string, depth int) {
	if r := str(s, "$ref"); r != "" {
		fmt.Fprintf(b, "if err:=validate%sRequestJSON(%s);err!=nil{return err}\n", pascal(refName(r)), expression)
		return
	}
	for _, key := range []string{"oneOf", "anyOf", "allOf"} {
		if arms := asSlice(s[key]); len(arms) > 0 {
			for _, a := range arms {
				if schemaType(a.(obj)) != "null" {
					g.checkRequestJSONValue(b, a.(obj), expression, depth+1)
					return
				}
			}
		}
	}
	var inner bytes.Buffer
	if schemaType(s) == "array" {
		item := sub(s, "items")
		g.checkRequestJSONValue(&inner, item, fmt.Sprintf("item%d", depth), depth+1)
		if inner.Len() > 0 {
			fmt.Fprintf(b, "var items%d []json.RawMessage;if err:=json.Unmarshal(%s,&items%d);err!=nil{return err};for _,item%d:=range items%d {\n%s}\n", depth, expression, depth, depth, depth, inner.String())
		}
	}
	if schemaType(s) == "object" && sub(s, "additionalProperties") != nil {
		g.checkRequestJSONValue(&inner, sub(s, "additionalProperties"), fmt.Sprintf("item%d", depth), depth+1)
		if inner.Len() > 0 {
			fmt.Fprintf(b, "var entries%d map[string]json.RawMessage;if err:=json.Unmarshal(%s,&entries%d);err!=nil{return err};for _,item%d:=range entries%d {\n%s}\n", depth, expression, depth, depth, depth, inner.String())
		}
	}
}
