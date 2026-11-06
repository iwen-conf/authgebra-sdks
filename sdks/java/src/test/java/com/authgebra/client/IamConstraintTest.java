package com.authgebra.client;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class IamConstraintTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    private static Models.CreateRoleRequest role(String code, String name) throws Exception {
        var node = JSON.createObjectNode().put("code", code).put("name", name);
        return Models.CreateRoleRequest.fromJson(JSON, node);
    }

    @Test void namesCountUnicodeCodePointsAndCodesUseTheEntireAsciiString() throws Exception {
        for (String name : List.of("名", "😀".repeat(64))) assertDoesNotThrow(() -> role("document_editor", name));
        for (String name : List.of("", "😀".repeat(65))) assertThrows(Exception.class, () -> role("document_editor", name));
        for (String code : List.of("a", "document_editor", "doc.type-1", "a".repeat(64))) assertDoesNotThrow(() -> role(code, "Role"));
        for (String code : List.of("", "User", "1user", "user/read", "user:read", "éuser", "user\n", "\nuser", "a".repeat(65)))
            assertThrows(Exception.class, () -> role(code, "Role"), "invalid code: " + code);
    }

    @Test void replaceSetsAllowEmptyAnd512ButReject513AndDuplicates() {
        var ids = new ArrayList<UUID>();
        for (int i = 1; i <= 513; i++) ids.add(new UUID(0, i));
        for (int size : List.of(0, 512)) {
            assertDoesNotThrow(() -> new Models.ReplaceUserRolesRequest(ids.subList(0, size)).validate());
            assertDoesNotThrow(() -> new Models.ReplaceRolePermissionsRequest(ids.subList(0, size)).validate());
        }
        assertThrows(IllegalArgumentException.class, () -> new Models.ReplaceUserRolesRequest(ids).validate());
        assertThrows(IllegalArgumentException.class, () -> new Models.ReplaceRolePermissionsRequest(ids).validate());
        var duplicate = List.of(ids.get(0), ids.get(0));
        assertThrows(IllegalArgumentException.class, () -> new Models.ReplaceUserRolesRequest(duplicate).validate());
        assertThrows(IllegalArgumentException.class, () -> new Models.ReplaceRolePermissionsRequest(duplicate).validate());
    }
}
