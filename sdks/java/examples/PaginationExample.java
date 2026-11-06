package examples;

import com.authgebra.client.AuthgebraClient;
import com.authgebra.client.CallOptions;
import com.authgebra.client.Config;
import com.authgebra.client.CursorStaleException;
import com.authgebra.client.Models;
import java.util.UUID;

/** Bounded page iteration. CURSOR_STALE is thrown, never hidden: you decide whether to start over. */
public final class PaginationExample {
    public static void main(String[] args) {
        try (AuthgebraClient client = AuthgebraClient.create(Config.builder()
                .baseUrl("https://authgebra.example.com")
                .tokenProvider(() -> System.getenv("AUTHGEBRA_TOKEN"))
                .build())) {
            var request = new Models.ListResourcesRequest(null, null, 50, "document:read", "document",
                    UUID.fromString(args[0]));
            var pages = client.authorization().listResourcePages(request, 20, CallOptions.defaults());
            while (pages.hasNext()) {
                pages.next().data().items().forEach(r -> System.out.println(r.externalId()));
            }
            if (pages.truncated()) {
                System.err.println("stopped at the page limit; more results exist");
            }
        } catch (CursorStaleException e) {
            System.err.println("authorization changed while paging; restart from the first page if you need a full list");
        }
    }
}
