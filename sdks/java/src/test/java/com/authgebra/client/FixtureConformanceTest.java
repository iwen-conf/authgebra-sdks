package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;

/** Runs every case in {@code sdks/fixtures/cases/*.json} against the real client over real sockets. */
class FixtureConformanceTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Path CASES = Path.of("..", "fixtures", "cases");
    private static final String SECRET_TOKEN = "tok-SECRET-must-never-leak-0123456789";
    private static final UUID SUBJECT = UUID.fromString("00000000-0000-7000-8000-000000000001");

    static Stream<JsonNode> fixtures() throws IOException {
        List<JsonNode> out = new ArrayList<>();
        try (Stream<Path> files = Files.list(CASES)) {
            for (Path file : files.filter(p -> p.toString().endsWith(".json")).sorted().toList()) {
                out.add(JSON.readTree(Files.readAllBytes(file)));
            }
        }
        return out.stream();
    }

    @Test
    void allFixturesAreDiscovered() throws IOException {
        assertTrue(fixtures().count() >= 25, "expected the 25 shared fixtures to be found");
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("fixtures")
    void fixtureBehavesIdentically(JsonNode fx) throws Exception {
        String id = fx.get("id").asText();
        JsonNode expect = fx.get("expect");
        if (fx.has("fault")) {
            runFault(fx, expect);
            return;
        }
        JsonNode response = fx.get("response");
        TestServers.Reply reply = reply(response);
        try (TestServers.Http server = new TestServers.Http((n, req) -> reply)) {
            AuthgebraClient client = client(server.baseUrl(), Duration.ofSeconds(5));
            Object outcome = invoke(client, fx);
            assertExpect(id, expect, outcome, server);
        }
    }

    // ------------------------------------------------------------------ faults

    private void runFault(JsonNode fx, JsonNode expect) throws Exception {
        TestServers.Fault.Mode mode = "timeout".equals(fx.get("fault").asText())
                ? TestServers.Fault.Mode.HANG : TestServers.Fault.Mode.RESET;
        try (TestServers.Fault fault = new TestServers.Fault(mode)) {
            AuthgebraClient client = client(fault.baseUrl(), Duration.ofMillis(400));
            Object outcome = invoke(client, fx);
            assertInstanceOf(TransportException.class, outcome, fx.get("id").asText());
            TransportException ex = (TransportException) outcome;
            assertEquals(expect.get("error").asText(), ex.kind().wire());
            if (expect.get("commit_state").isNull()) {
                assertNull(ex.commitState());
            } else {
                assertEquals(expect.get("commit_state").asText(), ex.commitState());
            }
            if (expect.has("auto_resent") && !expect.get("auto_resent").asBoolean()) {
                assertEquals(1, fault.connections(), "a write without opt-in must not be re-sent");
                assertEquals(1, ex.attempts());
            }
        }
    }

    // ----------------------------------------------------------------- invoke

    private static AuthgebraClient client(String baseUrl, Duration requestTimeout) {
        return AuthgebraClient.create(Config.builder()
                .baseUrl(baseUrl)
                .tokenProvider(TokenProvider.fixed(SECRET_TOKEN))
                .requestTimeout(requestTimeout)
                .retryPolicy(RetryPolicy.builder().withSleeper(d -> { }).build())
                .build());
    }

    /** Returns a {@link Result} or the thrown {@link AuthgebraException}. */
    private static Object invoke(AuthgebraClient client, JsonNode fx) {
        String op = fx.get("operation").asText();
        JsonNode request = fx.get("request");
        try {
            switch (op) {
                case "authz.check":
                    return client.authorization().check(
                            new Models.CheckRequest(null, "document:read", null, SUBJECT));
                case "authz.list_resources":
                    return client.authorization().listResources(
                            new Models.ListResourcesRequest(null, null, null, "document:read", "document", SUBJECT));
                case "users.list":
                    return client.admin().users().listUsers(Query.create());
                default:
                    return rawCall(client, op, request);
            }
        } catch (AuthgebraException e) {
            return e;
        }
    }

    private static Object rawCall(AuthgebraClient client, String op, JsonNode request) {
        String method = request.has("method") ? request.get("method").asText() : "POST";
        String path = request.has("path") ? request.get("path").asText() : switch (op) {
            case "users.update" -> "/api/v1/users/" + SUBJECT;
            case "auth.change_password" -> "/api/v1/auth/change-password";
            default -> "/api/v1/" + op.replace('.', '/');
        };
        if (op.equals("users.update")) {
            method = "PATCH";
        }
        Object body = request.has("body") ? request.get("body") : Map.of();
        CallOptions.Builder options = CallOptions.builder();
        if (request.has("idempotency_key") && !request.get("idempotency_key").isNull()) {
            options.idempotencyKey(request.get("idempotency_key").asText());
        }
        return client.raw().execute(method, path, body, options.build());
    }

    private static TestServers.Reply reply(JsonNode response) throws IOException {
        Map<String, String> headers = new java.util.LinkedHashMap<>();
        response.get("headers").fields().forEachRemaining(e -> headers.put(e.getKey(), e.getValue().asText()));
        byte[] body;
        if (response.has("body_raw")) {
            body = response.get("body_raw").asText().getBytes(StandardCharsets.UTF_8);
        } else {
            body = JSON.writeValueAsBytes(response.get("body"));
            headers.putIfAbsent("content-type", "application/json");
        }
        return new TestServers.Reply(response.get("status").asInt(), headers, body);
    }

    // ----------------------------------------------------------------- assert

    @SuppressWarnings("unchecked")
    private static void assertExpect(String id, JsonNode expect, Object outcome, TestServers.Http server) {
        String kind = expect.get("kind").asText();
        switch (kind) {
            case "ok" -> {
                if (outcome instanceof AuthgebraException e) {
                    fail(id + ": expected success but got " + e);
                }
                assertOk(id, expect, (Result<Object>) outcome, server);
            }
            case "api_error" -> {
                ApiException ex = assertInstanceOf(ApiException.class, outcome, id + ": " + outcome);
                assertApiError(id, expect, ex);
            }
            case "protocol_error" -> {
                ProtocolException ex = assertInstanceOf(ProtocolException.class, outcome, id + ": " + outcome);
                assertEquals(expect.get("error").asText(), ex.error(), id);
                assertEquals(expect.get("status").asInt(), ex.status(), id);
            }
            default -> fail("unknown expect.kind " + kind);
        }
    }

    private static void assertOk(String id, JsonNode expect, Result<Object> result, TestServers.Http server) {
        Object data = result.data();
        if (expect.has("decision")) {
            assertEquals(expect.get("decision").asText(),
                    ((Models.CheckResponse) data).decision(), id);
        }
        longOrAbsent(id, "authz_revision", expect, result.authzRevision());
        longOrAbsent(id, "snapshot_generation", expect, result.snapshotGeneration());
        if (expect.has("trace_id")) {
            assertEquals(expect.get("trace_id").isNull() ? null : expect.get("trace_id").asText(),
                    result.traceId().orElse(null), id + " trace_id");
        }
        if (expect.has("request_id")) {
            assertEquals(expect.get("request_id").asText(), result.requestId().orElse(null), id + " request_id");
        }
        if (expect.has("has_more")) {
            assertEquals(expect.get("has_more").asBoolean(), ((PagedData<?>) data).hasMore(), id);
        }
        if (expect.has("echo_username")) {
            assertEquals(expect.get("echo_username").asText(), ((JsonNode) data).get("username").asText(), id);
        }
        if (expect.has("request_body_utf8_exact")) {
            String sent = new String(server.requests.get(0).body(), StandardCharsets.UTF_8);
            assertTrue(sent.contains(expect.get("echo_username").asText()), id + ": body must carry raw UTF-8: " + sent);
            assertFalse(sent.contains("\\u"), id + ": no \\u escapes expected: " + sent);
        }
    }

    private static void longOrAbsent(String id, String field, JsonNode expect, java.util.Optional<Long> actual) {
        if (!expect.has(field)) {
            return;
        }
        if (expect.get(field).isNull()) {
            assertTrue(actual.isEmpty(), id + ": " + field + " must be absent, never a fabricated 0");
        } else {
            assertEquals(Long.parseLong(expect.get(field).asText()), actual.orElseThrow(), id + " " + field);
        }
    }

    private static void assertApiError(String id, JsonNode expect, ApiException ex) {
        assertEquals(expect.get("status").asInt(), ex.status(), id);
        assertEquals(expect.get("code").asText(), ex.code(), id);
        assertEquals(expect.get("known_code").asBoolean(), ex.knownCode(), id);
        assertEquals(textOrNull(expect, "request_id"), ex.requestId(), id + " request_id");
        assertEquals(textOrNull(expect, "trace_id"), ex.traceId(), id + " trace_id");
        assertEquals(boolOrNull(expect, "retryable"), ex.retryable(), id + " retryable");
        assertEquals(expect.get("retry_after_seconds").isNull() ? null : expect.get("retry_after_seconds").asLong(),
                ex.retryAfterSeconds(), id + " retry_after");
        assertEquals(textOrNull(expect, "commit_state"), ex.commitState(), id + " commit_state");
        if (expect.has("body_request_id")) {
            assertEquals(expect.get("body_request_id").asText(), ex.bodyRequestId(), id);
        }
        if (expect.has("request_id_mismatch")) {
            assertEquals(expect.get("request_id_mismatch").asBoolean(), ex.requestIdMismatch(), id);
        }
        if (expect.has("violations")) {
            List<Violation> actual = ex.violations();
            JsonNode wanted = expect.get("violations");
            assertEquals(wanted.size(), actual.size(), id + " violations count");
            for (int i = 0; i < wanted.size(); i++) {
                assertEquals(textOrNull(wanted.get(i), "path"), actual.get(i).path(), id + " violation[" + i + "].path");
                assertEquals(wanted.get(i).get("reason").asText(), actual.get(i).reason(), id + " violation[" + i + "]");
            }
        }
        // Tokens must never reach exception text.
        assertFalse(String.valueOf(ex.getMessage()).contains(SECRET_TOKEN), id);
        if (ErrorCodes.CURSOR_STALE.equals(ex.code())) {
            assertInstanceOf(CursorStaleException.class, ex);
        }
    }

    private static String textOrNull(JsonNode node, String field) {
        return !node.has(field) || node.get(field).isNull() ? null : node.get(field).asText();
    }

    private static Boolean boolOrNull(JsonNode node, String field) {
        return !node.has(field) || node.get(field).isNull() ? null : node.get(field).asBoolean();
    }
}
