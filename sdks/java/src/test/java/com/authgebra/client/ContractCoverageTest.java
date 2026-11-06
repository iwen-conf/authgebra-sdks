package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;

/** The SDK must cover the reviewed contract, and must have been regenerated from exactly that contract. */
class ContractCoverageTest {
    private static final Path OPENAPI = Path.of("..", "contracts", "openapi.json");
    private static final ObjectMapper JSON = new ObjectMapper();

    private static String camel(String snake) {
        StringBuilder sb = new StringBuilder();
        boolean up = false;
        for (char c : snake.toCharArray()) {
            if (c == '_') {
                up = true;
            } else {
                sb.append(up ? Character.toUpperCase(c) : c);
                up = false;
            }
        }
        return sb.toString();
    }

    private static Set<String> specOperationIds() throws Exception {
        Set<String> ids = new HashSet<>();
        for (JsonNode path : JSON.readTree(Files.readAllBytes(OPENAPI)).get("paths")) {
            for (JsonNode operation : path) {
                ids.add(operation.get("operationId").asText());
            }
        }
        return ids;
    }

    @Test
    void everyOperationInTheContractHasAMethod() throws Exception {
        Set<String> ids = specOperationIds();
        assertTrue(!ids.isEmpty(), "the reviewed contract must contain operations");
        List<String> missing = new ArrayList<>();
        for (String id : ids) {
            boolean found = false;
            for (Class<?> group : AdminClient.class.getClasses()) {
                for (Method m : group.getMethods()) {
                    found |= m.getName().equals(camel(id)) || m.getName().equals(camel(id) + "_");
                }
            }
            if (!found) {
                missing.add(id);
            }
        }
        assertTrue(missing.isEmpty(), "operations without an SDK method: " + missing);
    }

    @Test
    void sdkWasGeneratedFromTheCurrentContract() throws Exception {
        byte[] bytes = Files.readAllBytes(OPENAPI);
        StringBuilder sb = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(bytes)) {
            sb.append(String.format("%02x", b));
        }
        assertEquals(sb.toString(), OpenApiDigest.SHA256,
                "contracts/openapi.json changed: run `java tools/Generate.java` and review the diff");
        assertEquals(Files.readString(OPENAPI.resolveSibling("openapi.sha256")).trim(), OpenApiDigest.SHA256);
        assertEquals(specOperationIds(), new HashSet<>(OpenApiDigest.OPERATION_IDS));
    }

    @Test
    void knownErrorCodesMatchTheContractEnum() throws Exception {
        Set<String> declared = new HashSet<>();
        JSON.readTree(Files.readAllBytes(OPENAPI)).get("components").get("schemas").get("ApiErrorCode").get("enum")
                .forEach(n -> declared.add(n.asText()));
        assertEquals(declared, ErrorCodes.KNOWN);
    }
}
