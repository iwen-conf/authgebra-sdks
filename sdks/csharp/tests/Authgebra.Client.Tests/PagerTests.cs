using Xunit;

namespace Authgebra.Tests;

public class PagerTests
{
    private static string Page(string[] ids, string? next, long generation) =>
        "{\"data\":[" + string.Join(",", ids.Select(i => $"{{\"resource_type_code\":\"document\",\"external_id\":\"{i}\"}}")) + "]," +
        $"\"page\":{{\"next_cursor\":{(next is null ? "null" : $"\"{next}\"")},\"has_more\":{(next is null ? "false" : "true")}}}," +
        $"\"meta\":{{\"authz_revision\":{generation},\"snapshot_generation\":{generation}}}}}";

    private static ListResourcesRequest First() =>
        new() { SubjectId = Rig.Subject, Permission = "document:read", ResourceType = "document" };

    private static async Task<List<string>> Drain(AuthgebraClient client, PagerOptions? pager = null)
    {
        var all = new List<string>();
        await foreach (var page in client.Authorization.ListResourcesPagesAsync(First(), pager))
        {
            all.AddRange(page.Data.Select(r => r.ExternalId));
        }
        return all;
    }

    [Fact]
    public async Task FollowsCursorsInOrderAndStopsWhenThereAreNoMorePages()
    {
        var handler = new FakeHandler((n, _, _) => Task.FromResult(FakeHandler.Json(200, n switch
        {
            1 => Page(new[] { "a", "b" }, "c1", 5),
            2 => Page(new[] { "c" }, "c2", 5),
            _ => Page(new[] { "d" }, null, 5),
        })));
        using var client = Rig.Client(handler);
        Assert.Equal(new[] { "a", "b", "c", "d" }, await Drain(client));
        Assert.Contains("\"cursor\":\"c1\"", System.Text.Encoding.UTF8.GetString(handler.Requests[1].Body));
        Assert.Contains("\"cursor\":\"c2\"", System.Text.Encoding.UTF8.GetString(handler.Requests[2].Body));
    }

    [Fact]
    public async Task CursorStaleSurfacesAndIsNotAutomaticallyRestarted()
    {
        var handler = new FakeHandler((n, _, _) => Task.FromResult(n == 1
            ? FakeHandler.Json(200, Page(new[] { "a" }, "c1", 5))
            : FakeHandler.Json(409, """{"error":{"code":"CURSOR_STALE","message":"m","request_id":"req-abcdefgh"}}""")));
        using var client = Rig.Client(handler);
        await Assert.ThrowsAsync<CursorStaleException>(() => Drain(client));
        Assert.Equal(2, handler.Requests.Count); // 没有从首页重新开始
    }

    [Fact]
    public async Task PagesFromDifferentGenerationsAreNeverMerged()
    {
        var handler = new FakeHandler((n, _, _) => Task.FromResult(FakeHandler.Json(200,
            n == 1 ? Page(new[] { "a" }, "c1", 5) : Page(new[] { "b" }, null, 6))));
        using var client = Rig.Client(handler);
        var ex = await Assert.ThrowsAsync<PagerException>(() => Drain(client));
        Assert.Equal(PagerFailure.GenerationMismatch, ex.Failure);
    }

    [Fact]
    public async Task PageCountIsBounded()
    {
        var handler = new FakeHandler((n, _, _) => Task.FromResult(FakeHandler.Json(200, Page(new[] { "x" }, "more", 5))));
        using var client = Rig.Client(handler);
        var ex = await Assert.ThrowsAsync<PagerException>(() => Drain(client, new PagerOptions { MaxPages = 3 }));
        Assert.Equal(PagerFailure.PageLimitExceeded, ex.Failure);
        Assert.Equal(3, handler.Requests.Count);
    }

    [Fact]
    public async Task EnumerationIsPullBasedAndCancellable()
    {
        var handler = new FakeHandler((n, _, _) => Task.FromResult(FakeHandler.Json(200, Page(new[] { "x" }, "more", 5))));
        using var client = Rig.Client(handler);
        using var cts = new CancellationTokenSource();
        var seen = 0;
        await foreach (var _ in client.Authorization.ListResourcesPagesAsync(First(), null, null, cts.Token))
        {
            if (++seen == 2)
            {
                break; // 调用方不再拉取，就不会再发请求（背压）
            }
        }
        Assert.Equal(2, handler.Requests.Count);
    }
}
