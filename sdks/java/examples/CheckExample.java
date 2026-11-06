package examples;

import com.authgebra.client.ApiException;
import com.authgebra.client.AuthgebraClient;
import com.authgebra.client.AuthorizationClient;
import com.authgebra.client.Config;
import com.authgebra.client.Decision;
import com.authgebra.client.Models;
import com.authgebra.client.TransportException;
import java.util.UUID;

/** Single check + batch check. A deny is a normal result; NOT_READY is an error you must handle. */
public final class CheckExample {
    public static void main(String[] args) {
        UUID subject = UUID.fromString(args[0]);
        try (AuthgebraClient client = AuthgebraClient.create(Config.builder()
                .baseUrl("https://authgebra.example.com")
                .tokenProvider(() -> System.getenv("AUTHGEBRA_TOKEN"))
                .build())) {
            var result = client.authorization().check(new Models.CheckRequest(
                    null, "document:read", new Models.AuthzResourceRef("doc-123", "document"), subject));
            Decision decision = AuthorizationClient.decision(result);
            System.out.println(decision + " revision=" + result.authzRevision().orElse(-1L)
                    + " generation=" + result.snapshotGeneration().orElse(-1L)
                    + " trace=" + result.traceId().orElse("-"));

            var batch = client.authorization().batchCheck(new Models.BatchCheckRequest(
                    null, "document:read",
                    java.util.List.of(new Models.AuthzResourceRef("doc-1", "document"),
                            new Models.AuthzResourceRef("doc-2", "document")),
                    subject));
            System.out.println("allowed=" + batch.data().allowed().size() + " denied=" + batch.data().denied().size());
        } catch (ApiException e) {
            // NOT_READY etc.: the system could not decide. Fail closed in YOUR code, keep the error for diagnosis.
            System.err.println(e.code() + " status=" + e.status() + " request_id=" + e.requestId()
                    + " trace_id=" + e.traceId());
        } catch (TransportException e) {
            System.err.println("transport: " + e.kind().wire());
        }
    }
}
