// Command idempotent_write creates a user with an Idempotency-Key and handles the
// "committed but not yet published" outcome without ever resending the write.
package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"time"

	authgebra "github.com/iwen-conf/authgebra-sdks/sdks/go"
)

func main() {
	client, err := authgebra.New(authgebra.Config{
		BaseURL: os.Getenv("AUTHGEBRA_URL"),
		Token:   authgebra.StaticToken(os.Getenv("AUTHGEBRA_TOKEN")),
		// Writes are NEVER replayed unless you opt in AND the call has an Idempotency-Key.
		Retry: authgebra.RetryPolicy{RetryWrites: true},
	})
	if err != nil {
		log.Fatal(err)
	}
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	// Generate the key once per logical operation and persist it with your own
	// record, so a restart can re-send the same operation safely.
	key := "create-user-alice-2026-11-04"

	res, err := client.Admin.Users.CreateUser(ctx, authgebra.CreateUserParams{IdempotencyKey: key},
		authgebra.CreateUserRequest{Username: "alice", DisplayName: "Alice", InitialPassword: os.Getenv("INITIAL_PASSWORD")})

	var apiErr *authgebra.APIError
	var transport *authgebra.TransportError
	switch {
	case err == nil:
		fmt.Printf("created id=%s replayed=%v revision=%d\n", res.Data.ID, res.Replayed, res.Meta.AuthzRevision)
	case errors.As(err, &apiErr) && apiErr.CommittedUnpublished():
		// The change IS committed; only publication is pending. Do NOT resend and do
		// NOT report "authorization effective" until you have confirmed it (for
		// example by polling Authorization.Revision or reading the entity).
		fmt.Printf("committed, publication pending (request_id=%s trace_id=%s)\n", apiErr.RequestID, apiErr.TraceID)
	case errors.As(err, &transport) && transport.CommitState == authgebra.CommitStateUnknown:
		// Timeout / reset: it may or may not have been applied. Re-sending the SAME
		// operation with the SAME key is safe and returns the stored result.
		fmt.Println("outcome unknown; re-send with the same Idempotency-Key to find out")
	default:
		log.Fatal(err)
	}
}
