package authgebra

import (
	"fmt"
	"strings"
	"testing"
)

// Bounds originate in src/usecase/iam/service_common.rs, not SDK-only policy.
func TestIAMNameAndCodeMachineConstraints(t *testing.T) {
	for _, name := range []string{"名", strings.Repeat("😀", 64)} {
		if err := (CreateRoleRequest{Code: "document_editor", Name: name}).Validate(); err != nil {
			t.Fatalf("valid name rejected: %v", err)
		}
	}
	for _, name := range []string{"", strings.Repeat("😀", 65)} {
		if err := (CreateRoleRequest{Code: "document_editor", Name: name}).Validate(); err == nil {
			t.Fatalf("invalid name accepted: %q", name)
		}
	}
	for _, code := range []string{"a", "document_editor", "doc.type-1", strings.Repeat("a", 64)} {
		if err := (CreateRoleRequest{Code: code, Name: "Role"}).Validate(); err != nil {
			t.Fatalf("valid code rejected: %q: %v", code, err)
		}
	}
	for _, code := range []string{"", "User", "1user", "user/read", "user:read", "éuser", "user\n", "\nuser", strings.Repeat("a", 65)} {
		if err := (CreateRoleRequest{Code: code, Name: "Role"}).Validate(); err == nil {
			t.Fatalf("invalid code accepted: %q", code)
		}
	}
}

func TestIAMReplaceSetMachineConstraints(t *testing.T) {
	ids := make([]string, 513)
	for i := range ids {
		ids[i] = fmt.Sprintf("00000000-0000-4000-8000-%012d", i+1)
	}
	for _, count := range []int{0, 512, 513} {
		role := ReplaceUserRolesRequest{RoleIds: ids[:count]}
		permissions := ReplaceRolePermissionsRequest{PermissionIds: ids[:count]}
		for _, err := range []error{role.Validate(), permissions.Validate()} {
			if (count <= 512) != (err == nil) {
				t.Fatalf("set size %d: %v", count, err)
			}
		}
	}
	duplicate := []string{ids[0], ids[0]}
	if err := (ReplaceUserRolesRequest{RoleIds: duplicate}).Validate(); err == nil {
		t.Fatal("duplicate role accepted")
	}
	if err := (ReplaceRolePermissionsRequest{PermissionIds: duplicate}).Validate(); err == nil {
		t.Fatal("duplicate permission accepted")
	}
}
