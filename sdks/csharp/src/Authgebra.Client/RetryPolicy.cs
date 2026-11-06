namespace Authgebra;

/// <summary>有界重试策略：次数有上限，没有“无限重试”这个选项。</summary>
public sealed class RetryPolicy
{
    /// <summary>硬上限：任何配置都不能超过。</summary>
    public const int HardMaxRetries = 10;

    private int _maxRetries = 2;

    /// <summary>首次尝试之外最多再试几次（读请求默认 2；写请求另见 <see cref="AuthgebraClientOptions.RetryWrites"/>）。</summary>
    public int MaxRetries
    {
        get => _maxRetries;
        set
        {
            if (value is < 0 or > HardMaxRetries)
            {
                throw new ArgumentOutOfRangeException(nameof(value), $"MaxRetries must be within 0..{HardMaxRetries}.");
            }
            _maxRetries = value;
        }
    }

    public TimeSpan BaseDelay { get; set; } = TimeSpan.FromMilliseconds(200);
    public TimeSpan MaxDelay { get; set; } = TimeSpan.FromSeconds(5);

    /// <summary>服从服务端 <c>Retry-After</c>，但封顶，避免被对端拖住。</summary>
    public TimeSpan MaxRetryAfter { get; set; } = TimeSpan.FromSeconds(10);

    /// <summary>[0,1) 随机源；可注入以使测试确定。</summary>
    public Func<double> Random { get; set; } = () => System.Random.Shared.NextDouble();

    /// <summary>等待实现；可注入以使测试不真实休眠。</summary>
    public Func<TimeSpan, CancellationToken, Task> Delay { get; set; } = (span, token) => Task.Delay(span, token);

    internal TimeSpan ComputeDelay(int attempt, long? retryAfterSeconds)
    {
        if (retryAfterSeconds is { } seconds and >= 0)
        {
            var requested = TimeSpan.FromSeconds(Math.Min(seconds, MaxRetryAfter.TotalSeconds));
            return requested;
        }
        var exponential = BaseDelay.TotalMilliseconds * Math.Pow(2, attempt);
        var capped = Math.Min(exponential, MaxDelay.TotalMilliseconds);
        var jittered = capped * (0.5 + 0.5 * Random());
        return TimeSpan.FromMilliseconds(jittered);
    }
}
