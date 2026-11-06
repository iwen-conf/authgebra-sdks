package com.authgebra.client;

import java.util.Objects;

/**
 * Entry point. Thread-safe; create one per server and reuse it (it owns a connection pool unless you
 * inject your own {@link java.net.http.HttpClient}).
 *
 * <pre>{@code
 * var client = AuthgebraClient.create(Config.builder()
 *         .baseUrl("https://authgebra.example.com")
 *         .tokenProvider(() -> tokens.current())
 *         .build());
 * var result = client.authorization().check(new Models.CheckRequest(null, "document:read", null, subjectId));
 * }</pre>
 */
public final class AuthgebraClient implements AutoCloseable {
    private final Http http;
    private final AdminClient admin;
    private final AuthorizationClient authorization;
    private final RawClient raw;

    private AuthgebraClient(Config config) {
        this.http = new Http(config);
        this.admin = new AdminClient(http);
        this.authorization = new AuthorizationClient(admin.authz());
        this.raw = new RawClient(http);
    }

    /** Create a client. */
    public static AuthgebraClient create(Config config) {
        return new AuthgebraClient(Objects.requireNonNull(config, "config"));
    }

    /** Decision endpoints. */
    public AuthorizationClient authorization() {
        return authorization;
    }

    /** Every operation of the contract, grouped by tag (users, roles, policies, …). */
    public AdminClient admin() {
        return admin;
    }

    /** Untyped access for endpoints without a generated method. */
    public RawClient raw() {
        return raw;
    }

    /** Releases the SDK-created connection pool (JDK 21+). A client you injected is never closed. */
    @Override
    public void close() {
        http.close();
    }
}
