package com.authgebra.client;

import java.util.Iterator;
import java.util.NoSuchElementException;
import java.util.Objects;

/**
 * Decision endpoints: check, batch check, list, explain, revision. The SDK only calls the server;
 * it never evaluates RBAC/ABAC/ReBAC itself.
 *
 * <p>Semantics worth knowing: {@code decision=deny} is a normal {@link Result}. A
 * {@code 503 NOT_READY} is an {@link ApiException}: the system could not produce a trustworthy
 * decision — apply your own fail-closed policy, but keep the original error for diagnosis.
 */
public final class AuthorizationClient {
    /** Default bound for {@link #listResourcePages}. */
    public static final int DEFAULT_MAX_PAGES = 100;

    private final AdminClient.Authz authz;

    AuthorizationClient(AdminClient.Authz authz) {
        this.authz = authz;
    }

    /** Single decision. */
    public Result<Models.CheckResponse> check(Models.CheckRequest request) {
        return check(request, CallOptions.defaults());
    }

    public Result<Models.CheckResponse> check(Models.CheckRequest request, CallOptions options) {
        return authz.check(Objects.requireNonNull(request), options);
    }

    /** Many resources at once (server keeps order and de-duplicates). */
    public Result<Models.BatchCheckResponse> batchCheck(Models.BatchCheckRequest request) {
        return batchCheck(request, CallOptions.defaults());
    }

    public Result<Models.BatchCheckResponse> batchCheck(Models.BatchCheckRequest request, CallOptions options) {
        return authz.batchCheck(Objects.requireNonNull(request), options);
    }

    /** Explain a decision. */
    public Result<Models.ExplainResponse> explain(Models.ExplainRequest request) {
        return explain(request, CallOptions.defaults());
    }

    public Result<Models.ExplainResponse> explain(Models.ExplainRequest request, CallOptions options) {
        return authz.explain(Objects.requireNonNull(request), options);
    }

    /** Published revision / snapshot generation status. */
    public Result<Models.RevisionResponse> revision() {
        return authz.revision(CallOptions.defaults());
    }

    public Result<Models.RevisionResponse> revision(CallOptions options) {
        return authz.revision(options);
    }

    /** One page of authorized resources. */
    public Result<PagedData<Models.AuthzResourceRef>> listResources(Models.ListResourcesRequest request) {
        return listResources(request, CallOptions.defaults());
    }

    public Result<PagedData<Models.AuthzResourceRef>> listResources(Models.ListResourcesRequest request,
            CallOptions options) {
        return authz.listAuthorizedResources(Objects.requireNonNull(request), options);
    }

    /** Typed decision of a check result; unknown values are {@link Decision#UNKNOWN}, never ALLOW. */
    public static Decision decision(Result<Models.CheckResponse> result) {
        return result.data() == null ? Decision.UNKNOWN : Decision.of(result.data().decision());
    }

    /**
     * Page iterator bounded by {@code maxPages}. It never restarts and never merges generations:
     * {@code CURSOR_STALE} surfaces as {@link CursorStaleException}; if a page reports a different
     * {@code snapshot_generation} than the first, a {@link ProtocolException} is thrown. Call
     * {@link ResourcePages#cancel()} (or interrupt the thread) to stop.
     */
    public ResourcePages listResourcePages(Models.ListResourcesRequest first, int maxPages, CallOptions options) {
        if (maxPages < 1) {
            throw new IllegalArgumentException("maxPages must be >= 1");
        }
        return new ResourcePages(Objects.requireNonNull(first), maxPages, options);
    }

    /** See {@link #listResourcePages}. */
    public final class ResourcePages implements Iterator<Result<PagedData<Models.AuthzResourceRef>>> {
        private final Models.ListResourcesRequest first;
        private final int maxPages;
        private final CallOptions options;
        private String cursor;
        private boolean started;
        private boolean exhausted;
        private boolean cancelled;
        private int fetched;
        private Long generation;

        ResourcePages(Models.ListResourcesRequest first, int maxPages, CallOptions options) {
            this.first = first;
            this.maxPages = maxPages;
            this.options = options;
        }

        /** Stop iterating; {@link #hasNext()} becomes false. */
        public void cancel() {
            cancelled = true;
        }

        /** {@code true} when iteration stopped because {@code maxPages} was reached while more pages exist. */
        public boolean truncated() {
            return !exhausted && fetched >= maxPages && started;
        }

        @Override
        public boolean hasNext() {
            return !cancelled && !exhausted && fetched < maxPages;
        }

        @Override
        public Result<PagedData<Models.AuthzResourceRef>> next() {
            if (!hasNext()) {
                throw new NoSuchElementException();
            }
            if (Thread.currentThread().isInterrupted()) {
                throw new TransportException(TransportException.Kind.CANCELLED, null, 0, null);
            }
            Models.ListResourcesRequest request = started
                    ? new Models.ListResourcesRequest(first.context(), cursor, first.limit(), first.permission(),
                            first.resourceType(), first.subjectId())
                    : first;
            Result<PagedData<Models.AuthzResourceRef>> page = listResources(request, options);
            Long pageGeneration = page.snapshotGeneration().orElse(null);
            if (started && !Objects.equals(generation, pageGeneration)) {
                exhausted = true;
                throw new ProtocolException(ProtocolException.GENERATION_CHANGED, page.status(),
                        page.requestId().orElse(null), null);
            }
            generation = pageGeneration;
            started = true;
            fetched++;
            cursor = page.data().nextCursor();
            if (!page.data().hasMore() || cursor == null) {
                exhausted = true;
            }
            return page;
        }
    }
}
