package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.assertThrows;
import org.junit.jupiter.api.Test;

class ScalarProtocolTest {
    @Test
    void integerResponsesCannotSilentlyCoerceFractionsOrStrings() throws Exception {
        for (String invalid : new String[] {"1.5", "\"7\"", "true"}) {
            String body = "{\"data\":{\"db_revision\":" + invalid + ",\"snapshot_generation\":1,\"status\":\"in_sync\"}}";
            try (var server = new TestServers.Http((n, request) -> TestServers.Reply.json(200, body))) {
                var client = AuthgebraClient.create(Config.builder().baseUrl(server.baseUrl()).build());
                assertThrows(ProtocolException.class, () -> client.authorization().revision());
            }
        }
    }

    @Test
    void metadataIntegersCannotLoseTheirFraction() throws Exception {
        String body = "{\"data\":{\"db_revision\":1,\"snapshot_generation\":1,\"status\":\"in_sync\"},\"meta\":{\"authz_revision\":1.5}}";
        try (var server = new TestServers.Http((n, request) -> TestServers.Reply.json(200, body))) {
            var client = AuthgebraClient.create(Config.builder().baseUrl(server.baseUrl()).build());
            assertThrows(ProtocolException.class, () -> client.authorization().revision());
        }
    }
}
