package authgebra

import (
	"crypto/rand"
	"encoding/hex"
	"regexp"
	"strings"
)

var requestIDPattern = regexp.MustCompile(`^[A-Za-z0-9._-]{8,128}$`)

// ValidRequestID reports whether s is acceptable as an X-Request-ID value
// (same rule as the server: [A-Za-z0-9._-], 8..128 bytes).
func ValidRequestID(s string) bool { return requestIDPattern.MatchString(s) }

// NewRequestID returns a fresh random, valid request id.
func NewRequestID() string { return "authgebra-go-" + randHex(8) }

func randHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		// crypto/rand failing is unrecoverable for an ID generator; fail loudly.
		panic("authgebra: crypto/rand unavailable: " + err.Error())
	}
	return hex.EncodeToString(b)
}

func allZero(s string) bool { return strings.Trim(s, "0") == "" }

func isLowerHex(s string) bool {
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

// ValidTraceID reports whether s is a W3C trace id: 32 lowercase hex chars, not all zero.
func ValidTraceID(s string) bool { return len(s) == 32 && isLowerHex(s) && !allZero(s) }

// ValidTraceparent reports whether s is a valid W3C traceparent header value.
// Version 00 must be exactly 55 bytes; higher versions (never ff) may append
// "-"-separated fields. Uppercase hex, all-zero ids and bad lengths are rejected.
func ValidTraceparent(s string) bool {
	_, ok := TraceIDFromTraceparent(s)
	return ok
}

// TraceIDFromTraceparent extracts the trace id from a valid traceparent.
func TraceIDFromTraceparent(s string) (string, bool) {
	if len(s) < 55 || s[2] != '-' || s[35] != '-' || s[52] != '-' {
		return "", false
	}
	ver := s[0:2]
	if !isLowerHex(ver) || ver == "ff" {
		return "", false
	}
	if ver == "00" {
		if len(s) != 55 {
			return "", false
		}
	} else if len(s) > 55 && s[55] != '-' {
		return "", false
	}
	trace, parent, flags := s[3:35], s[36:52], s[53:55]
	if !ValidTraceID(trace) || len(parent) != 16 || !isLowerHex(parent) || allZero(parent) || !isLowerHex(flags) {
		return "", false
	}
	return trace, true
}

// NewTraceparent creates a new root traceparent (not sampled, flags 00).
func NewTraceparent() string {
	for {
		trace, span := randHex(16), randHex(8)
		if !allZero(trace) && !allZero(span) {
			return "00-" + trace + "-" + span + "-00"
		}
	}
}
