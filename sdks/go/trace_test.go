package authgebra

import (
	"strings"
	"testing"
)

func TestTraceparentValidation(t *testing.T) {
	good := "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
	if !ValidTraceparent(good) {
		t.Fatal("valid traceparent rejected")
	}
	bad := []string{
		"", "garbage", strings.ToUpper(good),
		"ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
		"00-00000000000000000000000000000000-00f067aa0ba902b7-01",
		"00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
		"00-4bf92f3577b34da6a3ce929d0e0e473-00f067aa0ba902b7-01",
		"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-0g",
		good + "-extra",
		"00_4bf92f3577b34da6a3ce929d0e0e4736_00f067aa0ba902b7_01",
	}
	for _, b := range bad {
		if ValidTraceparent(b) {
			t.Errorf("%q must be invalid", b)
		}
	}
	if !ValidTraceparent("01-" + good[3:] + "-extra") {
		t.Error("higher versions may append '-' separated fields")
	}
	for i := 0; i < 500; i++ {
		tp := NewTraceparent()
		if !ValidTraceparent(tp) {
			t.Fatalf("generated invalid traceparent %q", tp)
		}
		if !ValidRequestID(NewRequestID()) {
			t.Fatal("generated invalid request id")
		}
	}
}

func TestRequestIDValidation(t *testing.T) {
	for _, ok := range []string{"req-abcdefgh", "a.b_c-d1234", strings.Repeat("x", 128)} {
		if !ValidRequestID(ok) {
			t.Errorf("%q should be valid", ok)
		}
	}
	for _, bad := range []string{"short", "has space 1234", "bad\tchar-1234", strings.Repeat("x", 129), ""} {
		if ValidRequestID(bad) {
			t.Errorf("%q should be invalid", bad)
		}
	}
}
