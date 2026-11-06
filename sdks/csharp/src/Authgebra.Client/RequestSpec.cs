using System.Globalization;
using System.Text.RegularExpressions;

namespace Authgebra;

/// <summary>生成代码用的请求描述（内部）。</summary>
internal sealed partial class RequestSpec
{
    [GeneratedRegex("^[A-Za-z0-9._-]{1,128}$")]
    private static partial Regex IdempotencyKeyPattern();

    private static readonly HashSet<string> Protected = new(StringComparer.OrdinalIgnoreCase)
    {
        "authorization", "idempotency-key", "if-match", "x-request-id", "traceparent", "tracestate",
    };

    private RequestSpec(string method, string pathTemplate, bool enveloped)
    {
        Method = new HttpMethod(method);
        PathTemplate = pathTemplate;
        Enveloped = enveloped;
        Mutating = Method != HttpMethod.Get && Method != HttpMethod.Head && Method != HttpMethod.Options;
    }

    public HttpMethod Method { get; }
    public string PathTemplate { get; }
    public bool Enveloped { get; }
    public bool Paged { get; private set; }

    /// <summary>会改变服务端状态；决定重试与 commit_state 语义。</summary>
    public bool Mutating { get; private set; }

    public Dictionary<string, string> PathValues { get; } = new();
    public List<KeyValuePair<string, string>> Query { get; } = new();
    public Dictionary<string, string> Headers { get; } = new(StringComparer.OrdinalIgnoreCase);
    public object? Body { get; private set; }
    public string? IfMatch { get; private set; }
    public string? IdempotencyKey { get; private set; }

    public static RequestSpec Create(string method, string pathTemplate, bool enveloped) =>
        new(method, pathTemplate, enveloped);

    /// <summary>无副作用的 POST（授权判定类）按读请求处理：可有限重试，超时不标 unknown。</summary>
    public RequestSpec AsRead()
    {
        Mutating = false;
        return this;
    }

    public RequestSpec AsPaged()
    {
        Paged = true;
        return this;
    }

    public RequestSpec WithPath(string name, object value)
    {
        ArgumentNullException.ThrowIfNull(value, name);
        PathValues[name] = Format(value);
        return this;
    }

    public RequestSpec WithQuery(string name, object? value)
    {
        if (value is not null)
        {
            Query.Add(new KeyValuePair<string, string>(name, Format(value)));
        }
        return this;
    }

    public RequestSpec WithBody(object? body)
    {
        Body = body;
        return this;
    }

    public RequestSpec WithIfMatch(string? value)
    {
        if (value is not null)
        {
            if (value.Length == 0 || value.Any(char.IsControl))
            {
                throw new ArgumentException("If-Match must be a non-empty header value without control characters.", nameof(value));
            }
            IfMatch = value;
        }
        return this;
    }

    public RequestSpec WithIdempotencyKey(string? value)
    {
        if (value is not null)
        {
            if (!IdempotencyKeyPattern().IsMatch(value))
            {
                throw new ArgumentException("Idempotency-Key must match ^[A-Za-z0-9._-]{1,128}$.", nameof(value));
            }
            IdempotencyKey = value;
        }
        return this;
    }

    public RequestSpec WithHeader(string name, string? value)
    {
        if (value is not null)
        {
            if (Protected.Contains(name))
            {
                throw new ArgumentException($"Header {name} is managed by the SDK.", nameof(name));
            }
            Headers[name] = value;
        }
        return this;
    }

    internal static string Format(object value) => value switch
    {
        Guid g => g.ToString("D"),
        bool b => b ? "true" : "false",
        DateTimeOffset t => t.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.FFFFFFF'Z'", CultureInfo.InvariantCulture),
        IFormattable f => f.ToString(null, CultureInfo.InvariantCulture),
        _ => value.ToString() ?? "",
    };

    internal static void ValidateProtected(IEnumerable<string> names)
    {
        foreach (var name in names)
        {
            if (Protected.Contains(name))
            {
                throw new ArgumentException($"Header {name} is managed by the SDK and cannot be overridden.");
            }
        }
    }
}
