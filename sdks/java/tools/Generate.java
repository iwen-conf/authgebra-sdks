import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.Arrays;
import java.util.IdentityHashMap;

/**
 * Generates {@code Models.java}, {@code AdminClient.java} and {@code OpenApiDigest.java} from the
 * reviewed OpenAPI export ({@code sdks/contracts/openapi.json}). No third-party dependencies.
 *
 * <pre>
 *   cd sdks/java &amp;&amp; java tools/Generate.java      # or: cd sdks/java/tools &amp;&amp; java Generate.java
 * </pre>
 *
 * The output is committed. {@code OpenApiDigest.SHA256} embeds the digest of the input so a test
 * can fail when the contract changed but the SDK was not regenerated.
 */
public final class Generate {
    private static final String PKG = "com.authgebra.client";
    private static final Set<String> RESERVED = Set.of(
            "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const",
            "continue", "default", "do", "double", "else", "enum", "extends", "final", "finally", "float",
            "for", "goto", "if", "implements", "import", "instanceof", "int", "interface", "long", "native",
            "new", "package", "private", "protected", "public", "return", "short", "static", "strictfp",
            "super", "switch", "synchronized", "this", "throw", "throws", "transient", "try", "void",
            "volatile", "while", "record", "var", "yield", "sealed", "permits",
            "hashCode", "toString", "getClass", "notify", "notifyAll", "wait", "clone", "finalize");

    private static Map<String, Object> schemas;
    private static final Map<String, String> inlineRecords = new TreeMap<>();
    private static final Map<String, String> enumHolders = new TreeMap<>();
    private static final Map<String, String> namedRecords = new TreeMap<>();
    private static final Map<String, String> unions = new TreeMap<>();
    /** Prefix used when a type is referenced: empty inside Models, {@code Models.} elsewhere. */
    private static String qualifier = "Models.";
    private static final Map<Object, String> pointers = new IdentityHashMap<>();
    private static final List<Object> provenance = new ArrayList<>();
    private static final Map<String, Object> dynamicExceptions = new TreeMap<>();

    public static void main(String[] args) throws Exception {
        boolean check = Arrays.asList(args).contains("--check");
        args = Arrays.stream(args).filter(arg -> !arg.equals("--check")).toArray(String[]::new);
        Path spec = findSpec(args);
        Path outDir = args.length > 1 ? Path.of(args[1]) : locateOutDir();
        byte[] bytes = Files.readAllBytes(spec);
        @SuppressWarnings("unchecked")
        Map<String, Object> doc = (Map<String, Object>) new Json(new String(bytes, StandardCharsets.UTF_8)).parse();
        schemas = obj(path(doc, "components", "schemas"));
        indexPointers(doc, "#");

        for (var entry : schemas.entrySet()) {
            String name = entry.getKey();
            Map<String, Object> schema = obj(entry.getValue());
            rejectUnsupported(schema);
            if (schema.containsKey("enum")) {
                enumHolders.put(name, enumHolder(name, schema));
            } else if (schema.containsKey("properties")) {
                namedRecords.put(name, record(name, schema, name));
            } else if (schema.containsKey("oneOf")) {
                unions.put(name, union(name, schema));
            } else throw new IllegalArgumentException("unsupported named schema shape: " + name);
        }

        Map<String, StringBuilder> groups = new TreeMap<>();
        Map<String, Object> paths = obj(doc.get("paths"));
        List<String> operationIds = new ArrayList<>();
        for (var pathEntry : paths.entrySet()) {
            Map<String, Object> item = obj(pathEntry.getValue());
            for (var methodEntry : item.entrySet()) {
                Map<String, Object> op = obj(methodEntry.getValue());
                String tag = ((List<?>) op.get("tags")).get(0).toString();
                operationIds.add(op.get("operationId").toString());
                groups.computeIfAbsent(tag, k -> new StringBuilder())
                        .append(operation(pathEntry.getKey(), methodEntry.getKey(), op, tag));
            }
        }

        Path pkgDir = outDir.resolve(PKG.replace('.', '/'));
        Map<String, String> outputs = Map.of("Models.java", models(), "AdminClient.java", admin(groups),
                "OpenApiDigest.java", digest(sha256(bytes), operationIds),
                "models.provenance.json", jsonText(Map.of("schemas", provenance, "dynamicExceptions", dynamicExceptions.values().stream().toList())) + "\n");
        Path generated = check ? Files.createTempDirectory("authgebra-java-generated-") : pkgDir;
        Files.createDirectories(generated);
        try {
            for (var output : outputs.entrySet()) {
                Path candidate = generated.resolve(output.getKey());
                Files.writeString(candidate, output.getValue(), StandardCharsets.UTF_8);
                if (check && (!Files.exists(pkgDir.resolve(output.getKey()))
                        || Files.mismatch(candidate, pkgDir.resolve(output.getKey())) != -1)) {
                    throw new IllegalStateException("generated artifact differs: " + output.getKey());
                }
            }
        } finally {
            if (check) {
                for (String name : outputs.keySet()) Files.deleteIfExists(generated.resolve(name));
                Files.delete(generated);
            }
        }
        System.out.println("generated " + operationIds.size() + " operations, "
                + namedRecords.size() + " records, " + enumHolders.size() + " enums, "
                + inlineRecords.size() + " inline records -> " + pkgDir);
    }

    // ------------------------------------------------------------------ files

    private static String models() {
        StringBuilder sb = new StringBuilder(header());
        sb.append("import com.fasterxml.jackson.annotation.JsonIgnoreProperties;\n")

                .append("import com.fasterxml.jackson.annotation.JsonIgnore;\n")
                .append("import com.fasterxml.jackson.annotation.JsonInclude;\n")
                .append("import com.fasterxml.jackson.annotation.JsonProperty;\n")
                .append("import com.fasterxml.jackson.annotation.JsonTypeInfo;\n")
                .append("import com.fasterxml.jackson.annotation.JsonSubTypes;\n")
                .append("import com.fasterxml.jackson.annotation.JsonCreator;\n")
                .append("import com.fasterxml.jackson.annotation.JsonValue;\n")
                .append("import com.fasterxml.jackson.databind.JsonNode;\n")

                .append("import java.util.List;\nimport java.util.Map;\nimport java.util.UUID;\nimport java.math.BigInteger;\n\n")
                .append("/**\n * DTOs generated from the OpenAPI contract. Required properties retain their schema metadata;\n")
                .append(" * unknown JSON fields are ignored for forward-compatible responses, while\n")
                .append(" * optional JsonField components preserve absent, null and typed values without applying defaults.\n */\n")
                .append("@SuppressWarnings(\"unused\")\npublic final class Models {\n")
                .append("    private Models() {}\n\n")
                .append("    private static Long legacyUnsigned32(Number value) {\n")
                .append("        if (value == null) return null;\n")
                .append("        java.math.BigInteger integer = new java.math.BigDecimal(value.toString()).toBigIntegerExact();\n")
                .append("        if (integer.signum() < 0 || integer.compareTo(new BigInteger(\"4294967295\")) > 0) throw new IllegalArgumentException(\"uint32 value out of range\");\n")
                .append("        return integer.longValueExact();\n    }\n\n")
                .append("    private static String legacyEnum(Object value) {\n")
                .append("        if (value instanceof String text) return text;\n")
                .append("        throw new IllegalArgumentException(\"enum value must be a string or named enum value\");\n    }\n\n");
        enumHolders.values().forEach(v -> sb.append(v).append('\n'));
        unions.values().forEach(v -> sb.append(v).append('\n'));
        namedRecords.values().forEach(v -> sb.append(v).append('\n'));
        inlineRecords.values().forEach(v -> sb.append(v).append('\n'));
        sb.append(requestJsonValidators());
        sb.append("}\n");
        return sb.toString();
    }

    private static String admin(Map<String, StringBuilder> groups) {
        StringBuilder sb = new StringBuilder(header());
        sb.append("import com.fasterxml.jackson.core.type.TypeReference;\n")
                .append("import com.fasterxml.jackson.databind.JsonNode;\n")
                .append("import java.util.List;\nimport java.util.Map;\nimport java.util.Set;\nimport java.util.UUID;\n\n")
                .append("/** Every operation in the OpenAPI contract, grouped by tag. Generated; do not edit. */\n")
                .append("@SuppressWarnings(\"unused\")\npublic final class AdminClient {\n")
                .append("    private static final Object[] NO_ARGS = new Object[0];\n")
                .append("    private final Http http;\n\n")
                .append("    AdminClient(Http http) {\n        this.http = http;\n    }\n\n");
        for (String tag : groups.keySet()) {
            String method = camel(tag.replace('-', '_'));
            String type = pascal(tag.replace('-', '_'));
            sb.append("    /** Operations tagged {@code ").append(tag).append("}. */\n")
                    .append("    public ").append(type).append(' ').append(safe(method)).append("() {\n        return new ")
                    .append(type).append("(http);\n    }\n\n");
        }
        for (var group : groups.entrySet()) {
            String type = pascal(group.getKey().replace('-', '_'));
            sb.append("    /** Operations tagged {@code ").append(group.getKey()).append("}. */\n")
                    .append("    public static final class ").append(type).append(" {\n")
                    .append("        private final Http http;\n\n        ").append(type)
                    .append("(Http http) {\n            this.http = http;\n        }\n\n")
                    .append(group.getValue()).append("    }\n\n");
        }
        sb.append("}\n");
        return sb.toString();
    }

    private static String digest(String sha, List<String> operationIds) {
        StringBuilder ids = new StringBuilder();
        for (String id : operationIds) {
            ids.append(ids.length() == 0 ? "" : ",\n            ").append('"').append(id).append('"');
        }
        return header()
                + "import java.util.List;\n\n"
                + "/** Identity of the contract this SDK build was generated from. */\n"
                + "public final class OpenApiDigest {\n"
                + "    /** SHA-256 of {@code sdks/contracts/openapi.json}. */\n"
                + "    public static final String SHA256 = \"" + sha + "\";\n"
                + "    /** Every operationId covered by {@link AdminClient}. */\n"
                + "    public static final List<String> OPERATION_IDS = List.of(\n            " + ids + ");\n\n"
                + "    private OpenApiDigest() {}\n}\n";
    }

    private static String header() {
        return "// GENERATED by tools/Generate.java from sdks/contracts/openapi.json — DO NOT EDIT.\n"
                + "package " + PKG + ";\n\n";
    }

    // ------------------------------------------------------------- operations

    private static String operation(String path, String httpMethod, Map<String, Object> op, String tag) {
        String opId = op.get("operationId").toString();
        String method = safe(camel(opId));
        String constant = opId.toUpperCase();
        List<String> pathParams = new ArrayList<>();
        Set<String> queryParams = new LinkedHashSet<>();
        Set<String> headerParams = new LinkedHashSet<>();
        List<String> sigParts = new ArrayList<>();
        List<String> argNames = new ArrayList<>();
        List<Object> parameters = op.containsKey("parameters") ? list(op.get("parameters")) : List.of();
        for (String segment : path.split("/")) {
            if (segment.startsWith("{") && segment.endsWith("}")) {
                String name = segment.substring(1, segment.length() - 1);
                Map<String, Object> param = findParam(parameters, name);
                String type = "uuid".equals(str(obj(param.get("schema")).get("format"))) ? "UUID" : "String";
                sigParts.add(type + " " + safe(camel(name)));
                argNames.add(safe(camel(name)));
                pathParams.add(name);
            }
        }
        for (Object raw : parameters) {
            Map<String, Object> param = obj(raw);
            if ("query".equals(param.get("in"))) {
                queryParams.add(param.get("name").toString());
            } else if ("header".equals(param.get("in"))) {
                headerParams.add(param.get("name").toString());
            }
        }
        if (!queryParams.isEmpty()) {
            sigParts.add("Query query");
            argNames.add("query");
        }
        String bodyType = null;
        if (op.containsKey("requestBody")) {
            Map<String, Object> content = obj(obj(obj(op.get("requestBody")).get("content")).get("application/json"));
            bodyType = javaType(content.get("schema"), pascal(opId) + "Request");
            sigParts.add(bodyType + " body");
            argNames.add("body");
        }
        String pathArgs = pathParams.isEmpty() ? "NO_ARGS" : "new Object[] {"
                + String.join(", ", argNames.subList(0, pathParams.size())) + "}";
        String queryArg = queryParams.isEmpty() ? "null" : "query";
        String bodyArg = bodyType == null ? "null" : "body";

        String kind = "VOID";
        String dataType = "Void";
        String callKind = "callVoid";
        String typeRef = null;
        for (var entry : new TreeMap<>(obj(op.get("responses"))).entrySet()) {
            if (!entry.getKey().startsWith("2")) {
                continue;
            }
            Map<String, Object> content = obj(obj(entry.getValue()).get("content"));
            if (content.isEmpty()) {
                continue;
            }
            Object schema = obj(content.get("application/json")).get("schema");
            String ref = refName(schema);
            if (ref != null && ref.startsWith("ApiResponse_")) {
                kind = "ENVELOPE";
                dataType = box(javaType(obj(obj(schemas.get(ref)).get("properties")).get("data"), pascal(opId) + "Data"));
                callKind = "call";
                typeRef = "new TypeReference<" + dataType + ">() {}";
            } else if (ref != null && ref.startsWith("PagedResponse_")) {
                kind = "PAGED";
                Object items = obj(obj(obj(schemas.get(ref)).get("properties")).get("data")).get("items");
                String item = javaType(items, pascal(opId) + "Item");
                dataType = "PagedData<" + item + ">";
                callKind = "callPaged";
                typeRef = "new TypeReference<" + item + ">() {}";
            } else {
                kind = "BARE";
                dataType = box(javaType(schema, pascal(opId) + "Response"));
                callKind = "call";
                typeRef = "new TypeReference<" + dataType + ">() {}";
            }
            break;
        }
        boolean readOnly = "get".equals(httpMethod) || "head".equals(httpMethod)
                || ("authz".equals(tag) && "post".equals(httpMethod));

        StringBuilder out = new StringBuilder();
        out.append("        private static final Http.Operation ").append(constant)
                .append(" = new Http.Operation(\"").append(opId).append("\", \"").append(httpMethod.toUpperCase())
                .append("\", \"").append(path).append("\", ").append(setLiteral(queryParams)).append(", ")
                .append(setLiteral(headerParams)).append(", Http.Kind.").append(kind).append(", ")
                .append(readOnly).append(");\n\n");
        out.append("        /** {@code ").append(httpMethod.toUpperCase()).append(' ').append(path).append('}')
                .append(queryParams.isEmpty() ? "" : "; query " + queryParams)
                .append(headerParams.isEmpty() ? "" : "; headers " + headerParams).append(". */\n");
        List<String> withOptions = new ArrayList<>(sigParts);
        withOptions.add("CallOptions options");
        out.append("        public Result<").append(dataType).append("> ").append(method).append('(')
                .append(String.join(", ", withOptions)).append(") {\n")
                .append("            return http.").append(callKind).append('(').append(constant).append(", ")
                .append(pathArgs).append(", ").append(queryArg).append(", ").append(bodyArg).append(", options");
        if (typeRef != null) {
            out.append(", ").append(typeRef);
        }
        out.append(");\n        }\n\n");
        List<String> forward = new ArrayList<>(argNames);
        forward.add("CallOptions.defaults()");
        out.append("        /** Same as above with default {@link CallOptions}. */\n")
                .append("        public Result<").append(dataType).append("> ").append(method).append('(')
                .append(String.join(", ", sigParts)).append(") {\n            return ").append(method).append('(')
                .append(String.join(", ", forward)).append(");\n        }\n\n");
        return out.toString();
    }

    private static String box(String type) {
        return type;
    }

    private static Map<String, Object> findParam(List<Object> parameters, String name) {
        for (Object raw : parameters) {
            Map<String, Object> param = obj(raw);
            if (name.equals(param.get("name")) && "path".equals(param.get("in"))) {
                return param;
            }
        }
        throw new IllegalStateException("path parameter not declared: " + name);
    }

    private static String setLiteral(Set<String> values) {
        if (values.isEmpty()) {
            return "Set.of()";
        }
        List<String> quoted = new ArrayList<>();
        values.forEach(v -> quoted.add('"' + v + '"'));
        return "Set.of(" + String.join(", ", quoted) + ")";
    }

    // ------------------------------------------------------------------ types

    private static void indexPointers(Object node, String pointer) {
        if (node instanceof Map<?, ?> map) {
            pointers.put(node, pointer);
            map.forEach((key, value) -> indexPointers(value, pointer + "/" + key.toString().replace("~", "~0").replace("/", "~1")));
        } else if (node instanceof List<?> list) {
            for (int i = 0; i < list.size(); i++) indexPointers(list.get(i), pointer + "/" + i);
        }
    }

    private static String jsonText(Object value) {
        if (value == null) return "null";
        if (value instanceof String text) return "\"" + text.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + "\"";
        if (value instanceof Map<?, ?> map) {
            List<String> entries = new ArrayList<>();
            map.forEach((key, item) -> entries.add(jsonText(key.toString()) + ":" + jsonText(item)));
            entries.sort(String::compareTo);
            return "{" + String.join(",", entries) + "}";
        }
        if (value instanceof List<?> list) return "[" + String.join(",", list.stream().map(Generate::jsonText).toList()) + "]";
        return value.toString();
    }

    private static String openJson(Map<String, Object> schema) {
        String pointer = pointers.get(schema);
        if (pointer != null) dynamicExceptions.put(pointer, Map.of("pointer", pointer, "type", "JsonNode",
                "reason", "The reviewed schema allows arbitrary JSON values", "test", "ModelSemanticsTest.openJsonPreservesAllKinds"));
        return "JsonNode";
    }

    /** Tagged alternatives are records composed through a sealed interface; no field inheritance. */
    private static String union(String name, Map<String, Object> schema) {
        List<Object> variants = list(schema.get("oneOf"));
        String discriminator = null;
        for (String field : obj(obj(variants.get(0)).get("properties")).keySet()) {
            boolean tagged = true;
            Set<String> values = new LinkedHashSet<>();
            for (Object variant : variants) {
                Map<String, Object> prop = obj(obj(obj(variant).get("properties")).get(field));
                if (!(prop.get("enum") instanceof List<?> choices) || choices.size() != 1
                        || !values.add(choices.get(0).toString())) { tagged = false; break; }
            }
            if (tagged) { discriminator = field; break; }
        }
        if (discriminator == null) throw new IllegalArgumentException("union needs an unambiguous tag: " + name);
        StringBuilder out = new StringBuilder("    // Source: #/components/schemas/" + name + "\n");
        out.append("    @JsonTypeInfo(use = JsonTypeInfo.Id.NAME, property = \"").append(discriminator).append("\")\n")
                .append("    @JsonSubTypes({\n");
        List<String> records = new ArrayList<>();
        List<String> names = new ArrayList<>();
        for (int index = 0; index < variants.size(); index++) {
            Map<String, Object> branch = new LinkedHashMap<>(obj(variants.get(index)));
            pointers.put(branch, pointers.get(variants.get(index)));
            Map<String, Object> props = new LinkedHashMap<>(obj(branch.get("properties")));
            String tag = list(obj(props.remove(discriminator)).get("enum")).get(0).toString();
            String variantName = name + pascal(tag);
            names.add(variantName);
            out.append("        // Source: ").append(pointers.get(obj(obj(variants.get(index)).get("properties")).get(discriminator))).append('\n');
            out.append("        @JsonSubTypes.Type(value = ").append(variantName).append(".class, name = \"").append(tag).append("\")")
                    .append(index + 1 == variants.size() ? "\n" : ",\n");
            branch.put("properties", props);
            String generated = record(variantName, branch, variantName);
            records.add("    // Source: #/components/schemas/" + name + "/oneOf/" + index + "\n"
                    + generated.replaceFirst("\\) \\{", ") implements " + name + " {"));
        }
        provenance.add(Map.of("pointer", pointers.get(schema), "type", name,
                "file", "sdks/java/src/main/java/com/authgebra/client/Models.java", "fields", List.of()));
        out.append("    })\n    public sealed interface ").append(name).append(" permits ")
                .append(String.join(", ", names)).append(" { void validate(); }\n\n");
        records.forEach(out::append);
        return out.toString();
    }

    private static void rejectUnsupported(Map<String, Object> schema) {
        for (String keyword : List.of("not", "if", "then", "else", "dependentSchemas", "dependentRequired", "patternProperties", "unevaluatedProperties", "contains", "minContains", "maxContains", "prefixItems")) {
            if (schema.containsKey(keyword)) throw new IllegalArgumentException("unsupported structural schema keyword " + keyword + " at " + pointers.get(schema));
        }
        if (schema.get("type") instanceof List<?> types && types.stream().filter(value -> !value.equals("null")).count() > 1) {
            throw new IllegalArgumentException("unsupported primitive type union at " + pointers.get(schema));
        }
        if (schema.get("propertyNames") instanceof Map<?, ?> names && !names.equals(Map.of("type", "string"))) {
            throw new IllegalArgumentException("unsupported propertyNames constraint at " + pointers.get(schema));
        }
    }

    private static String javaType(Object schemaObj, String hint) {
        if (!(schemaObj instanceof Map<?, ?>)) {
            throw new IllegalArgumentException("schema object required at " + hint);
        }
        Map<String, Object> schema = obj(schemaObj);
        rejectUnsupported(schema);
        String ref = refName(schema);
        if (ref != null) {
            if (!schemas.containsKey(ref)) throw new IllegalArgumentException("unresolved schema reference: " + ref);
            Map<String, Object> target = obj(schemas.get(ref));
            if (target.containsKey("enum")) {
                return qualifier + ref;
            }
            if (target.containsKey("properties") || target.containsKey("oneOf")) return qualifier + ref;
            throw new IllegalArgumentException("unsupported referenced schema shape: " + ref);
        }
        if (schema.containsKey("oneOf") || schema.containsKey("anyOf") || schema.containsKey("allOf")) {
            List<Object> branches = list(schema.containsKey("oneOf") ? schema.get("oneOf")
                    : schema.containsKey("anyOf") ? schema.get("anyOf") : schema.get("allOf"));
            List<Object> real = new ArrayList<>();
            for (Object branch : branches) {
                if (!"null".equals(str(obj(branch).get("type")))) {
                    real.add(branch);
                }
            }
            if (real.size() == 1) return javaType(real.get(0), hint);
            throw new IllegalArgumentException("unsupported anonymous union: " + hint);
        }
        String type = primaryType(schema.get("type"));
        if (type == null) {
            if (!Set.of("description", "title", "example", "examples", "default", "deprecated", "readOnly", "writeOnly").containsAll(schema.keySet())) {
                throw new IllegalArgumentException("unsupported schema assertions without a type at " + pointers.get(schema));
            }
            return openJson(schema);
        }
        switch (type) {
            case "string":
                return "uuid".equals(str(schema.get("format"))) ? "UUID" : "String";
            case "integer":
                String integerFormat = str(schema.get("format"));
                if (integerFormat != null && !Set.of("int32", "int64", "uint32", "uint64").contains(integerFormat)) {
                    throw new IllegalArgumentException("unsupported integer format at " + pointers.get(schema));
                }
                if ("uint64".equals(integerFormat)) return "java.math.BigInteger";
                return "int32".equals(integerFormat) ? "Integer" : "Long";
            case "number":
                return "Double";
            case "boolean":
                return "Boolean";
            case "array":
                return "List<" + javaType(schema.get("items"), hint + "Item") + ">";
            case "object":
                if (schema.containsKey("properties")) {
                    return inline(schema, hint);
                }
                if (schema.get("additionalProperties") instanceof Map<?, ?>) {
                    Map<String, Object> ap = obj(schema.get("additionalProperties"));
                    return "Map<String, " + (ap.isEmpty() ? openJson(ap) : javaType(ap, hint + "Value")) + ">";
                }
                if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    Map<String, Object> closed = new LinkedHashMap<>(schema);
                    closed.put("properties", Map.of());
                    pointers.put(closed, pointers.get(schema));
                    return inline(closed, hint);
                }
                if (!schema.containsKey("additionalProperties") || Boolean.TRUE.equals(schema.get("additionalProperties"))) {
                    openJson(schema);
                    return "Map<String, JsonNode>";
                }
                throw new IllegalArgumentException("unsupported object properties at " + pointers.get(schema));
            default:
                throw new IllegalArgumentException("unsupported schema type at " + pointers.get(schemaObj));
        }
    }

    /** Wrapper payloads may reuse their explicitly named component when every machine keyword agrees. */
    private static String inline(Map<String, Object> schema, String hint) {
        String pointer = pointers.getOrDefault(schema, "");
        String[] segments = pointer.split("/");
        String expected = segments.length > 3 ? segments[3] : "";
        if (expected.startsWith("ApiResponse_") || expected.startsWith("PagedResponse_")) {
            expected = expected.substring(expected.indexOf('_') + 1);
            if (expected.startsWith("Vec_")) expected = expected.substring("Vec_".length());
        } else expected = "";
        Map<String, Object> component = obj(schemas.get(expected));
        if (!component.isEmpty() && machineShape(component).equals(machineShape(schema))) {
            return qualifier + expected;
        }
        if (!inlineRecords.containsKey(hint)) {
            inlineRecords.put(hint, "");
            inlineRecords.put(hint, record(hint, schema, "inline " + hint));
        }
        return qualifier + hint;
    }

    private static Object machineShape(Object value) {
        if (value instanceof Map<?, ?> source) {
            Map<String, Object> result = new TreeMap<>();
            source.forEach((key, item) -> {
                if (!Set.of("description", "title", "example", "examples", "externalDocs").contains(key.toString())) {
                    result.put(key.toString(), machineShape(item));
                }
            });
            return result;
        }
        if (value instanceof List<?> list) return list.stream().map(Generate::machineShape).toList();
        return value;
    }

    private static String enumName(Object schema) {
        String ref = refName(schema);
        if (ref != null) return obj(schemas.get(ref)).containsKey("enum") ? ref : null;
        Map<String, Object> node = obj(schema);
        for (String key : List.of("oneOf", "anyOf")) {
            if (node.get(key) instanceof List<?> arms) {
                List<?> values = arms.stream().filter(arm -> !"null".equals(obj(arm).get("type"))).toList();
                if (values.size() == 1) return enumName(values.get(0));
            }
        }
        return null;
    }

    private static String record(String name, Map<String, Object> schema, String doc) {
        Set<Object> required = new LinkedHashSet<>(schema.containsKey("required") ? list(schema.get("required")) : List.of());
        if (obj(schema.get("properties")).keySet().stream().anyMatch(key -> !required.contains(key))) {
            return optionalRecord(name, schema, doc);
        }
        String saved = qualifier;
        qualifier = "";
        try {
            StringBuilder out = new StringBuilder("    /** {@code " + doc.replace("*/", "") + "}. */\n")
                    .append("    // Source: ").append(pointers.get(schema)).append('\n')
                    .append("    @JsonIgnoreProperties(ignoreUnknown = true)\n")
                    .append("    @JsonInclude(JsonInclude.Include.NON_NULL)\n")
                    .append("    public record ").append(name).append("(\n");
            List<String> components = new ArrayList<>();
            List<String> legacyParams = new ArrayList<>();
            List<String> legacyArgs = new ArrayList<>();
            List<String> accessors = new ArrayList<>();
            List<Object> fields = new ArrayList<>();
            for (var property : obj(schema.get("properties")).entrySet()) {
                String jsonName = property.getKey();
                String type = javaType(property.getValue(), name + pascal(jsonName));
                boolean enumField = enumName(property.getValue()) != null;
                String field = safe(camel(jsonName));
                String member = enumField ? field + "Value" : field;
                String source = pointers.get(property.getValue());
                components.add("            // Source: " + source + "\n"
                        + (acceptsNull(obj(property.getValue())) ? "            @JsonInclude(JsonInclude.Include.ALWAYS)\n" : "")
                        + "            @JsonProperty(value = \"" + jsonName + "\", required = true) " + type + " " + member);
                fields.add(Map.of("pointer", source, "name", member, "json_name", jsonName, "type", type));
                legacyParams.add((enumField ? "Object" : "uint32".equals(concreteSchema(obj(property.getValue())).get("format")) ? "Number" : type) + " " + field);
                legacyArgs.add(enumField ? field + " == null ? null : " + field + " instanceof " + type
                        + " typed ? typed : new " + type + "(legacyEnum(" + field + "))"
                        : "uint32".equals(concreteSchema(obj(property.getValue())).get("format")) ? "legacyUnsigned32(" + field + ")" : field);
                if (enumField) accessors.add("        /** Compatible string view of the named enum. */\n"
                        + "        @JsonIgnore public String " + field + "() { return " + member + " == null ? null : " + member + ".value(); }\n");
            }
            out.append(String.join(",\n", components)).append("\n    ) {\n");
            if (!accessors.isEmpty() || obj(schema.get("properties")).values().stream().anyMatch(value -> "uint32".equals(concreteSchema(obj(value)).get("format")))) {
                out.append("        /** Compatibility constructor accepting former string enum values. */\n")
                        .append("        public ").append(name).append('(').append(String.join(", ", legacyParams))
                        .append(") { this(").append(String.join(", ", legacyArgs)).append("); }\n");
                accessors.forEach(out::append);
            }
            out.append(validationMethod(schema));
            if (name.endsWith("Request")) out.append("        /** Strict request JSON decoder; shared response models remain forward-compatible. */\n")
                    .append("        public static ").append(name).append(" fromJson(com.fasterxml.jackson.databind.ObjectMapper mapper, JsonNode input) throws java.io.IOException {\n")
                    .append("            if (input == null) throw new IllegalArgumentException(\"request JSON is required\");\n")
                    .append("            validate").append(name).append("RequestJSON(input);\n")
                    .append("            ").append(name).append(" value = mapper.treeToValue(input, ").append(name).append(".class); value.validate(); return value;\n        }\n");
            out.append("    }\n");
            provenance.add(Map.of("pointer", pointers.get(schema), "type", name,
                    "file", "sdks/java/src/main/java/com/authgebra/client/Models.java", "fields", fields));
            return out.toString();
        } finally {
            qualifier = saved;
        }
    }

    private static boolean acceptsNull(Map<String, Object> schema) {
        if ("null".equals(schema.get("type"))) return true;
        String ref = refName(schema);
        if (ref != null) return acceptsNull(obj(schemas.get(ref)));
        if (schema.get("type") instanceof List<?> types && types.contains("null")) return true;
        for (String key : List.of("oneOf", "anyOf")) {
            if (schema.get(key) instanceof List<?> arms && arms.stream().anyMatch(arm -> acceptsNull(obj(arm)))) return true;
        }
        return !schema.containsKey("type") && !schema.containsKey("enum") && !schema.containsKey("oneOf")
                && !schema.containsKey("anyOf") && !schema.containsKey("allOf") && !schema.containsKey("properties");
    }

    private static String optionalRecord(String name, Map<String, Object> schema, String doc) {
        String saved = qualifier;
        qualifier = "";
        try {
            Map<String, Object> props = obj(schema.get("properties"));
            Set<Object> required = new LinkedHashSet<>(schema.containsKey("required") ? list(schema.get("required")) : List.of());
            StringBuilder out = new StringBuilder("    /** {@code " + doc.replace("*/", "") + "}. */\n")
                    .append("    // Source: ").append(pointers.get(schema)).append('\n')
                    .append("    @JsonIgnoreProperties(ignoreUnknown = true)\n")
                    .append("    @JsonInclude(JsonInclude.Include.NON_NULL)\n")
                    .append("    public record ").append(name).append("(\n");
            List<String> components = new ArrayList<>();
            List<String> legacyParams = new ArrayList<>();
            List<String> legacyArgs = new ArrayList<>();
            List<String> factoryParams = new ArrayList<>();
            List<String> factoryArgs = new ArrayList<>();
            List<String> accessors = new ArrayList<>();
            List<String> checks = new ArrayList<>();
            List<String> clears = new ArrayList<>();
            List<Object> fields = new ArrayList<>();
            Map<String, String> members = new LinkedHashMap<>();
            Map<String, String> legacyValues = new LinkedHashMap<>();
            for (var property : props.entrySet()) {
                String jsonName = property.getKey();
                Map<String, Object> fieldSchema = obj(property.getValue());
                String type = javaType(fieldSchema, name + pascal(jsonName));
                boolean enumField = enumName(fieldSchema) != null;
                boolean optional = !required.contains(jsonName);
                boolean nullable = acceptsNull(fieldSchema);
                String field = safe(camel(jsonName));
                String typed = enumField ? field + "Value" : field;
                String member = optional ? typed + "Field" : typed;
                String componentType = optional ? "JsonField<" + type + ">" : type;
                String source = pointers.get(fieldSchema);
                components.add("            // Source: " + source + "\n"
                        + (optional ? "            @JsonInclude(JsonInclude.Include.NON_EMPTY)\n"
                            : nullable ? "            @JsonInclude(JsonInclude.Include.ALWAYS)\n" : "")
                        + "            @JsonProperty(value = \"" + jsonName + "\", required = " + !optional + ") "
                        + componentType + " " + member);
                fields.add(Map.of("pointer", source, "name", member, "json_name", jsonName, "type", componentType));
                members.put(jsonName, member);
                legacyParams.add((enumField ? "Object" : "uint32".equals(concreteSchema(obj(property.getValue())).get("format")) ? "Number" : type) + " " + field);
                String value = enumField ? field + " == null ? null : " + field + " instanceof " + type
                        + " typed ? typed : new " + type + "(legacyEnum(" + field + "))"
                        : "uint32".equals(concreteSchema(fieldSchema).get("format")) ? "legacyUnsigned32(" + field + ")" : field;
                legacyValues.put(jsonName, value);
                legacyArgs.add(optional ? "JsonField.fromNullable(" + value + ")" : value);
                factoryParams.add(componentType + " " + member);
                factoryArgs.add(member);
                if (optional) {
                    checks.add("            " + member + " = " + member + " == null ? JsonField.absent() : " + member + ";\n"
                            + (!nullable ? "            if (" + member + ".isNull()) throw new IllegalArgumentException(\"" + jsonName + " does not accept null\");\n" : ""));
                    accessors.add("        /** Compatible typed value view; inspect " + member + "() for presence. */\n"
                            + "        public " + type + " " + typed + "() { return " + member + ".orNull(); }\n");
                }
                if (enumField) accessors.add("        /** Compatible string view of the named enum. */\n"
                        + "        public String " + field + "() { return " + typed + "() == null ? null : " + typed + "().value(); }\n");
                if (Boolean.TRUE.equals(fieldSchema.get("x-authgebra-clearable"))) {
                    if (!optional || !nullable) throw new IllegalArgumentException("contradictory clearable field " + name + "." + jsonName);
                    clears.add(jsonName);
                }
            }
            // The ignored trailing marker prevents ambiguous legacy new Model(null, ...) overloads.
            components.add("            @JsonIgnore JsonField.Mode fieldsMode");
            out.append(String.join(",\n", components)).append("\n    ) {\n")
                    .append("        public ").append(name).append(" {\n");
            checks.forEach(out::append);
            out.append("            fieldsMode = JsonField.Mode.TYPED;\n        }\n")
                    .append("        /** Compatibility constructor; null continues to mean absent. */\n")
                    .append("        public ").append(name).append('(').append(String.join(", ", legacyParams))
                    .append(") { this(").append(String.join(", ", legacyArgs)).append(", JsonField.Mode.TYPED); }\n")
                    .append("        /** Construct explicit field states, including null and typed zero values. */\n")
                    .append("        public static ").append(name).append(" fromFields(").append(String.join(", ", factoryParams))
                    .append(") { return new ").append(name).append('(').append(String.join(", ", factoryArgs))
                    .append(", JsonField.Mode.TYPED); }\n");
            accessors.forEach(out::append);
            if (!clears.isEmpty()) {
                List<String> clearParams = new ArrayList<>(legacyParams);
                clears.forEach(field -> clearParams.add("boolean clear" + pascal(field)));
                List<String> clearArgs = new ArrayList<>();
                for (String jsonName : props.keySet()) {
                    String value = legacyValues.get(jsonName);
                    if (clears.contains(jsonName)) clearArgs.add("JsonField.legacy(" + value + ", clear" + pascal(jsonName) + ")");
                    else clearArgs.add(required.contains(jsonName) ? value : "JsonField.fromNullable(" + value + ")");
                }
                out.append("        public ").append(name).append('(').append(String.join(", ", clearParams))
                        .append(") { this(").append(String.join(", ", clearArgs)).append(", JsonField.Mode.TYPED); }\n");
                for (String field : clears) {
                    List<String> args = new ArrayList<>();
                    props.keySet().forEach(key -> args.add(key.equals(field) ? "JsonField.nullValue()" : members.get(key)));
                    out.append("        public boolean clear").append(pascal(field)).append("() { return ").append(members.get(field)).append(".isNull(); }\n")
                            .append("        public ").append(name).append(" withNull").append(pascal(field)).append("() { return new ")
                            .append(name).append('(').append(String.join(", ", args)).append(", JsonField.Mode.TYPED); }\n");
                }
            }
            out.append(validationMethod(schema));
            if (name.endsWith("Request")) out.append("        /** Strict request JSON decoder; shared response models remain forward-compatible. */\n")
                    .append("        public static ").append(name).append(" fromJson(com.fasterxml.jackson.databind.ObjectMapper mapper, JsonNode input) throws java.io.IOException {\n")
                    .append("            if (input == null) throw new IllegalArgumentException(\"request JSON is required\");\n")
                    .append("            validate").append(name).append("RequestJSON(input);\n")
                    .append("            ").append(name).append(" value = mapper.treeToValue(input, ").append(name).append(".class); value.validate(); return value;\n        }\n");
            out.append("    }\n");
            provenance.add(Map.of("pointer", pointers.get(schema), "type", name,
                    "file", "sdks/java/src/main/java/com/authgebra/client/Models.java", "fields", fields));
            return out.toString();
        } finally {
            qualifier = saved;
        }
    }

    private static Map<String, Object> concreteSchema(Map<String, Object> schema) {
        String reference = refName(schema);
        if (reference != null) return concreteSchema(obj(schemas.get(reference)));
        for (String key : List.of("oneOf", "anyOf", "allOf")) {
            if (schema.get(key) instanceof List<?> arms) {
                List<?> values = arms.stream().filter(arm -> !"null".equals(obj(arm).get("type"))).toList();
                if (values.size() == 1) return concreteSchema(obj(values.get(0)));
            }
        }
        return schema;
    }

    private static String validationValue(Map<String, Object> original, String expression, String path) {
        Map<String, Object> schema = concreteSchema(original);
        StringBuilder out = new StringBuilder();
        if (enumName(original) != null || schema.containsKey("properties") || schema.containsKey("oneOf")) {
            out.append("                ").append(expression).append(".validate();\n");
            return out.toString();
        }
        String type = primaryType(schema.get("type"));
        if (type == null) return "";
        switch (type) {
            case "integer", "number" -> {
                for (String key : List.of("minimum", "maximum")) {
                    if (!schema.containsKey(key)) continue;
                    String operator = key.equals("minimum") ? " < 0" : " > 0";
                    String value = schema.get(key).toString();
                    String comparison;
                    if ("uint64".equals(schema.get("format"))) {
                        comparison = expression + ".compareTo(new BigInteger(" + jsonText(value) + "))" + operator;
                    } else if (type.equals("integer")) {
                        comparison = expression + ".longValue()" + (key.equals("minimum") ? " < " : " > ") + value + "L";
                    } else {
                        comparison = expression + ".doubleValue()" + (key.equals("minimum") ? " < " : " > ") + value;
                    }
                    out.append("                if (").append(comparison).append(") throw new IllegalArgumentException(")
                            .append(jsonText(path + " violates " + key)).append(");\n");
                }
            }
            case "string" -> {
                for (String key : List.of("minLength", "maxLength")) {
                    if (!schema.containsKey(key)) continue;
                    out.append("                if (").append(expression).append(".codePointCount(0, ").append(expression).append(".length())")
                            .append(key.equals("minLength") ? " < " : " > ").append(schema.get(key))
                            .append(") throw new IllegalArgumentException(").append(jsonText(path + " violates " + key)).append(");\n");
                }
                if (schema.get("pattern") instanceof String pattern) {
                    out.append("                if (!java.util.regex.Pattern.compile(").append(jsonText(pattern)).append(").matcher(")
                            .append(expression).append(pattern.startsWith("^") && pattern.endsWith("$") ? ").matches()) throw new IllegalArgumentException(" : ").find()) throw new IllegalArgumentException(")
                            .append(jsonText(path + " violates pattern")).append(");\n");
                }
            }
            case "array" -> {
                for (String key : List.of("minItems", "maxItems")) {
                    if (!schema.containsKey(key)) continue;
                    out.append("                if (").append(expression).append(".size()")
                            .append(key.equals("minItems") ? " < " : " > ").append(schema.get(key))
                            .append(") throw new IllegalArgumentException(").append(jsonText(path + " violates " + key)).append(");\n");
                }
                if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                    Map<String, Object> itemSchema = concreteSchema(obj(schema.get("items")));
                    String itemType = primaryType(itemSchema.get("type"));
                    if (itemType == null || !List.of("string", "integer", "number", "boolean").contains(itemType)) {
                        throw new IllegalArgumentException("uniqueItems requires supported scalar elements at " + path);
                    }
                    out.append("                if (new java.util.HashSet<>(").append(expression).append(").size() != ")
                            .append(expression).append(".size()) throw new IllegalArgumentException(")
                            .append(jsonText(path + " violates uniqueItems")).append(");\n");
                }
                String item = "entry" + path.length();
                String inner = validationValue(obj(schema.get("items")), item, path + "[]");
                if (!inner.isEmpty()) out.append("                for (var ").append(item).append(" : ").append(expression)
                        .append(") { if (").append(item).append(" != null) {\n").append(inner).append("                } }\n");
            }
            case "object" -> {
                if (schema.get("additionalProperties") instanceof Map<?, ?> values) {
                    String item = "entry" + path.length();
                    String inner = validationValue(obj(values), item, path + ".*");
                    if (!inner.isEmpty()) out.append("                for (var ").append(item).append(" : ").append(expression)
                            .append(".values()) { if (").append(item).append(" != null) {\n").append(inner).append("                } }\n");
                }
            }
            default -> { }
        }
        return out.toString();
    }

    private static String validationMethod(Map<String, Object> schema) {
        StringBuilder out = new StringBuilder("        /** Check declared constraints without applying defaults or performing HTTP. */\n")
                .append("        public void validate() {\n");
        Set<Object> required = new LinkedHashSet<>(schema.containsKey("required") ? list(schema.get("required")) : List.of());
        for (var property : obj(schema.get("properties")).entrySet()) {
            String field = safe(camel(property.getKey()));
            if (enumName(property.getValue()) != null) field += "Value";
            String expression = field + "()";
            Map<String, Object> fieldSchema = obj(property.getValue());
            if (required.contains(property.getKey()) && !acceptsNull(fieldSchema)) {
                out.append("            if (").append(expression).append(" == null) throw new IllegalArgumentException(")
                        .append(jsonText(property.getKey() + " is required and non-nullable")).append(");\n");
            }
            String body = validationValue(fieldSchema, expression, property.getKey());
            if (!body.isEmpty()) out.append("            if (").append(expression).append(" != null) {\n").append(body).append("            }\n");
        }
        return out.append("        }\n").toString();
    }

    private static void visitRequestReferences(Map<String, Object> schema, Set<String> names) {
        String reference = refName(schema);
        if (reference != null) {
            if (names.add(reference)) visitRequestReferences(obj(schemas.get(reference)), names);
            return;
        }
        obj(schema.get("properties")).values().forEach(value -> visitRequestReferences(obj(value), names));
        if (schema.get("items") instanceof Map<?, ?> items) visitRequestReferences(obj(items), names);
        if (schema.get("additionalProperties") instanceof Map<?, ?> values) visitRequestReferences(obj(values), names);
        for (String key : List.of("oneOf", "anyOf", "allOf")) {
            if (schema.get(key) instanceof List<?> arms) arms.forEach(arm -> visitRequestReferences(obj(arm), names));
        }
    }

    private static String requestJsonValidators() {
        Set<String> names = new java.util.TreeSet<>();
        for (String name : schemas.keySet()) if (name.endsWith("Request")) {
            names.add(name); visitRequestReferences(obj(schemas.get(name)), names);
        }
        StringBuilder out = new StringBuilder();
        for (String name : names) out.append("    private static void validate").append(name).append("RequestJSON(JsonNode input) {\n")
                .append(requestJsonBody(obj(schemas.get(name)), "input", 0)).append("    }\n");
        return out.toString();
    }

    private static String requestJsonBody(Map<String, Object> schema, String expression, int depth) {
        String reference = refName(schema);
        if (reference != null) return "        validate" + reference + "RequestJSON(" + expression + ");\n";
        StringBuilder out = new StringBuilder();
        if (schema.containsKey("enum")) {
            out.append("        if (!").append(expression).append(".isTextual()) throw new IllegalArgumentException(\"enum must be a string\");\n")
                    .append("        switch (").append(expression).append(".asText()) { case ")
                    .append(String.join(",", list(schema.get("enum")).stream().map(Generate::jsonText).toList()))
                    .append(" -> {} default -> throw new IllegalArgumentException(\"unknown request enum value\"); }\n");
            return out.toString();
        }
        for (String union : List.of("oneOf", "anyOf", "allOf")) {
            if (!(schema.get(union) instanceof List<?> arms)) continue;
            List<?> real = arms.stream().filter(arm -> !"null".equals(obj(arm).get("type"))).toList();
            if (real.size() == 1) return requestJsonBody(obj(real.get(0)), expression, depth + 1);
            String tagKey = null;
            for (String key : obj(obj(real.get(0)).get("properties")).keySet()) {
                if (real.stream().allMatch(arm -> obj(obj(obj(arm).get("properties")).get(key)).get("enum") instanceof List<?> tags && tags.size() == 1)) {tagKey = key; break;}
            }
            if (tagKey == null) throw new IllegalArgumentException("request union lacks a stable discriminator");
            out.append("        switch (").append(expression).append(".path(").append(jsonText(tagKey)).append(").asText()) {\n");
            for (Object arm : real) {
                String tag = list(obj(obj(obj(arm).get("properties")).get(tagKey)).get("enum")).get(0).toString();
                out.append("        case ").append(jsonText(tag)).append(" -> {\n")
                        .append(requestJsonBody(obj(arm), expression, depth + 1)).append("        }\n");
            }
            return out.append("        default -> throw new IllegalArgumentException(\"unknown request union variant\"); }\n").toString();
        }
        String kind = primaryType(schema.get("type"));
        if (kind == null) return "";
        switch (kind) {
            case "object" -> {
                out.append("        if (!").append(expression).append(".isObject()) throw new IllegalArgumentException(\"request object expected\");\n");
                Map<String, Object> properties = obj(schema.get("properties"));
                if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    String iterator = "keys" + depth;
                    out.append("        for (var ").append(iterator).append(" = ").append(expression).append(".fieldNames(); ").append(iterator).append(".hasNext();) {\n")
                            .append("            if (!java.util.Set.of(").append(String.join(",", properties.keySet().stream().map(Generate::jsonText).toList()))
                            .append(").contains(").append(iterator).append(".next())) throw new IllegalArgumentException(\"unknown request property\"); }\n");
                }
                Set<Object> required = new LinkedHashSet<>(schema.containsKey("required") ? list(schema.get("required")) : List.of());
                for (var field : properties.entrySet()) {
                    String value = expression + ".get(" + jsonText(field.getKey()) + ")";
                    if (required.contains(field.getKey())) out.append("        if (!").append(expression).append(".has(")
                            .append(jsonText(field.getKey())).append(")) throw new IllegalArgumentException(").append(jsonText(field.getKey() + " is required")).append(");\n");
                    out.append("        if (").append(value).append(" != null) {\n");
                    if (!acceptsNull(obj(field.getValue()))) out.append("        if (").append(value).append(".isNull()) throw new IllegalArgumentException(")
                            .append(jsonText(field.getKey() + " does not accept null")).append(");\n");
                    String body = requestJsonBody(obj(field.getValue()), value, depth + 1);
                    if (!body.isEmpty()) out.append("        if (!").append(value).append(".isNull()) {\n").append(body).append("        }\n");
                    out.append("        }\n");
                }
                if (schema.get("additionalProperties") instanceof Map<?, ?> values) {
                    String item = "entry" + depth;
                    String body = requestJsonBody(obj(values), item, depth + 1);
                    if (!body.isEmpty()) {
                        out.append("        for (var ").append(item).append(" : ").append(expression).append(") {\n");
                        if (acceptsNull(obj(values))) out.append("        if (!").append(item).append(".isNull()) {\n").append(body).append("        }\n");
                        else out.append("        if (").append(item).append(".isNull()) throw new IllegalArgumentException(\"null map value\");\n").append(body);
                        out.append("        }\n");
                    }
                }
            }
            case "array" -> {
                String item = "entry" + depth;
                out.append("        if (!").append(expression).append(".isArray()) throw new IllegalArgumentException(\"request array expected\");\n");
                String body = requestJsonBody(obj(schema.get("items")), item, depth + 1);
                if (!body.isEmpty()) {
                    out.append("        for (var ").append(item).append(" : ").append(expression).append(") {\n");
                    if (acceptsNull(obj(schema.get("items")))) out.append("        if (!").append(item).append(".isNull()) {\n").append(body).append("        }\n");
                    else out.append("        if (").append(item).append(".isNull()) throw new IllegalArgumentException(\"null array item\");\n").append(body);
                    out.append("        }\n");
                }
            }
            case "string", "integer", "number", "boolean" -> {
                String predicate = switch (kind) {case "string" -> "isTextual";case "integer" -> "isIntegralNumber";case "number" -> "isNumber";default -> "isBoolean";};
                out.append("        if (!").append(expression).append('.').append(predicate).append("()) throw new IllegalArgumentException(\"request scalar type mismatch\");\n");
            }
            default -> throw new IllegalArgumentException("unsupported request schema kind " + kind);
        }
        return out.toString();
    }

    private static String enumHolder(String name, Map<String, Object> schema) {
        StringBuilder sb = new StringBuilder();
        sb.append("    /** Open string enum; unknown values survive round trips. */\n")
                .append("    // Source: ").append(pointers.get(schema)).append('\n')
                .append("    public record ").append(name).append("(@JsonValue String value) {\n")
                .append("        @JsonCreator(mode = JsonCreator.Mode.DELEGATING)\n        public ").append(name)
                .append(" { java.util.Objects.requireNonNull(value, \"value\"); }\n");
        sb.append("        @Override public String toString() { return value; }\n");        sb.append("        public void validate() { switch(value) { case ");
        List<String> enumValues = list(schema.get("enum")).stream().map(Generate::jsonText).toList();
        sb.append(String.join(",", enumValues)).append(" -> {} default -> throw new IllegalArgumentException(\"invalid ")
                .append(name).append(" value\"); } }\n");
        for (Object value : list(schema.get("enum"))) {
            String text = value.toString();
            String id = text.replaceAll("[^A-Za-z0-9]", "_").toUpperCase();
            if (id.isEmpty() || Character.isDigit(id.charAt(0))) {
                id = "V_" + id;
            }
            sb.append("        public static final ").append(name).append(' ').append(id).append(" = new ")
                    .append(name).append("(\"").append(text).append("\");\n");
        }
        sb.append("    }\n");
        provenance.add(Map.of("pointer", pointers.get(schema), "type", name,
                "file", "sdks/java/src/main/java/com/authgebra/client/Models.java", "fields", List.of()));
        return sb.toString();
    }

    // ---------------------------------------------------------------- helpers

    private static String primaryType(Object type) {
        if (type instanceof String s) {
            return s;
        }
        if (type instanceof List<?> l) {
            List<String> real = new ArrayList<>();
            for (Object item : l) {
                if (!"null".equals(item)) {
                    real.add(item.toString());
                }
            }
            return real.size() == 1 ? real.get(0) : null;
        }
        return null;
    }

    private static String refName(Object schema) {
        if (schema instanceof Map<?, ?> map && map.get("$ref") instanceof String ref) {
            if (!ref.startsWith("#/components/schemas/") || ref.substring("#/components/schemas/".length()).contains("/")) {
                throw new IllegalArgumentException("unsupported non-local schema reference: " + ref);
            }
            return ref.substring("#/components/schemas/".length());
        }
        return null;
    }

    private static String camel(String snake) {
        StringBuilder sb = new StringBuilder();
        boolean up = false;
        for (char c : snake.toCharArray()) {
            if (c == '_' || c == '-' || c == '.') {
                up = true;
            } else if (up) {
                sb.append(Character.toUpperCase(c));
                up = false;
            } else {
                sb.append(c);
            }
        }
        return sb.toString();
    }

    private static String pascal(String snake) {
        String c = camel(snake);
        return c.isEmpty() ? c : Character.toUpperCase(c.charAt(0)) + c.substring(1);
    }

    private static String safe(String identifier) {
        return RESERVED.contains(identifier) ? identifier + "_" : identifier;
    }

    private static String str(Object value) {
        return value == null ? null : value.toString();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> obj(Object value) {
        return value instanceof Map<?, ?> ? (Map<String, Object>) value : new LinkedHashMap<>();
    }

    @SuppressWarnings("unchecked")
    private static List<Object> list(Object value) {
        return (List<Object>) value;
    }

    private static Object path(Object root, String... keys) {
        Object current = root;
        for (String key : keys) {
            current = obj(current).get(key);
        }
        return current;
    }

    private static String sha256(byte[] bytes) throws Exception {
        StringBuilder sb = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(bytes)) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    private static Path findSpec(String[] args) throws IOException {
        if (args.length > 0) {
            return Path.of(args[0]);
        }
        for (String candidate : new String[] {"../contracts/openapi.json", "../../contracts/openapi.json"}) {
            if (Files.exists(Path.of(candidate))) {
                return Path.of(candidate);
            }
        }
        throw new IOException("openapi.json not found; pass its path as the first argument");
    }

    private static Path locateOutDir() {
        Path cwd = Path.of("").toAbsolutePath();
        return cwd.getFileName().toString().equals("tools")
                ? cwd.getParent().resolve("src/main/java")
                : cwd.resolve("src/main/java");
    }

    // ------------------------------------------------------------- JSON parser

    private static final class Json {
        private final String s;
        private int i;

        Json(String s) {
            this.s = s;
        }

        Object parse() {
            Object value = value();
            ws();
            if (i != s.length()) {
                throw new IllegalStateException("trailing JSON at " + i);
            }
            return value;
        }

        private void ws() {
            while (i < s.length() && Character.isWhitespace(s.charAt(i))) {
                i++;
            }
        }

        private Object value() {
            ws();
            char c = s.charAt(i);
            switch (c) {
                case '{': {
                    i++;
                    Map<String, Object> map = new LinkedHashMap<>();
                    ws();
                    if (s.charAt(i) == '}') {
                        i++;
                        return map;
                    }
                    while (true) {
                        ws();
                        String key = string();
                        ws();
                        expect(':');
                        map.put(key, value());
                        ws();
                        if (s.charAt(i) == ',') {
                            i++;
                            continue;
                        }
                        expect('}');
                        return map;
                    }
                }
                case '[': {
                    i++;
                    List<Object> list = new ArrayList<>();
                    ws();
                    if (s.charAt(i) == ']') {
                        i++;
                        return list;
                    }
                    while (true) {
                        list.add(value());
                        ws();
                        if (s.charAt(i) == ',') {
                            i++;
                            continue;
                        }
                        expect(']');
                        return list;
                    }
                }
                case '"':
                    return string();
                case 't':
                    i += 4;
                    return Boolean.TRUE;
                case 'f':
                    i += 5;
                    return Boolean.FALSE;
                case 'n':
                    i += 4;
                    return null;
                default: {
                    int start = i;
                    while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) {
                        i++;
                    }
                    String number = s.substring(start, i);
                    return number.contains(".") || number.contains("e") || number.contains("E")
                            ? (Object) new java.math.BigDecimal(number) : (Object) new java.math.BigInteger(number);
                }
            }
        }

        private void expect(char c) {
            if (s.charAt(i) != c) {
                throw new IllegalStateException("expected '" + c + "' at " + i);
            }
            i++;
        }

        private String string() {
            expect('"');
            StringBuilder sb = new StringBuilder();
            while (true) {
                char c = s.charAt(i++);
                if (c == '"') {
                    return sb.toString();
                }
                if (c == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case 'n' -> sb.append('\n');
                        case 't' -> sb.append('\t');
                        case 'r' -> sb.append('\r');
                        case 'b' -> sb.append('\b');
                        case 'f' -> sb.append('\f');
                        case 'u' -> {
                            sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                            i += 4;
                        }
                        default -> sb.append(e);
                    }
                } else {
                    sb.append(c);
                }
            }
        }
    }
}
