package com.authgebra.client;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

/** Query parameters for list endpoints. Unknown names are rejected per operation (typo protection). */
public final class Query {
    private final Map<String, String> values = new LinkedHashMap<>();

    /** New empty query. */
    public static Query create() {
        return new Query();
    }

    /** Set a parameter; {@code null} values are skipped (an unset parameter stays unset). */
    public Query set(String name, Object value) {
        Objects.requireNonNull(name, "name");
        if (value != null) {
            values.put(name, value.toString());
        }
        return this;
    }

    Map<String, String> values() {
        return values;
    }
}
