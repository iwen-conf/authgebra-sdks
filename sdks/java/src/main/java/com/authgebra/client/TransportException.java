package com.authgebra.client;

/** No usable HTTP response was obtained. */
public final class TransportException extends AuthgebraException {
    private static final long serialVersionUID = 1L;

    /** What went wrong on the wire. */
    public enum Kind {
        /** Connect or response timeout. */
        TIMEOUT("timeout"),
        /** Connection dropped or reset after it was established. */
        CONNECTION_RESET("connection_reset"),
        /** Could not connect at all. */
        CONNECT_FAILED("connect_failed"),
        /** TLS handshake / certificate failure. */
        TLS("tls"),
        /** The calling thread was interrupted. */
        CANCELLED("cancelled");

        private final String wire;

        Kind(String wire) {
            this.wire = wire;
        }

        /** Stable lowercase name shared with the other SDKs and the fixtures. */
        public String wire() {
            return wire;
        }
    }

    private final Kind kind;
    private final String commitState;
    private final int attempts;

    TransportException(Kind kind, String commitState, int attempts, Throwable cause) {
        super("transport failure: " + kind.wire() + (attempts > 1 ? " after " + attempts + " attempts" : ""), cause);
        this.kind = kind;
        this.commitState = commitState;
        this.attempts = attempts;
    }

    /** Failure kind. */
    public Kind kind() {
        return kind;
    }

    /**
     * {@link CommitState#UNKNOWN} for a write that may have reached the server (timeout after connect,
     * connection reset, interruption); {@code null} for reads and for failures before anything was sent.
     */
    public String commitState() {
        return commitState;
    }

    /** Number of attempts made (1 = no retry). */
    public int attempts() {
        return attempts;
    }
}
