# Authgebra SDKs

Official Go, Python, Java, Rust, C# and Zig clients for the Authgebra `/api/v1` API.
The SDKs call the server; they do not implement authorization evaluation.

Version **0.1.0**. [Downloads](https://github.com/iwen-conf/authgebra-sdks/releases/tag/v0.1.0)
include wheel/sdist, JAR/POM/sources/Javadoc, `.crate`, `.nupkg`, Go and Zig source archives,
and SHA-256 checksums. [Installation and distribution status](sdks/DISTRIBUTION.md).

The SDKs are **proprietary commercial software**, publicly downloadable, not open source.
No license to use or redistribute is implied by public access; see [LICENSE](LICENSE).
The Authgebra server and internal Git history are not included in this repository.

| Language | Source and examples | Minimum |
|---|---|---|
| Go | [sdks/go](sdks/go) | Go 1.22 |
| Python | [sdks/python](sdks/python) | Python 3.10 |
| Java | [sdks/java](sdks/java) | Java 17 |
| Rust | [sdks/rust](sdks/rust) | Rust 1.85 |
| C# | [sdks/csharp](sdks/csharp) | .NET 8 |
| Zig | [sdks/zig](sdks/zig) | Zig 0.17.0 |

[Shared protocol](sdks/README.md), [reviewed OpenAPI](sdks/contracts/openapi.json),
[field provenance](sdks/contracts/type-coverage.json), and [fixtures](sdks/fixtures).
PyPI, crates.io, Maven Central and NuGet.org publication is separate from GitHub distribution;
see the release notes for the actual registry status.

Release checksums are signed by GPG fingerprint `8AD333B2299768BD48C59B3826FB55A540AA6F51`.
The [public key](SDK-SIGNING-KEY.asc) is also a release asset.
