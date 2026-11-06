using System.Text.Json;
using System.Text.Json.Nodes;
using Xunit;

namespace Authgebra.Tests;

public class ModelGenerationTests
{
    [Fact]
    public void ClosedRequestImportsRejectUnknownRootFields()
    {
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<CreateUserRequest>(
            "{\"username\":\"alice\",\"display_name\":\"Alice\",\"initial_password\":\"password\",\"future_field\":true}"));
    }

    [Fact]
    public void ClosedRequestImportsRejectUnknownExclusiveNestedFields()
    {
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<ChangePasswordRequest>(
            "{\"new_password\":\"password\",\"reauth\":{\"password\":\"old-password\",\"future_field\":true}}"));
    }

    [Fact]
    public void SharedResponseModelKeepsUnknownFieldForwardCompatibility()
    {
        var value = JsonSerializer.Deserialize<AuthzResourceRef>(
            "{\"resource_type_code\":\"doc\",\"external_id\":\"one\",\"future_field\":true}")!;
        Assert.Equal("doc", value.ResourceTypeCode);
        Assert.Equal("one", value.ExternalId);
    }

    [Fact]
    public void KnownUnionsHaveNamedModels()
    {
        var assembly = typeof(AuthgebraClient).Assembly;
        Assert.NotNull(assembly.GetType("Authgebra.RewriteRuleDto"));
        Assert.NotNull(assembly.GetType("Authgebra.TypedValueDto"));
    }

    [Fact]
    public void KnownUnionVariantsRetainDiscriminatorsAndUnsignedPrecision()
    {
        var rule = RewriteRuleDto.FromInclude(new RewriteRuleDtoInclude { Relation = "member" });
        var encoded = JsonSerializer.Serialize(rule);
        using var parsed = JsonDocument.Parse(encoded);
        Assert.Equal("include", parsed.RootElement.GetProperty("kind").GetString());
        Assert.Equal("member", JsonSerializer.Deserialize<RewriteRuleDto>(encoded)!.AsInclude!.Relation);
        var unsigned = TypedValueDto.FromU64(new TypedValueDtoU64 { Value = ulong.MaxValue });
        var bytes = JsonSerializer.Serialize(unsigned);
        using var parsedUnsigned = JsonDocument.Parse(bytes);
        Assert.Equal(ulong.MaxValue, parsedUnsigned.RootElement.GetProperty("value").GetUInt64());
        Assert.Equal(ulong.MaxValue, JsonSerializer.Deserialize<TypedValueDto>(bytes)!.AsU64!.Value);
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<RewriteRuleDto>("{\"kind\":\"unknown\"}"));
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<RewriteRuleDto>("{}"));
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<LoginStatus>("42"));
        Assert.Throws<ArgumentException>(() => RewriteRuleDto.FromInclude(new RewriteRuleDtoInclude { Relation = "member", Kind = "inherit" }));
    }

    [Fact]
    public void IamCodeNameAndReplaceSetUseServerMachineBounds()
    {
        foreach (var code in new[] { "A", "a\n", "a b", new string('a', 65) })
            Assert.Throws<ArgumentException>(() => new CreateRoleRequest { Code = code, Name = "role" }.Validate());
        new CreateRoleRequest { Code = "a_z.9-", Name = "role" }.Validate();
        foreach (var name in new[] { "", new string('名', 65) })
            Assert.Throws<ArgumentException>(() => new CreateRoleRequest { Code = "role", Name = name }.Validate());
        new CreateRoleRequest { Code = "role", Name = new string('名', 64) }.Validate();
        Assert.Throws<ArgumentException>(() => new ReplaceUserRolesRequest { RoleIds = new[] { Rig.Subject, Rig.Subject } }.Validate());
        Assert.Throws<ArgumentException>(() => new ReplaceUserRolesRequest { RoleIds = Enumerable.Range(0, 513).Select(i => new Guid(i, 0, 0, new byte[8])).ToArray() }.Validate());
        new ReplaceUserRolesRequest { RoleIds = Array.Empty<Guid>() }.Validate();
    }

    [Fact]
    public void JsonValueRetainsExactOpenJson()
    {
        const string input = "{\"arbitrary\":[null,9223372036854775807,18446744073709551615,false]}";
        var value = JsonSerializer.Deserialize<JsonValue>(input);
        Assert.True(JsonNode.DeepEquals(JsonNode.Parse(input), JsonNode.Parse(JsonSerializer.Serialize(value))));
    }

    public static IEnumerable<object[]> DynamicExceptionPointers()
    {
        using var map = JsonDocument.Parse(File.ReadAllText(Rig.RepoFile("csharp/src/Authgebra.Client/Generated/Models.provenance.json")));
        return map.RootElement.GetProperty("dynamic_exceptions").EnumerateArray()
            .Select(entry => new object[] { entry.GetProperty("pointer").GetString()! }).ToArray();
    }

    [Theory]
    [MemberData(nameof(DynamicExceptionPointers))]
    public void RegisteredDynamicExceptionRetainsExactJson(string pointer)
    {
        var spec = JsonNode.Parse(File.ReadAllText(Rig.RepoFile("contracts/openapi.json")))!;
        var schema = Resolve(spec, pointer);
        Assert.Null(schema["properties"]);
        Assert.Null(schema["$ref"]);
        const string json = "{\"arbitrary\":[null,false,9223372036854775807,18446744073709551615]}";
        var value = JsonSerializer.Deserialize<JsonValue>(json);
        Assert.True(JsonNode.DeepEquals(JsonNode.Parse(json), JsonNode.Parse(JsonSerializer.Serialize(value))));
    }

    [Fact]
    public void SharedPatchValuesRetainOmissionNullAndNestedJson()
    {
        using var fixture = JsonDocument.Parse(File.ReadAllText(Rig.RepoFile("fixtures/request-semantics.json")));
        foreach (var value in fixture.RootElement.GetProperty("patch_values").EnumerateArray())
        {
            var request = new PatchPolicyRequest
            {
                Expression = value.ValueKind == JsonValueKind.Null ? Optional<JsonValue>.Null : new Optional<JsonValue>(new JsonValue(value)),
            };
            var serialized = JsonSerializer.SerializeToNode(request)!;
            Assert.True(serialized.AsObject().ContainsKey("expression"));
            Assert.True(JsonNode.DeepEquals(JsonNode.Parse(value.GetRawText()), serialized["expression"]));
        }
        Assert.False(JsonSerializer.SerializeToNode(new PatchPolicyRequest())!.AsObject().ContainsKey("expression"));
        foreach (var value in fixture.RootElement.GetProperty("integer_values").EnumerateArray())
        {
            var named = JsonSerializer.Deserialize<JsonValue>(value.GetRawText());
            Assert.Equal(value.GetRawText(), JsonSerializer.Serialize(named));
        }
    }

    [Fact]
    public void GeneratedValidationUsesMachineCollectionAndNumericBounds()
    {
        var empty = new BatchCheckRequest { SubjectId = Rig.Subject, Permission = "user:read", Resources = Array.Empty<AuthzResourceRef>() };
        Assert.Throws<ArgumentException>(() => empty.Validate());
        foreach (var limit in new[] { 0, 101 })
        {
            var invalid = new ListResourcesRequest { SubjectId = Rig.Subject, Permission = "user:read", ResourceType = "doc", Limit = limit };
            Assert.Throws<ArgumentOutOfRangeException>(() => invalid.Validate());
        }
        new ListResourcesRequest { SubjectId = Rig.Subject, Permission = "user:read", ResourceType = "doc", Limit = 100 }.Validate();
    }

    [Theory]
    [InlineData("{\"data\":{\"decision\":\"deny\",\"reason_code\":\"missing_permission\"},\"meta\":{}}")]
    [InlineData("{\"data\":{\"decision\":\"deny\"}}")]
    [InlineData("{\"data\":{\"decision\":\"deny\",\"reason_code\":null}}")]
    public async Task RequiredSuccessFieldsCannotBeMissingOrNonNullableNull(string json)
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, json)));
        using var client = Rig.Client(handler);
        var error = await Assert.ThrowsAsync<ProtocolException>(() => client.Authorization.CheckAsync(Rig.Check()));
        Assert.Equal("malformed_success_body", error.Reason);
    }

    [Theory]
    [InlineData("{\"data\":[]}")]
    [InlineData("{\"data\":[],\"page\":{}}")]
    [InlineData("{\"data\":[],\"page\":null}")]
    public async Task PagedEndpointsRejectMissingOrIncompletePage(string json)
    {
        var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, json)));
        using var client = Rig.Client(handler);
        var adminError = await Assert.ThrowsAsync<ProtocolException>(() => client.Admin.Users.ListUsersAsync());
        Assert.Equal("malformed_success_body", adminError.Reason);
        var authzError = await Assert.ThrowsAsync<ProtocolException>(() => client.Authorization.ListResourcesAsync(
            new ListResourcesRequest { SubjectId = Rig.Subject, Permission = "doc:read", ResourceType = "doc" }));
        Assert.Equal("malformed_success_body", authzError.Reason);
    }

    [Fact]
    public void OptionalNonNullableRequestFieldRejectsExplicitNull()
    {
        const string required = "\"username\":\"alice\",\"display_name\":\"Alice\",\"initial_password\":\"password\"";
        Assert.NotNull(JsonSerializer.Deserialize<CreateUserRequest>("{" + required + "}"));
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<CreateUserRequest>("{" + required + ",\"role_ids\":null}"));
        Assert.Empty(JsonSerializer.Deserialize<CreateUserRequest>("{" + required + ",\"role_ids\":[]}")!.RoleIds!);
    }

    [Fact]
    public void RequiredNullableResponseFieldsAcceptNullButRequireTheirKeys()
    {
        var schema = JsonNode.Parse(File.ReadAllText(Rig.RepoFile("contracts/openapi.json")))!;
        var complete = Example(schema, schema["components"]!["schemas"]!["ImpactSampleDto"]!)!.AsObject();
        complete["external_id"] = null;
        complete["resource_type"] = null;
        Assert.NotNull(JsonSerializer.Deserialize<ImpactSampleDto>(complete.ToJsonString()));
        complete.Remove("external_id");
        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize<ImpactSampleDto>(complete.ToJsonString()));
    }

    [Fact]
    public void ResponseOptionalFieldsDistinguishMissingNullAndValueWithoutChangingLegacyViews()
    {
        var missing = JsonSerializer.Deserialize<Meta>("{\"authz_revision\":1}")!;
        var cleared = JsonSerializer.Deserialize<Meta>("{\"authz_revision\":1,\"snapshot_generation\":null}")!;
        var valued = JsonSerializer.Deserialize<Meta>("{\"authz_revision\":1,\"snapshot_generation\":0}")!;
        Assert.False(missing.SnapshotGenerationField.IsSet);
        Assert.True(cleared.SnapshotGenerationField.IsSet && cleared.SnapshotGenerationField.IsNull);
        Assert.Equal(0, valued.SnapshotGeneration);
        Assert.False(JsonDocument.Parse(JsonSerializer.Serialize(missing)).RootElement.TryGetProperty("snapshot_generation", out _));
        Assert.Equal(JsonValueKind.Null, JsonDocument.Parse(JsonSerializer.Serialize(cleared)).RootElement.GetProperty("snapshot_generation").ValueKind);
    }

    [Fact]
    public async Task SharedCallerRequestIdVectorsAreValidatedBeforeHttp()
    {
        using var fixture = JsonDocument.Parse(File.ReadAllText(Rig.RepoFile("fixtures/request-semantics.json")));
        foreach (var vector in fixture.RootElement.GetProperty("request_ids").EnumerateArray())
        {
            var value = vector.GetProperty("value").GetString()!;
            var valid = vector.GetProperty("valid").GetBoolean();
            Assert.Equal(valid, TraceContext.IsValidRequestId(value));
            var handler = new FakeHandler((_, _, _) => Task.FromResult(FakeHandler.Json(200, Rig.DenyBody)));
            using var client = Rig.Client(handler);
            await client.Authorization.CheckAsync(Rig.Check(), new RequestOptions { RequestId = value });
            var sent = Assert.Single(handler.Requests).Headers["X-Request-ID"];
            Assert.True(TraceContext.IsValidRequestId(sent));
            if (valid) Assert.Equal(value, sent);
            else Assert.NotEqual(value, sent);
        }
    }

    [Fact]
    public void TraceparentVersionZeroAndFutureVersionsKeepTheirDistinctLengthRules()
    {
        const string tail = "-1234567890abcdef1234567890abcdef-1234567890abcdef-01";
        Assert.True(TraceContext.IsValidTraceparent("00" + tail));
        Assert.False(TraceContext.IsValidTraceparent("00" + tail + "-future"));
        Assert.True(TraceContext.IsValidTraceparent("01" + tail + "-future"));
        Assert.False(TraceContext.IsValidTraceparent("ff" + tail));
        Assert.False(TraceContext.IsValidTraceparent("01" + tail + "-future\r\nInjected: yes"));
    }

    [Fact]
    public void EveryGeneratedModelRoundTripsAnIndependentSchemaExample()
    {
        var spec = JsonNode.Parse(File.ReadAllText(Rig.RepoFile("contracts/openapi.json")))!;
        var map = JsonNode.Parse(File.ReadAllText(Rig.RepoFile("csharp/src/Authgebra.Client/Generated/Models.provenance.json")))!;
        var assembly = typeof(AuthgebraClient).Assembly;
        foreach (var entry in map["models"]!.AsArray())
        {
            var name = entry!["type"]!.GetValue<string>();
            var type = assembly.GetType("Authgebra." + name)!;
            Assert.NotNull(type);
            if (type.IsAbstract && type.IsSealed) continue; // compatibility constants have no instance wire value
            var schema = Resolve(spec, entry["pointer"]!.GetValue<string>());
            var example = Example(spec, schema);
            var json = example?.ToJsonString() ?? "null";
            var value = JsonSerializer.Deserialize(json, type);
            Assert.NotNull(value);
            var encoded = JsonSerializer.Serialize(value, type);
            Assert.True(JsonNode.DeepEquals(JsonNode.Parse(json), JsonNode.Parse(encoded)), name + " changed JSON shape");
        }
    }

    private static JsonNode Resolve(JsonNode spec, string pointer)
    {
        var current = spec;
        foreach (var part in pointer[2..].Split('/'))
        {
            var key = part.Replace("~1", "/").Replace("~0", "~");
            current = current is JsonArray array ? array[int.Parse(key)]! : current[key]!;
        }
        return current;
    }

    private static JsonNode? Example(JsonNode spec, JsonNode schema)
    {
        if (schema["$ref"] is { } reference) return Example(spec, Resolve(spec, reference.GetValue<string>()));
        if (schema["enum"] is JsonArray values) return values[0]!.DeepClone();
        if (schema["oneOf"] is JsonArray variants) return Example(spec, variants[0]!);
        var rawType = schema["type"];
        if (rawType is JsonArray types)
        {
            if (types.Any(t => t!.GetValue<string>() == "null")) return null;
            rawType = types[0];
        }
        switch (rawType?.GetValue<string>())
        {
            case "object":
                var result = new JsonObject();
                foreach (var field in schema["required"]?.AsArray() ?? new JsonArray())
                {
                    var key = field!.GetValue<string>();
                    result[key] = Example(spec, schema["properties"]![key]!);
                }
                return result;
            case "array":
                var array = new JsonArray();
                for (var i = 0; i < (schema["minItems"]?.GetValue<int>() ?? 0); i++) array.Add(Example(spec, schema["items"]!));
                return array;
            case "string":
                return System.Text.Json.Nodes.JsonValue.Create(schema["format"]?.GetValue<string>() == "uuid" ? "00000000-0000-4000-8000-000000000001" : "example");
            case "integer":
            case "number": return schema["minimum"]?.DeepClone() ?? System.Text.Json.Nodes.JsonValue.Create(0);
            case "boolean": return System.Text.Json.Nodes.JsonValue.Create(false);
            default: return null;
        }
    }
}
