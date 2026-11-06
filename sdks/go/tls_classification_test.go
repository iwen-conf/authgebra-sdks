package authgebra

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"net/url"
	"testing"
)

func TestTLSRecordHeaderErrorClassification(t *testing.T) {
	record := tls.RecordHeaderError{Msg: "unrecognized record"}
	for _, failure := range []error{record, &url.Error{Op: "Post", URL: "https://sdk.invalid", Err: fmt.Errorf("handshake: %w", record)}} {
		got := (&Client{}).classify(context.Background(), opSpec{Method: "POST", Path: "/api/v1/roles", Mutating: true}, failure)
		if got.Kind != TransportTLS {
			t.Fatalf("record header error classified as %s: %v", got.Kind, got)
		}
		if !errors.Is(got, failure) || got.CommitState != CommitStateUnknown {
			t.Fatal("transport cause or mutating commit state lost")
		}
	}
	got := (&Client{}).classify(context.Background(), opSpec{}, errors.New(record.Error()))
	if got.Kind != TransportNetwork {
		t.Fatal("TLS classification must use the error type, not its message")
	}
}
