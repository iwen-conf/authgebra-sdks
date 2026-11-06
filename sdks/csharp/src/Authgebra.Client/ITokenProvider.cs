namespace Authgebra;

/// <summary>为每个请求提供 Access Token；SDK 不缓存、不刷新、不记录它。</summary>
public interface ITokenProvider
{
    /// <summary>返回当前 Access Token；返回 null/空串表示本次请求匿名。</summary>
    ValueTask<string?> GetTokenAsync(CancellationToken cancellationToken);
}

/// <summary>固定 Token（测试与脚本用）。</summary>
public sealed class StaticTokenProvider : ITokenProvider
{
    private readonly string? _token;

    public StaticTokenProvider(string? token) => _token = token;

    public ValueTask<string?> GetTokenAsync(CancellationToken cancellationToken) => new(_token);

    public override string ToString() => $"StaticTokenProvider({Redact.Token(_token)})";
}

internal static class Redact
{
    /// <summary>只暴露长度，绝不暴露任何字符。</summary>
    public static string Token(string? token) =>
        string.IsNullOrEmpty(token) ? "<none>" : $"<redacted:{token.Length}>";
}
