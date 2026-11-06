package com.authgebra.client;

/**
 * Typed view of a decision string. {@code UNKNOWN} (a value this SDK does not know) is <em>never</em>
 * treated as {@code ALLOW}; treat it like any other untrusted answer.
 */
public enum Decision {
    ALLOW,
    DENY,
    UNKNOWN;

    /** Parse the wire value ({@code "allow"} / {@code "deny"}). */
    public static Decision of(String wire) {
        if ("allow".equals(wire)) {
            return ALLOW;
        }
        if ("deny".equals(wire)) {
            return DENY;
        }
        return UNKNOWN;
    }
}
