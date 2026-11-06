#!/usr/bin/env python3
"""Generate src/generated/{models,admin}.rs from the reviewed OpenAPI export.

    python3 tools/gen.py ../contracts/openapi.json src/generated

Deterministic (sorted, no timestamps). The OpenAPI sha256 is embedded so a test can detect a
stale generation. Objects and tagged unions retain their named types. Only schemas that
actually permit arbitrary JSON use `serde_json::Value`, with a provenance exception.
String enums retain unknown response values through an `Unknown(String)` arm.
"""
import hashlib
import json
import re
import sys
import argparse
import subprocess
import tempfile
from pathlib import Path

KEYWORDS = set(
    "as break const continue crate else enum extern false fn for if impl in let loop match mod "
    "move mut pub ref return self Self static struct super trait true type unsafe use where while "
    "async await dyn abstract become box do final macro override priv typeof unsized virtual "
    "yield try gen".split()
)
VALUE = "serde_json::Value"


def pascal(text):
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)) if p]
    out = "".join(p[:1].upper() + p[1:] for p in parts) or "Empty"
    return "N" + out if out[0].isdigit() else out


def ident(name):
    out = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not out or out[0].isdigit():
        out = "f_" + out
    return out + "_" if out in KEYWORDS else out


def doc(text, indent=""):
    if not text:
        return ""
    line = text.strip().splitlines()[0].strip().replace("*/", "* /")
    line = re.sub(r"\s+", " ", line)
    return f"{indent}/// {line}\n" if line else ""


class Gen:
    def __init__(self, spec):
        self.spec = spec
        self.S = spec["components"]["schemas"]
        self.extras = {}
        self.pointers = {}
        self.provenance = []
        self.exceptions = {}
        def refs(node):
            if isinstance(node, dict):
                found = {node["$ref"].split("/")[-1]} if str(node.get("$ref", "")).startswith("#/components/schemas/") else set()
                return found | set().union(*(refs(value) for value in node.values()), set())
            if isinstance(node, list):
                return set().union(*(refs(value) for value in node), set())
            return set()
        def reachable(roots):
            found, pending = set(), list(roots)
            while pending:
                name = pending.pop()
                if name in found or name not in self.S:
                    continue
                found.add(name)
                pending.extend(refs(self.S[name]) - found)
            return found
        requests, responses = set(), set()
        for item in spec["paths"].values():
            for operation in item.values():
                requests |= refs(operation.get("requestBody", {}))
                responses |= refs(operation.get("responses", {}))
        self.request_only = {pascal(name) for name in requests | (reachable(requests) - reachable(responses))}
        def pointers(node, pointer):
            if isinstance(node, dict):
                self.pointers[id(node)] = pointer
                for key, value in node.items():
                    pointers(value, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    pointers(value, pointer + "/" + str(index))
        pointers(spec, "#")
        def check_schema(schema, pointer):
            if not isinstance(schema, dict):
                raise ValueError("unsupported non-object schema at " + pointer)
            unsupported = {"not", "if", "then", "else", "dependentSchemas", "dependentRequired", "patternProperties", "prefixItems", "contains", "unevaluatedProperties", "unevaluatedItems"}
            if unsupported.intersection(schema):
                raise ValueError("unsupported schema keywords at " + pointer)
            if not any(key in schema for key in ("type", "$ref", "oneOf", "anyOf", "allOf")) and set(schema) - {"description", "title", "example", "examples", "default", "deprecated", "readOnly", "writeOnly"}:
                raise ValueError("unsupported schema assertions without a type at " + pointer)
            if schema.get("type") == "object" and not schema.get("properties") and schema.get("additionalProperties") is False:
                raise ValueError("unsupported schema closed empty object at " + pointer)
            if schema.get("type") == "array" and "items" not in schema:
                raise ValueError("array needs items at " + pointer)
            if "enum" in schema and (schema.get("type") != "string" or not all(isinstance(value, str) for value in schema["enum"])):
                raise ValueError("unsupported non-string enum at " + pointer)
            for key, value in schema.get("properties", {}).items():
                check_schema(value, pointer + "/properties/" + key)
            for key in ("items", "additionalProperties"):
                if isinstance(schema.get(key), dict):
                    check_schema(schema[key], pointer + "/" + key)
            for key in ("oneOf", "anyOf", "allOf"):
                for index, value in enumerate(schema.get(key, [])):
                    check_schema(value, pointer + "/" + key + "/" + str(index))
        for name, schema in self.S.items():
            check_schema(schema, "#/components/schemas/" + name)

    # ---- type resolution -------------------------------------------------
    @staticmethod
    def shape(value):
        if isinstance(value, dict):
            return {key: Gen.shape(item) for key, item in value.items() if key not in ("description", "title", "example", "examples")}
        if isinstance(value, list):
            return [Gen.shape(item) for item in value]
        return value

    def ty(self, s, prefix, hint=""):
        """-> (rust type, nullable)."""
        if "$ref" in s:
            if not s["$ref"].startswith("#/components/schemas/"):
                raise ValueError("unsupported non-local schema reference: " + s["$ref"])
            name = s["$ref"].split("/")[-1]
            if name not in self.S:
                raise ValueError("unresolved schema reference: " + s["$ref"])
            return prefix + pascal(name), False
        for key in ("oneOf", "anyOf"):
            if key in s:
                arms = s[key]
                non_null = [a for a in arms if a.get("type") != "null"]
                nullable = len(non_null) < len(arms)
                if len(non_null) == 1:
                    inner, inner_null = self.ty(non_null[0], prefix)
                    return inner, nullable or inner_null
                raise ValueError("unsupported anonymous union at " + self.pointers.get(id(s), hint))
        if "allOf" in s:
            if len(s["allOf"]) == 1:
                return self.ty(s["allOf"][0], prefix)
            raise ValueError("unsupported allOf at " + self.pointers.get(id(s), hint))
        t = s.get("type")
        nullable = False
        if isinstance(t, list):
            nullable = "null" in t
            rest = [x for x in t if x != "null"]
            if len(rest) != 1:
                raise ValueError("unsupported type combination at " + self.pointers.get(id(s), hint))
            t = rest[0]
        if t == "string":
            return "String", nullable
        if t == "integer":
            formats = {
                "int32": "i32", "int64": "i64", "uint8": "u8", "uint16": "u16",
                "uint32": "u32", "uint64": "u64",
            }
            fmt = s.get("format", "int64")
            if fmt not in formats:
                raise ValueError("unsupported integer format: " + fmt)
            return formats[fmt], nullable
        if t == "number":
            return "f64", nullable
        if t == "boolean":
            return "bool", nullable
        if t == "array":
            inner, inner_null = self.ty(s.get("items", {}), prefix, hint + "Item")
            return f"Vec<{'Option<' + inner + '>' if inner_null else inner}>", nullable
        if t == "object" and "properties" in s:
            pointer = self.pointers[id(s)]
            owner = pointer.split("/")[3] if pointer.startswith("#/components/schemas/") else ""
            for wrapper in ("ApiResponse_", "PagedResponse_"):
                if owner.startswith(wrapper):
                    target = owner[len(wrapper):]
                    target = target.removeprefix("Vec_")
                    if target in self.S and self.shape(s) == self.shape(self.S[target]):
                        return prefix + pascal(target), nullable
            if not hint:
                raise ValueError("anonymous object needs a name: " + self.pointers.get(id(s), ""))
            name = pascal(hint)
            if name in self.S:
                name += "Inline"
            self.extras[name] = s
            return prefix + name, nullable
        if t == "object" and isinstance(s.get("additionalProperties"), dict):
            item, item_null = self.ty(s["additionalProperties"], prefix, hint + "Value")
            return f"std::collections::BTreeMap<String, {'Option<' + item + '>' if item_null else item}>", nullable
        if t == "object" and not s.get("properties"):
            return "std::collections::BTreeMap<String, serde_json::Value>", nullable
        if t is None and not s.get("properties"):
            pointer = self.pointers[id(s)]
            self.exceptions[pointer] = {"pointer": pointer, "type": VALUE,
                "reason": "The reviewed schema allows arbitrary JSON values", "test": "model_semantics::open_json_preserves_all_kinds"}
            return VALUE, True
        raise ValueError("unsupported schema at " + self.pointers.get(id(s), hint))

    # ---- models ----------------------------------------------------------
    def kind(self, name):
        s = self.S[name]
        t = s.get("type")
        types = [x for x in t if x != "null"] if isinstance(t, list) else [t]
        if "enum" in s and types == ["string"]:
            return "enum"
        if "oneOf" in s:
            return "union"
        if types == ["object"] and s.get("properties"):
            return "struct"
        return "alias"

    def direct_refs(self, name):
        out = set()
        for v in self.S[name].get("properties", {}).values():
            t, _ = self.ty(v, "", name + pascal(self.pointers.get(id(v), "Field").split("/")[-1]))
            if re.fullmatch(r"[A-Z][A-Za-z0-9]*", t) and t not in ("String", "Vec"):
                out.add(t)
        return out

    def models(self):
        names = sorted(self.S)
        by_type = {pascal(n): n for n in names}
        graph = {pascal(n): self.direct_refs(n) if self.kind(n) == "struct" else set() for n in names}

        def reaches(src, dst, seen=None):
            seen = seen or set()
            for nxt in graph.get(src, ()):
                if nxt == dst:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    if reaches(nxt, dst, seen):
                        return True
            return False

        out = [
            "// @generated by tools/gen.py from sdks/contracts/openapi.json. DO NOT EDIT.\n",
            "use serde::{Deserialize, Deserializer, Serialize, Serializer};\n\n",
        ]
        for name in names:
            s = self.S[name]
            tn = pascal(name)
            kind = self.kind(name)
            if kind == "enum":
                out.append(self.emit_enum(tn, s))
            elif kind == "union":
                out.append(self.emit_union(tn, s))
            elif kind == "struct":
                out.append(self.emit_struct(tn, s, reaches))
            else:
                t, nullable = self.ty(s, "", tn)
                out.append(doc(s.get("description")))
                out.append(f"pub type {tn} = {'Option<' + t + '>' if nullable else t};\n\n")
        done = set()
        while set(self.extras) - done:
            for tn in sorted(set(self.extras) - done):
                done.add(tn)
                out.append(self.emit_struct(tn, self.extras[tn], lambda a, b: False))
        return "".join(out)

    def emit_union(self, name, schema):
        self.provenance.append({"pointer": self.pointers[id(schema)], "type": name,
            "file": "sdks/rust/src/generated/models.rs", "fields": []})
        variants = schema["oneOf"]
        tags = [key for key, prop in variants[0].get("properties", {}).items()
                if len(prop.get("enum", [])) == 1 and all(len(v.get("properties", {}).get(key, {}).get("enum", [])) == 1 for v in variants)]
        if len(tags) != 1:
            raise ValueError("union requires one discriminator: " + name)
        tag = tags[0]
        out = [f"/// Source: {self.pointers[id(schema)]}\n", "#[derive(Debug, Clone, Serialize, Deserialize)]\n",
               f"#[serde(tag = {json.dumps(tag)})]\npub enum {name} {{\n"]
        for branch in variants:
            props = branch["properties"]
            value = props[tag]["enum"][0]
            out.append(f"    /// Source: {self.pointers[id(branch)]}\n    #[serde(rename = {json.dumps(value)})]\n    {pascal(value)} {{\n")
            fields = [{"pointer": self.pointers[id(props[tag])], "name": tag, "type": "String", "discriminator": value}]
            for key, prop in props.items():
                if key == tag:
                    continue
                typ, nullable = self.ty(prop, "", name + pascal(value) + pascal(key))
                optional = key not in branch.get("required", [])
                attrs = [f"rename = {json.dumps(key)}"] if ident(key) != key else []
                if optional:
                    attrs += ["default", 'skip_serializing_if = "Option::is_none"']
                    helper = "double_option" if nullable else "optional_non_nullable"
                    attrs.append(f'deserialize_with = "crate::serde_helpers::{helper}"')
                elif nullable:
                    attrs.append('deserialize_with = "crate::serde_helpers::required_option"')
                if nullable:
                    typ = f"Option<{typ}>"
                if optional:
                    typ = f"Option<{typ}>"
                out.append(f"        /// Source: {self.pointers[id(prop)]}\n")
                if attrs:
                    out.append("        #[serde(" + ", ".join(attrs) + ")]\n")
                out.append(f"        {ident(key)}: {typ},\n")
                fields.append({"pointer": self.pointers[id(prop)], "name": ident(key), "type": typ, "json_name": key})
            out.append("    },\n")
            self.provenance.append({"pointer": self.pointers[id(branch)], "type": name + "::" + pascal(value),
                "file": "sdks/rust/src/generated/models.rs", "fields": fields})
        out.append("}\n\n")
        out.append(f"impl {name} {{\n    pub fn validate(&self) -> Result<(), String> {{\n        match self {{\n")
        for branch in variants:
            value = branch["properties"][tag]["enum"][0]
            checked = {key: self.rules(prop, "value", name + pascal(value) + pascal(key))
                       for key, prop in branch["properties"].items() if key != tag}
            checked = {key: lines for key, lines in checked.items() if lines}
            pattern = ", ".join(ident(key) for key in checked)
            pattern = pattern + ", .." if pattern else ".."
            out.append(f"            Self::{pascal(value)} {{ {pattern} }} => {{\n")
            for key, lines in checked.items():
                out.append(f"                let value = {ident(key)};\n")
                out.extend("                " + line + "\n" for line in lines)
            out.append("                Ok(())\n            },\n")
        out.append("        }\n    }\n}\n\n")
        return "".join(out)

    def rules(self, prop, expression, hint):
        if "$ref" in prop:
            name = prop["$ref"].split("/")[-1]
            return [f"{expression}.validate()?;"] if self.kind(name) in ("struct", "union") else []
        for key in ("oneOf", "anyOf"):
            if key in prop:
                arms = [arm for arm in prop[key] if arm.get("type") != "null"]
                if len(arms) == 1:
                    return self.rules(arms[0], expression, hint)
        typ = prop.get("type")
        if isinstance(typ, list):
            typ = next(item for item in typ if item != "null")
        lines = []
        if typ in ("integer", "number"):
            fmt = prop.get("format", "int64")
            for key, operator in (("minimum", "<"), ("maximum", ">")):
                if key not in prop:
                    continue
                bound = prop[key]
                if fmt.startswith("uint") and ((key == "minimum" and bound == 0) or (key == "maximum" and bound == 2 ** int(fmt[4:]) - 1)):
                    continue
                lines.append(f'if *{expression} {operator} {bound} {{ return Err({json.dumps(hint + ": " + key)}.to_owned()); }}')
        if typ in ("string", "array"):
            length = f"{expression}.chars().count()" if typ == "string" else f"{expression}.len()"
            for key, operator in (("minLength" if typ == "string" else "minItems", "<"), ("maxLength" if typ == "string" else "maxItems", ">")):
                if key in prop:
                    lines.append(f'if {length} {operator} {prop[key]} {{ return Err({json.dumps(hint + ": " + key)}.to_owned()); }}')
        if typ == "array":
            inner = self.rules(prop["items"], "item", hint + "Item")
            if inner:
                lines += [f"for item in {expression} {{"] + ["    " + line for line in inner] + ["}"]
        if typ == "object" and "properties" in prop:
            lines.append(f"{expression}.validate()?;")
        elif typ == "object" and isinstance(prop.get("additionalProperties"), dict):
            inner = self.rules(prop["additionalProperties"], "item", hint + "Value")
            if inner:
                lines += [f"for item in {expression}.values() {{"] + ["    " + line for line in inner] + ["}"]
        if "pattern" in prop:
            if prop["pattern"] != "^[a-z][a-z0-9_.-]{0,63}$":
                raise ValueError("unsupported validation pattern at " + self.pointers[id(prop)])
            lines.append(f'if !crate::serde_helpers::code_is_valid({expression}) {{ return Err({json.dumps(hint + ": pattern")}.to_owned()); }}')
        if prop.get("uniqueItems"):
            if prop.get("items", {}).get("type") not in ("string", "integer", "number", "boolean"):
                raise ValueError("unsupported uniqueItems element at " + self.pointers[id(prop)])
            lines.append(f'for (index, item) in {expression}.iter().enumerate() {{ if {expression}[..index].contains(item) {{ return Err({json.dumps(hint + ": uniqueItems")}.to_owned()); }} }}')
        for unsupported in ("multipleOf", "exclusiveMinimum", "exclusiveMaximum"):
            if unsupported in prop:
                raise ValueError("unsupported validation keyword " + unsupported + " at " + self.pointers[id(prop)])
        return lines

    def emit_enum(self, tn, s):
        self.provenance.append({"pointer": self.pointers[id(s)], "type": tn,
            "file": "sdks/rust/src/generated/models.rs", "fields": []})
        values = [v for v in s["enum"] if isinstance(v, str)]
        variants, used = [], set()
        for value in values:
            v = pascal(value)
            while v in used or v == "Unknown":
                v += "_"
            used.add(v)
            variants.append((v, value))
        o = [doc(s.get("description")), f"/// Source: {self.pointers[id(s)]}\n", "#[derive(Debug, Clone, PartialEq, Eq)]\n", f"pub enum {tn} {{\n"]
        for v, _ in variants:
            o.append(f"    {v},\n")
        o.append("    /// A value this SDK version does not know.\n    Unknown(String),\n}\n")
        o.append(f"impl {tn} {{\n    /// Wire string.\n    pub fn as_str(&self) -> &str {{\n        match self {{\n")
        for v, value in variants:
            o.append(f"            Self::{v} => {json.dumps(value)},\n")
        o.append("            Self::Unknown(other) => other,\n        }\n    }\n}\n")
        o.append(f"impl std::fmt::Display for {tn} {{\n    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {{\n        f.write_str(self.as_str())\n    }}\n}}\n")
        o.append(f"impl Serialize for {tn} {{\n    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {{\n        s.serialize_str(self.as_str())\n    }}\n}}\n")
        o.append(f"impl<'de> Deserialize<'de> for {tn} {{\n    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {{\n        let raw = String::deserialize(d)?;\n        Ok(match raw.as_str() {{\n")
        for v, value in variants:
            o.append(f"            {json.dumps(value)} => Self::{v},\n")
        o.append("            _ => Self::Unknown(raw),\n        })\n    }\n}\n\n")
        return "".join(o)

    def emit_struct(self, tn, s, reaches):
        required = set(s.get("required", []))
        o = [doc(s.get("description")), f"/// Source: {self.pointers[id(s)]}\n", "#[derive(Debug, Clone, Serialize, Deserialize)]\n", f"pub struct {tn} {{\n"]
        owner = self.pointers[id(s)].split("/")[3]
        if s.get("additionalProperties") is False and (tn in self.request_only or pascal(owner) in self.request_only):
            o.insert(-1, "#[serde(deny_unknown_fields)]\n")
        used = set()
        fields = []
        for key, prop in s["properties"].items():
            t, nullable = self.ty(prop, "", tn + pascal(key))
            if re.fullmatch(r"[A-Z][A-Za-z0-9]*", t) and t not in ("String", "Vec") and (t == tn or reaches(t, tn)):
                t = f"Box<{t}>"
            req = key in required
            attrs, ft = [], t
            if req and not nullable:
                pass
            elif req and nullable:
                ft = f"Option<{t}>"
                attrs.append('deserialize_with = "crate::serde_helpers::required_option"')
            elif not nullable:
                ft = f"Option<{t}>"
                attrs.append("default")
                attrs.append('skip_serializing_if = "Option::is_none"')
                attrs.append('deserialize_with = "crate::serde_helpers::optional_non_nullable"')
            else:
                ft = f"Option<Option<{t}>>"
                attrs += ["default", 'skip_serializing_if = "Option::is_none"',
                          'deserialize_with = "crate::serde_helpers::double_option"']
            name = ident(key)
            while name in used:
                name += "_"
            used.add(name)
            if name != key:
                attrs.insert(0, f"rename = {json.dumps(key)}")
            o.append(doc(prop.get("description"), "    "))
            o.append(f"    /// Source: {self.pointers[id(prop)]}\n")
            if attrs:
                o.append(f"    #[serde({', '.join(attrs)})]\n")
            o.append(f"    pub {name}: {ft},\n")
            fields.append({"pointer": self.pointers[id(prop)], "name": name, "type": ft, "json_name": key})
        o.append("}\n\n")
        self.provenance.append({"pointer": self.pointers[id(s)], "type": tn,
            "file": "sdks/rust/src/generated/models.rs", "fields": fields})
        o.append(f"impl {tn} {{\n    pub fn validate(&self) -> Result<(), String> {{\n")
        for key, prop in s["properties"].items():
            lines = self.rules(prop, "value", tn + pascal(key))
            if not lines:
                continue
            name = ident(key)
            _, nullable = self.ty(prop, "", tn + pascal(key))
            optional = key not in required
            if optional or nullable:
                pattern = "Some(Some(value))" if optional and nullable else "Some(value)"
                o.append(f"        if let {pattern} = &self.{name} {{\n")
                o.extend("            " + line + "\n" for line in lines)
                o.append("        }\n")
            else:
                o.append(f"        {{ let value = &self.{name};\n")
                o.extend("            " + line + "\n" for line in lines)
                o.append("        }\n")
        o.append("        Ok(())\n    }\n}\n\n")
        return "".join(o)

    # ---- admin operations --------------------------------------------------
    def operations(self):
        ops = []
        for path, item in self.spec["paths"].items():
            for method, op in item.items():
                ops.append((op["operationId"], method.upper(), path, op))
        ops.sort()
        return ops

    def resolve_param(self, p):
        if "$ref" in p:
            return self.spec["components"]["parameters"][p["$ref"].split("/")[-1]]
        return p

    def inline_ty(self, schema, hint):
        """Type for a schema that utoipa inlined into an envelope (data / items)."""
        return self.ty(schema, "models::", hint + "Data")

    def response_type(self, op):
        for status in ("200", "201", "202"):
            r = op["responses"].get(status)
            if r:
                break
        else:
            raise SystemExit(f"{op['operationId']}: no success response")
        schema = r["content"]["application/json"]["schema"]
        if "$ref" in schema:
            name = schema["$ref"].split("/")[-1]
            if name.startswith(("ApiResponse_", "PagedResponse_")):
                hint = name.split("_", 1)[1]
                t, nullable = self.inline_ty(self.S[name]["properties"]["data"], hint)
                return (f"Option<{t}>" if nullable else t), False
            if name in self.S:
                t, nullable = self.ty(schema, "models::")
                return (f"Option<{t}>" if nullable else t), True
        t, nullable = self.ty(schema, "models::")
        return (f"Option<{t}>" if nullable else t), True

    def admin(self, digest):
        out = [
            "// @generated by tools/gen.py from sdks/contracts/openapi.json. DO NOT EDIT.\n",
            "#![allow(unused_variables, clippy::too_many_arguments)]\n",
            "use reqwest::Method;\n\n",
            "use super::models;\nuse crate::admin::AdminClient;\nuse crate::client::{Call, Response, RetryClass};\n",
            "use crate::config::CallOptions;\nuse crate::error::Result;\n\n",
            f'/// sha256 of the OpenAPI document this file was generated from.\npub const OPENAPI_SHA256: &str = "{digest}";\n\n',
        ]
        ops = self.operations()
        out.append("/// `(operationId, method, path)` for every generated operation.\n")
        out.append("pub const OPERATIONS: &[(&str, &str, &str)] = &[\n")
        for opid, method, path, _ in ops:
            out.append(f'    ("{opid}", "{method}", "{path}"),\n')
        out.append("];\n\n")
        impl = ["impl AdminClient {\n"]
        for opid, method, path, op in ops:
            params = [self.resolve_param(p) for p in op.get("parameters", [])]
            fields, used = [], {"body"}
            segs = []
            for seg in [s for s in path.split("/") if s]:
                if seg.startswith("{"):
                    pname = seg.strip("{}")
                    prm = next(p for p in params if p["name"] == pname and p["in"] == "path")
                    t, _ = self.ty(prm["schema"], "models::")
                    f = ident(pname)
                    while f in used:
                        f += "_"
                    used.add(f)
                    fields.append(("path", pname, f, t, True, prm.get("description")))
                    segs.append(f"args.{f}.to_string()")
                else:
                    segs.append(f'"{seg}".to_owned()')
            for prm in params:
                if prm["in"] == "header":
                    if prm["name"] not in ("Idempotency-Key", "If-Match"):
                        raise SystemExit(f"{opid}: unsupported header {prm['name']}")
                elif prm["in"] == "query":
                    t, nullable = self.ty(prm["schema"], "models::")
                    f = ident(prm["name"])
                    while f in used:
                        f += "_"
                    used.add(f)
                    is_vec = t.startswith("Vec<")
                    req = bool(prm.get("required")) and not nullable
                    fields.append(("query", prm["name"], f, t, req, prm.get("description"), is_vec))
            body = op.get("requestBody")
            body_t = body_req = None
            if body:
                body_t, nullable = self.ty(body["content"]["application/json"]["schema"], "models::")
                body_req = bool(body.get("required")) and not nullable

            args = pascal(opid) + "Args"
            # struct
            primitives = {"String", "i32", "i64", "u32", "u64", "u8", "u16", "bool", "f64", VALUE}
            can_default = True
            decl = [doc(f"Arguments for `{opid}`."), "#[derive(Debug, Clone", ]
            lines = []
            for fld in fields:
                kind, wire, f, t, req = fld[:5]
                is_vec = len(fld) > 6 and fld[6]
                if is_vec:
                    ft = t
                elif req:
                    ft = t
                    can_default &= t in primitives
                else:
                    ft = f"Option<{t}>"
                lines.append(doc(fld[5], "    ") + f"    pub {f}: {ft},\n")
            if body_t:
                if body_req:
                    lines.append(f"    /// Request body.\n    pub body: {body_t},\n")
                    can_default = False
                else:
                    lines.append(f"    /// Request body (optional).\n    pub body: Option<{body_t}>,\n")
            decl[1] += ", Default)]\n" if can_default else ")]\n"
            out.append("".join(decl) + f"pub struct {args} {{\n" + "".join(lines) + "}\n\n")

            rt, bare = self.response_type(op)
            paged = any(response.get("content", {}).get("application/json", {}).get("schema", {}).get("$ref", "").startswith("#/components/schemas/PagedResponse_")
                        for status, response in op["responses"].items() if status.startswith("2"))
            result_name = pascal(opid) + "Result"
            out.append(f"/// Result for `{opid}`.\npub type {result_name} = Response<{rt}>;\n\n")
            klass = "RetryClass::Read" if (method == "GET" or (method == "POST" and "authz" in (op.get("tags") or []))) else "RetryClass::Write"
            summary = op.get("summary") or op.get("description") or opid
            impl.append(doc(summary, "    "))
            impl.append(f"    ///\n    /// `{method} {path}`\n")
            impl.append(f"    pub async fn {ident(opid)}(&self, args: {args}, opts: &CallOptions) -> Result<{result_name}> {{\n")
            needs_mut = bool(body_t) or bare or paged or any(f[0] == "query" for f in fields)
            impl.append(f"        let {'mut ' if needs_mut else ''}call = Call::new(Method::{method}, vec![{', '.join(segs)}]).class({klass});\n")
            for fld in fields:
                if fld[0] != "query":
                    continue
                kind, wire, f, t, req = fld[:5]
                is_vec = len(fld) > 6 and fld[6]
                if is_vec:
                    impl.append(f"        for value in &args.{f} {{\n            call = call.query({json.dumps(wire)}, value);\n        }}\n")
                elif req:
                    impl.append(f"        call = call.query({json.dumps(wire)}, &args.{f});\n")
                else:
                    impl.append(f"        if let Some(value) = &args.{f} {{\n            call = call.query({json.dumps(wire)}, value);\n        }}\n")
            if body_t:
                if body_req:
                    impl.append("        call = call.json(&args.body)?;\n")
                else:
                    impl.append("        if let Some(body) = &args.body {\n            call = call.json(body)?;\n        }\n")
            if paged:
                impl.append("        call = call.paged();\n")
            if bare:
                impl.append("        call = call.bare();\n")
            impl.append("        self.client.execute(call, opts).await\n    }\n\n")
        impl.append("}\n")
        out.append("".join(impl))
        return "".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", nargs="?", default=str(Path(__file__).resolve().parents[2] / "contracts/openapi.json"))
    parser.add_argument("out", nargs="?", default=str(Path(__file__).resolve().parents[1] / "src/generated"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = Path(args.spec).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    gen = Gen(json.loads(raw))
    admin = gen.admin(digest)  # first: it registers inlined envelope payload structs
    targets = {"admin.rs": admin, "models.rs": gen.models()}
    targets["model_tests.rs"] = "// Generated model registry; the sample oracle is independently implemented in tests/model_semantics.rs.\n#[test]\nfn every_component_model_roundtrips() {\n    let document = spec();\n" + "".join(
        f'    roundtrip::<models::{pascal(name)}>(&document, {json.dumps("/components/schemas/" + name)});\n'
        for name in sorted(gen.S)) + "}\n"
    targets["model_tests.rs"] += "\n#[test]\nfn every_open_json_exception_roundtrips() {\n    let document = spec();\n" + "".join(
        f'    exception_roundtrip::<models::{pascal(pointer.split("/")[3])}>(&document, {json.dumps(pointer)});\n'
        for pointer in sorted(gen.exceptions)) + "}\n"
    targets["models.provenance.json"] = json.dumps({"schemas": gen.provenance,
        "dynamicExceptions": list(gen.exceptions.values())}, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with tempfile.TemporaryDirectory(prefix="authgebra-rust-generated-") as directory:
        for name, text in targets.items():
            candidate = Path(directory) / name
            candidate.write_text(text, encoding="utf-8")
            if name.endswith(".rs"):
                subprocess.run(["rustfmt", "--edition", "2024", str(candidate)], check=True)
            destination = Path(args.out) / name
            if args.check:
                if not destination.exists() or destination.read_bytes() != candidate.read_bytes():
                    raise SystemExit("generated artifact differs: " + str(destination))
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(candidate.read_bytes())
    print(f"generated {len(gen.operations())} operations, sha256 {digest}")


if __name__ == "__main__":
    main()
