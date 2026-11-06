package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class PaginationTest {
    private static final Models.ListResourcesRequest FIRST =
            new Models.ListResourcesRequest(null, null, 2, "document:read", "document", UUID.randomUUID());

    private static String page(String id, String next, boolean more, long generation) {
        return "{\"data\":[{\"resource_type_code\":\"document\",\"external_id\":\"" + id + "\"}],"
                + "\"page\":{\"next_cursor\":" + (next == null ? "null" : "\"" + next + "\"") + ",\"has_more\":" + more + "},"
                + "\"meta\":{\"authz_revision\":" + generation + ",\"snapshot_generation\":" + generation + "}}";
    }

    private static AuthgebraClient client(String url) {
        return AuthgebraClient.create(Config.builder().baseUrl(url).requestTimeout(Duration.ofSeconds(5))
                .retryPolicy(RetryPolicy.none()).build());
    }

    @Test
    void walksPagesWithTheOpaqueCursorAndStopsWhenDone() throws Exception {
        try (var server = new TestServers.Http((n, r) -> n == 1
                ? TestServers.Reply.json(200, page("a", "cursor-1==opaque", true, 5))
                : TestServers.Reply.json(200, page("b", null, false, 5)))) {
            var pages = client(server.baseUrl()).authorization().listResourcePages(FIRST, 10, CallOptions.defaults());
            List<String> ids = new ArrayList<>();
            while (pages.hasNext()) {
                pages.next().data().items().forEach(i -> ids.add(i.externalId()));
            }
            assertEquals(List.of("a", "b"), ids);
            assertFalse(pages.truncated());
            assertTrue(new String(server.requests.get(1).body()).contains("\"cursor\":\"cursor-1==opaque\""));
        }
    }

    @Test
    void staleCursorSurfacesAndIsNotRestartedBehindTheCallersBack() throws Exception {
        String stale = "{\"error\":{\"code\":\"CURSOR_STALE\",\"message\":\"m\",\"request_id\":\"req-abcdefgh\"}}";
        try (var server = new TestServers.Http((n, r) -> n == 1
                ? TestServers.Reply.json(200, page("a", "c1", true, 5))
                : TestServers.Reply.json(409, stale))) {
            var pages = client(server.baseUrl()).authorization().listResourcePages(FIRST, 10, CallOptions.defaults());
            pages.next();
            assertThrows(CursorStaleException.class, pages::next);
            assertEquals(2, server.hits(), "no automatic restart from the first page");
        }
    }

    @Test
    void pagesFromDifferentGenerationsAreNeverStitchedTogether() throws Exception {
        try (var server = new TestServers.Http((n, r) -> n == 1
                ? TestServers.Reply.json(200, page("a", "c1", true, 5))
                : TestServers.Reply.json(200, page("b", null, false, 6)))) {
            var pages = client(server.baseUrl()).authorization().listResourcePages(FIRST, 10, CallOptions.defaults());
            pages.next();
            var ex = assertThrows(ProtocolException.class, pages::next);
            assertEquals(ProtocolException.GENERATION_CHANGED, ex.error());
        }
    }

    @Test
    void iterationIsBoundedAndCancellable() throws Exception {
        try (var server = new TestServers.Http((n, r) -> TestServers.Reply.json(200, page("x" + n, "c" + n, true, 5)))) {
            var pages = client(server.baseUrl()).authorization().listResourcePages(FIRST, 3, CallOptions.defaults());
            int count = 0;
            while (pages.hasNext()) {
                pages.next();
                count++;
            }
            assertEquals(3, count);
            assertTrue(pages.truncated());
            assertEquals(3, server.hits());

            var cancelled = client(server.baseUrl()).authorization().listResourcePages(FIRST, 50, CallOptions.defaults());
            cancelled.next();
            cancelled.cancel();
            assertFalse(cancelled.hasNext());
        }
        assertThrows(IllegalArgumentException.class,
                () -> client("http://127.0.0.1:1").authorization().listResourcePages(FIRST, 0, CallOptions.defaults()));
    }
}
