package authgebra

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"
)

func TestTLSIntegration(t *testing.T) {
	valid := os.Getenv("SDK_TLS_VALID_URL")
	if valid == "" {
		t.Skip("NOT_RUN: explicit SDK_TLS fixture required")
	}
	pem, err := os.ReadFile(os.Getenv("SDK_TLS_CA"))
	if err != nil {
		t.Fatal(err)
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(pem) {
		t.Fatal("fixture CA has no certificate")
	}
	for _, scenario := range []struct {
		name, address string
		trusted, fail bool
	}{
		{"trusted_ca", valid, true, false},
		{"untrusted_ca", valid, false, true},
		{"hostname_mismatch", os.Getenv("SDK_TLS_MISMATCH_URL"), true, true},
		{"invalid_handshake", os.Getenv("SDK_TLS_PLAIN_URL"), true, true},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			var logs bytes.Buffer
			const token = "tls-secret-token-must-not-leak"
			cfg := Config{BaseURL: scenario.address, Token: StaticToken(token),
				Retry: RetryPolicy{NoRetry: true}, Timeout: 5 * time.Second,
				Logger: slog.New(slog.NewTextHandler(&logs, &slog.HandlerOptions{Level: slog.LevelDebug}))}
			if scenario.trusted {
				cfg.TLSConfig = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}
			}
			client, err := New(cfg)
			if err != nil {
				t.Fatal(err)
			}
			defer client.Close()
			result, err := call[json.RawMessage](context.Background(), client,
				opSpec{ID: "tls_test", Method: http.MethodGet, Path: "/tls-test", Env: envData}, callArgs{}, nil)
			if scenario.fail {
				var transport *TransportError
				if !errors.As(err, &transport) || transport.Kind != TransportTLS || transport.Attempts != 1 {
					t.Fatalf("want one TLS failure, got %v", err)
				}
				if strings.Contains(err.Error(), token) {
					t.Fatal("token leaked through TLS error")
				}
			} else {
				if err != nil {
					t.Fatal(err)
				}
				var data struct {
					TLS bool `json:"tls"`
				}
				if err := json.Unmarshal(result.Data, &data); err != nil || !data.TLS {
					t.Fatalf("TLS positive control failed: %s, %v", result.Data, err)
				}
			}
			if strings.Contains(logs.String(), token) {
				t.Fatal("token leaked through TLS logs")
			}
		})
	}
}
