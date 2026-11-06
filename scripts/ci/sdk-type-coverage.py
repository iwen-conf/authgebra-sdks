#!/usr/bin/env python3
"""Independently bind reviewed OpenAPI fields to declarations in five generated SDKs.

No generator is imported. Manifests provide candidate locations; actual source declarations,
JSON names, schema shapes and open-JSON exceptions are checked against the reviewed document.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = {
    "go": "sdks/go/models.provenance.json",
    "python": "sdks/python/src/authgebra/_generated/provenance.json",
    "zig": "sdks/zig/src/models.provenance.json",
    "java": "sdks/java/src/main/java/com/authgebra/client/models.provenance.json",
    "rust": "sdks/rust/src/generated/models.provenance.json",
}
OPERATION_FILES = {
    "go": "sdks/go/ops_gen.go", "python": "sdks/python/src/authgebra/_generated/sync.py",
    "zig": "sdks/zig/src/admin_ops.zig", "java": "sdks/java/src/main/java/com/authgebra/client/AdminClient.java",
    "rust": "sdks/rust/src/generated/admin.rs",
}
EXCEPTION_TEST_FILES = {
    "go": "sdks/go/model_semantics_test.go",
    "python": "sdks/python/tests/test_typed_models.py",
    "zig": "sdks/zig/src/model_roundtrip_test.zig",
    "java": "sdks/java/src/test/java/com/authgebra/client/ModelSemanticsTest.java",
    "rust": "sdks/rust/tests/model_semantics.rs",
}
CONSTRAINTS = (
    "type", "format", "default", "enum", "const", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern", "minItems",
    "maxItems", "uniqueItems", "minProperties", "maxProperties", "additionalProperties",
    "readOnly", "writeOnly", "deprecated", "x-authgebra-clearable", "x-authgebra-normalized-value",
)
SOURCE = re.compile(r"Source:\s*(#[^\s]+)")
DYNAMIC = re.compile(r"\b(?:Any|any|interface\s*\{\s*\}|RawMessage|JsonNode|JSONValue|JsonValue|serde_json::Value|std\.json\.Value)\b")
SCHEMA_ANNOTATIONS = frozenset({"description", "title", "default", "example", "examples", "deprecated", "readOnly", "writeOnly", "$comment", "externalDocs", "xml"})
# This is the independently admitted generator vocabulary, not every possible JSON Schema keyword.
# A new assertion needs generator support and its own evidence before coverage can pass.
SUPPORTED_SCHEMA_KEYS = SCHEMA_ANNOTATIONS | frozenset({
    "$ref", "type", "properties", "required", "additionalProperties", "propertyNames", "items",
    "oneOf", "anyOf", "allOf", "enum", "format", "minimum", "maximum", "minLength", "maxLength",
    "pattern", "minItems", "maxItems", "uniqueItems",
})


def escaped(value):
    return value.replace("~", "~0").replace("/", "~1")


def resolve(document, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("#/"):
        raise ValueError("only document-local JSON pointers are accepted: " + str(pointer))
    value = document
    try:
        for part in pointer[2:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("unresolved JSON pointer: " + pointer) from None
    return value


def dereference(document, schema, seen=()):
    reference = schema.get("$ref")
    if reference is None:
        return schema
    if reference in seen:
        raise ValueError("cyclic reference cannot be flattened: " + reference)
    return {**dereference(document, resolve(document, reference), seen + (reference,)),
            **{key: value for key, value in schema.items() if key != "$ref"}}


def nullable(document, schema):
    schema = dereference(document, schema)
    kind = schema.get("type")
    if kind is not None and kind != "null" and not (isinstance(kind, list) and "null" in kind):
        return False
    if "const" in schema and schema["const"] is not None:
        return False
    if "enum" in schema and None not in schema["enum"]:
        return False
    if "oneOf" in schema and sum(nullable(document, arm) for arm in schema["oneOf"]) != 1:
        return False
    if "anyOf" in schema and not any(nullable(document, arm) for arm in schema["anyOf"]):
        return False
    if "allOf" in schema and not all(nullable(document, arm) for arm in schema["allOf"]):
        return False
    return True


def unconstrained(document, schema):
    schema = dereference(document, schema)
    return all(key in SCHEMA_ANNOTATIONS or key.startswith("x-") for key in schema)


def supported_schema(schema, pointer):
    unsupported = [key for key in schema if key not in SUPPORTED_SCHEMA_KEYS and not key.startswith("x-")]
    if unsupported:
        raise ValueError(f"unsupported schema assertions at {pointer}: {unsupported}")
    if "allOf" in schema and len(schema["allOf"]) != 1:
        raise ValueError("unsupported multi-arm allOf at " + pointer)
    if "$ref" in schema and any(key != "$ref" and key not in SCHEMA_ANNOTATIONS and not key.startswith("x-") for key in schema):
        raise ValueError("unsupported assertions beside $ref at " + pointer)
    if not any(key in schema for key in ("type", "$ref", "oneOf", "anyOf", "allOf")) and not unconstrained({}, schema):
        raise ValueError("unsupported assertions without a structural type at " + pointer)
    if "enum" in schema and (schema.get("type") != "string" or not schema["enum"] or not all(isinstance(value, str) for value in schema["enum"])):
        raise ValueError("unsupported non-string enum at " + pointer)
    if "propertyNames" in schema:
        names = schema["propertyNames"]
        if not isinstance(names, dict) or names.get("type") != "string" or any(key != "type" and key not in SCHEMA_ANNOTATIONS for key in names):
            raise ValueError("unsupported propertyNames assertions at " + pointer)
    if schema.get("type") == "object" and "properties" not in schema and (schema.get("additionalProperties") is False or "required" in schema):
        raise ValueError("unsupported closed or required open-object schema at " + pointer)


def permits_dynamic(document, schema):
    schema = dereference(document, schema)
    try:
        supported_schema(schema, "dynamic exception")
    except ValueError:
        return False
    if unconstrained(document, schema):
        return True
    if schema.get("type") == "array":
        return permits_dynamic(document, schema.get("items", {}))
    kind = schema.get("type")
    if kind == "object" or isinstance(kind, list) and "object" in kind:
        additional = schema.get("additionalProperties")
        return not schema.get("properties") and (additional is None or additional is True or isinstance(additional, dict) and permits_dynamic(document, additional))
    return False


def canonical(document, schema):
    def clean(value):
        if isinstance(value, dict):
            value = dereference(document, value) if "$ref" in value else value
            return {key: clean(child) for key, child in value.items() if key not in ("description", "title", "example", "examples")}
        if isinstance(value, list):
            return [clean(child) for child in value]
        return value
    return json.dumps(clean(schema), sort_keys=True, separators=(",", ":"))


def inventory(document):
    schemas, fields, operations = {}, {}, []

    def collect(pointer, schema):
        if not isinstance(schema, dict):
            raise ValueError("schema is not an object: " + pointer)
        supported_schema(schema, pointer)
        schemas[pointer] = schema
        for name, child in sorted(schema.get("properties", {}).items()):
            child_pointer = pointer + "/properties/" + escaped(name)
            fields[child_pointer] = {
                "json_name": name, "parent": pointer, "required": name in schema.get("required", []),
                "nullable": nullable(document, child),
                "constraints": {key: child[key] for key in CONSTRAINTS if key in child},
            }
            collect(child_pointer, child)
        for key in ("items", "additionalProperties"):
            if isinstance(schema.get(key), dict):
                collect(pointer + "/" + key, schema[key])
        for key in ("oneOf", "anyOf", "allOf"):
            for index, child in enumerate(schema.get(key, [])):
                collect(f"{pointer}/{key}/{index}", child)

    for name, schema in sorted(document["components"]["schemas"].items()):
        collect("#/components/schemas/" + escaped(name), schema)
    for path, item in sorted(document["paths"].items()):
        for method, operation in sorted(item.items()):
            if method not in ("get", "post", "put", "patch", "delete", "head", "options", "trace"):
                continue
            base = "#/paths/" + escaped(path) + "/" + method
            row = {"operationId": operation["operationId"], "method": method.upper(), "path": path,
                   "request": None, "responses": [], "parameters": operation.get("parameters", [])}
            body = operation.get("requestBody")
            if body is not None:
                for media, value in sorted(body.get("content", {}).items()):
                    pointer = base + "/requestBody/content/" + escaped(media) + "/schema"
                    collect(pointer, value["schema"])
                    row["request"] = {"pointer": pointer, "ref": value["schema"].get("$ref"), "required": body.get("required", False), "media_type": media}
            for status, response in sorted(operation.get("responses", {}).items()):
                for media, value in sorted(response.get("content", {}).items()):
                    pointer = base + "/responses/" + status + "/content/" + escaped(media) + "/schema"
                    collect(pointer, value["schema"])
                    row["responses"].append({"status": status, "pointer": pointer, "ref": value["schema"].get("$ref"), "media_type": media})
            operations.append(row)

    def check_refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                resolve(document, value["$ref"])
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)
    check_refs(document)
    ids = [operation["operationId"] for operation in operations]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate operationId")
    return schemas, fields, sorted(operations, key=lambda row: row["operationId"])


def source_fields(language, source, filename):
    """Read actual declarations, independently of generator manifest type claims."""
    result = {}
    if language == "python":
        tree = ast.parse(source)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            pointer, names = None, None
            annotations = {}
            for member in node.body:
                if not isinstance(member, ast.AnnAssign) or not isinstance(member.target, ast.Name):
                    continue
                if member.target.id == "__schema_pointer__":
                    pointer = ast.literal_eval(member.value)
                elif member.target.id == "__json_fields__":
                    names = ast.literal_eval(member.value)
                else:
                    annotations[member.target.id] = (ast.unparse(member.annotation), member.lineno)
            if pointer is not None and names is not None:
                for json_name, name in names.items():
                    if name not in annotations:
                        raise ValueError(f"{filename}: missing declared Python field {node.name}.{name}")
                    typ, line = annotations[name]
                    result[pointer + "/properties/" + escaped(json_name)] = {"type": typ, "name": name, "json_name": json_name, "line": line, "file": filename, "model": node.name}
        return result
    lines = source.splitlines()
    for index, line in enumerate(lines):
        match = SOURCE.search(line)
        if not match:
            continue
        pointer = match.group(1)
        if "/properties/" not in pointer:
            continue
        for offset, declaration in enumerate(lines[index + 1:index + 14], 1):
            if SOURCE.search(declaration):
                break
            found = None
            if language == "go":
                found = re.search(r'^\s*(\w+)\s+([^`]+?)\s+`json:"([^",]+)', declaration)
                if found:
                    name, typ, json_name = found.groups()
            elif language == "zig":
                found = re.search(r'^\s*(@"[^"]+"|\w+):\s*(.+),\s*$', declaration)
                if found:
                    name, typ = found.groups()
                    typ = typ.split(" = ", 1)[0]
                    json_name = name[2:-1] if name.startswith('@"') else name
            elif language == "java":
                found = re.search(r'@JsonProperty\((?:value\s*=\s*)?"([^"]+)"(?:,\s*required\s*=\s*(true|false))?\)\s+(.+?)\s+(\w+)[,)]?\s*$', declaration)
                if found:
                    json_name, required, typ, name = found.groups()
            elif language == "rust":
                found = re.search(r'^\s*(?:pub\s+)?(\w+):\s*(?!:)(.*)$', declaration)
                if found:
                    name, typ = found.groups()
                    type_lines = [typ]
                    depth = typ.count("<") - typ.count(">")
                    next_index = index + offset + 1
                    while depth > 0 or not type_lines[-1].rstrip().endswith(","):
                        if next_index >= len(lines) or SOURCE.search(lines[next_index]):
                            raise ValueError(f"{filename}:{index + offset + 1}: incomplete Rust field type")
                        continuation = lines[next_index].strip()
                        type_lines.append(continuation)
                        depth += continuation.count("<") - continuation.count(">")
                        next_index += 1
                    typ = " ".join(type_lines).rstrip().removesuffix(",")
                    json_name = pointer.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
                    attributes = "\n".join(lines[index + 1:index + offset])
                    renamed = re.search(r'rename\s*=\s*"([^"]+)"', attributes)
                    if renamed:
                        json_name = renamed.group(1)
                    elif name != json_name and not (name.endswith("_") and name[:-1] == json_name):
                        raise ValueError(f"{filename}:{index + offset + 1}: Rust JSON field name mismatch")
            if found:
                result[pointer] = {"type": typ.strip(), "name": name, "json_name": json_name,
                                   "line": index + offset + 1, "file": filename}
                if language == "java":
                    result[pointer]["required"] = required == "true"
                break
    return result


def python_nullable(expression, aliases, seen=()):
    node = ast.parse(expression, mode="eval").body

    def accepts_null(value):
        if isinstance(value, ast.Constant):
            return value.value is None or isinstance(value.value, str) and python_nullable(value.value, aliases, seen)
        if isinstance(value, ast.Name):
            if value.id == "JSONValue":
                return True
            if value.id in aliases and value.id not in seen:
                return python_nullable(aliases[value.id], aliases, seen + (value.id,))
            return False
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
            return accepts_null(value.left) or accepts_null(value.right)
        if isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name):
            if value.value.id == "Optional":
                return True
            if value.value.id == "Union":
                arms = value.slice.elts if isinstance(value.slice, ast.Tuple) else [value.slice]
                return any(accepts_null(arm) for arm in arms)
        return False
    return accepts_null(node)


def virtual_discriminator(language, document, pointer, models, sources):
    """A tagged union's tag may be supplied by serde/Jackson rather than a stored field."""
    if language not in ("java", "rust") or "/oneOf/" not in pointer:
        return None
    schema = resolve(document, pointer)
    values = schema.get("enum", [])
    if len(values) != 1:
        return None
    parent, _index = pointer.rsplit("/properties/", 1)
    union_pointer = parent.rsplit("/oneOf/", 1)[0]
    union = models.get(union_pointer)
    branch = models.get(parent)
    if union is None or branch is None:
        return None
    name, value = _index.replace("~1", "/").replace("~0", "~"), values[0]
    union_type = union.get("type", union.get("go_type"))
    branch_type = branch.get("type", branch.get("go_type"))
    filename = union["file"]
    source = sources[filename]
    if language == "java":
        declaration = re.search(r"public sealed interface\s+" + re.escape(union_type) + r"\b", source)
        if not declaration:
            return None
        annotations = source[max(0, declaration.start() - 1800):declaration.start()]
        if not re.search(r'@JsonTypeInfo\([^\n]*property\s*=\s*"' + re.escape(name) + '"', annotations):
            return None
        if not re.search(r'@JsonSubTypes.Type\(value\s*=\s*' + re.escape(branch_type) + r'\.class,\s*name\s*=\s*"' + re.escape(value) + '"', annotations):
            return None
    else:
        declaration = re.search(r"pub enum\s+" + re.escape(union_type) + r"\b", source)
        if not declaration:
            return None
        annotations = source[max(0, declaration.start() - 300):declaration.start()]
        if not re.search(r'#\[serde\(tag\s*=\s*"' + re.escape(name) + '"', annotations):
            return None
        variant = branch_type.split("::")[-1]
        body = source[declaration.end():declaration.end() + 6000]
        if not re.search(r'#\[serde\(rename\s*=\s*"' + re.escape(value) + r'"\)\]\s*' + re.escape(variant) + r"\b", body):
            return None
    return {"type": branch_type + " discriminator", "name": name, "json_name": name,
            "file": filename, "line": source[:declaration.start()].count("\n") + 1,
            "discriminator": value}


def manifest_rows(language, manifest):
    models, fields = {}, {}
    for model in manifest.get("models", manifest.get("schemas", [])):
        pointer = model.get("pointer", model.get("schema_pointer"))
        if pointer is None:
            continue
        models[pointer] = model
        for field in model.get("fields", []):
            row = {**field, "file": model["file"], "model": model.get("type", model.get("go_type"))}
            row["pointer"] = field.get("pointer", field.get("schema_pointer"))
            fields[row["pointer"]] = row
    for field in manifest.get("fields", []):
        fields[field.get("pointer", field.get("schema_pointer"))] = field
    return models, fields


def operation_bindings(root, document, operations, language, models, fields):
    filename = OPERATION_FILES[language]
    source = (root / filename).read_text(encoding="utf-8")
    result = {}
    python_methods, python_async_methods, python_responses = {}, {}, {}
    go_aliases = {}
    if language == "go":
        model_source = (root / "sdks/go/models_gen.go").read_text(encoding="utf-8")
        go_aliases = dict(re.findall(r"(?m)^type\s+(\w+)\s*=\s*([^\n]+)", model_source))
    if language == "python":
        python_methods = {node.name: node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef)}
        async_source = (root / "sdks/python/src/authgebra/_generated/aio.py").read_text(encoding="utf-8")
        python_async_methods = {node.name: node for node in ast.walk(ast.parse(async_source)) if isinstance(node, ast.AsyncFunctionDef)}
        model_source = (root / "sdks/python/src/authgebra/_generated/models.py").read_text(encoding="utf-8")
        python_responses = {node.name: node for node in ast.parse(model_source).body if isinstance(node, ast.ClassDef)}
    shape_types = {}
    for pointer, row in models.items():
        type_name = row.get("type", row.get("go_type"))
        shape_types.setdefault(canonical(document, resolve(document, pointer)), set()).add(type_name)
    ignored = {"Option", "Optional", "Union", "Vec", "List", "Map", "HashMap", "BTreeMap", "String",
               "str", "bool", "boolean", "int", "long", "float", "double", "i32", "i64", "u32", "u64",
               "list", "dict", "None", "Unset", "models", "Models", "std", "collections", "string"}
    for operation in operations:
        identifier = operation["operationId"]
        parts = identifier.split("_")
        camel = parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
        pascal = "".join(part[:1].upper() + part[1:] for part in parts)
        if language == "python":
            node = python_methods.get(identifier)
            if node is None:
                raise ValueError(f"python: operation method absent: {identifier}")
            async_node = python_async_methods.get(identifier)
            if async_node is None or ast.unparse(async_node.returns) != ast.unparse(node.returns):
                raise ValueError(f"python: asynchronous response signature differs: {identifier}")
            sync_args = {arg.arg: ast.unparse(arg.annotation) for arg in node.args.kwonlyargs}
            async_args = {arg.arg: ast.unparse(arg.annotation) for arg in async_node.args.kwonlyargs}
            if sync_args != async_args:
                raise ValueError(f"python: asynchronous parameter signature differs: {identifier}")
            declaration = ast.get_source_segment(source, node)
        elif language == "go":
            mapping = re.search(r'"' + re.escape(identifier) + r'"\s*:\s*"([^"]+)"', source)
            if not mapping:
                raise ValueError(f"go: operation method mapping absent: {identifier}")
            name = mapping.group(1).split(".")[-1]
            match = re.search(r"func \([^\n]+\) " + re.escape(name) + r"\([^\n]+", source)
            if not match:
                raise ValueError(f"go: operation method absent: {identifier}")
            declaration = source[match.start():source.find("\n}", match.end()) + 2]
            alias = re.search(r"type " + re.escape(name) + r"Result\s*=.*", source)
            if alias:
                declaration += "\n" + alias.group(0)
        elif language == "zig":
            match = re.search(r"pub fn " + re.escape(camel) + r"\(", source)
            if not match:
                raise ValueError(f"zig: operation method absent: {identifier}")
            declaration = source[match.start():source.find("\n    }\n", match.end())]
        elif language == "java":
            match = re.search(r"public [^\n]+ " + re.escape(camel) + r"_?\([^\n]+", source)
            if not match:
                raise ValueError(f"java: operation method absent: {identifier}")
            declaration = source[match.start():source.find("\n        }", match.end())]
        elif language == "rust":
            match = re.search(r"pub async fn " + re.escape(identifier) + r"\(", source)
            if not match:
                raise ValueError(f"rust: operation method absent: {identifier}")
            declaration = source[match.start():source.find("\n    }", match.end())]
            args = re.search(r"args:\s*(\w+)", declaration)
            if args:
                args_type = re.search(r"pub struct " + re.escape(args.group(1)) + r"\s*\{", source)
                if args_type:
                    declaration += source[args_type.start():source.find("\n}", args_type.end())]
            alias = re.search(r"pub type " + re.escape(pascal) + r"Result\s*=.*", source)
            if alias:
                declaration += "\n" + alias.group(0)
        else:
            raise ValueError(f"unsupported SDK language: {language}")
        if language == "go":
            expanded = set()
            while True:
                next_aliases = {name for name in go_aliases if name not in expanded and re.search(r"\b" + re.escape(name) + r"\b", declaration)}
                if not next_aliases:
                    break
                for name in sorted(next_aliases):
                    declaration += "\n" + go_aliases[name]
                expanded.update(next_aliases)
        method, path = re.escape(operation["method"]), re.escape(operation["path"])
        if language == "go":
            wire = re.search(r'ID:\s*"' + re.escape(identifier) + r'",\s*Method:\s*"' + method + r'",\s*Path:\s*"' + path + '"', source)
        elif language == "zig":
            wire = re.search(r'\.id\s*=\s*"' + re.escape(identifier) + r'",\s*\.method\s*=\s*"' + method + r'",\s*\.path\s*=\s*"' + path + '"', source)
        elif language == "rust":
            wire = re.search(r'"' + re.escape(identifier) + r'",\s*"' + method + r'",\s*"' + path + '"', source)
        elif language == "java":
            wire = re.search(r'new Http.Operation\("' + re.escape(identifier) + r'",\s*"' + method + r'",\s*"' + path + '"', source)
        elif language == "python":
            wire = re.search(r'["\']' + method + '["\']', declaration) and re.search(r'["\']' + path + '["\']', declaration)
        else:
            raise ValueError(f"unsupported SDK language: {language}")
        if not wire:
            raise ValueError(f"{language}: operation HTTP method/path mapping differs: {identifier}")
        request_type = None
        if operation["request"] is not None:
            pointer = operation["request"]["ref"] or operation["request"]["pointer"]
            row = models[pointer]
            request_type = row.get("type", row.get("go_type"))
            if not re.search(r"\b" + re.escape(request_type) + r"\b", declaration):
                raise ValueError(f"{language}: actual operation request type differs: {identifier} expected {request_type}")
        responses = []
        for response in operation["responses"]:
            if not response["status"].startswith("2"):
                continue
            pointer = response["ref"] or response["pointer"]
            schema = dereference(document, resolve(document, pointer))
            data_pointer = pointer + "/properties/data"
            if "data" in schema.get("properties", {}):
                payload_type = fields[data_pointer]["type"]
            else:
                row = models[pointer]
                payload_type = row.get("type", row.get("go_type"))
            candidates = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", payload_type)) - ignored
            leaf = dereference(document, resolve(document, data_pointer if "data" in schema.get("properties", {}) else pointer))
            while leaf.get("type") == "array":
                leaf = dereference(document, leaf["items"])
            equivalent_types = shape_types.get(canonical(document, leaf), set())
            if language == "python":
                returns = ast.unparse(node.returns)
                response_name = returns.split(".")[-1]
                cls = python_responses.get(response_name)
                if cls is None:
                    raise ValueError(f"python: response class absent for {identifier}")
                payload = next((item for item in cls.body if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id == "data"), None)
                actual_payload = ast.unparse(payload.annotation) if payload else ""
            else:
                actual_payload = declaration
            named_match = any(re.search(r"\b" + re.escape(name.split("::")[0]) + r"\b", actual_payload) for name in equivalent_types)
            if candidates and not named_match and not all(re.search(r"\b" + re.escape(candidate) + r"\b", actual_payload) for candidate in candidates):
                raise ValueError(f"{language}: actual operation response type differs: {identifier} expected {payload_type}")
            responses.append({"status": response["status"], "schema_pointer": pointer, "payload_type": payload_type})
        result[identifier] = {"file": filename, "request_type": request_type, "responses": responses}
    return result


def check_language(root, document, schemas, expected_fields, language, operations=None):
    manifest_path = root / MANIFESTS[language]
    if not manifest_path.is_file():
        raise ValueError(f"{language}: missing provenance manifest {manifest_path.relative_to(root)}")
    manifest = json.loads(manifest_path.read_bytes())
    models, claims = manifest_rows(language, manifest)
    files = {row["file"] for row in models.values()} | {row["file"] for row in claims.values()}
    actual, source_texts, python_aliases, python_descriptors = {}, {}, {}, {}
    for filename in sorted(files):
        if language == "go" and "/" not in filename:
            filename = "sdks/go/" + filename
        if Path(filename).is_absolute() or ".." in Path(filename).parts:
            raise ValueError(f"{language}: source path escapes repository: {filename}")
        path = root / filename
        if not path.is_file():
            raise ValueError(f"{language}: source file absent: {filename}")
        source = path.read_text(encoding="utf-8")
        source_texts[filename] = source
        actual.update(source_fields(language, source, filename))
        if language == "python":
            for node in ast.parse(source).body:
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                    python_aliases[node.target.id] = ast.unparse(node.value)
                    if node.target.id == "SCHEMAS":
                        python_descriptors.update(ast.literal_eval(node.value))
                elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "SCHEMAS" for target in node.targets):
                    python_descriptors.update(ast.literal_eval(node.value))
    if language == "python":
        for pointer, schema in schemas.items():
            target = schema.get("$ref", pointer)
            descriptor = python_descriptors.get(target)
            if descriptor is None:
                raise ValueError(f"python: actual runtime schema descriptor absent: {pointer}")
            expected = dereference(document, schema)
            for key in CONSTRAINTS + ("required",):
                if key == "additionalProperties":
                    left, right = expected.get(key), descriptor.get(key)
                    if isinstance(left, dict) and isinstance(right, dict):
                        continue
                elif expected.get(key) == descriptor.get(key):
                    continue
                else:
                    raise ValueError(f"python: actual runtime constraint differs at {pointer}: {key}")
                if expected.get(key) != descriptor.get(key):
                    raise ValueError(f"python: actual runtime constraint differs at {pointer}: {key}")
    for pointer in list(models) + list(claims):
        resolve(document, pointer)
    # A generated envelope may reuse a component instead of duplicating identical fields.
    parents_by_shape = {}
    for pointer, schema in schemas.items():
        if "properties" in schema and any(field.startswith(pointer + "/properties/") for field in actual):
            parents_by_shape.setdefault(canonical(document, schema), []).append(pointer)
    exceptions = manifest.get("dynamic_exceptions", manifest.get("dynamicExceptions", []))
    checked_exceptions = []
    for exception in exceptions:
        pointer = exception.get("pointer", exception.get("schema_pointer"))
        schema = resolve(document, pointer)
        if not permits_dynamic(document, schema):
            raise ValueError(f"{language}: dynamic exception masks a known schema: {pointer}")
        if not exception.get("reason") or not exception.get("test"):
            raise ValueError(f"{language}: dynamic exception needs a reason and test: {pointer}")
        test_file = EXCEPTION_TEST_FILES[language]
        test_source = (root / test_file).read_text(encoding="utf-8")
        test_name = exception["test"]
        if language == "zig":
            found = re.search(r'test\s+"' + re.escape(test_name) + '"', test_source)
        else:
            test_name = test_name.split("::")[-1].split(".")[-1]
            prefix = r"def\s+" if language == "python" else r"func\s+" if language == "go" else r"fn\s+" if language == "rust" else r"\b"
            found = re.search(prefix + re.escape(test_name) + r"\s*\(", test_source)
        if not found:
            raise ValueError(f"{language}: declared dynamic exception test is absent: {pointer}")
        checked_exceptions.append({**exception, "test_file": test_file, "test_line": test_source[:found.start()].count("\n") + 1})
    bindings = {}
    for pointer, expected in expected_fields.items():
        source_pointer = pointer
        declaration = actual.get(pointer)
        if declaration is None:
            declaration = virtual_discriminator(language, document, pointer, models, source_texts)
        if declaration is None:
            parent = expected["parent"]
            shape = canonical(document, schemas[parent])
            for equivalent in parents_by_shape.get(shape, []):
                source_pointer = equivalent + "/properties/" + escaped(expected["json_name"])
                if source_pointer in actual:
                    declaration = actual[source_pointer]
                    break
        if declaration is None:
            raise ValueError(f"{language}: no actual field declaration for {pointer}")
        if declaration["json_name"] != expected["json_name"]:
            raise ValueError(f"{language}: JSON name differs at {pointer}: {declaration['json_name']}")
        if language == "java" and "required" in declaration and declaration["required"] != expected["required"]:
            raise ValueError(f"java: required annotation differs: {pointer}")
        schema = schemas[pointer]
        if DYNAMIC.search(declaration["type"]) and not permits_dynamic(document, schema):
            raise ValueError(f"{language}: known field downgraded to dynamic {declaration['type']}: {pointer}")
        claim = claims.get(source_pointer)
        if claim is not None:
            for key in ("required", "nullable"):
                if key in claim and claim[key] != expected[key]:
                    raise ValueError(f"{language}: manifest {key} differs from reviewed schema: {pointer}")
        if language == "python":
            optional = "Unset" in declaration["type"]
            if optional == expected["required"]:
                raise ValueError(f"python: required/absent type differs: {pointer}")
            if python_nullable(declaration["type"], python_aliases) != expected["nullable"]:
                raise ValueError(f"python: nullable type differs: {pointer}")
        bindings[pointer] = {**declaration, "source_pointer": source_pointer}
    root_bindings = {}
    for name in document["components"]["schemas"]:
        pointer = "#/components/schemas/" + escaped(name)
        row = models.get(pointer)
        if row is None:
            raise ValueError(f"{language}: missing named component mapping {pointer}")
        type_name = row.get("type", row.get("go_type"))
        if not type_name or DYNAMIC.fullmatch(type_name):
            raise ValueError(f"{language}: component has no concrete named type: {pointer}")
        if not any(re.search(r"\b" + re.escape(type_name) + r"\b", source) for source in source_texts.values()):
            raise ValueError(f"{language}: claimed model absent from actual source: {type_name}")
        root_bindings[pointer] = {"type": type_name, "file": row["file"]}
    operation_rows = operation_bindings(root, document, operations, language, models, bindings) if operations is not None else {}
    return {"fields": bindings, "schemas": root_bindings, "operations": operation_rows,
            "dynamic_exceptions": sorted(checked_exceptions, key=lambda row: row.get("pointer", row.get("schema_pointer"))),
            "source_sha256": {filename: hashlib.sha256((root / filename).read_bytes()).hexdigest() for filename in sorted(source_texts)}}


def build(root, languages):
    raw = (root / "sdks/contracts/openapi.json").read_bytes()
    document = json.loads(raw)
    schemas, fields, operations = inventory(document)
    bindings = {language: check_language(root, document, schemas, fields, language, operations) for language in languages}
    return {
        "schema_version": 1, "openapi_sha256": hashlib.sha256(raw).hexdigest(),
        "scope": "All component schemas and JSON operation bodies; constraints are reviewed machine metadata, not proof of all server business rules.",
        "counts": {"operations": len(operations), "body_operations": sum(row["request"] is not None for row in operations),
                   "component_schemas": len(document["components"]["schemas"]), "schema_locations": len(schemas), "fields": len(fields)},
        "operations": operations, "fields": fields, "languages": bindings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=Path("sdks/contracts/type-coverage.json"))
    parser.add_argument("--languages", nargs="+", choices=sorted(MANIFESTS), default=sorted(MANIFESTS))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = build(args.root, args.languages)
        expected = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        output = args.output if args.output.is_absolute() else args.root / args.output
        if args.check:
            if not output.is_file() or output.read_bytes() != expected:
                raise ValueError("type coverage artifact differs; regenerate and review: " + str(output))
        else:
            output.write_bytes(expected)
        print(json.dumps({"type_coverage": "PASS", "languages": args.languages, **report["counts"]}, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print("type_coverage: FAIL: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
