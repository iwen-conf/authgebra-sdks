package com.authgebra.client;

/**
 * Supplies the bearer access token. Called before every attempt so rotated tokens are picked up.
 * Return {@code null} (or empty) for anonymous calls such as login or health.
 * The SDK never logs the value or puts it in an exception.
 */
@FunctionalInterface
public interface TokenProvider {
    /** Current access token, or {@code null}. */
    String token();

    /** A fixed token. */
    static TokenProvider fixed(String token) {
        return () -> token;
    }

    /** No credentials. */
    static TokenProvider none() {
        return () -> null;
    }
}
