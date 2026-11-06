package com.authgebra.client;

import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.function.Supplier;

/** Client configuration. Immutable; build with {@link #builder()}. */
public final class Config {
    /** Default connect timeout. */
    public static final Duration DEFAULT_CONNECT_TIMEOUT = Duration.ofSeconds(5);
    /** Default total request timeout. */
    public static final Duration DEFAULT_REQUEST_TIMEOUT = Duration.ofSeconds(30);
    /** Default response size limit (8 MiB). */
    public static final int DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024;

    private static final Set<String> RESERVED_HEADERS = Set.of(
            "authorization", "x-request-id", "traceparent", "tracestate", "content-type", "content-length",
            "host", "idempotency-key", "if-match", "accept", "transfer-encoding", "connection");

    private final URI baseUrl;
    private final TokenProvider tokenProvider;
    private final HttpClient httpClient;
    private final Duration connectTimeout;
    private final Duration requestTimeout;
    private final Map<String, String> headers;
    private final String userAgent;
    private final Supplier<String> requestIdSupplier;
    private final Supplier<TraceContext> traceContextSupplier;
    private final RetryPolicy retryPolicy;
    private final AuthgebraLogger logger;
    private final int maxResponseBytes;

    private Config(Builder b) {
        this.baseUrl = b.baseUrl;
        this.tokenProvider = b.tokenProvider;
        this.httpClient = b.httpClient;
        this.connectTimeout = b.connectTimeout;
        this.requestTimeout = b.requestTimeout;
        this.headers = Map.copyOf(b.headers);
        this.userAgent = b.userAgent;
        this.requestIdSupplier = b.requestIdSupplier;
        this.traceContextSupplier = b.traceContextSupplier;
        this.retryPolicy = b.retryPolicy;
        this.logger = b.logger;
        this.maxResponseBytes = b.maxResponseBytes;
    }

    static boolean isReservedHeader(String name) {
        return RESERVED_HEADERS.contains(name.toLowerCase(Locale.ROOT));
    }

    public static Builder builder() {
        return new Builder();
    }

    URI baseUrl() {
        return baseUrl;
    }

    TokenProvider tokenProvider() {
        return tokenProvider;
    }

    HttpClient httpClient() {
        return httpClient;
    }

    Duration connectTimeout() {
        return connectTimeout;
    }

    Duration requestTimeout() {
        return requestTimeout;
    }

    Map<String, String> headers() {
        return headers;
    }

    String userAgent() {
        return userAgent;
    }

    Supplier<String> requestIdSupplier() {
        return requestIdSupplier;
    }

    Supplier<TraceContext> traceContextSupplier() {
        return traceContextSupplier;
    }

    RetryPolicy retryPolicy() {
        return retryPolicy;
    }

    AuthgebraLogger logger() {
        return logger;
    }

    int maxResponseBytes() {
        return maxResponseBytes;
    }

    /** Builder. */
    public static final class Builder {
        private URI baseUrl;
        private TokenProvider tokenProvider = TokenProvider.none();
        private HttpClient httpClient;
        private Duration connectTimeout = DEFAULT_CONNECT_TIMEOUT;
        private Duration requestTimeout = DEFAULT_REQUEST_TIMEOUT;
        private final Map<String, String> headers = new LinkedHashMap<>();
        private String userAgentSuffix = "";
        private Supplier<String> requestIdSupplier = () -> "req-" + java.util.UUID.randomUUID().toString().replace("-", "");
        private Supplier<TraceContext> traceContextSupplier = TraceContext::newRoot;
        private RetryPolicy retryPolicy = RetryPolicy.defaults();
        private AuthgebraLogger logger = AuthgebraLogger.NOOP;
        private int maxResponseBytes = DEFAULT_MAX_RESPONSE_BYTES;
        private boolean allowInsecureHttp;
        private String userAgent;

        /** Server root, e.g. {@code https://authgebra.example.com}. Must be https unless loopback or opted out. */
        public Builder baseUrl(String value) {
            this.baseUrl = URI.create(Objects.requireNonNull(value, "baseUrl"));
            return this;
        }

        /** Bearer token source. Default: anonymous. */
        public Builder tokenProvider(TokenProvider value) {
            this.tokenProvider = Objects.requireNonNull(value);
            return this;
        }

        /**
         * Use your own {@link HttpClient} (TLS settings, proxy, executor). You own its lifecycle. Its own
         * timeouts are not changed; per-request timeouts from this config still apply.
         */
        public Builder httpClient(HttpClient value) {
            this.httpClient = value;
            return this;
        }

        /** Connect timeout for the SDK-created {@link HttpClient}. Must be positive. */
        public Builder connectTimeout(Duration value) {
            this.connectTimeout = requirePositive(value, "connectTimeout");
            return this;
        }

        /** Total time allowed per attempt. Must be positive. */
        public Builder requestTimeout(Duration value) {
            this.requestTimeout = requirePositive(value, "requestTimeout");
            return this;
        }

        /** Add a header sent on every request. Reserved names (Authorization, X-Request-ID, …) are rejected. */
        public Builder header(String name, String value) {
            if (RESERVED_HEADERS.contains(name.toLowerCase(Locale.ROOT))) {
                throw new IllegalArgumentException("header is managed by the SDK: " + name);
            }
            this.headers.put(name, Objects.requireNonNull(value));
            return this;
        }

        /** Appended to {@code authgebra-java/<version>} in the User-Agent. */
        public Builder userAgentSuffix(String value) {
            this.userAgentSuffix = value == null ? "" : value;
            return this;
        }

        /** Source of request ids; values must match {@code [A-Za-z0-9._-]{8,128}}. */
        public Builder requestIdSupplier(Supplier<String> value) {
            this.requestIdSupplier = Objects.requireNonNull(value);
            return this;
        }

        /** Source of the trace context for calls that do not pass an explicit traceparent. */
        public Builder traceContextSupplier(Supplier<TraceContext> value) {
            this.traceContextSupplier = Objects.requireNonNull(value);
            return this;
        }

        /** Retry policy (default: {@link RetryPolicy#defaults()}). */
        public Builder retryPolicy(RetryPolicy value) {
            this.retryPolicy = Objects.requireNonNull(value);
            return this;
        }

        /** Redacted event sink. */
        public Builder logger(AuthgebraLogger value) {
            this.logger = Objects.requireNonNull(value);
            return this;
        }

        /** Response size limit in bytes (1 KiB .. 256 MiB). */
        public Builder maxResponseBytes(int value) {
            if (value < 1024 || value > 256 * 1024 * 1024) {
                throw new IllegalArgumentException("maxResponseBytes must be within 1 KiB..256 MiB");
            }
            this.maxResponseBytes = value;
            return this;
        }

        /** Permit plain {@code http://} to a non-loopback host. Off by default; TLS verification cannot be disabled. */
        public Builder allowInsecureHttp(boolean value) {
            this.allowInsecureHttp = value;
            return this;
        }

        public Config build() {
            if (baseUrl == null || baseUrl.getHost() == null) {
                throw new IllegalArgumentException("baseUrl is required and must be absolute");
            }
            String scheme = baseUrl.getScheme() == null ? "" : baseUrl.getScheme().toLowerCase(Locale.ROOT);
            if (!scheme.equals("https") && !scheme.equals("http")) {
                throw new IllegalArgumentException("baseUrl must be http(s)");
            }
            if (scheme.equals("http") && !allowInsecureHttp && !isLoopback(baseUrl.getHost())) {
                throw new IllegalArgumentException(
                        "plain http is only allowed for loopback hosts; use https or allowInsecureHttp(true)");
            }
            if (baseUrl.getRawQuery() != null || baseUrl.getRawFragment() != null || baseUrl.getUserInfo() != null) {
                throw new IllegalArgumentException("baseUrl must not contain credentials, query or fragment");
            }
            this.userAgent = "authgebra-java/" + Version.VERSION + " java/" + System.getProperty("java.specification.version")
                    + (userAgentSuffix.isBlank() ? "" : " " + userAgentSuffix.strip());
            return new Config(this);
        }

        private static boolean isLoopback(String host) {
            return host.equals("localhost") || host.equals("127.0.0.1") || host.equals("[::1]") || host.equals("::1");
        }

        private static Duration requirePositive(Duration value, String name) {
            if (value == null || value.isNegative() || value.isZero()) {
                throw new IllegalArgumentException(name + " must be positive (no unbounded waits)");
            }
            return value;
        }
    }
}
