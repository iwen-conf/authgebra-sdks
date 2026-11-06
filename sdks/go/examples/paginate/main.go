// Command paginate lists authorized resources page by page.
//
// CURSOR_STALE is surfaced, never retried silently: the caller decides whether to
// start over. The iterator is capped (PageOptions.MaxPages) and honors ctx.
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
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()

	limit := int32(50)
	it := client.Authorization.ListResourcesPages(authgebra.ListResourcesRequest{
		SubjectID:    os.Getenv("SUBJECT_ID"),
		Permission:   "document:read",
		ResourceType: "document",
		Limit:        &limit,
	}, authgebra.PageOptions{MaxPages: 20})

	total := 0
	for it.Next(ctx) {
		page := it.Page()
		total += len(page.Data)
		fmt.Printf("page: %d resources (generation %d)\n", len(page.Data), *page.Meta.SnapshotGeneration)
	}
	switch err := it.Err(); {
	case err == nil:
		fmt.Println("done:", total)
	case errors.Is(err, &authgebra.APIError{Code: authgebra.CodeCursorStale}):
		// The authorization state changed mid-listing. Restarting is YOUR decision.
		log.Fatalf("list went stale after %d resources; restart from the first page if acceptable", total)
	default:
		log.Fatal(err)
	}
}
