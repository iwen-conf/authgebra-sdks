package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

class FieldPresenceTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    @Test
    void optionalNullableFieldsPreserveExplicitNullAndAbsence() throws Exception {
        for (String input : new String[]{"{\"authz_revision\":7}",
                "{\"authz_revision\":7,\"snapshot_generation\":null}",
                "{\"authz_revision\":7,\"snapshot_generation\":0}"}) {
            var model = JSON.readValue(input, Models.Meta.class);
            assertEquals(JSON.readTree(input), JSON.readTree(JSON.writeValueAsBytes(model)));
        }
        var patch = JSON.readValue("{\"email\":null}", Models.PatchUserRequest.class);
        assertEquals(JSON.readTree("{\"email\":null}"), JSON.readTree(JSON.writeValueAsBytes(patch)));
        assertNull(patch.email());
    }
    @Test
    void typedFactoriesPreserveZeroEmptyAndNestedStates() throws Exception {
        var nullable = Models.PatchUserRequest.fromFields(JsonField.absent(), JsonField.nullValue());
        assertEquals(JSON.readTree("{\"email\":null}"), JSON.readTree(JSON.writeValueAsBytes(nullable)));
        assertEquals(JSON.readTree("{\"email\":\"\"}"), JSON.readTree(JSON.writeValueAsBytes(
                Models.PatchUserRequest.fromFields(JsonField.absent(), JsonField.of("")))));
        for (String input : new String[]{"{\"subject_roles\":[]}", "{\"subject_attrs\":{}}"}) {
            var model = JSON.readValue(input, Models.FactSpecDto.class);
            assertEquals(JSON.readTree(input), JSON.readTree(JSON.writeValueAsBytes(model)));
        }
        assertEquals(JSON.readTree("{\"enabled\":false,\"priority\":0}"), JSON.readTree(JSON.writeValueAsBytes(
                JSON.readValue("{\"enabled\":false,\"priority\":0}", Models.PatchPolicyBindingRequest.class))));
        org.junit.jupiter.api.Assertions.assertThrows(Exception.class, () -> JSON.readValue(
                "{\"subject_roles\":null}", Models.FactSpecDto.class));
    }

    @Test
    void declaredConstraintsAreExecutableWithoutChangingTransportDefaults() {
        var subject = java.util.UUID.fromString("00000000-0000-4000-8000-000000000001");
        for (int limit : new int[]{0, 101}) {
            org.junit.jupiter.api.Assertions.assertThrows(IllegalArgumentException.class, () ->
                    new Models.ListResourcesRequest(null, null, limit, "doc:read", "doc", subject).validate());
        }
        for (int limit : new int[]{1, 100}) {
            new Models.ListResourcesRequest(null, null, limit, "doc:read", "doc", subject).validate();
        }
        org.junit.jupiter.api.Assertions.assertThrows(IllegalArgumentException.class, () ->
                new Models.BatchCheckRequest(null, "doc:read", java.util.List.of(), subject).validate());
    }

    @Test
    void nullableReferencesAndRequiredNullableModeRetainNull() throws Exception {
        String request = "{\"subject_id\":\"00000000-0000-4000-8000-000000000001\",\"permission\":\"doc:read\",\"resource\":null}";
        assertEquals(JSON.readTree(request), JSON.readTree(JSON.writeValueAsBytes(JSON.readValue(request, Models.CheckRequest.class))));
        String nullableMode = "{\"mode\":null,\"reauth\":{\"password\":\"test-password\"}}";
        var mode = JSON.readValue(nullableMode, Models.SetUserMfaPolicyRequest.class);
        assertEquals(JSON.readTree(nullableMode), JSON.readTree(JSON.writeValueAsBytes(mode)));
        org.junit.jupiter.api.Assertions.assertThrows(Exception.class, () -> JSON.readValue(
                "{\"reauth\":{\"password\":\"test-password\"}}", Models.SetUserMfaPolicyRequest.class));
        assertEquals(JSON.readTree(nullableMode), JSON.readTree(JSON.writeValueAsBytes(
                new Models.SetUserMfaPolicyRequest(null, new Models.ReauthProof(null, "test-password")))));
    }

    @Test
    void unsigned32UsesTheFullRangeAndKeepsIntegerConstructorCalls() throws Exception {
        var reference = JSON.readValue("{\"code\":\"policy\",\"version\":4294967295}", Models.AuthzPolicyRef.class);
        assertEquals(4294967295L, reference.version());
        reference.validate();
        assertEquals(1L, new Models.AuthzPolicyRef("policy", 1).version());
        org.junit.jupiter.api.Assertions.assertThrows(IllegalArgumentException.class, () ->
                new Models.AuthzPolicyRef("policy", 4294967296L).validate());
        org.junit.jupiter.api.Assertions.assertThrows(ArithmeticException.class, () ->
                new Models.AuthzPolicyRef("policy", (Number) 1.5));
    }

    @Test
    void requestImportIsStrictWhileSharedResponseModelsAcceptNewFields() throws Exception {
        String valid = "{\"subject_id\":\"00000000-0000-4000-8000-000000000001\",\"permission\":\"doc:read\"}";
        Models.CheckRequest.fromJson(JSON, JSON.readTree(valid));
        for (String input : new String[]{
                valid.substring(0, valid.length() - 1) + ",\"unknown\":true}",
                valid.substring(0, valid.length() - 1) + ",\"resource\":{\"resource_type_code\":\"doc\",\"external_id\":\"a\",\"unknown\":true}}",
                "{\"permission\":\"doc:read\"}",
                "{\"subject_id\":null,\"permission\":\"doc:read\"}"}) {
            org.junit.jupiter.api.Assertions.assertThrows(IllegalArgumentException.class, () ->
                    Models.CheckRequest.fromJson(JSON, JSON.readTree(input)));
        }
        var response = JSON.readValue("{\"resource_type_code\":\"doc\",\"external_id\":\"a\",\"future_field\":true}", Models.AuthzResourceRef.class);
        assertEquals("a", response.externalId());
    }

}
