package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class GeneratedArtifactMutationTest {
    @TempDir Path temporary;

    private int generate(boolean check) throws Exception {
        List<String> args = new ArrayList<>(List.of(
                Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                "tools/Generate.java", "../contracts/openapi.json", temporary.toString()));
        if (check) args.add("--check");
        Process process = new ProcessBuilder(args).redirectErrorStream(true)
                .redirectOutput(temporary.resolve("generator.log").toFile()).start();
        assertTrue(process.waitFor(30, java.util.concurrent.TimeUnit.SECONDS), "generator exceeded its test budget");
        return process.exitValue();
    }

    @Test
    void regeneratedBytesDetectFieldTagNullabilityEnumAndOperationDamage() throws Exception {
        assertEquals(0, generate(false));
        assertEquals(0, generate(true));
        Path models = temporary.resolve("com/authgebra/client/Models.java");
        String baseline = Files.readString(models);
        for (String[] mutation : List.of(
                new String[]{"            @JsonProperty(value = \"subject_id\", required = true) UUID subjectId", ""},
                new String[]{"value = \"subject_id\"", "value = \"subject_typo\""},
                new String[]{"JsonField<String> emailField", "String emailField"},
                new String[]{"new CommitState(\"committed_unpublished\")", "new CommitState(\"wrong\")"},
                new String[]{"JsonField<AuthzResourceRef> resourceField", "JsonField<JsonNode> resourceField"})) {
            String damaged = baseline.replaceFirst(java.util.regex.Pattern.quote(mutation[0]),
                    java.util.regex.Matcher.quoteReplacement(mutation[1]));
            assertNotEquals(baseline, damaged, "mutation did not target a real generated declaration");
            Files.writeString(models, damaged);
            assertNotEquals(0, generate(true), "generated corruption passed --check");
            assertEquals(damaged, Files.readString(models), "--check repaired rather than detected the corruption");
            Files.writeString(models, baseline);
        }
        Path operations = temporary.resolve("com/authgebra/client/AdminClient.java");
        String original = Files.readString(operations);
        String damaged = original.replaceFirst("Models.CreateUserRequest body", "Models.CreateRoleRequest body");
        assertNotEquals(original, damaged);
        Files.writeString(operations, damaged);
        assertNotEquals(0, generate(true));
        assertEquals(damaged, Files.readString(operations));
        Files.writeString(operations, original);
        assertEquals(0, generate(true));
    }
    @Test
    void unsupportedSchemasFailInsteadOfBecomingDynamicJson() throws Exception {
        var json = new com.fasterxml.jackson.databind.ObjectMapper();
        for (String schema : List.of("true", "{\"type\":\"array\"}",
                "{\"if\":{\"type\":\"string\"},\"then\":{\"minLength\":1}}",
                "{\"not\":{\"type\":\"string\"}}",
                "{\"type\":\"integer\",\"format\":\"unknown_integer\"}",
                "{\"type\":[\"string\",\"integer\"]}",
                "{\"minLength\":1}",
                "{\"$ref\":\"https://example.invalid/components/schemas/CheckRequest\"}",
                "{\"$ref\":\"#/components/schemas/MissingReference\"}")) {
            var document = json.readTree(Files.readString(Path.of("../contracts/openapi.json")));
            ((com.fasterxml.jackson.databind.node.ObjectNode) document.at("/components/schemas/CheckRequest/properties"))
                    .set("resource", json.readTree(schema));
            Path input = temporary.resolve("unsupported.json");
            Files.writeString(input, json.writeValueAsString(document));
            Process process = new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                    "tools/Generate.java", input.toString(), temporary.toString())
                    .redirectErrorStream(true).redirectOutput(temporary.resolve("unsupported.log").toFile()).start();
            assertTrue(process.waitFor(30, java.util.concurrent.TimeUnit.SECONDS));
            assertNotEquals(0, process.exitValue(), "unsupported schema silently generated: " + schema);
        }
    }

}
