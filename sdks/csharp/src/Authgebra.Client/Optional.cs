using System.Text.Json;
using System.Text.Json.Serialization;

namespace Authgebra;

/// <summary>
/// 三态字段：<see cref="Unset"/>（序列化时省略）、<see cref="Null"/>（显式 <c>null</c>）、有值。
/// 只用于「可空且非必填」的请求字段，使 PATCH 能区分「不改」「清空」「改成」。
/// </summary>
[JsonConverter(typeof(OptionalJsonConverterFactory))]
public readonly struct Optional<T> : IEquatable<Optional<T>>
{
    // 0 = 未提供（default），1 = 显式 null，2 = 有值。用显式状态而不是 `_value is null`，
    // 因为值类型（int、JsonElement…）永远不为 null，否则无法表示“显式 null”。
    private readonly byte _state;
    private readonly T? _value;

    private Optional(byte state, T? value)
    {
        _state = state;
        _value = value;
    }

    /// <summary>有值；若 <paramref name="value"/> 是 null 引用，则等同 <see cref="Null"/>。</summary>
    public Optional(T? value)
    {
        _state = value is null ? (byte)1 : (byte)2;
        _value = value;
    }

    /// <summary>未提供：序列化时该字段被省略。</summary>
    public static Optional<T> Unset => default;

    /// <summary>显式 null：序列化为 <c>"field": null</c>（对值类型同样成立）。</summary>
    public static Optional<T> Null => new(1, default);

    public bool IsSet => _state != 0;
    public bool IsNull => _state == 1;
    public T? Value => _value;

    public static implicit operator Optional<T>(T? value) => new(value);

    /// <summary>Preserves the previous JsonElement request construction path when a field becomes typed.</summary>
    public static implicit operator Optional<T>(Optional<JsonElement> value)
    {
        if (!value.IsSet) return Unset;
        if (value.IsNull) return Null;
        return new Optional<T>(value.Value.Deserialize<T>(Json.Options));
    }

    public bool Equals(Optional<T> other) =>
        _state == other._state && EqualityComparer<T?>.Default.Equals(_value, other._value);

    public override bool Equals(object? obj) => obj is Optional<T> other && Equals(other);
    public override int GetHashCode() => HashCode.Combine(_state, _value);
    public override string ToString() => _state == 0 ? "<unset>" : _state == 1 ? "null" : _value?.ToString() ?? "";
}

internal sealed class OptionalJsonConverterFactory : JsonConverterFactory
{
    public override bool CanConvert(Type typeToConvert) =>
        typeToConvert.IsGenericType && typeToConvert.GetGenericTypeDefinition() == typeof(Optional<>);

    public override JsonConverter? CreateConverter(Type typeToConvert, JsonSerializerOptions options) =>
        (JsonConverter?)Activator.CreateInstance(
            typeof(OptionalJsonConverter<>).MakeGenericType(typeToConvert.GetGenericArguments()[0]));
}

internal sealed class OptionalJsonConverter<T> : JsonConverter<Optional<T>>
{
    public override bool HandleNull => true;

    public override Optional<T> Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType == JsonTokenType.Null)
        {
            return Optional<T>.Null;
        }
        return new Optional<T>(JsonSerializer.Deserialize<T>(ref reader, options));
    }

    public override void Write(Utf8JsonWriter writer, Optional<T> value, JsonSerializerOptions options)
    {
        if (!value.IsSet)
        {
            // 属性上的 WhenWritingDefault 已把 Unset 省略；走到这里说明被放在了不能省略的位置。
            throw new InvalidOperationException("Optional<T>.Unset cannot be serialized outside an object property.");
        }
        if (value.IsNull)
        {
            writer.WriteNullValue();
            return;
        }
        JsonSerializer.Serialize(writer, value.Value!, options);
    }
}
