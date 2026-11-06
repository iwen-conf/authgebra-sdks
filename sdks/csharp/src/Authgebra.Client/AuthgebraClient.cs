using System.Diagnostics;
using System.Net.Http.Headers;
using System.Net.Security;
using System.Security.Authentication;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace Authgebra;

/// <summary>
/// Authgebra <c>/api/v1</c> 客户端。线程安全，应长期复用（不要每次请求新建）。
/// SDK 只调用服务端，不做任何授权计算。
/// </summary>
public sealed class AuthgebraClient : IDisposable
{
    public const string SdkVersion = "0.1.0";
    public const string UserAgentProduct = "authgebra-csharp";

    private readonly AuthgebraClientOptions _options;
    private readonly HttpClient _http;
    private readonly bool _ownsHttp;
    private readonly Uri _baseUri;
    private readonly string _userAgent;
    private readonly ILogger? _logger;

    /// <param name="options">配置；构造后修改 <see cref="AuthgebraClientOptions.DefaultHeaders"/> 之外的字段不会生效。</param>
    /// <param name="httpClient">
    /// 可注入的 HttpClient（IHttpClientFactory 友好，SDK 不会 Dispose 它，也不会改它的配置）。
    /// 省略时 SDK 自建带连接池寿命与连接超时的实例；证书校验始终使用平台默认，没有关闭开关。
    /// </param>
    public AuthgebraClient(AuthgebraClientOptions options, HttpClient? httpClient = null)
    {
        ArgumentNullException.ThrowIfNull(options);
        if (!Uri.TryCreate(options.BaseUrl, UriKind.Absolute, out var baseUri)
            || (baseUri.Scheme != Uri.UriSchemeHttps && baseUri.Scheme != Uri.UriSchemeHttp))
        {
            throw new ArgumentException("BaseUrl must be an absolute http(s) URL.", nameof(options));
        }
        if (baseUri.Scheme == Uri.UriSchemeHttp && !options.AllowInsecureHttp)
        {
            throw new ArgumentException(
                "BaseUrl uses plain http:// which would send tokens in clear text; set AllowInsecureHttp only for local development.",
                nameof(options));
        }
        if (options.RequestTimeout <= TimeSpan.Zero || options.RequestTimeout == Timeout.InfiniteTimeSpan)
        {
            throw new ArgumentException("RequestTimeout must be a finite positive duration.", nameof(options));
        }
        if (options.MaxResponseBytes <= 0)
        {
            throw new ArgumentException("MaxResponseBytes must be positive.", nameof(options));
        }
        RequestSpec.ValidateProtected(options.DefaultHeaders.Keys);

        _options = options;
        _baseUri = baseUri;
        _logger = options.Logger;
        _userAgent = $"{UserAgentProduct}/{SdkVersion}" + (string.IsNullOrWhiteSpace(options.UserAgentSuffix) ? "" : $" {options.UserAgentSuffix}");
        if (httpClient is null)
        {
            var handler = new SocketsHttpHandler
            {
                ConnectTimeout = options.ConnectTimeout,
                PooledConnectionLifetime = TimeSpan.FromMinutes(5),
                SslOptions = new SslClientAuthenticationOptions(),
            };
            _http = new HttpClient(handler, disposeHandler: true) { Timeout = Timeout.InfiniteTimeSpan };
            _ownsHttp = true;
        }
        else
        {
            _http = httpClient;
        }

        Authorization = new AuthorizationClient(this);
        Admin = new AdminClient(this);
    }

    /// <summary>授权判定：Check / BatchCheck / ListResources / Explain / Revision。</summary>
    public AuthorizationClient Authorization { get; }

    /// <summary>管理面：全部 101 个操作按资源分组。</summary>
    public AdminClient Admin { get; }

    public void Dispose()
    {
        if (_ownsHttp)
        {
            _http.Dispose();
        }
    }

    internal async Task<ApiResult<T>> SendAsync<T>(RequestSpec spec, RequestOptions? callOptions, CancellationToken cancellationToken)
    {
        var ro = callOptions ?? new RequestOptions();
        if (ro.TraceParent is not null && !TraceContext.IsValidTraceparent(ro.TraceParent))
        {
            throw new ArgumentException("TraceParent is not a valid W3C traceparent.", nameof(callOptions));
        }
        if (ro.Headers is not null)
        {
            RequestSpec.ValidateProtected(ro.Headers.Keys);
        }
        var traceparent = ro.TraceParent ?? TraceContext.FromCurrentActivity() ?? TraceContext.NewTraceparent();
        byte[]? body = spec.Body is null ? null : JsonSerializer.SerializeToUtf8Bytes(spec.Body, spec.Body.GetType(), Json.Options);

        var maxRetries = ro.MaxRetries ?? _options.Retry.MaxRetries;
        if (maxRetries is < 0 or > RetryPolicy.HardMaxRetries)
        {
            throw new ArgumentOutOfRangeException(nameof(callOptions), "MaxRetries out of range.");
        }
        var retryWrites = ro.RetryWrites ?? _options.RetryWrites;
        var allowedRetries = !spec.Mutating ? maxRetries : (retryWrites && spec.IdempotencyKey is not null ? maxRetries : 0);
        var timeout = ro.Timeout ?? _options.RequestTimeout;
        if (timeout <= TimeSpan.Zero || timeout == Timeout.InfiniteTimeSpan)
        {
            throw new ArgumentOutOfRangeException(nameof(callOptions), "Timeout must be finite and positive.");
        }

        for (var attempt = 0; ; attempt++)
        {
            var requestId = TraceContext.IsValidRequestId(ro.RequestId) ? ro.RequestId
                : (_options.GenerateRequestId ? TraceContext.NewRequestId() : null);
            try
            {
                return await AttemptAsync<T>(spec, ro, requestId, traceparent, body, timeout, cancellationToken).ConfigureAwait(false);
            }
            catch (AuthgebraException ex) when (attempt < allowedRetries && IsRetryable(ex))
            {
                long? retryAfter = ex is ApiException api ? api.RetryAfterSeconds : null;
                var delay = _options.Retry.ComputeDelay(attempt, retryAfter);
                _logger?.LogDebug("authgebra retry {Attempt}/{Max} {Method} {Path} after {Delay}ms ({Kind})",
                    attempt + 1, allowedRetries, spec.Method, spec.PathTemplate, (int)delay.TotalMilliseconds, ex.GetType().Name);
                await _options.Retry.Delay(delay, cancellationToken).ConfigureAwait(false);
            }
        }
    }

    private static bool IsRetryable(AuthgebraException ex) => ex switch
    {
        // AUTHZ_RELOAD_PENDING / IDENTITY_RELOAD_PENDING / CURSOR_STALE / 其余一律不在名单内，永不重试。
        ApiException api => api.Code is ApiErrorCode.RateLimited or ApiErrorCode.NotReady or ApiErrorCode.IdempotencyInProgress,
        TransportException t => t.Kind != TransportErrorKind.Tls,
        _ => false,
    };

    private async Task<ApiResult<T>> AttemptAsync<T>(
        RequestSpec spec, RequestOptions ro, string? requestId, string traceparent, byte[]? body,
        TimeSpan timeout, CancellationToken ct)
    {
        var started = Stopwatch.GetTimestamp();
        using var request = await BuildRequestAsync(spec, ro, requestId, traceparent, body, ct).ConfigureAwait(false);
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
        linked.CancelAfter(timeout);

        int status;
        byte[] payload;
        HttpResponseHeaders headers;
        HttpContentHeaders? contentHeaders;
        try
        {
            using var response = await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, linked.Token).ConfigureAwait(false);
            status = (int)response.StatusCode;
            headers = response.Headers;
            contentHeaders = response.Content.Headers;
            if (response.Content.Headers.ContentLength is { } declared && declared > _options.MaxResponseBytes)
            {
                throw new ProtocolException("response_too_large", status) { SentRequestId = requestId, SentTraceParent = traceparent };
            }
            payload = await ReadBoundedAsync(response.Content, status, linked.Token).ConfigureAwait(false);
            // 头在 response 释放后仍可读（HttpResponseHeaders 是内存集合）。
            return Interpret<T>(spec, status, headers, payload, requestId, traceparent, started);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw; // 调用方取消：原样传播，不包装。
        }
        catch (ProtocolException ex) when (ex.SentTraceParent is null)
        {
            ex.SentRequestId = requestId;
            ex.SentTraceParent = traceparent;
            throw;
        }
        catch (OperationCanceledException ex)
        {
            throw Transport(TransportErrorKind.Timeout, spec, ex, requestId, traceparent);
        }
        catch (HttpRequestException ex)
        {
            throw Transport(Classify(ex), spec, ex, requestId, traceparent);
        }
        catch (IOException ex)
        {
            throw Transport(TransportErrorKind.ConnectionReset, spec, ex, requestId, traceparent);
        }
    }

    private async Task<HttpRequestMessage> BuildRequestAsync(
        RequestSpec spec, RequestOptions ro, string? requestId, string traceparent, byte[]? body, CancellationToken ct)
    {
        var path = spec.PathTemplate;
        foreach (var (name, value) in spec.PathValues)
        {
            path = path.Replace("{" + name + "}", Uri.EscapeDataString(value), StringComparison.Ordinal);
        }
        var builder = new StringBuilder(_baseUri.GetLeftPart(UriPartial.Path).TrimEnd('/')).Append(path);
        if (spec.Query.Count > 0)
        {
            builder.Append('?').AppendJoin('&', spec.Query.Select(q => $"{Uri.EscapeDataString(q.Key)}={Uri.EscapeDataString(q.Value)}"));
        }
        var request = new HttpRequestMessage(spec.Method, builder.ToString());
        request.Headers.TryAddWithoutValidation("Accept", "application/json");
        request.Headers.TryAddWithoutValidation("User-Agent", _userAgent);
        request.Headers.TryAddWithoutValidation("traceparent", traceparent);
        if (ro.TraceState is not null)
        {
            request.Headers.TryAddWithoutValidation("tracestate", ro.TraceState);
        }
        if (requestId is not null)
        {
            request.Headers.TryAddWithoutValidation("X-Request-ID", requestId);
        }
        foreach (var (name, value) in _options.DefaultHeaders)
        {
            request.Headers.TryAddWithoutValidation(name, value);
        }
        if (ro.Headers is not null)
        {
            foreach (var (name, value) in ro.Headers)
            {
                request.Headers.TryAddWithoutValidation(name, value);
            }
        }
        foreach (var (name, value) in spec.Headers)
        {
            request.Headers.TryAddWithoutValidation(name, value);
        }
        if (spec.IfMatch is not null)
        {
            request.Headers.TryAddWithoutValidation("If-Match", spec.IfMatch);
        }
        if (spec.IdempotencyKey is not null)
        {
            request.Headers.TryAddWithoutValidation("Idempotency-Key", spec.IdempotencyKey);
        }
        if (_options.TokenProvider is { } provider)
        {
            var token = await provider.GetTokenAsync(ct).ConfigureAwait(false);
            if (!string.IsNullOrEmpty(token))
            {
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
            }
        }
        if (body is not null)
        {
            // 每次尝试都用同一份字节：重试时 payload 逐字节相同（幂等键语义的前提）。
            request.Content = new ByteArrayContent(body);
            request.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json") { CharSet = "utf-8" };
        }
        return request;
    }

    private async Task<byte[]> ReadBoundedAsync(HttpContent content, int status, CancellationToken ct)
    {
        await using var stream = await content.ReadAsStreamAsync(ct).ConfigureAwait(false);
        using var buffer = new MemoryStream();
        var chunk = new byte[16 * 1024];
        int read;
        while ((read = await stream.ReadAsync(chunk, ct).ConfigureAwait(false)) > 0)
        {
            if (buffer.Length + read > _options.MaxResponseBytes)
            {
                throw new ProtocolException("response_too_large", status);
            }
            buffer.Write(chunk, 0, read);
        }
        return buffer.ToArray();
    }

    private static TransportException Transport(TransportErrorKind kind, RequestSpec spec, Exception inner, string? requestId, string traceparent) =>
        new(kind, spec.Mutating, inner) { SentRequestId = requestId, SentTraceParent = traceparent };

    private static TransportErrorKind Classify(HttpRequestException ex)
    {
        for (Exception? e = ex; e is not null; e = e.InnerException)
        {
            if (e is AuthenticationException)
            {
                return TransportErrorKind.Tls;
            }
        }
        return ex.HttpRequestError switch
        {
            HttpRequestError.SecureConnectionError => TransportErrorKind.Tls,
            HttpRequestError.ConnectionError or HttpRequestError.NameResolutionError => TransportErrorKind.ConnectionFailed,
            _ => HasIoCause(ex) ? TransportErrorKind.ConnectionReset : TransportErrorKind.ConnectionFailed,
        };
    }

    private static bool HasIoCause(HttpRequestException ex)
    {
        for (var e = ex.InnerException; e is not null; e = e.InnerException)
        {
            if (e is IOException or System.Net.Sockets.SocketException)
            {
                return true;
            }
        }
        return ex.HttpRequestError is HttpRequestError.ResponseEnded or HttpRequestError.InvalidResponse or HttpRequestError.HttpProtocolError;
    }

    // ---- 响应解释 -----------------------------------------------------------------------------

    private ApiResult<T> Interpret<T>(RequestSpec spec, int status, HttpResponseHeaders headers, byte[] payload, string? sentRequestId, string traceparent, long started)
    {
        var headerRequestId = First(headers, "X-Request-ID");
        var requestIdHeaderValid = TraceContext.IsValidRequestId(headerRequestId);
        var headerTraceId = HeaderTraceId(headers);

        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(payload);
        }
        catch (JsonException ex)
        {
            throw new ProtocolException("non_json_response", status, Snippet(payload), ex)
            {
                RequestId = requestIdHeaderValid ? headerRequestId : null,
                TraceId = headerTraceId,
                SentRequestId = sentRequestId,
                SentTraceParent = traceparent,
            };
        }

        using (document)
        {
            var root = document.RootElement;
            if (status is >= 200 and < 300)
            {
                var result = ParseSuccess<T>(spec, status, root, headers, requestIdHeaderValid ? headerRequestId : null, headerTraceId, sentRequestId, traceparent);
                _logger?.LogDebug("authgebra {Method} {Path} -> {Status} request_id={RequestId} trace_id={TraceId} {Elapsed}ms",
                    spec.Method, spec.PathTemplate, status, result.RequestId, result.TraceId, (int)Stopwatch.GetElapsedTime(started).TotalMilliseconds);
                return result;
            }
            var error = ParseError(status, root, headers, headerRequestId, requestIdHeaderValid, headerTraceId, sentRequestId, traceparent);
            _logger?.LogDebug("authgebra {Method} {Path} -> {Status} code={Code} request_id={RequestId} trace_id={TraceId} {Elapsed}ms",
                spec.Method, spec.PathTemplate, status, (error as ApiException)?.Code, error.RequestId, error.TraceId, (int)Stopwatch.GetElapsedTime(started).TotalMilliseconds);
            throw error;
        }
    }

    private static ApiResult<T> ParseSuccess<T>(
        RequestSpec spec, int status, JsonElement root, HttpResponseHeaders headers,
        string? requestId, string? headerTraceId, string? sentRequestId, string traceparent)
    {
        ProtocolException Bad(Exception? inner = null) => new("malformed_success_body", status, null, inner)
        {
            RequestId = requestId,
            TraceId = headerTraceId,
            SentRequestId = sentRequestId,
            SentTraceParent = traceparent,
        };

        T data;
        Meta? meta = null;
        Page? page = null;
        try
        {
            if (spec.Enveloped)
            {
                if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("data", out var dataElement))
                {
                    throw Bad();
                }
                data = dataElement.Deserialize<T>(Json.Options) ?? throw Bad();
                if (root.TryGetProperty("meta", out var metaElement) && metaElement.ValueKind != JsonValueKind.Null)
                {
                    if (metaElement.ValueKind != JsonValueKind.Object) throw Bad();
                    meta = metaElement.Deserialize<Meta>(Json.Options);
                }
                if (spec.Paged && (!root.TryGetProperty("page", out var requiredPage) || requiredPage.ValueKind != JsonValueKind.Object)) throw Bad();
                if (root.TryGetProperty("page", out var pageElement) && pageElement.ValueKind == JsonValueKind.Object)
                {
                    page = pageElement.Deserialize<Page>(Json.Options);
                }
            }
            else
            {
                data = root.Deserialize<T>(Json.Options) ?? throw Bad();
            }
        }
        catch (JsonException ex)
        {
            throw Bad(ex);
        }

        long? revisionHeader = long.TryParse(First(headers, "X-Authz-Revision"), System.Globalization.NumberStyles.None,
            System.Globalization.CultureInfo.InvariantCulture, out var parsed) ? parsed : null;
        return new ApiResult<T>(data, status)
        {
            Meta = meta,
            Page = page,
            RequestId = requestId,
            TraceId = headerTraceId,
            ETag = First(headers, "ETag"),
            AuthzRevisionHeader = revisionHeader,
            Replayed = string.Equals(First(headers, "Idempotency-Replayed"), "true", StringComparison.OrdinalIgnoreCase),
        };
    }

    private static AuthgebraException ParseError(
        int status, JsonElement root, HttpResponseHeaders headers, string? headerRequestId, bool headerValid,
        string? headerTraceId, string? sentRequestId, string traceparent)
    {
        var fallbackRequestId = headerValid ? headerRequestId : null;
        var fallbackTrace = headerTraceId;
        if (root.ValueKind != JsonValueKind.Object
            || !root.TryGetProperty("error", out var error) || error.ValueKind != JsonValueKind.Object
            || !error.TryGetProperty("code", out var codeElement) || codeElement.ValueKind != JsonValueKind.String
            || string.IsNullOrEmpty(codeElement.GetString()))
        {
            return new ProtocolException("malformed_error_body", status)
            {
                RequestId = fallbackRequestId,
                TraceId = fallbackTrace,
                SentRequestId = sentRequestId,
                SentTraceParent = traceparent,
            };
        }

        var code = codeElement.GetString()!;
        var message = StringOf(error, "message") ?? "";
        var bodyRequestId = StringOf(error, "request_id");
        var bodyRequestIdValid = TraceContext.IsValidRequestId(bodyRequestId);
        var resolvedRequestId = headerValid ? headerRequestId : (bodyRequestIdValid ? bodyRequestId : null);
        var mismatch = headerValid && bodyRequestIdValid && headerRequestId != bodyRequestId;
        var bodyTrace = StringOf(error, "trace_id");
        var traceId = TraceContext.IsValidTraceId(bodyTrace) ? bodyTrace : fallbackTrace;

        JsonElement? details = null;
        var violations = new List<Violation>();
        if (error.TryGetProperty("details", out var detailsElement) && detailsElement.ValueKind != JsonValueKind.Null)
        {
            details = detailsElement.Clone();
            if (detailsElement.ValueKind == JsonValueKind.Object
                && detailsElement.TryGetProperty("violations", out var list) && list.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in list.EnumerateArray())
                {
                    try
                    {
                        var violation = item.Deserialize<Violation>(Json.Options);
                        if (violation is not null && !string.IsNullOrEmpty(violation.Reason))
                        {
                            violations.Add(violation);
                        }
                    }
                    catch (JsonException)
                    {
                        // 一个违规项畸形不应让整个错误体不可用；跳过。
                    }
                }
            }
        }

        bool? retryable = error.TryGetProperty("retryable", out var r) && r.ValueKind is JsonValueKind.True or JsonValueKind.False
            ? r.GetBoolean() : null;
        long? retryAfter = error.TryGetProperty("retry_after_seconds", out var ra) && ra.ValueKind == JsonValueKind.Number && ra.TryGetInt64(out var raValue)
            ? raValue
            : long.TryParse(First(headers, "Retry-After"), System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out var h) ? h : null;
        var commitRaw = StringOf(error, "commit_state");
        CommitStateKind? commit = commitRaw == "committed_unpublished" ? CommitStateKind.CommittedUnpublished : null;

        ApiException ex = code == ApiErrorCode.CursorStale
            ? new CursorStaleException(status, message)
            : new ApiException(status, code, message);
        ex.RequestId = resolvedRequestId;
        ex.TraceId = traceId;
        ex.SentRequestId = sentRequestId;
        ex.SentTraceParent = traceparent;
        ex.BodyRequestId = bodyRequestId;
        ex.RequestIdHeaderValid = headerValid;
        ex.RequestIdMismatch = mismatch;
        ex.Details = details;
        ex.Violations = violations;
        ex.Retryable = retryable;
        ex.RetryAfterSeconds = retryAfter;
        ex.CommitState = commit;
        ex.CommitStateRaw = commitRaw;
        return ex;
    }

    private static string? First(HttpResponseHeaders headers, string name) =>
        headers.TryGetValues(name, out var values) ? values.FirstOrDefault() : null;

    private static string? HeaderTraceId(HttpResponseHeaders headers)
    {
        var direct = First(headers, "X-Trace-ID");
        if (TraceContext.IsValidTraceId(direct))
        {
            return direct;
        }
        return TraceContext.TraceIdOf(First(headers, "traceparent"));
    }

    private static string? StringOf(JsonElement obj, string name) =>
        obj.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    private static string Snippet(byte[] payload)
    {
        var text = Encoding.UTF8.GetString(payload, 0, Math.Min(payload.Length, 256));
        return new string(text.Select(c => char.IsControl(c) ? ' ' : c).ToArray());
    }
}
