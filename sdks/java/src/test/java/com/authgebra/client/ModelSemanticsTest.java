package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;

class ModelSemanticsTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    @Test
    void taggedModelsPreserveTheirWireShapeAndPrecision() throws Exception {
        for (String text : List.of("{\"kind\":\"direct\"}", "{\"kind\":\"include\",\"relation\":\"member\"}",
                "{\"kind\":\"inherit\",\"parent_object_type\":\"doc\",\"parent_relation\":\"parent\",\"relation\":\"viewer\"}")) {
            var value = JSON.readValue(text, Models.RewriteRuleDto.class);
            assertEquals(JSON.readTree(text), JSON.valueToTree(value));
        }
        String unsigned = "{\"type\":\"u64\",\"value\":18446744073709551615}";
        var value = JSON.readValue(unsigned, Models.TypedValueDto.class);
        assertEquals(new BigInteger("18446744073709551615"), ((Models.TypedValueDtoU64) value).value());
        assertEquals(JSON.readTree(unsigned), JSON.valueToTree(value));
        assertThrows(Exception.class, () -> JSON.readValue("{\"kind\":\"unregistered\"}", Models.RewriteRuleDto.class));
    }

    @Test
    void openJsonPreservesAllKinds() throws Exception {
        JsonNode vectors = JSON.readTree(Files.readString(Path.of("../fixtures/request-semantics.json")));
        for (JsonNode node : vectors.get("patch_values")) {
            var decoded = JSON.treeToValue(node, JsonNode.class);
            assertEquals(node, JSON.readTree(JSON.writeValueAsBytes(decoded)));
        }
        for (JsonNode node : vectors.get("integer_values")) {
            assertEquals(node.bigIntegerValue(), JSON.readTree(JSON.writeValueAsBytes(node)).bigIntegerValue());
        }
    }

    @Test
    void namedEnumsRetainLegacyStringViewsAndUnknownValues() throws Exception {
        var value = new Models.RevisionResponse(7L, 7L, "newer_status");
        assertEquals("newer_status", value.status());
        assertEquals(new Models.RevisionStatus("newer_status"), value.statusValue());
        assertEquals("newer_status", JSON.valueToTree(value).get("status").asText());
    }

    @Test
    void callerIdsFollowTheSharedVectors() throws Exception {
        var cases = JSON.readTree(Files.readString(Path.of("../fixtures/request-semantics.json"))).get("request_ids");
        for (JsonNode item : cases) {
            String supplied = item.get("value").asText();
            var options = CallOptions.builder().requestId(supplied).build();
            if (item.get("valid").asBoolean()) assertEquals(supplied, options.requestId());
            else assertNull(options.requestId());
        }
    }
}
