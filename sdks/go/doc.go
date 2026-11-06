// Package authgebra is the official Go client for the Authgebra HTTP API (/api/v1).
//
// The SDK is a thin, typed wrapper: it never computes authorization decisions.
// Behavior (result vs error, retries, commit_state, tracing, 64-bit precision)
// is specified once for all languages in sdks/README.md and verified against the
// shared fixtures in sdks/fixtures/cases.
//
//	client, err := authgebra.New(authgebra.Config{
//		BaseURL: "https://authgebra.example.com",
//		Token:   authgebra.StaticToken(os.Getenv("AUTHGEBRA_TOKEN")),
//	})
//
// A deny decision is a successful Result; HTTP-level problems are *APIError,
// *TransportError or *ProtocolError (use errors.As).
//
// The models (models_gen.go) and one method per OpenAPI operation (ops_gen.go)
// are generated from sdks/contracts/openapi.json; do not edit them by hand.
//
//go:generate go run ./internal/gen -spec ../contracts/openapi.json -out .
package authgebra

// Version is the SDK version, reported in the User-Agent header.
const Version = "0.1.0"
