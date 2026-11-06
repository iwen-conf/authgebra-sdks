package com.authgebra.client;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.regex.Pattern;

/** Per-call overrides. Immutable; build with {@link #builder()}. */
public final class CallOptions {
    static final Pattern REQUEST_ID = Pattern.compile("[A-Za-z0-9._-]{8,128}");
    static final Pattern IDEMPOTENCY_KEY = Pattern.compile("[A-Za-z0-9._-]{1,128}");
    private static final CallOptions DEFAULTS = new Builder().build();

    private final String requestId;
    private final String traceparent;
    private final String tracestate;
    private final String idempotencyKey;
    private final String ifMatch;
    private final Map<String, String> headers;
    private final Duration requestTimeout;
    private final RetryPolicy retryPolicy;
    private final Boolean readOnly;

    private CallOptions(Builder b) {
        this.requestId = b.requestId;
        this.traceparent = b.traceparent;
        this.tracestate = b.tracestate;
        this.idempotencyKey = b.idempotencyKey;
        this.ifMatch = b.ifMatch;
        this.headers = Map.copyOf(b.headers);
        this.requestTimeout = b.requestTimeout;
        this.retryPolicy = b.retryPolicy;
        this.readOnly = b.readOnly;
    }

    /** No overrides. */
    public static CallOptions defaults() {
        return DEFAULTS;
    }

    public static Builder builder() {
        return new Builder();
    }

    String requestId() {
        return requestId;
    }

    String traceparent() {
        return traceparent;
    }

    String tracestate() {
        return tracestate;
    }

    String idempotencyKey() {
        return idempotencyKey;
    }

    String ifMatch() {
        return ifMatch;
    }

    Map<String, String> headers() {
        return headers;
    }

    Duration requestTimeout() {
        return requestTimeout;
    }

    RetryPolicy retryPolicy() {
        return retryPolicy;
    }

    Boolean readOnly() {
        return readOnly;
    }

    /** Builder. */
    public static final class Builder {
        private String requestId;
        private String traceparent;
        private String tracestate;
        private String idempotencyKey;
        private String ifMatch;
        private final Map<String, String> headers = new LinkedHashMap<>();
        private Duration requestTimeout;
        private RetryPolicy retryPolicy;
        private Boolean readOnly;

        /** Use this {@code X-Request-ID} ({@code [A-Za-z0-9._-]{8,128}}) instead of a generated one. */
        public Builder requestId(String value) {
            this.requestId = value != null && REQUEST_ID.matcher(value).matches() ? value : null;
            return this;
        }

        /** Send exactly this W3C {@code traceparent} (you own the span). Default: the SDK creates one per call. */
        public Builder traceparent(String value) {
            if (value != null && TraceContext.parse(value) == null) {
                throw new IllegalArgumentException("not a valid W3C traceparent");
            }
            this.traceparent = value;
            return this;
        }

        /** Forward a W3C {@code tracestate}; only sent together with a traceparent. */
        public Builder tracestate(String value) {
            if (value != null && (value.length() > 512 || !value.chars().allMatch(c -> c >= 0x20 && c <= 0x7e))) {
                throw new IllegalArgumentException("tracestate must be printable ASCII, at most 512 characters");
            }
            this.tracestate = value;
            return this;
        }

        /** {@code Idempotency-Key} ({@code [A-Za-z0-9._-]{1,128}}); only operations that declare it accept one. */
        public Builder idempotencyKey(String value) {
            if (value != null && !IDEMPOTENCY_KEY.matcher(value).matches()) {
                throw new IllegalArgumentException("idempotency key must match [A-Za-z0-9._-]{1,128}");
            }
            this.idempotencyKey = value;
            return this;
        }

        /** {@code If-Match} value (the entity {@code ETag}); only operations that declare it accept one. */
        public Builder ifMatch(String value) {
            if (value != null && (value.indexOf('\r') >= 0 || value.indexOf('\n') >= 0)) {
                throw new IllegalArgumentException("If-Match must not contain line breaks");
            }
            this.ifMatch = value;
            return this;
        }

        /** Extra header for this call. {@code Authorization}, request/trace ids and framing headers are reserved. */
        public Builder header(String name, String value) {
            this.headers.put(Objects.requireNonNull(name), Objects.requireNonNull(value));
            return this;
        }

        /** Override the total timeout for this call. */
        public Builder requestTimeout(Duration value) {
            if (value != null && (value.isNegative() || value.isZero())) {
                throw new IllegalArgumentException("requestTimeout must be positive");
            }
            this.requestTimeout = value;
            return this;
        }

        /** Override the retry policy for this call. */
        public Builder retryPolicy(RetryPolicy value) {
            this.retryPolicy = value;
            return this;
        }

        /**
         * Override read/write classification (used by {@link RawClient} for POST endpoints that are really reads).
         * Generated operations already carry the right value.
         */
        public Builder readOnly(boolean value) {
            this.readOnly = value;
            return this;
        }

        public CallOptions build() {
            return new CallOptions(this);
        }
    }
}
