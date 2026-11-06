"""Check / batch / list / explain against a running Authgebra server.

    AUTHGEBRA_URL=https://authgebra.example AUTHGEBRA_TOKEN=... python examples/quickstart.py <subject-uuid>
"""

from __future__ import annotations

import os
import sys

from authgebra import (
    BatchCheckRequest,
    CheckRequest,
    Client,
    Config,
    CursorStaleError,
    ExplainRequest,
    ListResourcesRequest,
    NotReadyError,
    ResourceRef,
)


def main(subject_id: str) -> None:
    config = Config(
        base_url=os.environ["AUTHGEBRA_URL"],
        token_provider=lambda: os.environ["AUTHGEBRA_TOKEN"],
    )
    doc = ResourceRef("document", "doc-123")
    with Client(config) as client:
        try:
            result = client.authorization.check(CheckRequest(subject_id, "document:read", resource=doc))
        except NotReadyError as error:
            # NOT a deny: the server could not reach a trustworthy decision.  Apply *your*
            # safe-refusal policy, but keep the error for diagnostics.
            print("authorization unavailable:", error)
            return
        print("allowed" if result.allowed else "denied", result.reason_code)
        print("revision", result.meta.authz_revision if result.meta else None, "trace", result.info.trace_id)

        batch = client.authorization.batch_check(BatchCheckRequest(subject_id, "document:read", [doc]))
        print(len(batch.allowed), "allowed,", len(batch.denied), "denied")

        print(client.authorization.explain(ExplainRequest(subject_id, "document:read", resource=doc)).decision)

        listing = ListResourcesRequest(subject_id, "document:read", "document", limit=50)
        try:
            for page in client.authorization.iter_list_resources(listing, max_pages=20):
                for item in page.items:
                    print(item.external_id)
        except CursorStaleError:
            # The snapshot changed under the cursor.  Restarting is YOUR decision; the SDK never
            # stitches pages from different generations.
            print("listing is stale; restart from the first page if that is acceptable")


if __name__ == "__main__":
    main(sys.argv[1])
