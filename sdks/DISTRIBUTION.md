# SDK 0.1.0 distribution

The public SDK repository is https://github.com/iwen-conf/authgebra-sdks.
The Authgebra server repository remains private. The SDK remains proprietary commercial
software: public download does not grant an open-source license. See each package's LICENSE.

The five SDKs use version **0.1.0** and the same reviewed OpenAPI contract
`907bd9616dba9993c479cf551bc1e69ac046776b72dd58f5af1138be10c34c25`.
Native and real-service tests cover request/response models, PATCH, tracing, retries,
idempotency and TLS. Supported versions: Go 1.22+, Python 3.10+, Java 17+, Rust 1.85+,
and Zig 0.17.0.

C# support and its GitHub binary distribution were withdrawn at the maintainer’s request on 2026-11-06.

## GitHub distribution

Download packages and `SHA256SUMS` from
https://github.com/iwen-conf/authgebra-sdks/releases/tag/v0.1.0.

| Language | Package / installation |
|---|---|
| Go | `go get github.com/iwen-conf/authgebra-sdks/sdks/go@v0.1.0` |
| Python | `authgebra-0.1.0-py3-none-any.whl` and `authgebra-0.1.0.tar.gz` |
| Java | `authgebra-java-0.1.0.jar`, POM, sources and Javadoc |
| Rust | `authgebra-client-0.1.0.crate`, or git dependency on tag `v0.1.0` |
| Zig | `authgebra-zig-0.1.0.tar.gz` (`zig fetch --save=authgebra_zig <URL>`) |

PyPI, crates.io and Maven Central are separate publication channels.
Their publication requires registry credentials / namespace ownership and is not implied
by a GitHub Release. Registry installation remains NOT_RUN until a successful upload
and independent download are recorded. Package hashes and consumer checks accompany
the GitHub Release.

No server deployment or server production-readiness claim is part of this SDK release.
