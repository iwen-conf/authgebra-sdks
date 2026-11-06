package com.authgebra.client;

import java.util.Set;

/** Error codes the server is known to emit under {@code /api/v1}. Unknown codes are preserved verbatim. */
public final class ErrorCodes {
    public static final String INVALID_REQUEST = "INVALID_REQUEST";
    public static final String CURSOR_INVALID = "CURSOR_INVALID";
    public static final String AUTH_REQUIRED = "AUTH_REQUIRED";
    public static final String INVALID_CREDENTIALS = "INVALID_CREDENTIALS";
    public static final String TOKEN_EXPIRED = "TOKEN_EXPIRED";
    public static final String FORBIDDEN = "FORBIDDEN";
    public static final String RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND";
    public static final String REVISION_CONFLICT = "REVISION_CONFLICT";
    public static final String STATE_CONFLICT = "STATE_CONFLICT";
    public static final String IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT";
    public static final String IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS";
    public static final String CURSOR_STALE = "CURSOR_STALE";
    public static final String RESOURCE_ALIAS_COLLISION = "RESOURCE_ALIAS_COLLISION";
    public static final String PRECONDITION_FAILED = "PRECONDITION_FAILED";
    public static final String PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE";
    public static final String VALIDATION_ERROR = "VALIDATION_ERROR";
    public static final String POLICY_INVALID = "POLICY_INVALID";
    public static final String POLICY_TOO_COMPLEX = "POLICY_TOO_COMPLEX";
    public static final String RATE_LIMITED = "RATE_LIMITED";
    public static final String INTERNAL_ERROR = "INTERNAL_ERROR";
    public static final String NOT_READY = "NOT_READY";
    public static final String AUTHZ_RELOAD_PENDING = "AUTHZ_RELOAD_PENDING";
    public static final String IDENTITY_RELOAD_PENDING = "IDENTITY_RELOAD_PENDING";

    /** Every code above (a test keeps this equal to the OpenAPI {@code ApiErrorCode} enum). */
    public static final Set<String> KNOWN = Set.of(
            INVALID_REQUEST, CURSOR_INVALID, AUTH_REQUIRED, INVALID_CREDENTIALS, TOKEN_EXPIRED, FORBIDDEN,
            RESOURCE_NOT_FOUND, REVISION_CONFLICT, STATE_CONFLICT, IDEMPOTENCY_CONFLICT, IDEMPOTENCY_IN_PROGRESS,
            CURSOR_STALE, RESOURCE_ALIAS_COLLISION, PRECONDITION_FAILED, PAYLOAD_TOO_LARGE, VALIDATION_ERROR,
            POLICY_INVALID, POLICY_TOO_COMPLEX, RATE_LIMITED, INTERNAL_ERROR, NOT_READY, AUTHZ_RELOAD_PENDING,
            IDENTITY_RELOAD_PENDING);

    private ErrorCodes() {}
}
