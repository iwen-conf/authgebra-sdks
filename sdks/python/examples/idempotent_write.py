"""An idempotent admin write, and what to do when the server says "committed but not yet published"."""

from __future__ import annotations

import os

from authgebra import (
    ApiError,
    Client,
    CommitState,
    Config,
    ReloadPendingError,
    RetryPolicy,
    TransportError,
    new_idempotency_key,
    api_models,
)


def main() -> None:
    config = Config(
        base_url=os.environ["AUTHGEBRA_URL"],
        token_provider=lambda: os.environ["AUTHGEBRA_TOKEN"],
        # Writes are never auto-resent unless you opt in here AND pass an Idempotency-Key.
        retry=RetryPolicy(max_retries=2, retry_writes=True),
    )
    key = new_idempotency_key()  # persist it if you may need to resend after a crash
    body = api_models.CreateUserRequest(
        username="alice", display_name="Alice", initial_password=os.environ["NEW_USER_PASSWORD"],
    )
    with Client(config) as client:
        try:
            created = client.admin.users.create_user(body=body, idempotency_key=key)
            print("created", created.data, "replayed" if created.replayed else "first time")
        except ReloadPendingError as error:
            # The mutation DID execute (commit_state == committed_unpublished) but the new
            # authorization state is not published yet.  Do not resend; do not report "in effect".
            assert error.commit_state is CommitState.COMMITTED_UNPUBLISHED
            print("committed, waiting for publication; request_id =", error.request_id)
        except TransportError as error:
            # commit_state is UNKNOWN: the user may or may not exist.  Resending with the SAME
            # key and the SAME body is safe and returns the stored result if it did.
            print("unknown outcome:", error.error, error.commit_state)
        except ApiError as error:
            for violation in error.violations:
                print(violation.path, violation.reason)
            raise


if __name__ == "__main__":
    main()
