using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.RegularExpressions;

namespace Authgebra;

/// <summary>
/// W3C Trace Context 与 request id 的校验/生成。三个标识互不混用：
/// request_id 标识一次 HTTP 请求，trace_id 关联一条调用链，span_id 标识一个执行片段。
/// </summary>
public static partial class TraceContext
{
    [GeneratedRegex("^[A-Za-z0-9._-]{8,128}\\z")]
    private static partial Regex RequestIdPattern();

    [GeneratedRegex("^[0-9a-f]{32}\\z")]
    private static partial Regex TraceIdPattern();

    [GeneratedRegex("^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}\\z")]
    private static partial Regex TraceparentPattern();

    public static bool IsValidRequestId(string? value) => value is not null && RequestIdPattern().IsMatch(value);

    /// <summary>32 位小写十六进制且非全零。</summary>
    public static bool IsValidTraceId(string? value) =>
        value is not null && TraceIdPattern().IsMatch(value) && value != new string('0', 32);

    /// <summary>version≠ff，trace id、parent id 均非全零，全小写。</summary>
    public static bool IsValidTraceparent(string? value)
    {
        if (value is null || value.Length < 55 || value.Any(char.IsControl)
            || !TraceparentPattern().IsMatch(value[..55]) || value.StartsWith("ff-", StringComparison.Ordinal))
        {
            return false;
        }
        if (value.StartsWith("00-", StringComparison.Ordinal) ? value.Length != 55 : value.Length > 55 && value[55] != '-') return false;
        return value[3..35] != new string('0', 32) && value[36..52] != new string('0', 16);
    }

    public static string? TraceIdOf(string? traceparent) =>
        IsValidTraceparent(traceparent) ? traceparent![3..35] : null;

    /// <summary>新建一个 traceparent（随机 trace id 与 span id，均非全零）。</summary>
    public static string NewTraceparent(bool sampled = false)
    {
        Span<byte> trace = stackalloc byte[16];
        Span<byte> span = stackalloc byte[8];
        do
        {
            RandomNumberGenerator.Fill(trace);
        }
        while (trace.IndexOfAnyExcept((byte)0) < 0);
        do
        {
            RandomNumberGenerator.Fill(span);
        }
        while (span.IndexOfAnyExcept((byte)0) < 0);
        return $"00-{Convert.ToHexString(trace).ToLowerInvariant()}-{Convert.ToHexString(span).ToLowerInvariant()}-{(sampled ? "01" : "00")}";
    }

    internal static string NewRequestId() => "sdk-" + Guid.NewGuid().ToString("N");

    /// <summary>取当前 <see cref="Activity"/> 的 W3C 标识（若有）。</summary>
    internal static string? FromCurrentActivity()
    {
        var activity = Activity.Current;
        if (activity is null || activity.IdFormat != ActivityIdFormat.W3C)
        {
            return null;
        }
        var flags = activity.Recorded ? "01" : "00";
        var candidate = $"00-{activity.TraceId.ToHexString()}-{activity.SpanId.ToHexString()}-{flags}";
        return IsValidTraceparent(candidate) ? candidate : null;
    }
}
