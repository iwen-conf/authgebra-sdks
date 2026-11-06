test {
    _ = @import("authgebra");
    _ = @import("mock.zig");
    _ = @import("conformance_test.zig");
    _ = @import("behavior_test.zig");
    _ = @import("integration_test.zig");
    _ = @import("tls_test.zig");
    _ = @import("model_roundtrip_test.zig");
    _ = @import("model_test.zig");
}
