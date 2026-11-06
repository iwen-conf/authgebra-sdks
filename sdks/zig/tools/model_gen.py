"""Named Zig models for every reviewed schema; unknown schema shapes are errors."""
import json
import re


def ident(name):
    return '@"' + name.replace('"', '\\"') + '"'


def pascal(name):
    return ''.join(x[:1].upper() + x[1:] for x in re.split(r'[^A-Za-z0-9]+', name) if x)


def pointer_part(name):
    return name.replace('~', '~0').replace('/', '~1')


class Models:
    def __init__(self, spec):
        self.schemas = spec['components']['schemas']
        self.entries = {name: (schema, '#/components/schemas/' + pointer_part(name)) for name, schema in self.schemas.items()}
        self.fields = []
        self.exceptions = []
        self.aliases = {}
        for name, wrapper in self.schemas.items():
            if name.startswith('ApiResponse_Vec_'):
                target = name[len('ApiResponse_Vec_'):]
                pointer = '#/components/schemas/' + pointer_part(name) + '/properties/data/items'
                value = wrapper.get('properties', {}).get('data', {}).get('items')
            elif name.startswith('ApiResponse_'):
                target = name[len('ApiResponse_'):]
                pointer = '#/components/schemas/' + pointer_part(name) + '/properties/data'
                value = wrapper.get('properties', {}).get('data')
            elif name.startswith('PagedResponse_'):
                target = name[len('PagedResponse_'):]
                pointer = '#/components/schemas/' + pointer_part(name) + '/properties/data/items'
                value = wrapper.get('properties', {}).get('data', {}).get('items')
            else:
                continue
            if target in self.schemas and value == self.schemas[target]:
                self.aliases[pointer] = target

    def nullable(self, schema):
        if not any(key in schema for key in ('type', '$ref', 'enum', 'oneOf', 'anyOf', 'allOf')):
            return True
        if isinstance(schema.get('type'), list):
            return 'null' in schema['type']
        return any(arm.get('type') == 'null' for key in ('oneOf', 'anyOf') for arm in schema.get(key, []))

    def normalized(self, schema):
        schema = dict(schema)
        for keyword in ('not', 'if', 'then', 'else', 'prefixItems', 'patternProperties', 'dependentSchemas', 'dependentRequired', 'unevaluatedProperties', 'unevaluatedItems', 'contains', 'minContains', 'maxContains', 'minProperties', 'maxProperties', 'multipleOf', 'const'):
            if keyword in schema:
                raise ValueError('unsupported Zig schema keyword ' + keyword)
        if 'pattern' in schema and schema['pattern'] != r'^[a-z][a-z0-9_.-]{0,63}$':
            raise ValueError('unsupported Zig pattern ' + schema['pattern'])
        if schema.get('uniqueItems') and schema.get('items') != {'type': 'string', 'format': 'uuid'}:
            raise ValueError('unsupported Zig uniqueItems element schema; UUID strings are supported')
        if 'propertyNames' in schema and schema['propertyNames'] != {'type': 'string'}:
            raise ValueError('unsupported Zig propertyNames constraints')
        if isinstance(schema.get('type'), list):
            types = [x for x in schema['type'] if x != 'null']
            if len(types) != 1:
                raise ValueError('unsupported type set ' + str(schema))
            schema['type'] = types[0]
        for key in ('oneOf', 'anyOf'):
            if key in schema:
                arms = [a for a in schema[key] if a.get('type') != 'null']
                if len(arms) == 1:
                    return self.normalized(arms[0])
        if 'allOf' in schema:
            if len(schema['allOf']) != 1:
                raise ValueError('multi-arm allOf requires explicit composition support')
            return self.normalized(schema['allOf'][0])
        return schema

    def ty(self, schema, hint, pointer):
        if pointer in self.aliases:
            return self.aliases[pointer]
        schema = self.normalized(schema)
        if '$ref' in schema:
            ref = schema['$ref']
            if not ref.startswith('#/components/schemas/') or ref.split('/')[-1] not in self.schemas:
                raise ValueError('unresolved reference ' + ref)
            return ref.split('/')[-1]
        if 'enum' in schema or 'oneOf' in schema or 'anyOf' in schema or schema.get('type') == 'object':
            if hint not in self.entries:
                self.entries[hint] = (schema, pointer)
            elif self.entries[hint][0] != schema:
                raise ValueError('type name collision ' + hint)
            return hint
        typ = schema.get('type')
        if typ == 'string':
            if schema.get('format') not in (None, 'uuid', 'date-time'):
                raise ValueError('unsupported Zig string format ' + str(schema['format']))
            return '[]const u8'
        if typ == 'integer':
            fmt = schema.get('format', 'int64')
            if fmt not in ('int32', 'int64', 'uint8', 'uint16', 'uint32', 'uint64'):
                raise ValueError('unsupported integer format ' + fmt)
            if schema.get('minimum') == 0:
                return {'int32': 'u32', 'int64': 'u64'}.get(fmt, fmt.replace('uint', 'u'))
            return {'int32': 'i32', 'int64': 'i64', 'uint8': 'u8', 'uint16': 'u16', 'uint32': 'u32', 'uint64': 'u64'}[fmt]
        if typ == 'boolean':
            return 'bool'
        if typ == 'number':
            if schema.get('format') not in (None, 'float', 'double'):
                raise ValueError('unsupported Zig number format ' + str(schema['format']))
            return 'f64'
        if typ == 'array':
            if 'items' not in schema:
                raise ValueError('array has no items at ' + pointer)
            inner = self.ty(schema['items'], hint + 'Item', pointer + '/items')
            if self.nullable(schema['items']):
                inner = '?' + inner
            return '[]const ' + inner
        annotations = {'description', 'title', 'example', 'examples', 'default', 'deprecated', 'readOnly', 'writeOnly', '$comment', 'externalDocs'}
        if typ is None and all(k in annotations or k.startswith('x-') for k in schema):
            self.exceptions.append({'pointer': pointer, 'type': 'JsonValue', 'reason': 'OpenAPI leaves the JSON value unconstrained', 'test': 'open JSON exception ' + pointer})
            return 'JsonValue'
        raise ValueError('unsupported schema at ' + pointer + ': ' + json.dumps(schema))

    def validate_value(self, prop, expr):
        schema = self.normalized(prop)
        out = ['try support.validate(' + expr + ');']
        for key, op in [('minimum', '<'), ('maximum', '>'), ('exclusiveMinimum', '<='), ('exclusiveMaximum', '>=')]:
            if key in schema:
                out.append('if (' + expr + ' ' + op + ' ' + str(schema[key]) + ') return error.InvalidValue;')
        for key, op in [('minItems', '<'), ('maxItems', '>')]:
            if key in schema:
                out.append('if (' + expr + '.len ' + op + ' ' + str(schema[key]) + ') return error.InvalidValue;')
        for key, op in [('minLength', '<'), ('maxLength', '>')]:
            if key in schema:
                out.append('if (try std.unicode.utf8CountCodepoints(' + expr + ') ' + op + ' ' + str(schema[key]) + ') return error.InvalidValue;')
        if 'pattern' in schema:
            out.append('if (!support.validIamCode(' + expr + ')) return error.InvalidValue;')
        if schema.get('uniqueItems'):
            out.append('if (!support.uniqueUuids(' + expr + ')) return error.InvalidValue;')
        return out

    def validation(self, props, req):
        out = ['    pub fn validate(self: @This()) anyerror!void {']
        if not props:
            out.append('        _ = self;')
        for name, prop in sorted(props.items()):
            expr = 'self.' + ident(name)
            if name not in req:
                out += ['        switch (' + expr + ') {', '            .absent => {},', '            .null => ' + ('{},' if self.nullable(prop) else 'return error.NullNotAllowed,'), '            .value => |v| {']
                out += ['                ' + line for line in self.validate_value(prop, 'v')]
                out += ['            },', '        }']
            elif self.nullable(prop):
                out += ['        if (' + expr + ') |v| {']
                out += ['            ' + line for line in self.validate_value(prop, 'v')]
                out += ['        }']
            else:
                out += ['        ' + line for line in self.validate_value(prop, expr)]
        return out + ['    }']

    def emit(self, name, raw, pointer):
        schema = self.normalized(raw)
        prefix = ['/// Source: ' + pointer]
        if 'enum' in schema:
            if schema.get('type') != 'string' or not all(isinstance(x, str) for x in schema['enum']):
                raise ValueError('unsupported enum at ' + pointer)
            return '\n'.join(prefix + ['pub const ' + name + ' = enum { ' + ', '.join(ident(x) for x in schema['enum']) + ' };'])
        arms = schema.get('oneOf', schema.get('anyOf'))
        if arms:
            discriminator = None
            for key in sorted(arms[0].get('properties', {})):
                if all(len(a.get('properties', {}).get(key, {}).get('enum', [])) == 1 for a in arms):
                    vals = [a['properties'][key]['enum'][0] for a in arms]
                    if len(set(vals)) == len(vals):
                        discriminator = key
                        break
            if discriminator is None:
                raise ValueError('union lacks a distinct machine-readable tag at ' + pointer)
            variant_key = 'oneOf' if 'oneOf' in schema else 'anyOf'
            variants = []
            for i, arm in enumerate(arms):
                tag = arm['properties'][discriminator]['enum'][0]
                variant_name = name + pascal(tag)
                variants.append((tag, variant_name))
                self.entries[variant_name] = (arm, pointer + '/' + variant_key + '/' + str(i))
            out = prefix + ['pub const ' + name + ' = union(enum) {']
            out += ['    ' + ident(tag) + ': ' + tn + ',' for tag, tn in variants]
            out += ['    pub fn jsonStringify(self: @This(), out: anytype) !void {', '        switch (self) {']
            out += ['            .' + ident(tag) + ' => |v| try out.write(v),' for tag, _ in variants]
            out += ['        }', '    }', '    pub fn jsonParse(a: std.mem.Allocator, source: anytype, options: std.json.ParseOptions) !@This() {', '        return jsonParseFromValue(a, try support.parseAny(a, source, options), options);', '    }', '    pub fn jsonParseFromValue(a: std.mem.Allocator, value: std.json.Value, options: std.json.ParseOptions) !@This() {', '        if (value != .object) return error.UnexpectedToken;', '        const tag = value.object.get(' + json.dumps(discriminator) + ') orelse return error.MissingField;', '        if (tag != .string) return error.UnexpectedToken;']
            out += ['        if (std.mem.eql(u8, tag.string, ' + json.dumps(tag) + ')) return .{ .' + ident(tag) + ' = try std.json.parseFromValueLeaky(' + tn + ', a, value, options) };' for tag, tn in variants]
            out += ['        return error.InvalidEnumTag;', '    }', '};']
            return '\n'.join(out)
        if schema.get('type') == 'object':
            props = schema.get('properties')
            if not props:
                ap = schema.get('additionalProperties', {})
                if ap is False:
                    return '\n'.join(prefix + ['pub const ' + name + ' = struct {};'])
                if ap is True:
                    ap = {}
                inner = self.ty(ap, name + 'Value', pointer + '/additionalProperties')
                return '\n'.join(prefix + ['pub const ' + name + ' = support.Object(' + inner + ');'])
            req = set(schema.get('required', []))
            out = prefix + ['pub const ' + name + ' = struct {']
            for field, prop in sorted(props.items()):
                pp = pointer + '/properties/' + pointer_part(field)
                typ = self.ty(prop, name + pascal(field), pp)
                required = field in req
                nullable = self.nullable(prop)
                default = ''
                if not required:
                    typ = 'Optional(' + typ + ')'
                    default = ' = .absent'
                elif nullable:
                    typ = '?' + typ
                elif len(prop.get('enum', [])) == 1:
                    default = ' = .' + ident(prop['enum'][0])
                out += ['    /// Source: ' + pp, '    ' + ident(field) + ': ' + typ + default + ',']
                self.fields.append({'pointer': pp, 'type': typ, 'model': name, 'field': field, 'required': required, 'nullable': nullable, 'constraints': {k: v for k,v in prop.items() if k not in ('description', '$ref', 'type', 'properties', 'items')}, 'file': 'sdks/zig/src/models.zig'})
            out += self.validation(props, req)
            out += ['    pub fn jsonStringify(self: @This(), out: anytype) !void {', '        try support.writeObject(self, out);', '    }', '};']
            return '\n'.join(out)
        return '\n'.join(prefix + ['pub const ' + name + ' = ' + self.ty(schema, name, pointer) + ';'])

    def generate(self):
        rendered = {}
        while True:
            pending = sorted(set(self.entries) - set(rendered))
            if not pending:
                break
            for name in pending:
                rendered[name] = self.emit(name, *self.entries[name])
        prefix = '// GENERATED by tools/gen_admin.py; DO NOT EDIT.\nconst std = @import("std");\npub const support = @import("model_support.zig");\npub const Optional = support.Optional;\npub const JsonValue = support.JsonValue;\n\n'
        return prefix + '\n\n'.join(rendered[name] for name in sorted(rendered)) + '\n'

    def example(self, raw):
        if '$ref' in raw:
            return self.example(self.schemas[raw['$ref'].split('/')[-1]])
        if self.nullable(raw):
            return None
        schema = self.normalized(raw)
        if 'enum' in schema:
            return schema['enum'][0]
        arms = schema.get('oneOf', schema.get('anyOf'))
        if arms:
            return self.example(arms[0])
        typ = schema.get('type')
        if typ == 'object':
            return {key: self.example(schema['properties'][key]) for key in sorted(schema.get('required', []))}
        if typ == 'array':
            return [self.example(schema['items']) for _ in range(schema.get('minItems', 0))]
        if typ == 'string':
            return '00000000-0000-4000-8000-000000000001' if schema.get('format') == 'uuid' else 'x' * max(schema.get('minLength', 0), 1)
        if typ in ('integer', 'number'):
            return schema.get('minimum', 0)
        if typ == 'boolean':
            return False
        return None

    def tests(self):
        out = ['// GENERATED by tools/gen_admin.py; DO NOT EDIT.', 'const std = @import("std");', 'const models = @import("authgebra").models;', 'fn roundtrip(comptime T: type, bytes: []const u8) !void {', '    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);', '    defer arena.deinit();', '    const a = arena.allocator();', '    const parsed = try std.json.parseFromSliceLeaky(T, a, bytes, .{ .ignore_unknown_fields = true });', '    const encoded = try std.json.Stringify.valueAlloc(a, parsed, .{});', '    try std.testing.expectEqualStrings(bytes, encoded);', '}']
        for name, (schema, _) in sorted(self.entries.items()):
            value = json.dumps(self.example(schema), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            out.append('test "generated model ' + name + ' roundtrip" { try roundtrip(models.' + name + ', ' + json.dumps(value, ensure_ascii=False) + '); }')
        for entry in sorted(self.exceptions, key=lambda item: item['pointer']):
            value = '{"arbitrary":[null,false,9223372036854775807,18446744073709551615]}'
            out.append('test ' + json.dumps(entry['test']) + ' { try roundtrip(models.JsonValue, ' + json.dumps(value) + '); }')
        return '\n'.join(out) + '\n'

    def response_type(self, op):
        successes = sorted((code,r) for code,r in op['responses'].items() if code.startswith('2'))
        code, response = successes[0]
        content = response.get('content', {}).get('application/json')
        if not content:
            if code == '204':
                return 'NoContent', False
            raise ValueError('unsupported non-JSON success')
        schema = content['schema']
        name = self.ty(schema, pascal(op['operationId']) + 'Response', '#/paths')
        resolved = self.schemas[schema['$ref'].split('/')[-1]] if '$ref' in schema else schema
        if name.startswith(('ApiResponse_', 'PagedResponse_')):
            data = resolved['properties']['data']
            if name == 'ApiResponse_Value':
                return 'JsonValue', True
            if name.startswith('PagedResponse_'):
                item = data['items']
                return '[]const ' + self.ty(item, name + 'DataItem', '#/components/schemas/' + name + '/properties/data/items'), True
            suffix = name.removeprefix('ApiResponse_')
            if suffix.startswith('Vec_'):
                return '[]const ' + suffix.removeprefix('Vec_'), True
            return suffix, True
        return name, False
