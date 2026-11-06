#!/usr/bin/env python3
"""从 sdks/contracts/openapi.json 生成 C# 模型与全部操作方法（输出已提交，改契约后重跑）。

用法：python3 sdks/csharp/tools/gen.py            # 写 src/Authgebra.Client/Generated/*.g.cs
      python3 sdks/csharp/tools/gen.py --check    # 生成结果必须与已提交文件逐字节一致（CI 同步校验）

事实源只有 openapi.json；本脚本不含任何协议常量。输出里的 ContractInfo.OpenApiSha256 来自
受审 JSON 的实际字节，测试再独立核对已评审摘要，契约更新而忘记重新生成会让测试变红。
"""
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "sdks/contracts/openapi.json"
OUT = ROOT / "sdks/csharp/src/Authgebra.Client/Generated"

# 无副作用的 POST（授权判定类，见 docs/02-API记录/05-Authorization-API.md）：SDK 按读请求处理（可有限重试）。
READ_ONLY_POSTS = {"check", "batch_check", "explain", "list_authorized_resources"}
KEYWORDS = set(
    "abstract as base bool break byte case catch char checked class const continue decimal default delegate do double "
    "else enum event explicit extern false finally fixed float for foreach goto if implicit in int interface internal is "
    "lock long namespace new null object operator out override params private protected public readonly ref return "
    "sbyte sealed short sizeof stackalloc static string struct switch this throw true try typeof uint ulong unchecked "
    "unsafe ushort using virtual void volatile while from".split()
)

doc = json.loads(CONTRACT.read_text(encoding="utf-8"))
SCHEMAS = doc["components"]["schemas"]
POINTERS = {}
def record_pointers(value, pointer):
    if isinstance(value, dict):
        POINTERS[id(value)] = pointer
        for key, child in value.items():
            record_pointers(child, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            record_pointers(child, pointer + "/" + str(index))
record_pointers(doc, "#")
UNIONS = {}
DYNAMIC_EXCEPTIONS = []
FIELD_PROVENANCE = []
PAYLOAD_ALIASES = {}
for _name, _wrapper in SCHEMAS.items():
    if _name.startswith("ApiResponse_Vec_"):
        _target = _name[len("ApiResponse_Vec_"):]
        _value = _wrapper.get("properties", {}).get("data", {}).get("items")
    elif _name.startswith("ApiResponse_"):
        _target = _name[len("ApiResponse_"):]
        _value = _wrapper.get("properties", {}).get("data")
    elif _name.startswith("PagedResponse_"):
        _target = _name[len("PagedResponse_"):]
        _value = _wrapper.get("properties", {}).get("data", {}).get("items")
    else:
        continue
    if _target in SCHEMAS and _value == SCHEMAS[_target]:
        PAYLOAD_ALIASES[POINTERS[id(_value)]] = _target


def pascal(text: str) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", text) if w]
    out = "".join(w[:1].upper() + (w[1:].lower() if w.isupper() else w[1:]) for w in words)
    if not out:
        out = "X"
    return "_" + out if out[0].isdigit() else out


def camel(text: str) -> str:
    p = pascal(text)
    c = p[:1].lower() + p[1:]
    return "@" + c if c in KEYWORDS else c


def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def is_nullable(schema: dict) -> bool:
    if not any(key in schema for key in ("type", "$ref", "enum", "oneOf", "anyOf", "allOf")):
        return True
    t = schema.get("type")
    if isinstance(t, list) and "null" in t:
        return True
    for key in ("oneOf", "anyOf"):
        if any(isinstance(v, dict) and v.get("type") == "null" for v in schema.get(key, [])):
            return True
    return False


def non_null_variants(schema: dict):
    for key in ("oneOf", "anyOf"):
        if key in schema:
            return [v for v in schema[key] if not (isinstance(v, dict) and v.get("type") == "null")]
    return None


def base_type(schema: dict):
    t = schema.get("type")
    if isinstance(t, list):
        rest = [x for x in t if x != "null"]
        return rest[0] if len(rest) == 1 else None
    return t


UNSUPPORTED_SCHEMA_KEYS = {"not", "if", "then", "else", "patternProperties", "prefixItems", "contains", "minContains", "maxContains", "dependentSchemas", "dependentRequired", "unevaluatedProperties", "unevaluatedItems", "minProperties", "maxProperties", "multipleOf", "const"}


def validate_schema_structure(schema):
    unsupported = UNSUPPORTED_SCHEMA_KEYS.intersection(schema)
    if unsupported:
        raise ValueError("unsupported C# schema keyword " + ", ".join(sorted(unsupported)))
    if "propertyNames" in schema and schema["propertyNames"] != {"type": "string"}:
        raise ValueError("unsupported C# propertyNames constraints")
    if "$ref" in schema and (not schema["$ref"].startswith("#/components/schemas/") or ref_name(schema["$ref"]) not in SCHEMAS):
        raise ValueError("unresolved schema reference " + schema["$ref"])
    for child in schema.get("properties", {}).values():
        validate_schema_structure(child)
    for key in ("items", "additionalProperties"):
        if isinstance(schema.get(key), dict):
            validate_schema_structure(schema[key])
    for key in ("oneOf", "anyOf", "allOf"):
        for child in schema.get(key, []):
            validate_schema_structure(child)


for _schema in SCHEMAS.values():
    validate_schema_structure(_schema)


# ---- 类型映射 ----------------------------------------------------------------------------------

class Registry:
    def __init__(self):
        self.models = {}  # name -> schema
        self.enums = {}
        self.queue = []

    def add_inline(self, hint: str, schema: dict) -> str:
        name = pascal(hint)
        base, n = name, 2
        while name in self.models and self.models[name] is not schema:
            name = f"{base}{n}"
            n += 1
        if name not in self.models:
            self.models[name] = schema
            self.queue.append(name)
        return name


REG = Registry()
for _n, _s in sorted(SCHEMAS.items()):
    if "enum" in _s and base_type(_s) == "string":
        REG.enums[_n] = _s
    elif "properties" in _s and base_type(_s) == "object":
        REG.models[_n] = _s
        REG.queue.append(_n)
    elif non_null_variants(_s):
        UNIONS[_n] = _s
    else:
        raise ValueError("unsupported named schema " + _n)


def cs_type(schema: dict, hint: str, nullable_ok=True):
    """返回 (不含可空标记的 C# 类型, 是否为值类型, 是否可空)。"""
    validate_schema_structure(schema)
    nullable = is_nullable(schema)
    if POINTERS.get(id(schema)) in PAYLOAD_ALIASES:
        return PAYLOAD_ALIASES[POINTERS[id(schema)]], False, nullable
    variants = non_null_variants(schema)
    if variants is not None:
        if len(variants) == 1:
            t, v, _ = cs_type(variants[0], hint)
            return t, v, nullable
        name = pascal(hint)
        UNIONS[name] = schema
        return name, False, nullable
    if "allOf" in schema and len(schema["allOf"]) == 1:
        t, v, _ = cs_type(schema["allOf"][0], hint)
        return t, v, nullable
    if "$ref" in schema:
        name = ref_name(schema["$ref"])
        if name in REG.enums:
            return ("ApiErrorCodeValue" if name == "ApiErrorCode" else name), True, nullable
        if name in REG.models:
            return name, False, nullable
        if name in UNIONS:
            return name, False, nullable
        raise ValueError("unresolved schema reference " + schema["$ref"])
    t = base_type(schema)
    fmt = schema.get("format")
    if t == "string":
        if fmt not in (None, "uuid", "date-time"):
            raise ValueError("unsupported C# string format " + str(fmt))
        if "enum" in schema:
            name = pascal(hint)
            REG.enums[name] = schema
            return name, True, nullable
        if fmt == "uuid":
            return "Guid", True, nullable
        if fmt == "date-time":
            return "DateTimeOffset", True, nullable
        return "string", False, nullable
    if t == "integer":
        integers = {None: "long", "int32": "int", "int64": "long", "uint8": "byte", "uint16": "ushort", "uint32": "uint", "uint64": "ulong"}
        if fmt not in integers:
            raise ValueError("unsupported C# integer format " + str(fmt))
        return integers[fmt], True, nullable
    if t == "number":
        if fmt not in (None, "float", "double"):
            raise ValueError("unsupported C# number format " + str(fmt))
        return "double", True, nullable
    if t == "boolean":
        return "bool", True, nullable
    if t == "array":
        if "items" not in schema:
            raise ValueError("array is missing its item schema " + hint)
        item, iv, inull = cs_type(schema["items"], hint + "Item")
        return f"IReadOnlyList<{item}{'?' if (inull or False) and True else ''}>", False, nullable
    if t == "object":
        if "properties" in schema:
            return REG.add_inline(hint, schema), False, nullable
        ap = schema.get("additionalProperties")
        if isinstance(ap, dict):
            item, iv, inull = cs_type(ap, hint + "Value")
            return f"IReadOnlyDictionary<string, {item}{'?' if inull else ''}>", False, nullable
        if ap is not False:
            DYNAMIC_EXCEPTIONS.append({"pointer": POINTERS[id(schema)], "type": "IReadOnlyDictionary<string, JsonValue>", "reason": "OpenAPI permits arbitrary values under dynamic object keys", "test": "RegisteredDynamicExceptionRetainsExactJson"})
            return "IReadOnlyDictionary<string, JsonValue>", False, nullable
        return REG.add_inline(hint, schema), False, nullable
    annotations = {"description", "title", "example", "examples", "default", "deprecated", "readOnly", "writeOnly", "$comment", "externalDocs"}
    if not any(k in schema for k in ("type", "properties", "oneOf", "anyOf", "allOf", "$ref")) and all(k in annotations or k.startswith("x-") for k in schema):
        DYNAMIC_EXCEPTIONS.append({"pointer": POINTERS.get(id(schema)), "type": "JsonValue", "reason": "OpenAPI permits arbitrary JSON", "test": "RegisteredDynamicExceptionRetainsExactJson"})
        return "JsonValue", True, nullable
    raise ValueError("unsupported schema " + str(schema))


def with_null(t: str, is_value: bool, nullable: bool) -> str:
    return t + "?" if nullable and not t.endswith("?") else t


# ---- 请求侧 schema 闭包 ------------------------------------------------------------------------

def collect_refs(node, acc):
    if isinstance(node, dict):
        if "$ref" in node:
            n = ref_name(node["$ref"])
            if n not in acc:
                acc.add(n)
                collect_refs(SCHEMAS.get(n, {}), acc)
        for v in node.values():
            collect_refs(v, acc)
    elif isinstance(node, list):
        for v in node:
            collect_refs(v, acc)


REQUEST_SCHEMAS = set()
for _path, _item in doc["paths"].items():
    for _m, _op in _item.items():
        _rb = _op.get("requestBody")
        if _rb:
            collect_refs(_rb, REQUEST_SCHEMAS)


# Presence in a response graph preserves forward compatibility for shared DTOs.
# Follow actual schema identities (including inline variants), not model name heuristics.
def schema_nodes(node, result, visited):
    if isinstance(node, dict):
        result.add(id(node))
        alias = PAYLOAD_ALIASES.get(POINTERS.get(id(node)))
        if alias is not None and alias not in visited:
            visited.add(alias)
            schema_nodes(SCHEMAS[alias], result, visited)
        if "$ref" in node:
            name = ref_name(node["$ref"])
            if name not in visited:
                visited.add(name)
                schema_nodes(SCHEMAS[name], result, visited)
        for value in node.values():
            schema_nodes(value, result, visited)
    elif isinstance(node, list):
        for value in node:
            schema_nodes(value, result, visited)


REQUEST_NODES, RESPONSE_NODES, REQUEST_ROOT_NODES = set(), set(), set()
for _item in doc["paths"].values():
    for _op in _item.values():
        for _media in _op.get("requestBody", {}).get("content", {}).values():
            _schema = _media["schema"]
            _root = SCHEMAS[ref_name(_schema["$ref"])] if "$ref" in _schema else _schema
            REQUEST_ROOT_NODES.add(id(_root))
            schema_nodes(_schema, REQUEST_NODES, set())
        for _response in _op["responses"].values():
            for _media in _response.get("content", {}).values():
                schema_nodes(_media["schema"], RESPONSE_NODES, set())



# ---- 模型输出 ----------------------------------------------------------------------------------

def doc_comment(text, indent):
    if not text:
        return ""
    lines = [l.strip() for l in str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").splitlines()]
    lines = [l for l in lines if l] or []
    if not lines:
        return ""
    body = "\n".join(f"{indent}/// {l}" for l in lines)
    return f"{indent}/// <summary>\n{body}\n{indent}/// </summary>\n"


def constraint_checks(schema, value, field):
    if "$ref" in schema:
        schema = SCHEMAS[ref_name(schema["$ref"])]
    variants = non_null_variants(schema)
    if variants is not None and len(variants) == 1:
        schema = variants[0]
    result = [f"ContractModel.Nested({value});"]
    for key, operator in (("minimum", "<"), ("maximum", ">"), ("exclusiveMinimum", "<="), ("exclusiveMaximum", ">=")):
        if key in schema:
            result.append(f"if ({value} {operator} {schema[key]}) throw new ArgumentOutOfRangeException({json.dumps(field)});")
    for key, operator in (("minLength", "<"), ("maxLength", ">")):
        if key in schema:
            result.append(f"if (ContractModel.CharacterCount({value}) {operator} {schema[key]}) throw new ArgumentException(\"string length violates the contract\", {json.dumps(field)});")
    for key, operator in (("minItems", "<"), ("maxItems", ">")):
        if key in schema:
            result.append(f"if ({value}.Count {operator} {schema[key]}) throw new ArgumentException(\"array length violates the contract\", {json.dumps(field)});")
    if "pattern" in schema:
        if schema["pattern"] == r"^[a-z][a-z0-9_.-]{0,63}$":
            result.append(f"if (!ContractModel.ValidIamCode({value})) throw new ArgumentException(\"value does not match the contract pattern\", {json.dumps(field)});")
        else:
            raise ValueError("unsupported C# pattern " + schema["pattern"])
    if schema.get("uniqueItems"):
        result.append(f"if (!ContractModel.Unique({value})) throw new ArgumentException(\"array items must be unique\", {json.dumps(field)});")
    if "enum" in schema and len(schema["enum"]) > 0:
        choices = ", ".join(json.dumps(v) for v in schema["enum"])
        result.append(f"if (!new[] {{ {choices} }}.Contains({value}.ToString(), StringComparer.Ordinal)) throw new ArgumentException(\"value is outside the contract enum\", {json.dumps(field)});")
    return result


def emit_validation(name, schema, is_req):
    required = set(schema.get("required", []))
    out = ["        public void Validate()\n        {\n"]
    for field, prop in sorted(schema.get("properties", {}).items()):
        pname = pascal(field)
        if pname == name: pname += "Value"
        t, is_value, nullable = cs_type(prop, name + pascal(field))
        expr = pname
        optional_wrapper = (is_req and nullable and field not in required) or (not is_req and field not in required)
        indent = "            "
        if optional_wrapper:
            if not is_req: expr += "Field"
            if not nullable:
                out.append(f"            if ({expr}.IsNull) throw new ArgumentException(\"field is not nullable\", {json.dumps(field)});\n")
            out.append(f"            if ({expr}.IsSet && !{expr}.IsNull)\n            {{\n")
            expr += ".Value"
            indent += "    "
        elif nullable or field not in required:
            out.append(f"            if ({expr} is not null)\n            {{\n")
            if is_value: expr += ".Value"
            indent += "    "
        elif not is_value:
            out.append(f"            ArgumentNullException.ThrowIfNull({expr});\n")
        if optional_wrapper and not is_value:
            out.append(indent + f"ArgumentNullException.ThrowIfNull({expr});\n")
        for line in constraint_checks(prop, expr, field):
            out.append(indent + line + "\n")
        if optional_wrapper or nullable or field not in required:
            out.append("            }\n")
    out.append("        }\n")
    return "".join(out)


def shape_value_checks(prop, expr, field, depth=0):
    schema = prop
    if "$ref" in schema:
        return []  # the named child's own callback checks its shape
    variants = non_null_variants(schema)
    if variants is not None and len(variants) == 1:
        return shape_value_checks(variants[0], expr, field, depth)
    typ = base_type(schema)
    if typ == "array":
        child = schema["items"]
        item = "item" + str(depth)
        lines = [f"foreach (var {item} in {expr})", "{"]
    elif typ == "object" and isinstance(schema.get("additionalProperties"), dict):
        child = schema["additionalProperties"]
        item = "item" + str(depth)
        lines = [f"foreach (var {item} in {expr}.Values)", "{"]
    else:
        return []
    nullable = is_nullable(child)
    if nullable:
        lines += [f"    if ({item} is not null)", "    {"]
    else:
        lines.append(f"    ContractModel.NonNull({item}, {json.dumps(field + '[]')});")
    lines += ["    " + line for line in shape_value_checks(child, item, field + "[]", depth + 1)]
    if nullable: lines.append("    }")
    return lines + ["}"]


def emit_shape_checks(name, schema, is_req):
    required = set(schema.get("required", []))
    out = ["        void IJsonOnDeserialized.OnDeserialized()\n        {\n"]
    for field, prop in sorted(schema.get("properties", {}).items()):
        pname = pascal(field)
        if pname == name: pname += "Value"
        t, is_value, nullable = cs_type(prop, name + pascal(field))
        optional_wrapper = (is_req and nullable and field not in required) or (not is_req and field not in required)
        expr = pname + ("Field" if not is_req and field not in required else "")
        indent = "            "
        if optional_wrapper:
            if not nullable: out.append(f"            if ({expr}.IsNull) throw new JsonException(\"{field} is not nullable\");\n")
            out.append(f"            if ({expr}.IsSet && !{expr}.IsNull)\n            {{\n")
            expr += ".Value"
            indent += "    "
        elif nullable or field not in required:
            out.append(f"            if ({expr} is not null)\n            {{\n")
            indent += "    "
        if optional_wrapper or not nullable:
            out.append(indent + f"ContractModel.NonNull({expr}, {json.dumps(field)});\n")
        for line in shape_value_checks(prop, expr, field):
            out.append(indent + line + "\n")
        if optional_wrapper or nullable or field not in required: out.append("            }\n")
    out.append("        }\n")
    return "".join(out)


def emit_model(name: str, schema: dict) -> str:
    is_req = name in REQUEST_SCHEMAS
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    out = [doc_comment(schema.get("description"), "    "), "    // Source: " + POINTERS[id(schema)] + "\n"]
    if schema.get("additionalProperties") is False and (
        id(schema) in REQUEST_ROOT_NODES or id(schema) in REQUEST_NODES - RESPONSE_NODES
    ):
        out.append("    [JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]\n")
    out.append(f"    public sealed class {name} : IContractModel, IJsonOnDeserialized\n    {{\n")
    used = set()
    for json_name, ps in sorted(props.items()):
        t, is_value, nullable = cs_type(ps, name + pascal(json_name))
        pname = pascal(json_name)
        if pname == name:
            pname += "Value"
        while pname in used:
            pname += "_"
        used.add(pname)
        out.append(doc_comment(ps.get("description"), "        "))
        req = json_name in required
        if ps.get("x-authgebra-clearable") is True and (not nullable or req or not name.startswith("Patch")):
            raise ValueError("invalid clearable contract at " + POINTERS[id(ps)])
        attrs = f'        [JsonPropertyName("{json_name}")]\n'
        if req: attrs += "        [JsonRequired]\n"
        if is_req and not req and not nullable:
            converter_type = t + ("?" if is_value else "")
            attrs += f"        [JsonConverter(typeof(NonNullOptionalConverter<{converter_type}>))]\n"
        out.append("        // Source: " + POINTERS[id(ps)] + "\n")
        FIELD_PROVENANCE.append({"pointer": POINTERS[id(ps)], "type": (f"Optional<{t}>" if not is_req and not req else t), "model": name, "field": (pname + "Field" if not is_req and not req else pname), "required": req, "nullable": nullable, "file": "sdks/csharp/src/Authgebra.Client/Generated/Models.g.cs", "constraints": {k: v for k, v in ps.items() if k not in ("description", "properties", "items", "$ref", "type")}})
        if is_req:
            if nullable and not req:
                typ = f"Optional<{t}>"
                attrs += "        [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)]\n"
                out.append(attrs + f"        public {typ} {pname} {{ get; init; }}\n\n")
            elif req:
                typ = with_null(t, is_value, nullable)
                if len(ps.get("enum", [])) == 1:
                    out.append(attrs + f"        public {typ} {pname} {{ get; init; }} = {t}.{pascal(ps['enum'][0])};\n\n")
                else:
                    out.append(attrs + f"        public required {typ} {pname} {{ get; init; }}\n\n")
            else:
                typ = t + "?"
                attrs += "        [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]\n"
                out.append(attrs + f"        public {typ} {pname} {{ get; init; }}\n\n")
        else:
            if req and not nullable:
                typ = t
                init = "" if is_value else " = default!;"
                out.append(attrs + f"        public {typ} {pname} {{ get; init; }}{init}\n\n")
            elif req:
                typ = t + "?"
                out.append(attrs + f"        public {typ} {pname} {{ get; init; }}\n\n")
            else:
                # The wire field tracks presence; the existing nullable property is a compatibility view.
                attrs += "        [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)]\n"
                out.append(attrs + f"        public Optional<{t}> {pname}Field {{ get; init; }}\n\n")
                out.append(f"        [JsonIgnore]\n        public {t}? {pname}\n        {{\n            get => {pname}Field.IsSet && !{pname}Field.IsNull ? {pname}Field.Value : ({t}?)null;\n            init => {pname}Field = value is null ? Optional<{t}>.Null : new Optional<{t}>(value{'.Value' if is_value else ''});\n        }}\n\n")
    out.append(emit_shape_checks(name, schema, is_req))
    out.append(emit_validation(name, schema, is_req))
    out.append("    }\n\n")
    return "".join(out)


def emit_enum(name: str, schema: dict) -> str:
    out = [doc_comment(schema.get("description"), "    "), "    // Source: " + POINTERS[id(schema)] + "\n"]
    raw = name == "ApiErrorCode"
    if raw:
        out.append(f"    public static class {name}\n    {{\n")
    else:
        out.append(f"    [JsonConverter(typeof({name}JsonConverter))]\n    public readonly record struct {name}(string Value)\n    {{\n")
        out.append(f"        public static implicit operator {name}(string value) => new(value);\n        public static implicit operator string({name} value) => value.Value;\n        public override string ToString() => Value;\n")
    seen = set()
    values = []
    for v in schema["enum"]:
        member = pascal(v)
        while member in seen or member == name:
            member += "_"
        seen.add(member)
        initializer = json.dumps(v) if raw else "new(" + json.dumps(v) + ")"
        out.append(f"        public {'const string' if raw else 'static readonly ' + name} {member} = {initializer};\n")
        values.append(json.dumps(v))
    out.append(f"        public static readonly IReadOnlySet<string> All = new HashSet<string> {{ {', '.join(values)} }};\n    }}\n\n")
    if not raw:
        out.append(f"    internal sealed class {name}JsonConverter : JsonConverter<{name}>\n    {{\n        public override {name} Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options) => reader.TokenType == JsonTokenType.String ? new(reader.GetString() ?? throw new JsonException(\"enum must be a string\")) : throw new JsonException(\"enum must be a string\");\n        public override void Write(Utf8JsonWriter writer, {name} value, JsonSerializerOptions options) => writer.WriteStringValue(value.Value);\n    }}\n\n")
    if raw:
        out.append(emit_enum("ApiErrorCodeValue", schema))
    return "".join(out)


def emit_union(name: str, schema: dict) -> str:
    arms = non_null_variants(schema)
    if not arms:
        raise ValueError("union has no variants " + name)
    discriminator = None
    for key in sorted(arms[0].get("properties", {})):
        if all(len(arm.get("properties", {}).get(key, {}).get("enum", [])) == 1 for arm in arms):
            tags = [arm["properties"][key]["enum"][0] for arm in arms]
            if len(set(tags)) == len(tags):
                discriminator = key
                break
    if discriminator is None:
        raise ValueError("union requires an explicit discriminator " + name)
    variants = []
    for arm in arms:
        tag = arm["properties"][discriminator]["enum"][0]
        tn = REG.add_inline(name + pascal(tag), arm)
        if name in REQUEST_SCHEMAS:
            REQUEST_SCHEMAS.add(tn)
        variants.append((tag, tn))
    out = ["    // Source: " + POINTERS[id(schema)] + "\n", f"    [JsonConverter(typeof({name}JsonConverter))]\n    public sealed class {name} : IContractModel\n    {{\n"]
    for tag, tn in variants:
        member = pascal(tag)
        tag_property = pascal(discriminator)
        out.append(f"        private readonly {tn}? _{member};\n        private {name}({tn} value) => _{member} = value;\n")
        out.append(f"        public static {name} From{member}({tn} value)\n        {{\n            ArgumentNullException.ThrowIfNull(value);\n            if (value.{tag_property}.Value != {json.dumps(tag)}) throw new ArgumentException(\"union discriminator does not match its variant\", nameof(value));\n            return new(value);\n        }}\n        public {tn}? As{member} => _{member};\n")
    out.append("        public void Validate()\n        {\n")
    for tag, _ in variants:
        member = pascal(tag)
        out.append(f"            if (_{member} is {{ }} value{member}) {{ value{member}.Validate(); return; }}\n")
    out.append('            throw new InvalidOperationException("union has no variant");\n        }\n')
    out += ["    }\n", f"    internal sealed class {name}JsonConverter : JsonConverter<{name}>\n    {{\n        public override {name} Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)\n        {{\n            using var doc = JsonDocument.ParseValue(ref reader);\n            var root = doc.RootElement;\n            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty({json.dumps(discriminator)}, out var tag) || tag.ValueKind != JsonValueKind.String) throw new JsonException(\"missing union discriminator\");\n            return tag.GetString() switch\n            {{\n"]
    for tag, tn in variants:
        out.append(f"                {json.dumps(tag)} => {name}.From{pascal(tag)}(root.Deserialize<{tn}>(options) ?? throw new JsonException(\"missing variant\")),\n")
    out.append('                _ => throw new JsonException("unknown union tag"),\n            };\n        }\n')
    out.append(f"        public override void Write(Utf8JsonWriter writer, {name} value, JsonSerializerOptions options)\n        {{\n")
    for tag, _ in variants:
        member = pascal(tag)
        out.append(f"            if (value.As{member} is {{ }} v{member}) {{ JsonSerializer.Serialize(writer, v{member}, options); return; }}\n")
    out.append('            throw new JsonException("invalid union variant");\n        }\n    }\n\n')
    return "".join(out)


# ---- 操作 --------------------------------------------------------------------------------------

TAG_CLASS = {
    "users": "UsersApi", "roles": "RolesApi", "permissions": "PermissionsApi", "resources": "ResourcesApi",
    "resource-types": "ResourceTypesApi", "attributes": "AttributesApi", "policies": "PoliciesApi",
    "policy-bindings": "PolicyBindingsApi", "relation-definitions": "RelationDefinitionsApi",
    "relation-tuples": "RelationTuplesApi", "audit": "AuditApi", "dashboard": "DashboardApi", "auth": "AuthApi",
    "setup": "SetupApi", "health": "HealthApi", "authz": "AuthzApi",
}


def resolve_data_type(env_name: str, is_paged: bool):
    data = SCHEMAS[env_name]["properties"]["data"]
    suffix = env_name.split("_", 1)[1]
    is_array = base_type(data) == "array"
    if suffix.startswith("Vec_") and suffix[4:] in REG.models and is_array:
        return f"IReadOnlyList<{suffix[4:]}>"
    if suffix in REG.models:
        return f"IReadOnlyList<{suffix}>" if is_array else suffix
    if suffix == "Value" or (not data.get("properties") and not is_array):
        return "JsonValue"
    t, _, _ = cs_type(data, suffix)
    return t


def param_type(p):
    t, is_value, nullable = cs_type(p["schema"], "P")
    return t, is_value


def emit_operation(path: str, method: str, op: dict) -> (str, str):
    op_id = op["operationId"]
    tag = op["tags"][0]
    # 返回类型
    success = sorted(k for k in op["responses"] if k.startswith("2"))[0]
    content = op["responses"][success]["content"]["application/json"]["schema"]
    rname = ref_name(content["$ref"])
    enveloped = rname.startswith(("ApiResponse_", "PagedResponse_"))
    if enveloped:
        rtype = resolve_data_type(rname, rname.startswith("PagedResponse_"))
    else:
        rtype = rname if rname in REG.models else cs_type(content, rname)[0]
    params = op.get("parameters", [])
    path_params = [p for p in params if p["in"] == "path"]
    query_params = [p for p in params if p["in"] == "query"]
    header_params = [p for p in params if p["in"] == "header"]
    req_args, opt_args, chain = [], [], []
    used = {"options", "cancellationToken", "body"}

    def unique(n):
        base, i = n, 2
        while n in used:
            n = f"{base}{i}"
            i += 1
        used.add(n)
        return n

    for p in path_params:
        t, _ = param_type(p)
        n = unique(camel(p["name"]))
        req_args.append(f"{t} {n}")
        chain.append(f'.WithPath("{p["name"]}", {n})')
    rb = op.get("requestBody")
    if rb:
        bschema = rb["content"]["application/json"]["schema"]
        bt, _, _ = cs_type(bschema, pascal(op_id) + "Body")
        if rb.get("required", False):
            req_args.append(f"{bt} body")
        else:
            opt_args.append(f"{bt}? body = null")
        chain.append(".WithBody(body)")
    for p in header_params:
        n = unique(camel(p["name"]))
        if p["name"] == "If-Match":
            call = f".WithIfMatch({n})"
        elif p["name"] == "Idempotency-Key":
            call = f".WithIdempotencyKey({n})"
        else:
            call = f'.WithHeader("{p["name"]}", {n})'
        if p.get("required"):
            req_args.append(f"string {n}")
            chain.append(call)
        else:
            opt_args.append(f"string? {n} = null")
            chain.append(call)
    for p in query_params:
        t, is_value = param_type(p)
        n = unique(camel(p["name"]))
        opt_args.append(f"{t}? {n} = null")
        chain.append(f'.WithQuery("{p["name"]}", {n})')
    sig = ", ".join(req_args + opt_args + ["RequestOptions? options = null", "CancellationToken cancellationToken = default"])
    if rname.startswith("PagedResponse_"):
        chain.append(".AsPaged()")
    if op_id in READ_ONLY_POSTS:
        chain.append(".AsRead()")
    mname = pascal(op_id) + "Async"
    summary = doc_comment(op.get("summary"), "        ")
    summary += f"        /// <remarks><c>{method.upper()} {path}</c> · operationId <c>{op_id}</c></remarks>\n"
    body = (
        f'            => _core.SendAsync<{rtype}>(RequestSpec.Create("{method.upper()}", "{path}", {str(enveloped).lower()})'
        + "".join(f"\n                {c}" for c in chain)
        + ", options, cancellationToken);\n"
    )
    code = f"{summary}        public Task<ApiResult<{rtype}>> {mname}({sig})\n{body}\n"
    return TAG_CLASS[tag], code, op_id, mname


def main():
    check = "--check" in sys.argv
    ops = []
    for path in sorted(doc["paths"]):
        for method in sorted(doc["paths"][path]):
            ops.append((path, method, doc["paths"][path][method]))
    classes = {}
    names = []
    for path, method, op in ops:
        cls, code, op_id, mname = emit_operation(path, method, op)
        classes.setdefault(cls, []).append(code)
        names.append((op_id, cls, mname))

    # 模型（队列可能在处理中增长：内联对象）
    union_code = {name: emit_union(name, schema) for name, schema in sorted(UNIONS.items())}
    model_code = {}
    done = set()
    while REG.queue:
        n = REG.queue.pop(0)
        if n in done:
            continue
        done.add(n)
        model_code[n] = emit_model(n, REG.models[n])

    sha = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    header = (
        "// <auto-generated>由 sdks/csharp/tools/gen.py 从 sdks/contracts/openapi.json 生成，禁止手工修改。</auto-generated>\n"
        "#nullable enable\n#pragma warning disable CS1591, CS8618\n"
    )
    models = (
        header
        + "using System.Text.Json;\nusing System.Text.Json.Serialization;\n\nnamespace Authgebra\n{\n"
        + "".join(emit_enum(n, s) for n, s in sorted(REG.enums.items()))
        + "".join(union_code[n] for n in sorted(union_code))
        + "".join(model_code[n] for n in sorted(model_code))
        + "}\n"
    )
    api = (
        header
        + "using System.Text.Json;\n\nnamespace Authgebra\n{\n"
        + f'    public static class ContractInfo\n    {{\n        public const string OpenApiSha256 = "{sha}";\n'
        + f"        public const int OperationCount = {len(ops)};\n    }}\n\n"
        + "".join(
            f"    public sealed class {cls}\n    {{\n        private readonly AuthgebraClient _core;\n"
            f"        internal {cls}(AuthgebraClient core) => _core = core;\n\n" + "".join(codes) + "    }\n\n"
            for cls, codes in sorted(classes.items())
        )
        + "}\n"
    )
    provenance = {"aliases": [{"pointer": pointer, "target": "#/components/schemas/" + target, "type": target} for pointer, target in sorted(PAYLOAD_ALIASES.items())], "models": [{"pointer": POINTERS[id(schema)], "type": name, "file": "sdks/csharp/src/Authgebra.Client/Generated/Models.g.cs"} for name, schema in sorted({**REG.models, **REG.enums, **UNIONS, "ApiErrorCodeValue": SCHEMAS["ApiErrorCode"]}.items())], "fields": sorted(FIELD_PROVENANCE, key=lambda f: f["pointer"]), "dynamic_exceptions": sorted({entry["pointer"]: entry for entry in DYNAMIC_EXCEPTIONS}.values(), key=lambda f: f["pointer"] or "")}
    targets = {OUT / "Models.g.cs": models, OUT / "Api.g.cs": api, OUT / "Models.provenance.json": json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2) + "\n"}
    if check:
        with tempfile.TemporaryDirectory(prefix="authgebra-csharp-generated-") as directory:
            candidate = Path(directory)
            for path, text in targets.items():
                (candidate / path.name).write_bytes(text.encode("utf-8"))
            bad = [str(path) for path in targets if not path.exists() or path.read_bytes() != (candidate / path.name).read_bytes()]
        if bad:
            print("生成结果与已提交文件不一致：" + ", ".join(bad), file=sys.stderr)
            sys.exit(1)
        print(f"csharp 生成物与契约一致（{len(ops)} operations, sha256 {sha[:12]}…）")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    for p, text in targets.items():
        p.write_text(text, encoding="utf-8")
    print(f"生成 {len(ops)} 个操作、{len(model_code)} 个模型、{len(REG.enums)} 个枚举常量类 → {OUT}")


if __name__ == "__main__":
    main()
