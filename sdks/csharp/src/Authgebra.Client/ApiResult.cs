namespace Authgebra;

/// <summary>所有成功结果共有的只读视图（便于一致性测试与通用代码）。</summary>
public interface IApiResult
{
    int Status { get; }
    Meta? Meta { get; }
    Page? Page { get; }
    string? RequestId { get; }
    string? TraceId { get; }
    object? DataObject { get; }
}

/// <summary>
/// HTTP 2xx 的结果。<c>decision=deny</c> 也是成功结果。
/// 管理读取没有 <c>meta</c> 时 <see cref="Meta"/> 为 null——<b>不是</b> authz_revision=0。
/// </summary>
public sealed class ApiResult<T> : IApiResult
{
    public ApiResult(T data, int status)
    {
        Data = data;
        Status = status;
    }

    public T Data { get; }
    public int Status { get; }

    /// <summary>授权版本信息；只有决策与写接口带。</summary>
    public Meta? Meta { get; init; }

    /// <summary>分页状态；仅列表接口。</summary>
    public Page? Page { get; init; }

    /// <summary>响应头 <c>X-Request-ID</c>（合法时）。</summary>
    public string? RequestId { get; init; }

    /// <summary><c>X-Trace-ID</c> 或响应 <c>traceparent</c> 中的 trace id（合法时）。</summary>
    public string? TraceId { get; init; }

    public string? ETag { get; init; }

    /// <summary>响应头 <c>X-Authz-Revision</c>；与 <see cref="Meta"/> 独立，不互相填充。</summary>
    public long? AuthzRevisionHeader { get; init; }

    /// <summary><c>Idempotency-Replayed: true</c>：这是幂等重放响应。</summary>
    public bool Replayed { get; init; }

    object? IApiResult.DataObject => Data;
}
