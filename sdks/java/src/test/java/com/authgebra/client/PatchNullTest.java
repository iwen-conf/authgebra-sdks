package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class PatchNullTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    private Object clear(Object patch, String field) {
        var method = assertDoesNotThrow(() -> patch.getClass().getMethod("withNull" + field),
                "typed PATCH must expose explicit null for " + field);
        return assertDoesNotThrow(() -> method.invoke(patch));
    }

    @Test
    void typedPatchDistinguishesAbsentNullAndValue() throws Exception {
        for (Object patch : List.of(new Models.PatchUserRequest(null, null),
                new Models.PatchRoleRequest(null, null),
                new Models.PatchPolicyRequest(null, null, null, null),
                new Models.PatchResourceTypeRequest(null, null, null))) {
            String field = patch instanceof Models.PatchUserRequest ? "Email" : "Description";
            String key = field.toLowerCase();
            assertEquals("{}", JSON.writeValueAsString(patch));
            assertEquals("{\"" + key + "\":null}", JSON.writeValueAsString(clear(patch, field)));
        }
        assertEquals("{\"email\":\"replacement\"}",
                JSON.writeValueAsString(new Models.PatchUserRequest(null, "replacement")));
    }

    @Test
    void typedPatchNullReachesHttp() throws Exception {
        UUID id = UUID.fromString("00000000-0000-7000-8000-000000000001");
        try (var server = new TestServers.Http((n, request) -> TestServers.Reply.json(200,
                "{\"data\":" + TestServers.userData(id.toString()) + "}"))) {
            var client = AuthgebraClient.create(Config.builder().baseUrl(server.baseUrl()).build());
            var patch = (Models.PatchUserRequest) clear(new Models.PatchUserRequest(null, null), "Email");
            client.admin().users().patchUser(id, patch, CallOptions.builder().ifMatch("W/\"1\"").build());
            assertEquals("{\"email\":null}", new String(server.requests.get(0).body(), StandardCharsets.UTF_8));
        }
    }

    @Test
    void conflictingValueAndNullFails() throws Exception {
        var constructor = assertDoesNotThrow(() -> Models.PatchUserRequest.class
                .getConstructor(String.class, String.class, boolean.class));
        var error = assertThrows(java.lang.reflect.InvocationTargetException.class,
                () -> constructor.newInstance(null, "replacement", true));
        assertEquals(IllegalArgumentException.class, error.getCause().getClass());
    }
}
