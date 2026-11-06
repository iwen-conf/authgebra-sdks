using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Xunit;

namespace Authgebra.Tests;

/// <summary>SDK 必须覆盖契约里的每个操作，且生成物必须与受审的 OpenAPI 同步。</summary>
public class ContractTests
{
    private static JsonDocument Spec() => JsonDocument.Parse(File.ReadAllBytes(Rig.RepoFile("contracts/openapi.json")));

    private static string Pascal(string id) =>
        string.Concat(id.Split('_', StringSplitOptions.RemoveEmptyEntries).Select(w => char.ToUpperInvariant(w[0]) + w[1..])) + "Async";

    [Fact]
    public void EveryOperationIdHasAGeneratedMethod()
    {
        using var spec = Spec();
        var methods = typeof(AuthgebraClient).Assembly.GetTypes()
            .Where(t => t.IsPublic && t.Namespace == "Authgebra" && t.Name.EndsWith("Api", StringComparison.Ordinal))
            .SelectMany(t => t.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly))
            .Select(m => m.Name)
            .ToList();

        var operationIds = spec.RootElement.GetProperty("paths").EnumerateObject()
            .SelectMany(p => p.Value.EnumerateObject().Select(m => m.Value.GetProperty("operationId").GetString()!))
            .ToList();

        Assert.NotEmpty(operationIds);
        Assert.Equal(ContractInfo.OperationCount, operationIds.Count);
        var missing = operationIds.Where(id => !methods.Contains(Pascal(id))).ToList();
        Assert.True(missing.Count == 0, "operations without an SDK method: " + string.Join(", ", missing));
        Assert.Equal(operationIds.Count, methods.Count); // 没有多出契约里不存在的“假方法”
    }

    [Fact]
    public void GeneratedCodeIsInSyncWithTheReviewedOpenApiDigest()
    {
        var actual = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(Rig.RepoFile("contracts/openapi.json")))).ToLowerInvariant();
        Assert.Equal(File.ReadAllText(Rig.RepoFile("contracts/openapi.sha256")).Trim(), actual);
        Assert.Equal(ContractInfo.OpenApiSha256, actual);
        using var spec = Spec();
        var count = spec.RootElement.GetProperty("paths").EnumerateObject().Sum(path => path.Value.EnumerateObject().Count());
        Assert.Equal(ContractInfo.OperationCount, count);
    }

    [Fact]
    public void KnownErrorCodesMatchTheContractEnum()
    {
        using var spec = Spec();
        var declared = spec.RootElement.GetProperty("components").GetProperty("schemas").GetProperty("ApiErrorCode")
            .GetProperty("enum").EnumerateArray().Select(e => e.GetString()!).ToHashSet();
        Assert.True(declared.SetEquals(ApiErrorCode.All));
    }
}
