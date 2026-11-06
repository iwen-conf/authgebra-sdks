package authgebra

import "testing"

func TestInvalidProvidedRequestIDIsDiscarded(t *testing.T) {
	for _, input := range readRequestSemantics(t).RequestIDs {
		c, wire, _ := newTestClient(t, respond(200, nil, okCheckBody()), nil)
		if err := check(c, WithRequestID(input.Value)); err != nil {
			t.Fatal(err)
		}
		got := wire.last().header.Get("X-Request-ID")
		if input.Valid {
			if got != input.Value {
				t.Fatal("valid request ID was changed")
			}
		} else if got == input.Value || !ValidRequestID(got) {
			t.Fatal("invalid request ID was not discarded")
		}
	}
}
