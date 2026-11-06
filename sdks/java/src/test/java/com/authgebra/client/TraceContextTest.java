package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class TraceContextTest {
    private static final String VALID = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";

    @Test
    void invalidCallerRequestIdsAreDiscarded() {
        for (String value : new String[] {"bad", "bad id with spaces", "valid-id\r\nInjected: yes"}) {
            assertNull(CallOptions.builder().requestId(value).build().requestId());
        }
        assertEquals("request-valid-123", CallOptions.builder().requestId("request-valid-123").build().requestId());
    }

    @Test
    void parsesValidAndRejectsEverythingTheServerRejects() {
        TraceContext ctx = TraceContext.parse(VALID);
        assertNotNull(ctx);
        assertEquals("4bf92f3577b34da6a3ce929d0e0e4736", ctx.traceId());
        assertEquals(VALID, ctx.toTraceparent());
        for (String bad : new String[] {
                null, "", "garbage", VALID.toUpperCase(),
                "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
                "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
                "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
                "00-4bf92f3577b34da6a3ce929d0e0e473-00f067aa0ba902b7-01",
                "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-0g",
                VALID + "-extra"}) {
            assertNull(TraceContext.parse(bad), String.valueOf(bad));
        }
        assertNotNull(TraceContext.parse("01-" + VALID.substring(3) + "-extra"), "higher versions may append fields");
    }

    @Test
    void generatedContextsAreValidAndDistinct() {
        for (int i = 0; i < 500; i++) {
            TraceContext root = TraceContext.newRoot();
            assertNotNull(TraceContext.parse(root.toTraceparent()));
            TraceContext child = root.newSpan();
            assertEquals(root.traceId(), child.traceId());
            assertNotEquals(root.spanId(), child.spanId());
        }
        assertThrows(IllegalArgumentException.class, () -> new TraceContext("0".repeat(32), "1".repeat(16), "00"));
    }
}
