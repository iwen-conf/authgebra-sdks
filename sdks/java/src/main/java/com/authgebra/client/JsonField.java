package com.authgebra.client;

import com.fasterxml.jackson.core.JsonGenerator;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.BeanProperty;
import com.fasterxml.jackson.databind.DeserializationContext;
import com.fasterxml.jackson.databind.JavaType;
import com.fasterxml.jackson.databind.JsonDeserializer;
import com.fasterxml.jackson.databind.JsonMappingException;
import com.fasterxml.jackson.databind.JsonSerializer;
import com.fasterxml.jackson.databind.SerializerProvider;
import com.fasterxml.jackson.databind.annotation.JsonDeserialize;
import com.fasterxml.jackson.databind.annotation.JsonSerialize;
import com.fasterxml.jackson.databind.deser.ContextualDeserializer;
import java.io.IOException;
import java.util.Objects;

/** A typed JSON property that distinguishes absence, explicit null, and a value. */
@JsonSerialize(using = JsonField.Serializer.class)
@JsonDeserialize(using = JsonField.Deserializer.class)
public final class JsonField<T> {
    private enum State { ABSENT, NULL, VALUE }

    /** Marks generated field-based constructors, keeping legacy null constructors unambiguous. */
    public enum Mode { TYPED }

    private final State state;
    private final T value;

    private JsonField(State state, T value) {
        this.state = state;
        this.value = value;
    }

    public static <T> JsonField<T> absent() { return new JsonField<>(State.ABSENT, null); }
    public static <T> JsonField<T> nullValue() { return new JsonField<>(State.NULL, null); }
    public static <T> JsonField<T> of(T value) { return new JsonField<>(State.VALUE, Objects.requireNonNull(value)); }

    /** Legacy constructors used null for absence. */
    public static <T> JsonField<T> fromNullable(T value) { return value == null ? absent() : of(value); }

    static <T> JsonField<T> legacy(T value, boolean clear) {
        if (clear && value != null) throw new IllegalArgumentException("PATCH cannot have both a value and explicit null");
        return clear ? nullValue() : fromNullable(value);
    }

    public boolean isSet() { return state != State.ABSENT; }
    public boolean isNull() { return state == State.NULL; }
    public T orNull() { return value; }

    @Override public boolean equals(Object other) {
        return other instanceof JsonField<?> field && state == field.state && Objects.equals(value, field.value);
    }
    @Override public int hashCode() { return Objects.hash(state, value); }
    @Override public String toString() { return "JsonField[" + state + "]"; }

    public static final class Serializer extends JsonSerializer<JsonField<?>> {
        @Override public boolean isEmpty(SerializerProvider provider, JsonField<?> value) {
            return value == null || !value.isSet();
        }
        @Override public void serialize(JsonField<?> field, JsonGenerator generator, SerializerProvider provider)
                throws IOException {
            if (!field.isSet()) throw JsonMappingException.from(generator, "an absent property has no standalone JSON value");
            if (field.isNull()) generator.writeNull();
            else provider.defaultSerializeValue(field.value, generator);
        }
    }

    public static final class Deserializer extends JsonDeserializer<JsonField<?>> implements ContextualDeserializer {
        private final JavaType valueType;
        public Deserializer() { this(null); }
        private Deserializer(JavaType valueType) { this.valueType = valueType; }

        @Override public JsonDeserializer<?> createContextual(DeserializationContext context, BeanProperty property)
                throws JsonMappingException {
            JavaType wrapper = property == null ? context.getContextualType() : property.getType();
            if (wrapper == null || wrapper.containedTypeCount() != 1) {
                throw JsonMappingException.from(context, "JsonField requires its concrete value type");
            }
            return new Deserializer(wrapper.containedType(0));
        }
        @Override public JsonField<?> deserialize(JsonParser parser, DeserializationContext context) throws IOException {
            if (valueType == null) throw JsonMappingException.from(context, "JsonField has no contextual value type");
            return JsonField.of(context.readValue(parser, valueType));
        }
        @Override public JsonField<?> getNullValue(DeserializationContext context) { return JsonField.nullValue(); }
        @Override public JsonField<?> getAbsentValue(DeserializationContext context) { return JsonField.absent(); }
    }
}
