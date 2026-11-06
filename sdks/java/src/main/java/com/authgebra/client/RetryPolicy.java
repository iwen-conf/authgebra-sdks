package com.authgebra.client;

import java.time.Duration;
import java.util.Objects;

/**
 * Bounded retry policy. Defaults are conservative:
 * <ul>
 *   <li>reads retry at most {@code maxRetries} (2) times on {@code RATE_LIMITED}, {@code NOT_READY},
 *       timeouts, connection resets and connect failures;</li>
 *   <li>writes are <b>never</b> retried unless {@link Builder#retryIdempotentWrites(boolean)} is enabled
 *       <em>and</em> the call carries an {@code Idempotency-Key}; then only {@code NOT_READY},
 *       {@code IDEMPOTENCY_IN_PROGRESS} and transport failures are retried, with the same key and bytes;</li>
 *   <li>{@code AUTHZ_RELOAD_PENDING} / {@code IDENTITY_RELOAD_PENDING} are never retried;</li>
 *   <li>there is no unbounded mode.</li>
 * </ul>
 */
public final class RetryPolicy {
    /** Upper bound accepted for {@code maxRetries}. */
    public static final int MAX_ALLOWED_RETRIES = 10;

    /** Sleeps between attempts (replaceable in tests). */
    @FunctionalInterface
    public interface Sleeper {
        void sleep(Duration duration) throws InterruptedException;
    }

    private final int maxRetries;
    private final Duration baseDelay;
    private final Duration maxDelay;
    private final Duration maxRetryAfter;
    private final boolean retryIdempotentWrites;
    private final Sleeper sleeper;

    private RetryPolicy(Builder b) {
        this.maxRetries = b.maxRetries;
        this.baseDelay = b.baseDelay;
        this.maxDelay = b.maxDelay;
        this.maxRetryAfter = b.maxRetryAfter;
        this.retryIdempotentWrites = b.retryIdempotentWrites;
        this.sleeper = b.sleeper;
    }

    /** Defaults: 2 retries, 200 ms base, 5 s cap, honour Retry-After up to 10 s, no write retries. */
    public static RetryPolicy defaults() {
        return builder().build();
    }

    /** Never retry anything. */
    public static RetryPolicy none() {
        return builder().maxRetries(0).build();
    }

    public static Builder builder() {
        return new Builder();
    }

    public int maxRetries() {
        return maxRetries;
    }

    public Duration baseDelay() {
        return baseDelay;
    }

    public Duration maxDelay() {
        return maxDelay;
    }

    public Duration maxRetryAfter() {
        return maxRetryAfter;
    }

    public boolean retryIdempotentWrites() {
        return retryIdempotentWrites;
    }

    Sleeper sleeper() {
        return sleeper;
    }

    /** Builder. */
    public static final class Builder {
        private int maxRetries = 2;
        private Duration baseDelay = Duration.ofMillis(200);
        private Duration maxDelay = Duration.ofSeconds(5);
        private Duration maxRetryAfter = Duration.ofSeconds(10);
        private boolean retryIdempotentWrites;
        private Sleeper sleeper = d -> Thread.sleep(d.toMillis());

        /** Retries after the first attempt, 0..10. */
        public Builder maxRetries(int value) {
            if (value < 0 || value > MAX_ALLOWED_RETRIES) {
                throw new IllegalArgumentException("maxRetries must be within 0.." + MAX_ALLOWED_RETRIES);
            }
            this.maxRetries = value;
            return this;
        }

        public Builder baseDelay(Duration value) {
            this.baseDelay = positive(value, "baseDelay");
            return this;
        }

        public Builder maxDelay(Duration value) {
            this.maxDelay = positive(value, "maxDelay");
            return this;
        }

        /** Ceiling applied to a server supplied {@code Retry-After}. */
        public Builder maxRetryAfter(Duration value) {
            this.maxRetryAfter = positive(value, "maxRetryAfter");
            return this;
        }

        /** Opt in to retrying writes that carry an {@code Idempotency-Key}. Off by default. */
        public Builder retryIdempotentWrites(boolean value) {
            this.retryIdempotentWrites = value;
            return this;
        }

        Builder sleeper(Sleeper value) {
            this.sleeper = Objects.requireNonNull(value);
            return this;
        }

        /** Test hook (public so other packages' tests can avoid real sleeping). */
        public Builder withSleeper(Sleeper value) {
            return sleeper(value);
        }

        public RetryPolicy build() {
            return new RetryPolicy(this);
        }

        private static Duration positive(Duration value, String name) {
            if (value == null || value.isNegative() || value.isZero()) {
                throw new IllegalArgumentException(name + " must be positive");
            }
            return value;
        }
    }
}
