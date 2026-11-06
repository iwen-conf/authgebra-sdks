package com.authgebra.client;

/** Values of {@code commitState} on errors. {@code null} means "nothing is known". */
public final class CommitState {
    /** Server said: the transaction committed, the snapshot is not published yet. Never resend. */
    public static final String COMMITTED_UNPUBLISHED = "committed_unpublished";
    /** SDK inference: a write timed out or the connection broke — it may or may not have committed. */
    public static final String UNKNOWN = "unknown";

    private CommitState() {}
}
