// Command batch checks one permission against many resources.
package main

import (
	"context"
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
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	res, err := client.Authorization.BatchCheck(ctx, authgebra.BatchCheckRequest{
		SubjectID:  os.Getenv("SUBJECT_ID"),
		Permission: "document:read",
		Resources: []authgebra.AuthzResourceRef{
			{ResourceTypeCode: "document", ExternalID: "doc-1"},
			{ResourceTypeCode: "document", ExternalID: "doc-2"},
		},
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("allowed=%d denied=%d (generation %v)\n", len(res.Data.Allowed), len(res.Data.Denied), *res.Meta.SnapshotGeneration)
}
