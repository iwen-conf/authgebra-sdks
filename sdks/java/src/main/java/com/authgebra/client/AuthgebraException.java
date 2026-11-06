package com.authgebra.client;

/**
 * Root of every error the SDK raises. Exactly three families exist:
 * <ul>
 *   <li>{@link ApiException} — the server answered with a well-formed error body;</li>
 *   <li>{@link TransportException} — no usable HTTP response (timeout, reset, TLS, …);</li>
 *   <li>{@link ProtocolException} — a response arrived but could not be understood.</li>
 * </ul>
 * Messages never contain tokens, request bodies or response bodies.
 */
public abstract sealed class AuthgebraException extends RuntimeException
        permits ApiException, TransportException, ProtocolException {
    private static final long serialVersionUID = 1L;

    AuthgebraException(String message, Throwable cause) {
        super(message, cause);
    }
}
