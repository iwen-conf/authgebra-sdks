package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.net.http.HttpClient;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.cert.CertificateFactory;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;
import org.junit.jupiter.api.Test;

class TlsIntegrationTest {
    private static final String TOKEN = "tls-secret-token-must-not-leak";

    @Test
    void realTlsCertificateHostnameAndHandshakeMatrix() throws Exception {
        String valid = System.getenv("SDK_TLS_VALID_URL");
        assumeTrue(valid != null && !valid.isEmpty(), "NOT_RUN: explicit SDK_TLS fixture required");
        KeyStore trust = KeyStore.getInstance(KeyStore.getDefaultType());
        trust.load(null, null);
        try (var pem = Files.newInputStream(Path.of(System.getenv("SDK_TLS_CA")))) {
            trust.setCertificateEntry("sdk-test-ca", CertificateFactory.getInstance("X.509").generateCertificate(pem));
        }
        var managers = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        managers.init(trust);
        SSLContext tls = SSLContext.getInstance("TLS");
        tls.init(null, managers.getTrustManagers(), null);
        for (var scenario : List.of(
                new Scenario("trusted_ca", valid, true, false),
                new Scenario("untrusted_ca", valid, false, true),
                new Scenario("hostname_mismatch", System.getenv("SDK_TLS_MISMATCH_URL"), true, true),
                new Scenario("invalid_handshake", System.getenv("SDK_TLS_PLAIN_URL"), true, true))) {
            List<String> logs = new ArrayList<>();
            var config = Config.builder().baseUrl(scenario.url()).tokenProvider(TokenProvider.fixed(TOKEN))
                    .retryPolicy(RetryPolicy.none()).requestTimeout(Duration.ofSeconds(5)).logger(logs::add);
            if (scenario.trusted()) {
                config.httpClient(HttpClient.newBuilder().sslContext(tls).connectTimeout(Duration.ofSeconds(5))
                        .version(HttpClient.Version.HTTP_1_1).build());
            }
            try (var client = AuthgebraClient.create(config.build())) {
                if (scenario.fail()) {
                    var error = assertThrows(TransportException.class,
                            () -> client.raw().execute("GET", "/tls-test", null, CallOptions.defaults()), scenario.name());
                    assertEquals(TransportException.Kind.TLS, error.kind(), scenario.name());
                    assertEquals(1, error.attempts(), scenario.name());
                    assertFalse(error.toString().contains(TOKEN), "token leaked through TLS error");
                } else {
                    var result = client.raw().execute("GET", "/tls-test", null, CallOptions.defaults());
                    assertTrue(result.data().path("tls").asBoolean(), "TLS positive control failed");
                }
                assertFalse(String.join("\n", logs).contains(TOKEN), "token leaked through TLS logs");
            }
        }
    }

    private record Scenario(String name, String url, boolean trusted, boolean fail) {}
}
