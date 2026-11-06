package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.lang.reflect.ParameterizedType;
import java.lang.reflect.RecordComponent;
import java.lang.reflect.Type;
import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ModelFieldCoverageTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    private static Class<?> raw(Type type) {
        return type instanceof ParameterizedType parameterized ? (Class<?>) parameterized.getRawType() : (Class<?>) type;
    }

    private static Type valueType(Type type) {
        return raw(type) == JsonField.class ? ((ParameterizedType) type).getActualTypeArguments()[0] : type;
    }

    private static void checkType(JsonNode document, JsonNode schema, Type declared, String pointer) throws Exception {
        Type type = valueType(declared);
        Class<?> actual = raw(type);
        if (schema.has("$ref")) {
            String reference = schema.get("$ref").asText();
            String name = reference.substring(reference.lastIndexOf('/') + 1);
            assertEquals(Class.forName("com.authgebra.client.Models$" + name), actual, pointer);
            return;
        }
        for (String union : List.of("oneOf", "anyOf", "allOf")) {
            if (!schema.has(union)) continue;
            List<JsonNode> arms = new ArrayList<>();
            schema.get(union).forEach(arm -> { if (!"null".equals(arm.path("type").asText())) arms.add(arm); });
            assertEquals(1, arms.size(), "anonymous union must have a named representation: " + pointer);
            checkType(document, arms.get(0), type, pointer);
            return;
        }
        JsonNode kind = schema.get("type");
        String primary = kind == null ? "" : kind.isTextual() ? kind.asText() :
                java.util.stream.StreamSupport.stream(kind.spliterator(), false).map(JsonNode::asText)
                        .filter(value -> !value.equals("null")).findFirst().orElse("");
        switch (primary) {
            case "string" -> assertEquals(schema.path("format").asText().equals("uuid") ? UUID.class : String.class, actual, pointer);
            case "boolean" -> assertEquals(Boolean.class, actual, pointer);
            case "integer" -> assertEquals(switch (schema.path("format").asText()) {
                case "uint64" -> BigInteger.class;
                case "int32" -> Integer.class;
                default -> Long.class;
            }, actual, pointer);
            case "number" -> assertEquals(Double.class, actual, pointer);
            case "array" -> {
                assertEquals(List.class, actual, pointer);
                checkType(document, schema.get("items"), ((ParameterizedType) type).getActualTypeArguments()[0], pointer + "/items");
            }
            case "object" -> {
                if (schema.has("properties")) {
                    assertTrue(actual.isRecord(), "known object lost its named record: " + pointer);
                    assertFalse(actual == JsonNode.class, pointer);
                } else if (schema.path("additionalProperties").isObject()) {
                    assertEquals(Map.class, actual, pointer);
                    Type[] arguments = ((ParameterizedType) type).getActualTypeArguments();
                    assertEquals(String.class, arguments[0], pointer);
                    checkType(document, schema.get("additionalProperties"), arguments[1], pointer + "/additionalProperties");
                } else assertEquals(JsonNode.class, actual, "only open objects may use dynamic JSON: " + pointer);
            }
            case "" -> assertEquals(JsonNode.class, actual, "only unconstrained schemas may use dynamic JSON: " + pointer);
            default -> fail("unverified schema kind " + primary + " at " + pointer);
        }
    }

    @Test
    void everyRootPropertyHasItsActualTypedJsonMember() throws Exception {
        JsonNode document = JSON.readTree(Files.readString(Path.of("../contracts/openapi.json")));
        var roots = document.at("/components/schemas").fields();
        int checked = 0;
        while (roots.hasNext()) {
            var entry = roots.next();
            JsonNode schema = entry.getValue();
            if (!schema.has("properties")) continue;
            Class<?> model = Class.forName("com.authgebra.client.Models$" + entry.getKey());
            assertTrue(model.isRecord(), entry.getKey());
            Map<String, RecordComponent> members = new LinkedHashMap<>();
            for (RecordComponent component : model.getRecordComponents()) {
                JsonProperty annotation = component.getAccessor().getAnnotation(JsonProperty.class);
                if (annotation != null) assertNull(members.put(annotation.value(), component), "duplicate JSON name");
            }
            Set<String> expected = new java.util.LinkedHashSet<>();
            schema.get("properties").fieldNames().forEachRemaining(expected::add);
            assertEquals(expected, members.keySet(), entry.getKey());
            var properties = schema.get("properties").fields();
            while (properties.hasNext()) {
                var property = properties.next();
                String pointer = "#/components/schemas/" + entry.getKey() + "/properties/" + property.getKey();
                RecordComponent component = members.get(property.getKey());
                boolean required = false;
                for (JsonNode value : schema.path("required")) if (value.asText().equals(property.getKey())) required = true;
                assertEquals(required, component.getAccessor().getAnnotation(JsonProperty.class).required(), pointer);
                assertEquals(!required, raw(component.getGenericType()) == JsonField.class, pointer + " presence state");
                checkType(document, property.getValue(), component.getGenericType(), pointer);
                checked++;
            }
        }
        assertTrue(checked > 0);
        System.out.println("schema properties checked against native record members: " + checked);
    }
}
