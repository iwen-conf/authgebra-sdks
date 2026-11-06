package com.authgebra.client;

import java.security.SecureRandom;
import java.util.Locale;

/**
 * W3C {@code traceparent} value. Trace id (16 bytes), span id (8 bytes) — lowercase hex, never all zero.
 * The request id is a different thing and is never derived from these.
 *
 * @param traceId 32 lowercase hex characters
 * @param spanId  16 lowercase hex characters
 * @param flags   two hex characters; {@code 01} = sampled
 */
public record TraceContext(String traceId, String spanId, String flags) {
    private static final SecureRandom RANDOM = new SecureRandom();

    public TraceContext {
        if (!isHex(traceId, 32) || isZero(traceId)) {
            throw new IllegalArgumentException("trace id must be 32 lowercase hex characters and not all zero");
        }
        if (!isHex(spanId, 16) || isZero(spanId)) {
            throw new IllegalArgumentException("span id must be 16 lowercase hex characters and not all zero");
        }
        if (!isHex(flags, 2)) {
            throw new IllegalArgumentException("flags must be two lowercase hex characters");
        }
    }

    /** A fresh root context (not sampled). */
    public static TraceContext newRoot() {
        return new TraceContext(randomHex(16), randomHex(8), "00");
    }

    /** Same trace, new span (a child of the caller's span). */
    public TraceContext newSpan() {
        return new TraceContext(traceId, randomHex(8), flags);
    }

    /** {@code 00-<trace>-<span>-<flags>}. */
    public String toTraceparent() {
        return "00-" + traceId + "-" + spanId + "-" + flags;
    }

    /** Strict parse; {@code null} when the value is not a valid traceparent (uppercase hex, zero ids, ff version, bad length). */
    public static TraceContext parse(String value) {
        if (value == null || value.length() < 55 || value.charAt(2) != '-' || value.charAt(35) != '-'
                || value.charAt(52) != '-') {
            return null;
        }
        String version = value.substring(0, 2);
        if (!isHex(version, 2) || "ff".equals(version)) {
            return null;
        }
        if ("00".equals(version) ? value.length() != 55 : value.length() > 55 && value.charAt(55) != '-') {
            return null;
        }
        try {
            return new TraceContext(value.substring(3, 35), value.substring(36, 52), value.substring(53, 55));
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    /** Valid 32-hex trace id (lowercase, non-zero)? */
    public static boolean isValidTraceId(String value) {
        return value != null && isHex(value, 32) && !isZero(value);
    }

    private static boolean isHex(String value, int length) {
        if (value == null || value.length() != length) {
            return false;
        }
        for (int i = 0; i < length; i++) {
            char c = value.charAt(i);
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
                return false;
            }
        }
        return true;
    }

    private static boolean isZero(String hex) {
        return hex.chars().allMatch(c -> c == '0');
    }

    private static String randomHex(int bytes) {
        while (true) {
            byte[] data = new byte[bytes];
            RANDOM.nextBytes(data);
            StringBuilder sb = new StringBuilder();
            for (byte b : data) {
                sb.append(String.format(Locale.ROOT, "%02x", b));
            }
            if (!isZero(sb.toString())) {
                return sb.toString();
            }
        }
    }
}
