using System.Text.Json;
using System.Text.Json.Serialization;

namespace Authgebra;

/// <summary>Arbitrary JSON only for fields whose OpenAPI schema leaves the value unconstrained.</summary>
[JsonConverter(typeof(JsonValueConverter))]
public readonly struct JsonValue
{
    private readonly JsonElement _value;

    public JsonValue(JsonElement value) => _value = value.Clone();
    public JsonValueKind ValueKind => _value.ValueKind;
    public JsonElement Element => _value;
    public JsonElement GetProperty(string propertyName) => _value.GetProperty(propertyName);
    public string GetRawText() => _value.GetRawText();
    public static implicit operator JsonValue(JsonElement value) => new(value);
    public static implicit operator JsonElement(JsonValue value) => value._value;
}

internal sealed class JsonValueConverter : JsonConverter<JsonValue>
{
    public override bool HandleNull => true;

    public override JsonValue Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        using var document = JsonDocument.ParseValue(ref reader);
        return new JsonValue(document.RootElement);
    }

    public override void Write(Utf8JsonWriter writer, JsonValue value, JsonSerializerOptions options) => value.Element.WriteTo(writer);
}
