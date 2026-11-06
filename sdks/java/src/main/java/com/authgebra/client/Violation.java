package com.authgebra.client;

/**
 * One field-level problem from {@code error.details.violations[]}.
 *
 * @param path   JSON Pointer to the offending field, or {@code null} when the server could not locate it
 * @param reason stable machine code (rely on this, not on {@code message})
 * @param message human readable text
 */
public record Violation(String path, String reason, String message) {}
