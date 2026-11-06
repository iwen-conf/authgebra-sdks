package com.authgebra.client;

/**
 * {@code 409 CURSOR_STALE}: the authorization generation moved while paging. The SDK never restarts
 * pagination or stitches pages from different generations; the caller decides whether to start over.
 */
public final class CursorStaleException extends ApiException {
    private static final long serialVersionUID = 1L;

    CursorStaleException(Builder builder) {
        super(builder);
    }
}
