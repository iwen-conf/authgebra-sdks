package authgebra

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"testing"
	"time"
)

func TestRealServerInflight(t *testing.T) {
	if os.Getenv("AUTHGEBRA_IT_INFLIGHT") != "1" || os.Getenv("AUTHGEBRA_IT_BASE_URL") == "" {
		t.Skip("NOT_RUN: explicit in-flight barrier required")
	}
	if os.Getenv("AUTHGEBRA_IT_CONFIRM_ISOLATED") != "1" {
		t.Fatal("confirm disposable SDK surface")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	control := func(t *testing.T, name string) {
		t.Helper()
		command := os.Getenv(name)
		if command == "" {
			t.Fatal("missing " + name)
		}
		commandCtx, stop := context.WithTimeout(context.Background(), 15*time.Second)
		defer stop()
		if err := exec.CommandContext(commandCtx, "sh", "-c", command).Run(); err != nil {
			t.Fatalf("in-flight control %s failed: %v", name, err)
		}
	}
	anon, err := New(Config{BaseURL: os.Getenv("AUTHGEBRA_IT_BASE_URL")})
	if err != nil {
		t.Fatal(err)
	}
	defer anon.Close()
	login, err := anon.Admin.Auth.Login(ctx, LoginRequest{Identifier: os.Getenv("AUTHGEBRA_IT_USER"), Password: os.Getenv("AUTHGEBRA_IT_PASSWORD")})
	if err != nil {
		t.Fatal(err)
	}
	if login.Data.Status != LoginStatusAuthenticated || login.Data.AccessToken == nil {
		t.Fatal("in-flight login did not authenticate")
	}
	client, err := New(Config{BaseURL: os.Getenv("AUTHGEBRA_IT_BASE_URL"), Token: StaticToken(*login.Data.AccessToken), Retry: RetryPolicy{NoRetry: true}})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	for _, different := range []bool{false, true} {
		name := "same_body"
		if different {
			name = "different_body"
		}
		t.Run(name, func(t *testing.T) {
			username, key := "it-flight-go-"+randHex(8), NewRequestID()
			body := CreateUserRequest{Username: username, DisplayName: username, InitialPassword: os.Getenv("AUTHGEBRA_IT_PASSWORD")}
			changed := body
			changed.DisplayName = "different"
			create := func(body CreateUserRequest) (*Result[UserDto], error) {
				return client.Admin.Users.CreateUser(ctx, CreateUserParams{IdempotencyKey: key}, body, WithRequestID(NewRequestID()))
			}
			held := false
			defer func() {
				if held {
					control(t, "AUTHGEBRA_IT_INFLIGHT_RELEASE_CMD")
				}
			}()
			control(t, "AUTHGEBRA_IT_INFLIGHT_HOLD_CMD")
			held = true
			type outcome struct {
				result *Result[UserDto]
				err    error
			}
			first := make(chan outcome, 1)
			go func() { result, err := create(body); first <- outcome{result, err} }()
			control(t, "AUTHGEBRA_IT_INFLIGHT_WAIT_CMD")
			second := body
			if different {
				second = changed
			}
			_, pendingErr := create(second)
			control(t, "AUTHGEBRA_IT_INFLIGHT_RELEASE_CMD")
			held = false
			var created outcome
			select {
			case created = <-first:
			case <-ctx.Done():
				t.Fatal(ctx.Err())
			}
			var pending *APIError
			if !errors.As(pendingErr, &pending) || pending.Status != 409 || pending.Code != CodeIdempotencyInProgress || pending.Retryable == nil || !*pending.Retryable {
				t.Fatalf("in-flight duplicate must return retryable 409 IDEMPOTENCY_IN_PROGRESS: %v", pendingErr)
			}
			if created.err != nil || created.result.Status != 201 {
				t.Fatalf("first write failed: %v", created.err)
			}
			replay, err := create(body)
			if err != nil || !replay.Replayed || replay.Data.ID != created.result.Data.ID {
				t.Fatalf("same-key replay lost write: %v", err)
			}
			found, err := client.Admin.Users.ListUsers(ctx, ListUsersParams{Q: username})
			if err != nil || len(found.Data) != 1 || found.Data[0].ID != created.result.Data.ID {
				t.Fatalf("in-flight write is not unique: %v", err)
			}
			if different {
				_, err := create(changed)
				var conflict *APIError
				if !errors.As(err, &conflict) || conflict.Status != 409 || conflict.Code != CodeIdempotencyConflict || conflict.Retryable == nil || *conflict.Retryable {
					t.Fatalf("after publication different body must conflict: %v", err)
				}
			}
		})
	}
}
