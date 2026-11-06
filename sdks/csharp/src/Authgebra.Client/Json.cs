using System.Text.Encodings.Web;
using System.Text.Json;

namespace Authgebra;

internal static class Json
{
    /// <summary>
    /// 统一序列化选项：数字严格（64 位整数不经浮点）、不自动补默认值、BMP 字符原样输出（非 ASCII 不转成 \uXXXX）。
    /// 未知字段在反序列化时被忽略（服务端可以向后兼容地新增字段）。
    /// </summary>
    public static readonly JsonSerializerOptions Options = Create();

    private static JsonSerializerOptions Create()
    {
        var options = new JsonSerializerOptions
        {
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            PropertyNameCaseInsensitive = false,
            DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.Never,
            NumberHandling = System.Text.Json.Serialization.JsonNumberHandling.Strict,
            WriteIndented = false,
        };
        options.MakeReadOnly(populateMissingResolver: true);
        return options;
    }
}
