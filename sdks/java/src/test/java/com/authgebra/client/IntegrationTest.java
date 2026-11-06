package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

/** Native live-server tests; absent environment is a reported skip, never integration evidence. */
class IntegrationTest {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static String env(String name) {
        String value = System.getenv(name);
        assertNotNull(value, "missing " + name);
        return value;
    }
    private static String id() { return UUID.randomUUID().toString().replace("-", ""); }
    private static JsonNode tree(Object value) { return JSON.valueToTree(value); }
    private final java.util.List<AuthgebraClient> clients = new java.util.ArrayList<>();
    @org.junit.jupiter.api.AfterEach void closeClients() { clients.forEach(AuthgebraClient::close); }
    private final class Session {
        final String prefix = "it-java-" + id().substring(0, 12);
        final String password = env("AUTHGEBRA_IT_PASSWORD");
        AuthgebraClient client;
        String subject;
        Session() {
            assumeTrue(System.getenv("AUTHGEBRA_IT_BASE_URL") != null, "NOT_RUN: AUTHGEBRA_IT_BASE_URL unset");
            assertEquals("1", env("AUTHGEBRA_IT_CONFIRM_ISOLATED"));
            client = AuthgebraClient.create(Config.builder().baseUrl(env("AUTHGEBRA_IT_BASE_URL")).build());
            clients.add(client);
            JsonNode login = call("POST", "/api/v1/auth/login", Map.of("identifier", env("AUTHGEBRA_IT_USER"), "password", password), null).get("data");
            assertEquals("authenticated", login.get("status").asText());
            client = AuthgebraClient.create(Config.builder().baseUrl(env("AUTHGEBRA_IT_BASE_URL"))
                .tokenProvider(TokenProvider.fixed(login.get("access_token").asText())).build());
            clients.add(client);
            subject = client.admin().auth().me(CallOptions.defaults()).data().user().id().toString();
        }
        JsonNode call(String method, String path, Object body, String key) {
            return call(method, path, body, key, CallOptions.builder().requestId("it-java-" + id()).idempotencyKey(key).build());
        }
        JsonNode call(String method, String path, Object body, String key, CallOptions options) {
            Result<?> r;
            if (path.equals("/api/v1/authz/list-resources")) {
                r = client.authorization().listResources(JSON.convertValue(body, Models.ListResourcesRequest.class), options);
            } else {
                r = client.raw().execute(method, path, body, options);
            }
            ObjectNode out = JSON.createObjectNode();
            out.put("status", r.status()); out.set("meta", tree(r.meta().orElse(null)));
            out.put("request_id", r.requestId().orElse(null)); out.put("trace_id", r.traceId().orElse(null));
            out.put("replayed", r.header("Idempotency-Replayed").orElse("").equals("true"));
            if (r.data() instanceof PagedData<?> page) {
                out.set("data", tree(page.items()));
                out.set("page", tree(Map.of("has_more", page.hasMore(), "next_cursor", page.nextCursor() == null ? "" : page.nextCursor())));
            } else out.set("data", tree(r.data()));
            return out;
        }
        ApiException error(String code, int status, String method, String path, Object body, String key) {
            ApiException e = assertThrows(ApiException.class, () -> call(method, path, body, key));
            assertEquals(code, e.code()); assertEquals(status, e.status());
            assertNotNull(e.requestId()); assertNotNull(e.traceId());
            return e;
        }
    }
    @Test void realServerContract() {
        assumeTrue(System.getenv("AUTHGEBRA_IT_BASE_URL") != null, "NOT_RUN: AUTHGEBRA_IT_BASE_URL unset");
        Session s = new Session();
        s.error("INVALID_CREDENTIALS", 401, "POST", "/api/v1/auth/login", Map.of("identifier", env("AUTHGEBRA_IT_USER"), "password", s.password + "-wrong"), null);
        for (String permission : new String[]{"user:read", "nope:nope"}) {
            var request = JSON.convertValue(Map.of("subject_id", s.subject, "permission", permission), Models.CheckRequest.class);
            var r = s.client.authorization().check(request);
            assertEquals(permission.equals("user:read") ? "allow" : "deny", r.data().decision());
            assertTrue(r.authzRevision().orElseThrow() > 0); assertTrue(r.snapshotGeneration().orElseThrow() > 0);
        }
        s.call("POST", "/api/v1/resource-types", Map.of("code", s.prefix, "name", s.prefix), null);
        var attr = s.call("POST", "/api/v1/attributes", Map.of("code", s.prefix, "name", s.prefix, "owner_type", "resource", "value_type", "string"), null);
        String secret = "private-" + id();
        var b = Map.of("resource_type_code", s.prefix, "external_id", "b");
        var a = Map.of("resource_type_code", s.prefix, "external_id", "a");
        var missing = Map.of("resource_type_code", s.prefix, "external_id", "missing");
        for (var ref : java.util.List.of(b, a)) s.call("POST", "/api/v1/resources", Map.of(
            "resource_type_code", s.prefix, "external_id", ref.get("external_id"), "name", ref.get("external_id"),
            "attributes", java.util.List.of(Map.of("attribute_id", attr.get("data").get("id").asText(), "value", secret))), null);
        var batch = s.call("POST", "/api/v1/authz/batch-check", Map.of("subject_id", s.subject, "permission", "user:read", "resources", java.util.List.of(b, missing, a, b)), null).get("data");
        assertEquals(2, batch.get("allowed").size()); assertEquals(1, batch.get("denied").size());
        assertEquals("b", batch.at("/allowed/0/resource/external_id").asText());
        assertEquals("a", batch.at("/allowed/1/resource/external_id").asText());
        assertEquals("missing", batch.at("/denied/0/resource/external_id").asText());
        var pageBody = new java.util.LinkedHashMap<String, Object>(Map.of("subject_id", s.subject, "permission", "user:read", "resource_type", s.prefix, "limit", 1));
        var first = s.call("POST", "/api/v1/authz/list-resources", pageBody, null);
        assertEquals(1, first.get("data").size()); assertTrue(first.at("/page/has_more").asBoolean());
        String cursor = first.at("/page/next_cursor").asText(); assertFalse(cursor.isEmpty());
        pageBody.put("cursor", cursor); var second = s.call("POST", "/api/v1/authz/list-resources", pageBody, null);
        assertEquals(1, second.get("data").size()); assertNotEquals(first.get("data"), second.get("data"));
        assertEquals(first.get("meta"), second.get("meta")); assertFalse(second.at("/page/has_more").asBoolean());
        pageBody.put("cursor", (cursor.startsWith("A") ? "B" : "A") + cursor.substring(1));
        s.error("CURSOR_INVALID", 400, "POST", "/api/v1/authz/list-resources", pageBody, null);
        var explain = s.call("POST", "/api/v1/authz/explain", Map.of("subject_id", s.subject, "permission", "user:read", "resource", b), null);
        assertEquals("allow", explain.at("/data/decision").asText()); assertFalse(explain.toString().contains(secret));
        var rev = s.client.authorization().revision().data(); assertTrue(rev.dbRevision() > 0); assertEquals(rev.dbRevision(), rev.snapshotGeneration()); assertEquals("in_sync", rev.status());
        var body = Map.of("username", s.prefix, "display_name", s.prefix, "initial_password", s.password); String key = id();
        var created = s.call("POST", "/api/v1/users", body, key); var replay = s.call("POST", "/api/v1/users", body, key);
        assertEquals(201, created.get("status").asInt()); assertTrue(replay.get("replayed").asBoolean()); assertEquals(created.at("/data/id"), replay.at("/data/id"));
        var conflict = s.error("IDEMPOTENCY_CONFLICT", 409, "POST", "/api/v1/users", Map.of("username", s.prefix, "display_name", "different", "initial_password", s.password), key);
        assertEquals(Boolean.FALSE, conflict.retryable());
        var found = s.client.raw().execute("GET", "/api/v1/users", Query.create().set("q", s.prefix), null, CallOptions.defaults()).data();
        assertEquals(1, found.size()); assertEquals(created.at("/data/id"), found.get(0).get("id"));
        String trace = id(); String requestId = s.prefix + "-missing";
        var e = assertThrows(ApiException.class, () -> s.call("GET", "/api/v1/users/" + UUID.randomUUID(), null, null,
            CallOptions.builder().requestId(requestId).traceparent("00-" + trace + "-1234567890abcdef-01").build()));
        assertEquals("RESOURCE_NOT_FOUND", e.code()); assertEquals(404, e.status()); assertEquals(trace, e.traceId()); assertEquals(requestId, e.requestId());
        System.out.println("IT-1..9 PASS");
    }
    @ParameterizedTest @CsvSource({"query,invalid_query_parameter", "path,invalid_path_parameter", "unknown,unknown_field"})
    void realServerInvalidInput(String kind, String reason) {
        assumeTrue(System.getenv("AUTHGEBRA_IT_BASE_URL") != null, "NOT_RUN: AUTHGEBRA_IT_BASE_URL unset");
        Session s = new Session();
        ApiException e;
        if (kind.equals("query")) {
            e = assertThrows(ApiException.class, () -> s.client.raw().execute("GET", "/api/v1/users", Query.create().set("limit", "abc"), null, CallOptions.defaults()));
            assertEquals("INVALID_REQUEST", e.code()); assertEquals(400, e.status());
        } else if (kind.equals("path")) e = s.error("INVALID_REQUEST", 400, "GET", "/api/v1/users/not-a-uuid", null, null);
        else e = s.error("INVALID_REQUEST", 400, "POST", "/api/v1/auth/login", Map.of("identifier", env("AUTHGEBRA_IT_USER"), "password", s.password, "extra", true), null);
        assertFalse(e.violations().isEmpty()); assertEquals(reason, e.violations().get(0).reason());
    }
    private static void faultCommand(String name) throws Exception {
        Process p = new ProcessBuilder("sh", "-c", env(name)).start();
        try {
            assertTrue(p.waitFor(15, java.util.concurrent.TimeUnit.SECONDS), "fault command deadline");
            assertEquals(0, p.exitValue());
        } finally { if (p.isAlive()) p.destroyForcibly(); }
    }
    @Test void realServerCommittedUnpublished() throws Exception {
        assumeTrue("1".equals(System.getenv("AUTHGEBRA_IT_FAULT")), "NOT_RUN: fault injection not enabled");
        Session s = new Session(); String key = id();
        var body = Map.of("username", s.prefix, "display_name", s.prefix, "initial_password", s.password);
        try {
            faultCommand("AUTHGEBRA_IT_FAULT_ON_CMD");
            var e = s.error("AUTHZ_RELOAD_PENDING", 503, "POST", "/api/v1/users", body, key);
            assertEquals("committed_unpublished", e.commitState()); assertEquals(Boolean.FALSE, e.retryable());
        } finally { faultCommand("AUTHGEBRA_IT_FAULT_OFF_CMD"); }
        long deadline = System.nanoTime() + java.time.Duration.ofSeconds(60).toNanos();
        while (true) {
            try {
                var rev = s.client.authorization().revision().data();
                if (rev.dbRevision().equals(rev.snapshotGeneration())) break;
            } catch (ApiException pending) {
                assertEquals("NOT_READY", pending.code()); assertEquals(503, pending.status());
            }
            assertTrue(System.nanoTime() < deadline, "publication recovery deadline");
            java.util.concurrent.locks.LockSupport.parkNanos(java.time.Duration.ofMillis(200).toNanos());
        }
        var replay = s.call("POST", "/api/v1/users", body, key);
        var found = s.client.raw().execute("GET", "/api/v1/users", Query.create().set("q", s.prefix), null, CallOptions.defaults()).data();
        assertTrue(replay.get("replayed").asBoolean()); assertEquals(1, found.size()); assertEquals(replay.at("/data/id"), found.get(0).get("id"));
        System.out.println("IT-11 PASS");
    }

    @Test void realServerInflight() throws Exception {
        assumeTrue("1".equals(System.getenv("AUTHGEBRA_IT_INFLIGHT")), "NOT_RUN: in-flight barrier not enabled");
        Session s = new Session();
        for (boolean different : new boolean[]{false, true}) {
            String username = "it-flight-java-" + id().substring(0, 12), key = id();
            var body = Map.of("username", username, "display_name", username, "initial_password", s.password);
            var changed = Map.of("username", username, "display_name", "different", "initial_password", s.password);
            var executor = java.util.concurrent.Executors.newSingleThreadExecutor();
            java.util.concurrent.Future<JsonNode> first;
            RuntimeException secondFailure = null;
            try {
                faultCommand("AUTHGEBRA_IT_INFLIGHT_HOLD_CMD");
                first = executor.submit(() -> s.call("POST", "/api/v1/users", body, key));
                faultCommand("AUTHGEBRA_IT_INFLIGHT_WAIT_CMD");
                try { s.call("POST", "/api/v1/users", different ? changed : body, key); }
                catch (RuntimeException failure) { secondFailure = failure; }
            } finally {
                try { faultCommand("AUTHGEBRA_IT_INFLIGHT_RELEASE_CMD"); }
                finally { executor.shutdown(); }
            }
            var created = first.get(35, java.util.concurrent.TimeUnit.SECONDS);
            assertEquals(201, created.path("status").asInt());
            var pending = assertInstanceOf(ApiException.class, secondFailure);
            assertEquals("IDEMPOTENCY_IN_PROGRESS", pending.code());
            assertEquals(409, pending.status());
            assertEquals(Boolean.TRUE, pending.retryable());
            var replay = s.call("POST", "/api/v1/users", body, key);
            assertTrue(replay.path("replayed").asBoolean());
            assertEquals(created.at("/data/id"), replay.at("/data/id"));
            var found = s.client.admin().users().listUsers(Query.create().set("q", username)).data().items();
            assertEquals(1, found.size());
            assertEquals(created.at("/data/id").asText(), found.get(0).id().toString());
            if (different) {
                var conflict = s.error("IDEMPOTENCY_CONFLICT", 409, "POST", "/api/v1/users", changed, key);
                assertEquals(Boolean.FALSE, conflict.retryable());
            }
        }
        System.out.println("IT-INFLIGHT PASS");
    }

    @Test void typedRoleCrudReplaysAndClearsDescription() {
        assumeTrue(System.getenv("AUTHGEBRA_IT_BASE_URL") != null, "NOT_RUN: AUTHGEBRA_IT_BASE_URL unset");
        Session session = new Session();
        var roles = session.client.admin().roles();
        String code = session.prefix + "-typed-role";
        String name = "Typed role 中文";
        String description = "typed &<> 中文";
        var body = new Models.CreateRoleRequest(code, description, name);
        var createOptions = CallOptions.builder().idempotencyKey(id()).build();
        Result<Models.RoleDto> created = roles.createRole(body, createOptions);
        assertEquals(201, created.status());
        UUID roleId = created.data().id();
        assertNotNull(roleId);
        boolean deleted = false;
        try {
            java.util.function.Consumer<Models.RoleDto> assertRole = role -> {
                assertEquals(roleId, role.id());
                assertEquals(code, role.code());
                assertEquals(name, role.name());
                assertNotNull(role.etag()); assertFalse(role.etag().isEmpty());
                assertTrue(role.version() >= 1);
                assertEquals(Boolean.TRUE, role.enabled());
                assertEquals(Boolean.FALSE, role.isSystem());
                assertEquals(0L, role.usersCount());
                assertEquals(0L, role.permissionsCount());
            };
            assertRole.accept(created.data());
            assertEquals(description, created.data().description());
            Result<Models.RoleDto> replay = roles.createRole(body, createOptions);
            assertRole.accept(replay.data());
            assertEquals("true", replay.header("Idempotency-Replayed").orElseThrow());
            assertEquals(description, replay.data().description());
            var fetched = roles.getRole(roleId);
            assertRole.accept(fetched.data());
            assertEquals(description, fetched.data().description());
            var clear = Models.PatchRoleRequest.fromFields(JsonField.nullValue(), JsonField.absent());
            Result<Models.RoleDto> patched = roles.patchRole(roleId, clear,
                    CallOptions.builder().ifMatch(fetched.data().etag()).build());
            assertRole.accept(patched.data());
            assertNull(patched.data().description());
            assertTrue(patched.data().version() > fetched.data().version());
            var cleared = roles.getRole(roleId);
            assertRole.accept(cleared.data());
            assertNull(cleared.data().description());
            assertEquals(200, roles.deleteRole(roleId, CallOptions.builder().ifMatch(cleared.data().etag()).build()).status());
            deleted = true;
            var missing = assertThrows(ApiException.class, () -> roles.getRole(roleId));
            assertEquals(404, missing.status());
            assertEquals("RESOURCE_NOT_FOUND", missing.code());
        } finally {
            if (!deleted) {
                var current = roles.getRole(roleId);
                roles.deleteRole(roleId, CallOptions.builder().ifMatch(current.data().etag()).build());
            }
        }
        System.out.println("IT-TYPED-ROLE PASS");
    }

}
