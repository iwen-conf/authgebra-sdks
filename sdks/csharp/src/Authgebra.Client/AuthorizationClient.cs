using System.Runtime.CompilerServices;

namespace Authgebra;

/// <summary>分页迭代器的保护参数。</summary>
public sealed class PagerOptions
{
    /// <summary>最多拉取的页数（防止无限翻页）；超出抛 <see cref="PagerException"/>。</summary>
    public int MaxPages { get; set; } = 1000;
}

/// <summary>
/// 授权判定客户端。<c>decision=deny</c> 是成功结果；<c>NOT_READY</c> 等是异常——
/// 绝不能把异常当作 Deny 吞掉（业务方自行决定安全拒绝，但要保留原始错误）。
/// </summary>
public sealed class AuthorizationClient
{
    internal AuthorizationClient(AuthgebraClient core) => Api = new AuthzApi(core);

    /// <summary>与契约一一对应的生成方法。</summary>
    public AuthzApi Api { get; }

    public Task<ApiResult<CheckResponse>> CheckAsync(CheckRequest request, RequestOptions? options = null, CancellationToken cancellationToken = default) =>
        Api.CheckAsync(request, options, cancellationToken);

    public Task<ApiResult<BatchCheckResponse>> BatchCheckAsync(BatchCheckRequest request, RequestOptions? options = null, CancellationToken cancellationToken = default) =>
        Api.BatchCheckAsync(request, options, cancellationToken);

    public Task<ApiResult<ExplainResponse>> ExplainAsync(ExplainRequest request, RequestOptions? options = null, CancellationToken cancellationToken = default) =>
        Api.ExplainAsync(request, options, cancellationToken);

    public Task<ApiResult<RevisionResponse>> RevisionAsync(RequestOptions? options = null, CancellationToken cancellationToken = default) =>
        Api.RevisionAsync(options, cancellationToken);

    /// <summary>单页列表：cursor 不透明，由调用方原样回传。</summary>
    public Task<ApiResult<IReadOnlyList<AuthzResourceRef>>> ListResourcesAsync(ListResourcesRequest request, RequestOptions? options = null, CancellationToken cancellationToken = default) =>
        Api.ListAuthorizedResourcesAsync(request, options, cancellationToken);

    /// <summary>
    /// 逐页迭代。保证：
    /// <list type="bullet">
    /// <item>遇到 <c>CURSOR_STALE</c> 抛 <see cref="CursorStaleException"/>，<b>不</b>自动从首页重来；</item>
    /// <item>各页的 <c>snapshot_generation</c> 必须一致，否则抛 <see cref="PagerException"/>（绝不静默拼接不同代的结果）；</item>
    /// <item>页数有上限（<see cref="PagerOptions.MaxPages"/>）；按需拉取（背压），取消即停。</item>
    /// </list>
    /// </summary>
    public async IAsyncEnumerable<ApiResult<IReadOnlyList<AuthzResourceRef>>> ListResourcesPagesAsync(
        ListResourcesRequest first,
        PagerOptions? pager = null,
        RequestOptions? options = null,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        var maxPages = (pager ?? new PagerOptions()).MaxPages;
        if (maxPages <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(pager), "MaxPages must be positive.");
        }
        var request = first;
        long? generation = null;
        for (var pages = 0; ; pages++)
        {
            if (pages >= maxPages)
            {
                throw new PagerException(PagerFailure.PageLimitExceeded, $"stopped after {maxPages} pages; more pages remain.");
            }
            var page = await ListResourcesAsync(request, options, cancellationToken).ConfigureAwait(false);
            var current = page.Meta?.SnapshotGeneration;
            if (pages > 0 && current != generation)
            {
                throw new PagerException(PagerFailure.GenerationMismatch,
                    "snapshot_generation changed between pages; results from different generations must not be merged.");
            }
            generation = current;
            yield return page;
            if (page.Page is not { HasMore: true, NextCursor: { Length: > 0 } next })
            {
                yield break;
            }
            request = new ListResourcesRequest
            {
                SubjectId = first.SubjectId,
                Permission = first.Permission,
                ResourceType = first.ResourceType,
                Context = first.Context,
                Limit = first.Limit,
                Cursor = next,
            };
        }
    }
}
