package com.authgebra.client;

import com.fasterxml.jackson.databind.JsonNode;

/**
 * Untyped escape hatch for endpoints the generated clients do not cover yet. Same auth, ids, tracing,
 * retry and error rules as every other call. POST/PUT/PATCH/DELETE are treated as writes unless
 * {@link CallOptions.Builder#readOnly(boolean)} says otherwise.
 */
public final class RawClient {
    private final Http http;

    RawClient(Http http) {
        this.http = http;
    }

    /** Execute {@code method path} with an optional JSON body; returns the {@code data} node. */
    public Result<JsonNode> execute(String method, String path, Object body, CallOptions options) {
        return http.raw(method, path, null, body, options);
    }

    /** Same with query parameters. */
    public Result<JsonNode> execute(String method, String path, Query query, Object body, CallOptions options) {
        return http.raw(method, path, query, body, options);
    }
}
