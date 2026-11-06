package com.authgebra.client;

/**
 * Minimal logging hook. Messages are already redacted: they contain the operation id, HTTP method,
 * path <em>template</em>, request id, status and attempt number — never tokens, headers, query values
 * or bodies.
 */
@FunctionalInterface
public interface AuthgebraLogger {
    /** Receive one event. */
    void log(String event);

    /** Discards everything (default). */
    AuthgebraLogger NOOP = event -> { };
}
