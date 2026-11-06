package com.authgebra.client;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.List;
import java.util.Optional;

/**
 * The server answered with a well-formed error body. An authorization <em>deny</em> is never an
 * {@code ApiException}; {@code NOT_READY} is: the system could not produce a trustworthy decision and
 * must not be treated as a deny by the SDK.
 */
public non-sealed class ApiException extends AuthgebraException {
    private static final long serialVersionUID = 1L;

    private final int status;
    private final String code;
    private final boolean knownCode;
    private final String serverMessage;
    private final String requestId;
    private final String bodyRequestId;
    private final boolean requestIdMismatch;
    private final String traceId;
    private final transient JsonNode details;
    private final List<Violation> violations;
    private final Boolean retryable;
    private final Long retryAfterSeconds;
    private final String commitState;
    private final String responseSnippet;

    ApiException(Builder b) {
        super("HTTP " + b.status + " " + b.code + (b.requestId == null ? "" : " (request_id=" + b.requestId + ")"), null);
        this.status = b.status;
        this.code = b.code;
        this.knownCode = ErrorCodes.KNOWN.contains(b.code);
        this.serverMessage = b.serverMessage;
        this.requestId = b.requestId;
        this.bodyRequestId = b.bodyRequestId;
        this.requestIdMismatch = b.requestIdMismatch;
        this.traceId = b.traceId;
        this.details = b.details;
        this.violations = List.copyOf(b.violations);
        this.retryable = b.retryable;
        this.retryAfterSeconds = b.retryAfterSeconds;
        this.commitState = b.commitState;
        this.responseSnippet = b.responseSnippet;
    }

    /** HTTP status. */
    public int status() {
        return status;
    }

    /** Error code exactly as sent (never remapped). */
    public String code() {
        return code;
    }

    /** Whether {@link #code()} is one this SDK version knows. Unknown codes keep their HTTP status. */
    public boolean knownCode() {
        return knownCode;
    }

    /** Server supplied message (for humans; never branch on it). */
    public String serverMessage() {
        return serverMessage;
    }

    /** Request id: the response header when valid, else the body value; {@code null} if neither. */
    public String requestId() {
        return requestId;
    }

    /** The request id inside the body when it differs from the header (only then non-null). */
    public String bodyRequestId() {
        return bodyRequestId;
    }

    /** {@code true} when header and body carried different valid request ids. */
    public boolean requestIdMismatch() {
        return requestIdMismatch;
    }

    /** W3C trace id (32 lowercase hex) from the body or {@code X-Trace-ID}; {@code null} if absent. */
    public String traceId() {
        return traceId;
    }

    /** Raw {@code error.details}, or {@code null}. */
    public JsonNode details() {
        return details;
    }

    /** {@code error.details.violations}, in server order; empty if none. */
    public List<Violation> violations() {
        return violations;
    }

    /** Tri-state retry hint: {@code TRUE}/{@code FALSE} when the server said so, {@code null} when it did not. */
    public Boolean retryable() {
        return retryable;
    }

    /** Suggested wait in seconds (body field, else {@code Retry-After}); {@code null} if none. */
    public Long retryAfterSeconds() {
        return retryAfterSeconds;
    }

    /**
     * {@link CommitState#COMMITTED_UNPUBLISHED} when the server declared it; {@code null} otherwise
     * (absence is <em>not</em> "not committed").
     */
    public String commitState() {
        return commitState;
    }

    /** First bytes of the response body (≤ 1 KiB, UTF-8), for diagnostics. Not part of {@link #getMessage()}. */
    public Optional<String> responseSnippet() {
        return Optional.ofNullable(responseSnippet);
    }

    static Builder builder() {
        return new Builder();
    }

    static final class Builder {
        int status;
        String code;
        String serverMessage = "";
        String requestId;
        String bodyRequestId;
        boolean requestIdMismatch;
        String traceId;
        JsonNode details;
        List<Violation> violations = List.of();
        Boolean retryable;
        Long retryAfterSeconds;
        String commitState;
        String responseSnippet;

        ApiException build() {
            return ErrorCodes.CURSOR_STALE.equals(code) ? new CursorStaleException(this) : new ApiException(this);
        }
    }
}
