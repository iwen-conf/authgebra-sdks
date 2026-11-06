using Microsoft.Extensions.Logging;

namespace Authgebra;

/// <summary>客户端配置。默认值全部取安全保守档：校验 TLS、有限超时、有限重试、写请求不重发。</summary>
public sealed class AuthgebraClientOptions
{
    /// <summary>服务根地址，例如 <c>https://authgebra.example.com</c>。</summary>
    public string BaseUrl { get; set; } = "";

    /// <summary>Access Token 提供者；为空则所有请求匿名。</summary>
    public ITokenProvider? TokenProvider { get; set; }

    /// <summary>仅在 SDK 自建 HttpClient 时生效的连接超时。</summary>
    public TimeSpan ConnectTimeout { get; set; } = TimeSpan.FromSeconds(5);

    /// <summary>单次尝试（含读取响应体）的总超时。</summary>
    public TimeSpan RequestTimeout { get; set; } = TimeSpan.FromSeconds(30);

    /// <summary>响应体大小上限（字节）；超出按协议错误处理。</summary>
    public long MaxResponseBytes { get; set; } = 8L * 1024 * 1024;

    public RetryPolicy Retry { get; set; } = new();

    /// <summary>
    /// 是否允许写请求重试。即使为 true，也只在请求带 <c>Idempotency-Key</c> 时才重试；
    /// <c>AUTHZ_RELOAD_PENDING</c>/<c>IDENTITY_RELOAD_PENDING</c> 永不重试。
    /// </summary>
    public bool RetryWrites { get; set; }

    /// <summary>默认拒绝明文 http://（Token 会以明文传输）；仅本机开发或测试时打开。</summary>
    public bool AllowInsecureHttp { get; set; }

    /// <summary>为每次尝试自动生成 <c>X-Request-ID</c>。</summary>
    public bool GenerateRequestId { get; set; } = true;

    /// <summary>附加到 User-Agent 末尾的应用标识。</summary>
    public string? UserAgentSuffix { get; set; }

    /// <summary>每个请求都带的头；不得与受保护头冲突。</summary>
    public IDictionary<string, string> DefaultHeaders { get; } = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

    /// <summary>可选日志；只写方法、路径模板、状态、request_id、trace_id、耗时，绝不写头/体/查询值。</summary>
    public ILogger? Logger { get; set; }
}
