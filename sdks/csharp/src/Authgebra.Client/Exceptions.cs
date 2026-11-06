using System.Text.Json;

namespace Authgebra;

/// <summary>SDK 已知的写操作提交状态。</summary>
public enum CommitStateKind
{
    /// <summary>服务端声明：事务已提交、快照尚未发布。mutation 已执行，不得重发。</summary>
    CommittedUnpublished,

    /// <summary>SDK 推断：写请求超时/连接中断，结果未知（<b>不是</b>“未提交”）。</summary>
    Unknown,
}

/// <summary>SDK 全部异常的基类。绝不包含 Token。</summary>
public abstract class AuthgebraException : Exception
{
    protected AuthgebraException(string message, Exception? inner = null) : base(message, inner)
    {
    }

    /// <summary>服务端上报的 request_id（响应头优先，其次响应体）；服务端没给就是 null，<b>不</b>用本端发送值冒充。</summary>
    public string? RequestId { get; internal set; }

    /// <summary>服务端上报的 W3C trace id（32 位小写十六进制）；没给就是 null。</summary>
    public string? TraceId { get; internal set; }

    /// <summary>本端发出的 <c>X-Request-ID</c>（用于传输失败等拿不到服务端回显时的关联排障）。</summary>
    public string? SentRequestId { get; internal set; }

    /// <summary>本端发出的 <c>traceparent</c>。</summary>
    public string? SentTraceParent { get; internal set; }
}

/// <summary>服务端返回了符合契约的错误体（<c>{"error":{...}}</c>）。</summary>
public class ApiException : AuthgebraException
{
    public ApiException(int status, string code, string serverMessage)
        : base($"{code} (HTTP {status}): {serverMessage}")
    {
        Status = status;
        Code = code;
        ServerMessage = serverMessage;
        KnownCode = ApiErrorCode.All.Contains(code);
    }

    public int Status { get; }

    /// <summary>服务端错误码的原始字符串；未知码原样保留，不会映射成已知码。</summary>
    public string Code { get; }

    /// <summary>该码是否在本 SDK 对应的契约里。服务端新增码时为 false。</summary>
    public bool KnownCode { get; }

    /// <summary>服务端给出的可读说明；仅供展示，SDK 与调用方都不应据此判断类型。</summary>
    public string ServerMessage { get; }

    /// <summary>响应体里的 request_id（与响应头不一致时用于对照）。</summary>
    public string? BodyRequestId { get; internal set; }

    /// <summary>响应头 <c>X-Request-ID</c> 存在且合法。</summary>
    public bool RequestIdHeaderValid { get; internal set; }

    /// <summary>响应头与响应体的 request_id 都合法但不相等；<see cref="AuthgebraException.RequestId"/> 取响应头。</summary>
    public bool RequestIdMismatch { get; internal set; }

    /// <summary>脱敏的 <c>details</c>（已 Clone，可在任意时刻读取）。</summary>
    public JsonElement? Details { get; internal set; }

    /// <summary>字段级违规项，保持服务端顺序；没有则为空。</summary>
    public IReadOnlyList<Violation> Violations { get; internal set; } = Array.Empty<Violation>();

    /// <summary>三态：<c>true</c>/<c>false</c>/<c>null</c>（服务端未声明）。不是写请求的重放许可。</summary>
    public bool? Retryable { get; internal set; }

    public long? RetryAfterSeconds { get; internal set; }

    /// <summary>服务端声明的提交状态；未声明或取值本 SDK 不认识时为 null（见 <see cref="CommitStateRaw"/>）。</summary>
    public CommitStateKind? CommitState { get; internal set; }

    public string? CommitStateRaw { get; internal set; }
}

/// <summary><c>CURSOR_STALE</c>：授权列表 cursor 已跨代失效。默认抛出，由调用方决定是否从首页重来。</summary>
public sealed class CursorStaleException : ApiException
{
    public CursorStaleException(int status, string serverMessage) : base(status, ApiErrorCode.CursorStale, serverMessage)
    {
    }
}

/// <summary>传输层失败（超时、连接中断、TLS）。写请求的 <see cref="CommitState"/> 为 <see cref="CommitStateKind.Unknown"/>。</summary>
public sealed class TransportException : AuthgebraException
{
    public TransportException(TransportErrorKind kind, bool wasWrite, Exception? inner = null)
        : base($"transport failure: {Name(kind)}" + (wasWrite ? " (write: commit state unknown)" : ""), inner)
    {
        Kind = kind;
        WasWrite = wasWrite;
        CommitState = wasWrite ? CommitStateKind.Unknown : null;
    }

    public TransportErrorKind Kind { get; }

    /// <summary>稳定的 snake_case 码：<c>timeout</c>/<c>connection_reset</c>/<c>connection_failed</c>/<c>tls</c>。</summary>
    public string Code => Name(Kind);

    public bool WasWrite { get; }

    /// <summary>写请求为 Unknown：不得假设未提交；核对只能用同一个 Idempotency-Key 或读取实体。</summary>
    public CommitStateKind? CommitState { get; }

    private static string Name(TransportErrorKind kind) => kind switch
    {
        TransportErrorKind.Timeout => "timeout",
        TransportErrorKind.ConnectionReset => "connection_reset",
        TransportErrorKind.ConnectionFailed => "connection_failed",
        TransportErrorKind.Tls => "tls",
        _ => "unknown",
    };
}

public enum TransportErrorKind
{
    Timeout,
    ConnectionReset,
    ConnectionFailed,
    Tls,
}

/// <summary>响应无法按契约理解：非 JSON、缺必需字段、超过大小上限等。保留 HTTP 状态码。</summary>
public sealed class ProtocolException : AuthgebraException
{
    public ProtocolException(string reason, int status, string? bodySnippet = null, Exception? inner = null)
        : base($"protocol error: {reason} (HTTP {status})", inner)
    {
        Reason = reason;
        Status = status;
        BodySnippet = bodySnippet;
    }

    /// <summary><c>non_json_response</c>、<c>malformed_error_body</c>、<c>malformed_success_body</c>、<c>response_too_large</c>。</summary>
    public string Reason { get; }

    public int Status { get; }

    /// <summary>响应体前 256 字节（控制字符已替换）；仅非 JSON 的诊断用途。</summary>
    public string? BodySnippet { get; }
}

/// <summary>分页迭代器的保护性中止。</summary>
public sealed class PagerException : AuthgebraException
{
    public PagerException(PagerFailure failure, string message) : base(message) => Failure = failure;

    public PagerFailure Failure { get; }
}

public enum PagerFailure
{
    PageLimitExceeded,
    GenerationMismatch,
}
