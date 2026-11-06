package com.authgebra.client;

import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * A successful (2xx) response. {@code decision=deny} is a successful result.
 *
 * @param <T> payload type
 */
public final class Result<T> {
    private final int status;
    private final T data;
    private final Models.Meta meta;
    private final String requestId;
    private final String traceId;
    private final boolean requestIdMismatch;
    private final Map<String, List<String>> headers;

    Result(int status, T data, Models.Meta meta, String requestId, String traceId, boolean requestIdMismatch,
            Map<String, List<String>> headers) {
        this.status = status;
        this.data = data;
        this.meta = meta;
        this.requestId = requestId;
        this.traceId = traceId;
        this.requestIdMismatch = requestIdMismatch;
        this.headers = headers;
    }

    /** HTTP status (200, 201, …). */
    public int status() {
        return status;
    }

    /** Business payload ({@code data}); {@code null} for operations with no body. */
    public T data() {
        return data;
    }

    /** The {@code meta} object when the server sent one (decision and write endpoints only). */
    public Optional<Models.Meta> meta() {
        return Optional.ofNullable(meta);
    }

    /** {@code meta.authz_revision}; empty when there is no meta — never a fabricated 0. */
    public Optional<Long> authzRevision() {
        return meta == null ? Optional.empty() : Optional.ofNullable(meta.authzRevision());
    }

    /** {@code meta.snapshot_generation} (decision endpoints). */
    public Optional<Long> snapshotGeneration() {
        return meta == null ? Optional.empty() : Optional.ofNullable(meta.snapshotGeneration());
    }

    /** Valid {@code X-Request-ID} from the response; empty if missing or invalid. */
    public Optional<String> requestId() {
        return Optional.ofNullable(requestId);
    }

    /** Valid W3C trace id from {@code X-Trace-ID} (or the response {@code traceparent}); empty otherwise. */
    public Optional<String> traceId() {
        return Optional.ofNullable(traceId);
    }

    /** Always {@code false} on success (the body carries no request id); kept for symmetry with errors. */
    public boolean requestIdMismatch() {
        return requestIdMismatch;
    }

    /** First value of a response header (case-insensitive). */
    public Optional<String> header(String name) {
        List<String> values = headers.get(name.toLowerCase(java.util.Locale.ROOT));
        return values == null || values.isEmpty() ? Optional.empty() : Optional.of(values.get(0));
    }
}
