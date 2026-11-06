// Command check shows a single authorization decision.
//
//	AUTHGEBRA_URL=https://authgebra.example.com AUTHGEBRA_TOKEN=... \
//	SUBJECT_ID=<uuid> go run ./examples/check
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
	})
	if err != nil {
		log.Fatal(err)
	}
	defer client.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	res, err := client.Authorization.Check(ctx, authgebra.CheckRequest{
		SubjectID:  os.Getenv("SUBJECT_ID"),
		Permission: "document:read",
		Resource:   &authgebra.AuthzResourceRef{ResourceTypeCode: "document", ExternalID: "doc-123"},
	})
	if err != nil {
		var apiErr *authgebra.APIError
		if errors.As(err, &apiErr) {
			// 503 NOT_READY means the system could not reach a trustworthy decision:
			// fail closed in YOUR code, but keep the ids for troubleshooting.
			log.Fatalf("authorization system error: %v (code=%s request_id=%s trace_id=%s)",
				err, apiErr.Code, apiErr.RequestID, apiErr.TraceID)
		}
		log.Fatal(err)
	}
	// deny is a successful result, not an error.
	fmt.Printf("decision=%s reason=%s revision=%d trace_id=%s\n",
		res.Data.Decision, res.Data.ReasonCode, res.Meta.AuthzRevision, res.TraceID)
}
