const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    const mod = b.addModule("authgebra", .{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
    });

    // Fixture + contract locations, relative to the package root (read at test time).
    const opts = b.addOptions();
    opts.addOption([]const u8, "fixtures_dir", "../fixtures/cases");
    opts.addOption([]const u8, "openapi_path", "../contracts/openapi.json");

    const test_mod = b.createModule(.{
        .root_source_file = b.path("src/tests.zig"),
        .target = target,
        .optimize = optimize,
        .imports = &.{
            .{ .name = "authgebra", .module = mod },
            .{ .name = "build_options", .module = opts.createModule() },
        },
    });
    const tests = b.addTest(.{ .root_module = test_mod });
    const run_tests = b.addRunArtifact(tests);
    run_tests.setCwd(b.path(".")); // fixture paths are relative to the package root
    // Live tests depend on environment and remote state, not just source hashes.
    run_tests.has_side_effects = true;
    const test_step = b.step("test", "Run unit, fixture-conformance and operation-coverage tests");
    test_step.dependOn(&run_tests.step);

    // `zig build check-generated` fails when src/admin_ops.zig is stale versus the OpenAPI export.
    const gen_check = b.addSystemCommand(&.{ "python3", "tools/gen_admin.py", "--check" });
    gen_check.setCwd(b.path("."));
    const gen_step = b.step("check-generated", "Fail if src/admin_ops.zig is out of date");
    gen_step.dependOn(&gen_check.step);

    const exe = b.addExecutable(.{
        .name = "authgebra-example",
        .root_module = b.createModule(.{
            .root_source_file = b.path("examples/check.zig"),
            .target = target,
            .optimize = optimize,
            .imports = &.{.{ .name = "authgebra", .module = mod }},
        }),
    });
    b.installArtifact(exe);
    const run_example = b.addRunArtifact(exe);
    run_example.addPassthruArgs();
    const run_step = b.step("run-example", "Run the Check example (needs AUTHGEBRA_URL, AUTHGEBRA_TOKEN)");
    run_step.dependOn(&run_example.step);
}
