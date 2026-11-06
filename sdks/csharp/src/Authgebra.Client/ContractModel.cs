using System.Collections;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace Authgebra;

/// <summary>Validates the machine-readable constraints of a named contract model.</summary>
public interface IContractModel
{
    void Validate();
}

internal static class ContractModel
{
    internal static void NonNull([System.Diagnostics.CodeAnalysis.NotNull] object? value, string field)
    {
        if (value is null) throw new JsonException(field + " is not nullable");
    }
    internal static void Nested(object? value)
    {
        if (value is IContractModel model) model.Validate();
        else if (value is IDictionary dictionary)
        {
            foreach (var item in dictionary.Values) Nested(item);
        }
        else if (value is IEnumerable items && value is not string)
        {
            foreach (var item in items) Nested(item);
        }
    }

    internal static bool ValidIamCode(string value)
    {
        if (value.Length is < 1 or > 64 || value[0] is < 'a' or > 'z') return false;
        foreach (var ch in value.AsSpan(1))
            if (ch is not (>= 'a' and <= 'z') and not (>= '0' and <= '9') and not '_' and not '.' and not '-') return false;
        return true;
    }

    internal static int CharacterCount(string value)
    {
        var count = 0;
        foreach (var rune in value.EnumerateRunes()) count++;
        return count;
    }

    internal static bool Unique<T>(IReadOnlyList<T> values)
    {
        var nodes = values.Select(value => JsonSerializer.SerializeToNode(value)).ToArray();
        for (var i = 0; i < nodes.Length; i++)
        {
            for (var j = i + 1; j < nodes.Length; j++)
            {
                if (JsonNode.DeepEquals(nodes[i], nodes[j])) return false;
            }
        }
        return true;
    }
}

internal sealed class NonNullOptionalConverter<T> : JsonConverter<T>
{
    public override bool HandleNull => true;

    public override T Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType == JsonTokenType.Null) throw new JsonException("field is optional but not nullable");
        var value = JsonSerializer.Deserialize<T>(ref reader, options);
        if (value is null) throw new JsonException("field is not nullable");
        return value;
    }

    public override void Write(Utf8JsonWriter writer, T value, JsonSerializerOptions options) => JsonSerializer.Serialize(writer, value, options);
}
