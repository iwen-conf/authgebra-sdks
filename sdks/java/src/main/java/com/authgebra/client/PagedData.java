package com.authgebra.client;

import java.util.List;

/**
 * One page of a list response.
 *
 * @param items      page content, in server order
 * @param nextCursor opaque cursor for the next page, or {@code null}
 * @param hasMore    whether another page exists
 */
public record PagedData<T>(List<T> items, String nextCursor, boolean hasMore) {}
