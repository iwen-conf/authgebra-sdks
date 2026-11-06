package examples;

import com.authgebra.client.ApiException;
import com.authgebra.client.AuthgebraClient;
import com.authgebra.client.CallOptions;
import com.authgebra.client.CommitState;
import com.authgebra.client.Config;
import com.authgebra.client.Models;
import com.authgebra.client.RetryPolicy;
import com.authgebra.client.TransportException;
import java.util.UUID;

/**
 * Create a user with an Idempotency-Key. Replays are opt-in and only happen with that key.
 * AUTHZ_RELOAD_PENDING means the write COMMITTED; never submit it again.
 */
public final class IdempotentWriteExample {
    public static void main(String[] args) {
        try (AuthgebraClient client = AuthgebraClient.create(Config.builder()
                .baseUrl("https://authgebra.example.com")
                .tokenProvider(() -> System.getenv("AUTHGEBRA_TOKEN"))
                .retryPolicy(RetryPolicy.builder().retryIdempotentWrites(true).build())
                .build())) {
            String key = "create-alice-" + UUID.randomUUID();
            var options = CallOptions.builder().idempotencyKey(key).build();
            var created = client.admin().users().createUser(
                    new Models.CreateUserRequest("Alice", null, "a-strong-initial-password", null, "alice"), options);
            System.out.println("created " + created.data().id() + " revision=" + created.authzRevision().orElse(-1L));
        } catch (ApiException e) {
            if (CommitState.COMMITTED_UNPUBLISHED.equals(e.commitState())) {
                System.err.println("committed but not yet published; wait/poll, do NOT resubmit. request_id=" + e.requestId());
            } else if ("IDEMPOTENCY_CONFLICT".equals(e.code())) {
                System.err.println("this key was already used with a different payload");
            } else {
                System.err.println(e.code() + " " + e.violations());
            }
        } catch (TransportException e) {
            // commitState()==unknown for a write: it may have been applied. Re-check with the SAME key or read the entity.
            System.err.println("unknown outcome: " + e.kind().wire() + " commit_state=" + e.commitState());
        }
    }
}
