package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class RetryAndSafetyTest {
    private static final String TOKEN = "tok-SECRET-must-never-leak-0123456789";
    private static final String NOT_READY = "{\"error\":{\"code\":\"NOT_READY\",\"message\":\"m\",\"request_id\":\"req-abcdefgh\"}}";
    private static final String REVISION_OK = "{\"data\":{\"db_revision\":1,\"snapshot_generation\":1,\"status\":\"in_sync\"},\"meta\":{\"authz_revision\":1}}";
    private static final String USER_CREATED = "{\"data\":" + TestServers.userData("00000000-0000-7000-8000-000000000001")
            + ",\"meta\":{\"authz_revision\":7,\"snapshot_generation\":7}}";

    private final List<Duration> sleeps = Collections.synchronizedList(new ArrayList<>());
    private final List<String> logs = Collections.synchronizedList(new ArrayList<>());

    private AuthgebraClient client(String baseUrl, boolean retryWrites) {
        return AuthgebraClient.create(Config.builder()
                .baseUrl(baseUrl)
                .tokenProvider(TokenProvider.fixed(TOKEN))
                .requestTimeout(Duration.ofSeconds(5))
                .logger(logs::add)
                .retryPolicy(RetryPolicy.builder().retryIdempotentWrites(retryWrites).withSleeper(sleeps::add).build())
                .build());
    }

    private static TestServers.Reply reply(int status, String body) {
        return TestServers.Reply.json(status, body);
    }

    @Test
    void readsRetryTransientFailuresAndEventuallySucceed() throws Exception {
        try (var server = new TestServers.Http((n, r) -> n < 3 ? reply(503, NOT_READY) : reply(200, REVISION_OK))) {
            var result = client(server.baseUrl(), false).authorization().revision();
            assertEquals(3, server.hits());
            assertEquals(2, sleeps.size());
            assertEquals("in_sync", result.data().status());
        }
    }

    @Test
    void retriesAreBoundedNeverUnlimited() throws Exception {
        try (var server = new TestServers.Http((n, r) -> reply(503, NOT_READY))) {
            var ex = assertThrows(ApiException.class, () -> client(server.baseUrl(), false).authorization().revision());
            assertEquals("NOT_READY", ex.code());
            assertEquals(3, server.hits(), "1 attempt + 2 retries by default");
        }
        assertThrows(IllegalArgumentException.class, () -> RetryPolicy.builder().maxRetries(11));
    }

    @Test
    void retryAfterIsHonouredButCapped() throws Exception {
        String limited = "{\"error\":{\"code\":\"RATE_LIMITED\",\"message\":\"m\",\"request_id\":\"req-abcdefgh\"}}";
        try (var server = new TestServers.Http((n, r) -> n == 1
                ? new TestServers.Reply(429, java.util.Map.of("Content-Type", "application/json", "Retry-After", "3600"),
                        limited.getBytes(StandardCharsets.UTF_8))
                : reply(200, REVISION_OK))) {
            client(server.baseUrl(), false).authorization().revision();
            assertEquals(List.of(Duration.ofSeconds(10)), sleeps, "3600s must be capped to maxRetryAfter");
        }
    }

    @Test
    void writesAreNeverRetriedByDefault() throws Exception {
        try (var server = new TestServers.Http((n, r) -> reply(503, NOT_READY))) {
            var opts = CallOptions.builder().idempotencyKey("idem-0001").build();
            assertThrows(ApiException.class, () -> client(server.baseUrl(), false).admin().users()
                    .createUser(new Models.CreateUserRequest(null, null, null, null, "alice"), opts));
            assertEquals(1, server.hits());
        }
    }

    @Test
    void optedInWritesStillNeedAnIdempotencyKey() throws Exception {
        try (var server = new TestServers.Http((n, r) -> reply(503, NOT_READY))) {
            assertThrows(ApiException.class, () -> client(server.baseUrl(), true).admin().users()
                    .createUser(new Models.CreateUserRequest(null, null, null, null, "alice")));
            assertEquals(1, server.hits(), "no Idempotency-Key => no replay");
        }
    }

    @Test
    void optedInWritesReplayWithTheSameKeyAndBytes() throws Exception {
        try (var server = new TestServers.Http((n, r) -> n == 1 ? reply(503, NOT_READY) : reply(201, USER_CREATED))) {
            var opts = CallOptions.builder().idempotencyKey("idem-0002").build();
            var result = client(server.baseUrl(), true).admin().users()
                    .createUser(new Models.CreateUserRequest(null, null, null, null, "alice"), opts);
            assertEquals(2, server.hits());
            var first = server.requests.get(0);
            var second = server.requests.get(1);
            assertEquals("idem-0002", first.header("idempotency-key"));
            assertEquals(first.header("idempotency-key"), second.header("idempotency-key"));
            assertEquals(new String(first.body(), StandardCharsets.UTF_8), new String(second.body(), StandardCharsets.UTF_8));
            assertEquals("{\"username\":\"alice\"}", new String(first.body(), StandardCharsets.UTF_8),
                    "absent fields stay absent: no nulls, no defaults");
            assertEquals("alice", result.data().username());
            assertEquals(7L, result.authzRevision().orElseThrow());
        }
    }

    @Test
    void reloadPendingIsNeverReplayedEvenWhenOptedIn() throws Exception {
        String pending = "{\"error\":{\"code\":\"AUTHZ_RELOAD_PENDING\",\"message\":\"m\",\"request_id\":\"req-abcdefgh\",\"retryable\":false,\"commit_state\":\"committed_unpublished\"}}";
        try (var server = new TestServers.Http((n, r) -> reply(503, pending))) {
            var opts = CallOptions.builder().idempotencyKey("idem-0003").build();
            var ex = assertThrows(ApiException.class, () -> client(server.baseUrl(), true).admin().users()
                    .createUser(new Models.CreateUserRequest(null, null, null, null, "alice"), opts));
            assertEquals(1, server.hits());
            assertEquals(CommitState.COMMITTED_UNPUBLISHED, ex.commitState());
            assertEquals(Boolean.FALSE, ex.retryable());
        }
    }

    @Test
    void staleCursorIsNeverRetriedOrRestarted() throws Exception {
        String stale = "{\"error\":{\"code\":\"CURSOR_STALE\",\"message\":\"m\",\"request_id\":\"req-abcdefgh\"}}";
        try (var server = new TestServers.Http((n, r) -> reply(409, stale))) {
            assertThrows(CursorStaleException.class, () -> client(server.baseUrl(), false).authorization().listResources(
                    new Models.ListResourcesRequest(null, "opaque", null, "document:read", "document", UUID.randomUUID())));
            assertEquals(1, server.hits());
        }
    }

    @Test
    void rejectsHeadersAndQueryTheOperationDoesNotDeclare() throws Exception {
        try (var server = new TestServers.Http((n, r) -> reply(200, REVISION_OK))) {
            var client = client(server.baseUrl(), false);
            var key = CallOptions.builder().idempotencyKey("idem-0004").build();
            assertThrows(IllegalArgumentException.class, () -> client.admin().users().getUser(UUID.randomUUID(), key));
            assertThrows(IllegalArgumentException.class,
                    () -> client.admin().users().listUsers(Query.create().set("not_a_param", "x")));
            assertEquals(0, server.hits(), "validation happens before anything is sent");
        }
    }

    @Test
    void tokenNeverAppearsInLogsOrExceptionText() throws Exception {
        try (var server = new TestServers.Http((n, r) -> reply(403,
                "{\"error\":{\"code\":\"FORBIDDEN\",\"message\":\"m\",\"request_id\":\"req-abcdefgh\"}}"))) {
            var ex = assertThrows(ApiException.class, () -> client(server.baseUrl(), false).authorization().revision());
            assertFalse(ex.toString().contains(TOKEN));
            assertFalse(String.valueOf(ex.getMessage()).contains(TOKEN));
            assertTrue(logs.stream().noneMatch(l -> l.contains(TOKEN)), logs.toString());
            assertEquals("Bearer " + TOKEN, server.requests.get(0).header("authorization"));
        }
    }

    @Test
    void anonymousCallsSendNoAuthorizationHeader() throws Exception {
        try (var server = new TestServers.Http((n, r) -> reply(200, "{\"status\":\"live\"}"))) {
            var client = AuthgebraClient.create(Config.builder().baseUrl(server.baseUrl()).build());
            client.admin().health().live();
            assertNull(server.requests.get(0).header("authorization"));
            assertTrue(server.requests.get(0).header("user-agent").startsWith("authgebra-java/" + Version.VERSION));
        }
    }

    @Test
    void requestIdAndTraceparentAreInjectedAndStableAcrossAttempts() throws Exception {
        try (var server = new TestServers.Http((n, r) -> n < 3 ? reply(503, NOT_READY) : reply(200, REVISION_OK))) {
            client(server.baseUrl(), false).authorization().revision();
            var a = server.requests.get(0);
            var b = server.requests.get(1);
            assertTrue(CallOptions.REQUEST_ID.matcher(a.header("x-request-id")).matches());
            assertEquals(a.header("x-request-id"), b.header("x-request-id"), "one logical call = one request id");
            var ta = TraceContext.parse(a.header("traceparent"));
            var tb = TraceContext.parse(b.header("traceparent"));
            assertNotNull(ta);
            assertNotNull(tb);
            assertEquals(ta.traceId(), tb.traceId());
            assertNotEquals(ta.spanId(), tb.spanId(), "each attempt is its own span");
        }
    }

    @Test
    void explicitTraceContextIsForwardedUnchanged() throws Exception {
        String tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
        try (var server = new TestServers.Http((n, r) -> reply(200, REVISION_OK))) {
            var opts = CallOptions.builder().traceparent(tp).tracestate("vendor=1").requestId("my-request-0001").build();
            client(server.baseUrl(), false).authorization().revision(opts);
            assertEquals(tp, server.requests.get(0).header("traceparent"));
            assertEquals("vendor=1", server.requests.get(0).header("tracestate"));
            assertEquals("my-request-0001", server.requests.get(0).header("x-request-id"));
        }
        assertThrows(IllegalArgumentException.class, () -> CallOptions.builder().traceparent("00-nope"));
        assertEquals(null, CallOptions.builder().requestId("short").build().requestId());
    }

    @Test
    void oversizedResponsesAreRefused() throws Exception {
        byte[] big = ("{\"data\":\"" + "x".repeat(4000) + "\"}").getBytes(StandardCharsets.UTF_8);
        try (var server = new TestServers.Http((n, r) -> new TestServers.Reply(200, java.util.Map.of("Content-Type", "application/json"), big))) {
            var client = AuthgebraClient.create(Config.builder().baseUrl(server.baseUrl()).maxResponseBytes(1024).build());
            var ex = assertThrows(ProtocolException.class, () -> client.raw().execute("GET", "/api/v1/x", null, CallOptions.defaults()));
            assertEquals(ProtocolException.RESPONSE_TOO_LARGE, ex.error());
        }
    }

    @Test
    void redirectsAreNotFollowed() throws Exception {
        try (var server = new TestServers.Http((n, r) -> new TestServers.Reply(302,
                java.util.Map.of("Location", "http://127.0.0.1:1/elsewhere"), new byte[0]))) {
            assertThrows(ProtocolException.class, () -> client(server.baseUrl(), false).authorization().revision());
            assertEquals(1, server.hits());
        }
    }

    @Test
    void aStalledBodyIsBoundedByTheRequestTimeout() throws Exception {
        try (ServerSocket socket = new ServerSocket(0, 5, InetAddress.getLoopbackAddress())) {
            Thread t = new Thread(() -> {
                try (Socket c = socket.accept()) {
                    c.getInputStream().read(new byte[2048]);
                    c.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
                            .getBytes(StandardCharsets.UTF_8));
                    c.getOutputStream().flush();
                    Thread.sleep(5000);
                } catch (IOException | InterruptedException ignored) {
                    // test ends
                }
            });
            t.setDaemon(true);
            t.start();
            var client = AuthgebraClient.create(Config.builder().baseUrl("http://127.0.0.1:" + socket.getLocalPort())
                    .requestTimeout(Duration.ofMillis(500)).retryPolicy(RetryPolicy.none()).build());
            long start = System.nanoTime();
            var ex = assertThrows(TransportException.class, () -> client.authorization().revision());
            assertEquals(TransportException.Kind.TIMEOUT, ex.kind());
            assertTrue(Duration.ofNanos(System.nanoTime() - start).toMillis() < 3000, "must not hang on a stalled body");
        }
    }

    @Test
    void configRejectsUnsafeOrUnboundedSettings() {
        assertThrows(IllegalArgumentException.class, () -> Config.builder().baseUrl("http://example.com").build());
        assertThrows(IllegalArgumentException.class, () -> Config.builder().baseUrl("https://u:p@example.com").build());
        assertThrows(IllegalArgumentException.class, () -> Config.builder().requestTimeout(Duration.ZERO));
        assertThrows(IllegalArgumentException.class, () -> Config.builder().connectTimeout(Duration.ofSeconds(-1)));
        assertThrows(IllegalArgumentException.class, () -> Config.builder().header("Authorization", "x"));
        Config.builder().baseUrl("https://authgebra.example.com").build();
        Config.builder().baseUrl("http://localhost:8080").build();
        assertInstanceOf(Config.class, Config.builder().baseUrl("http://example.com").allowInsecureHttp(true).build());
    }
}
