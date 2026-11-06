package com.authgebra.client;

/** A response arrived but violates the contract (non-JSON, missing required fields, oversized, …). */
public final class ProtocolException extends AuthgebraException {
    private static final long serialVersionUID = 1L;

    /** Stable error names. */
    public static final String NON_JSON_RESPONSE = "non_json_response";
    public static final String MALFORMED_ERROR_BODY = "malformed_error_body";
    public static final String MALFORMED_SUCCESS_BODY = "malformed_success_body";
    public static final String RESPONSE_TOO_LARGE = "response_too_large";
    public static final String GENERATION_CHANGED = "generation_changed";

    private final String error;
    private final int status;
    private final String requestId;

    ProtocolException(String error, int status, String requestId, Throwable cause) {
        super("protocol error: " + error + (status > 0 ? " (HTTP " + status + ")" : ""), cause);
        this.error = error;
        this.status = status;
        this.requestId = requestId;
    }

    /** Stable machine name (see the constants). */
    public String error() {
        return error;
    }

    /** HTTP status of the offending response; 0 when not tied to one. */
    public int status() {
        return status;
    }

    /** Request id when a valid one was present on the response; else {@code null}. */
    public String requestId() {
        return requestId;
    }
}
