namespace Authgebra;

/// <summary>单次调用的覆盖项；全部可省略。</summary>
public sealed class RequestOptions
{
    /// <summary>自带 <c>X-Request-ID</c>（须匹配 <c>^[A-Za-z0-9._-]{8,128}$</c>）；缺省时 SDK 为每次尝试生成一个。</summary>
    public string? RequestId { get; set; }

    /// <summary>自带 W3C <c>traceparent</c>；缺省时取当前 <see cref="System.Diagnostics.Activity"/>，再缺省则新建。</summary>
    public string? TraceParent { get; set; }

    /// <summary>随 <c>traceparent</c> 一起透传的 <c>tracestate</c>（原样发送）。</summary>
    public string? TraceState { get; set; }

    /// <summary>覆盖单次尝试的总超时。</summary>
    public TimeSpan? Timeout { get; set; }

    /// <summary>覆盖最大重试次数。</summary>
    public int? MaxRetries { get; set; }

    /// <summary>仅对本次调用开启写重试（仍要求同一个 <c>Idempotency-Key</c>）。</summary>
    public bool? RetryWrites { get; set; }

    /// <summary>附加请求头；不得覆盖 Authorization、Idempotency-Key、If-Match、X-Request-ID、traceparent。</summary>
    public IDictionary<string, string>? Headers { get; set; }
}
