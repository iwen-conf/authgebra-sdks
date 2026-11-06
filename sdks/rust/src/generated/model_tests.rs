// Generated model registry; the sample oracle is independently implemented in tests/model_semantics.rs.
#[test]
fn every_component_model_roundtrips() {
    let document = spec();
    roundtrip::<models::ApiErrorBody>(&document, "/components/schemas/ApiErrorBody");
    roundtrip::<models::ApiErrorCode>(&document, "/components/schemas/ApiErrorCode");
    roundtrip::<models::ApiErrorPayload>(&document, "/components/schemas/ApiErrorPayload");
    roundtrip::<models::ApiResponseAttributeDto>(
        &document,
        "/components/schemas/ApiResponse_AttributeDto",
    );
    roundtrip::<models::ApiResponseAttributesDto>(
        &document,
        "/components/schemas/ApiResponse_AttributesDto",
    );
    roundtrip::<models::ApiResponseAttributesStatsDto>(
        &document,
        "/components/schemas/ApiResponse_AttributesStatsDto",
    );
    roundtrip::<models::ApiResponseAuditStatsDto>(
        &document,
        "/components/schemas/ApiResponse_AuditStatsDto",
    );
    roundtrip::<models::ApiResponseAuthOkDto>(
        &document,
        "/components/schemas/ApiResponse_AuthOkDto",
    );
    roundtrip::<models::ApiResponseBatchCheckResponse>(
        &document,
        "/components/schemas/ApiResponse_BatchCheckResponse",
    );
    roundtrip::<models::ApiResponseBindingsStatsDto>(
        &document,
        "/components/schemas/ApiResponse_BindingsStatsDto",
    );
    roundtrip::<models::ApiResponseCheckResponse>(
        &document,
        "/components/schemas/ApiResponse_CheckResponse",
    );
    roundtrip::<models::ApiResponseCompilePreviewDto>(
        &document,
        "/components/schemas/ApiResponse_CompilePreviewDto",
    );
    roundtrip::<models::ApiResponseDashboardSummaryDto>(
        &document,
        "/components/schemas/ApiResponse_DashboardSummaryDto",
    );
    roundtrip::<models::ApiResponseEffectivePermissionsDto>(
        &document,
        "/components/schemas/ApiResponse_EffectivePermissionsDto",
    );
    roundtrip::<models::ApiResponseEnrollmentStartResponse>(
        &document,
        "/components/schemas/ApiResponse_EnrollmentStartResponse",
    );
    roundtrip::<models::ApiResponseEnrollmentVerifyResponse>(
        &document,
        "/components/schemas/ApiResponse_EnrollmentVerifyResponse",
    );
    roundtrip::<models::ApiResponseExplainResponse>(
        &document,
        "/components/schemas/ApiResponse_ExplainResponse",
    );
    roundtrip::<models::ApiResponseImpactPreviewDto>(
        &document,
        "/components/schemas/ApiResponse_ImpactPreviewDto",
    );
    roundtrip::<models::ApiResponseLoginResponse>(
        &document,
        "/components/schemas/ApiResponse_LoginResponse",
    );
    roundtrip::<models::ApiResponseMeResponse>(
        &document,
        "/components/schemas/ApiResponse_MeResponse",
    );
    roundtrip::<models::ApiResponseMfaEnrollResponse>(
        &document,
        "/components/schemas/ApiResponse_MfaEnrollResponse",
    );
    roundtrip::<models::ApiResponseMfaRecoveryCodesResponse>(
        &document,
        "/components/schemas/ApiResponse_MfaRecoveryCodesResponse",
    );
    roundtrip::<models::ApiResponseMfaStatusResponse>(
        &document,
        "/components/schemas/ApiResponse_MfaStatusResponse",
    );
    roundtrip::<models::ApiResponseMutationAckDto>(
        &document,
        "/components/schemas/ApiResponse_MutationAckDto",
    );
    roundtrip::<models::ApiResponsePermissionDto>(
        &document,
        "/components/schemas/ApiResponse_PermissionDto",
    );
    roundtrip::<models::ApiResponsePoliciesStatsDto>(
        &document,
        "/components/schemas/ApiResponse_PoliciesStatsDto",
    );
    roundtrip::<models::ApiResponsePolicyBindingDto>(
        &document,
        "/components/schemas/ApiResponse_PolicyBindingDto",
    );
    roundtrip::<models::ApiResponsePolicyDetailDto>(
        &document,
        "/components/schemas/ApiResponse_PolicyDetailDto",
    );
    roundtrip::<models::ApiResponsePolicyDto>(
        &document,
        "/components/schemas/ApiResponse_PolicyDto",
    );
    roundtrip::<models::ApiResponsePolicyTestCaseWriteDto>(
        &document,
        "/components/schemas/ApiResponse_PolicyTestCaseWriteDto",
    );
    roundtrip::<models::ApiResponsePolicyValidationDto>(
        &document,
        "/components/schemas/ApiResponse_PolicyValidationDto",
    );
    roundtrip::<models::ApiResponsePublishPolicyResultDto>(
        &document,
        "/components/schemas/ApiResponse_PublishPolicyResultDto",
    );
    roundtrip::<models::ApiResponseRelationDefinitionDto>(
        &document,
        "/components/schemas/ApiResponse_RelationDefinitionDto",
    );
    roundtrip::<models::ApiResponseRelationTupleDto>(
        &document,
        "/components/schemas/ApiResponse_RelationTupleDto",
    );
    roundtrip::<models::ApiResponseResourceDto>(
        &document,
        "/components/schemas/ApiResponse_ResourceDto",
    );
    roundtrip::<models::ApiResponseResourceTypeDto>(
        &document,
        "/components/schemas/ApiResponse_ResourceTypeDto",
    );
    roundtrip::<models::ApiResponseResourceTypesStatsDto>(
        &document,
        "/components/schemas/ApiResponse_ResourceTypesStatsDto",
    );
    roundtrip::<models::ApiResponseResourcesStatsDto>(
        &document,
        "/components/schemas/ApiResponse_ResourcesStatsDto",
    );
    roundtrip::<models::ApiResponseRevisionResponse>(
        &document,
        "/components/schemas/ApiResponse_RevisionResponse",
    );
    roundtrip::<models::ApiResponseRoleDto>(&document, "/components/schemas/ApiResponse_RoleDto");
    roundtrip::<models::ApiResponseRolePermissionsDto>(
        &document,
        "/components/schemas/ApiResponse_RolePermissionsDto",
    );
    roundtrip::<models::ApiResponseRolesStatsDto>(
        &document,
        "/components/schemas/ApiResponse_RolesStatsDto",
    );
    roundtrip::<models::ApiResponseTokenPair>(
        &document,
        "/components/schemas/ApiResponse_TokenPair",
    );
    roundtrip::<models::ApiResponseUserDto>(&document, "/components/schemas/ApiResponse_UserDto");
    roundtrip::<models::ApiResponseUserMfaPolicyDto>(
        &document,
        "/components/schemas/ApiResponse_UserMfaPolicyDto",
    );
    roundtrip::<models::ApiResponseUserRolesDto>(
        &document,
        "/components/schemas/ApiResponse_UserRolesDto",
    );
    roundtrip::<models::ApiResponseUsersStatsDto>(
        &document,
        "/components/schemas/ApiResponse_UsersStatsDto",
    );
    roundtrip::<models::ApiResponseValue>(&document, "/components/schemas/ApiResponse_Value");
    roundtrip::<models::ApiResponseVecPolicyTestCaseDto>(
        &document,
        "/components/schemas/ApiResponse_Vec_PolicyTestCaseDto",
    );
    roundtrip::<models::ApiResponseVecSessionDto>(
        &document,
        "/components/schemas/ApiResponse_Vec_SessionDto",
    );
    roundtrip::<models::AttributeAssignmentDto>(
        &document,
        "/components/schemas/AttributeAssignmentDto",
    );
    roundtrip::<models::AttributeDto>(&document, "/components/schemas/AttributeDto");
    roundtrip::<models::AttributesDto>(&document, "/components/schemas/AttributesDto");
    roundtrip::<models::AttributesStatsDto>(&document, "/components/schemas/AttributesStatsDto");
    roundtrip::<models::AuditEventDto>(&document, "/components/schemas/AuditEventDto");
    roundtrip::<models::AuditStatsDto>(&document, "/components/schemas/AuditStatsDto");
    roundtrip::<models::AuditTargetDto>(&document, "/components/schemas/AuditTargetDto");
    roundtrip::<models::AuditWriterSummaryDto>(
        &document,
        "/components/schemas/AuditWriterSummaryDto",
    );
    roundtrip::<models::AuthOkDto>(&document, "/components/schemas/AuthOkDto");
    roundtrip::<models::AuthUserDto>(&document, "/components/schemas/AuthUserDto");
    roundtrip::<models::AuthzBatchDecision>(&document, "/components/schemas/AuthzBatchDecision");
    roundtrip::<models::AuthzExplainNode>(&document, "/components/schemas/AuthzExplainNode");
    roundtrip::<models::AuthzPolicyExplanation>(
        &document,
        "/components/schemas/AuthzPolicyExplanation",
    );
    roundtrip::<models::AuthzPolicyRef>(&document, "/components/schemas/AuthzPolicyRef");
    roundtrip::<models::AuthzRelationPathHop>(
        &document,
        "/components/schemas/AuthzRelationPathHop",
    );
    roundtrip::<models::AuthzResourceRef>(&document, "/components/schemas/AuthzResourceRef");
    roundtrip::<models::BatchCheckRequest>(&document, "/components/schemas/BatchCheckRequest");
    roundtrip::<models::BatchCheckResponse>(&document, "/components/schemas/BatchCheckResponse");
    roundtrip::<models::BindingSpecDto>(&document, "/components/schemas/BindingSpecDto");
    roundtrip::<models::BindingsStatsDto>(&document, "/components/schemas/BindingsStatsDto");
    roundtrip::<models::CapabilitiesDto>(&document, "/components/schemas/CapabilitiesDto");
    roundtrip::<models::ChangePasswordRequest>(
        &document,
        "/components/schemas/ChangePasswordRequest",
    );
    roundtrip::<models::CheckRequest>(&document, "/components/schemas/CheckRequest");
    roundtrip::<models::CheckResponse>(&document, "/components/schemas/CheckResponse");
    roundtrip::<models::CommitState>(&document, "/components/schemas/CommitState");
    roundtrip::<models::CompilePreviewDto>(&document, "/components/schemas/CompilePreviewDto");
    roundtrip::<models::CreateAttributeRequest>(
        &document,
        "/components/schemas/CreateAttributeRequest",
    );
    roundtrip::<models::CreatePermissionRequest>(
        &document,
        "/components/schemas/CreatePermissionRequest",
    );
    roundtrip::<models::CreatePolicyBindingRequest>(
        &document,
        "/components/schemas/CreatePolicyBindingRequest",
    );
    roundtrip::<models::CreatePolicyRequest>(&document, "/components/schemas/CreatePolicyRequest");
    roundtrip::<models::CreateRelationDefinitionRequest>(
        &document,
        "/components/schemas/CreateRelationDefinitionRequest",
    );
    roundtrip::<models::CreateRelationTupleRequest>(
        &document,
        "/components/schemas/CreateRelationTupleRequest",
    );
    roundtrip::<models::CreateResourceRequest>(
        &document,
        "/components/schemas/CreateResourceRequest",
    );
    roundtrip::<models::CreateResourceTypeRequest>(
        &document,
        "/components/schemas/CreateResourceTypeRequest",
    );
    roundtrip::<models::CreateRoleRequest>(&document, "/components/schemas/CreateRoleRequest");
    roundtrip::<models::CreateUserRequest>(&document, "/components/schemas/CreateUserRequest");
    roundtrip::<models::DashboardSummaryDto>(&document, "/components/schemas/DashboardSummaryDto");
    roundtrip::<models::EffectivePermissionsDto>(
        &document,
        "/components/schemas/EffectivePermissionsDto",
    );
    roundtrip::<models::EnrollmentStartRequest>(
        &document,
        "/components/schemas/EnrollmentStartRequest",
    );
    roundtrip::<models::EnrollmentStartResponse>(
        &document,
        "/components/schemas/EnrollmentStartResponse",
    );
    roundtrip::<models::EnrollmentVerifyRequest>(
        &document,
        "/components/schemas/EnrollmentVerifyRequest",
    );
    roundtrip::<models::EnrollmentVerifyResponse>(
        &document,
        "/components/schemas/EnrollmentVerifyResponse",
    );
    roundtrip::<models::ExplainRequest>(&document, "/components/schemas/ExplainRequest");
    roundtrip::<models::ExplainResponse>(&document, "/components/schemas/ExplainResponse");
    roundtrip::<models::ExplainTraceOptions>(&document, "/components/schemas/ExplainTraceOptions");
    roundtrip::<models::FactSpecDto>(&document, "/components/schemas/FactSpecDto");
    roundtrip::<models::IdentitySnapshotSummaryDto>(
        &document,
        "/components/schemas/IdentitySnapshotSummaryDto",
    );
    roundtrip::<models::ImpactAcknowledgementDto>(
        &document,
        "/components/schemas/ImpactAcknowledgementDto",
    );
    roundtrip::<models::ImpactBaselineDto>(&document, "/components/schemas/ImpactBaselineDto");
    roundtrip::<models::ImpactBindingDto>(&document, "/components/schemas/ImpactBindingDto");
    roundtrip::<models::ImpactCandidateDto>(&document, "/components/schemas/ImpactCandidateDto");
    roundtrip::<models::ImpactDifferencesDto>(
        &document,
        "/components/schemas/ImpactDifferencesDto",
    );
    roundtrip::<models::ImpactModeDto>(&document, "/components/schemas/ImpactModeDto");
    roundtrip::<models::ImpactPreviewDto>(&document, "/components/schemas/ImpactPreviewDto");
    roundtrip::<models::ImpactSampleDto>(&document, "/components/schemas/ImpactSampleDto");
    roundtrip::<models::ListResourcesRequest>(
        &document,
        "/components/schemas/ListResourcesRequest",
    );
    roundtrip::<models::LiveResponse>(&document, "/components/schemas/LiveResponse");
    roundtrip::<models::LoginRequest>(&document, "/components/schemas/LoginRequest");
    roundtrip::<models::LoginResponse>(&document, "/components/schemas/LoginResponse");
    roundtrip::<models::LoginStatus>(&document, "/components/schemas/LoginStatus");
    roundtrip::<models::LogoutAllRequest>(&document, "/components/schemas/LogoutAllRequest");
    roundtrip::<models::MeMfaDto>(&document, "/components/schemas/MeMfaDto");
    roundtrip::<models::MeResponse>(&document, "/components/schemas/MeResponse");
    roundtrip::<models::Meta>(&document, "/components/schemas/Meta");
    roundtrip::<models::MfaDisableRequest>(&document, "/components/schemas/MfaDisableRequest");
    roundtrip::<models::MfaEnrollRequest>(&document, "/components/schemas/MfaEnrollRequest");
    roundtrip::<models::MfaEnrollResponse>(&document, "/components/schemas/MfaEnrollResponse");
    roundtrip::<models::MfaEnrollVerifyRequest>(
        &document,
        "/components/schemas/MfaEnrollVerifyRequest",
    );
    roundtrip::<models::MfaPolicyModeBody>(&document, "/components/schemas/MfaPolicyModeBody");
    roundtrip::<models::MfaRecoveryCodesResponse>(
        &document,
        "/components/schemas/MfaRecoveryCodesResponse",
    );
    roundtrip::<models::MfaRecoveryRequest>(&document, "/components/schemas/MfaRecoveryRequest");
    roundtrip::<models::MfaRegenerateRequest>(
        &document,
        "/components/schemas/MfaRegenerateRequest",
    );
    roundtrip::<models::MfaStatusResponse>(&document, "/components/schemas/MfaStatusResponse");
    roundtrip::<models::MfaVerifyRequest>(&document, "/components/schemas/MfaVerifyRequest");
    roundtrip::<models::MutationAckDto>(&document, "/components/schemas/MutationAckDto");
    roundtrip::<models::Page>(&document, "/components/schemas/Page");
    roundtrip::<models::PagedResponseAttributeDto>(
        &document,
        "/components/schemas/PagedResponse_AttributeDto",
    );
    roundtrip::<models::PagedResponseAuditEventDto>(
        &document,
        "/components/schemas/PagedResponse_AuditEventDto",
    );
    roundtrip::<models::PagedResponseAuthzResourceRef>(
        &document,
        "/components/schemas/PagedResponse_AuthzResourceRef",
    );
    roundtrip::<models::PagedResponsePermissionDto>(
        &document,
        "/components/schemas/PagedResponse_PermissionDto",
    );
    roundtrip::<models::PagedResponsePolicyBindingDto>(
        &document,
        "/components/schemas/PagedResponse_PolicyBindingDto",
    );
    roundtrip::<models::PagedResponsePolicyDto>(
        &document,
        "/components/schemas/PagedResponse_PolicyDto",
    );
    roundtrip::<models::PagedResponseRelationDefinitionDto>(
        &document,
        "/components/schemas/PagedResponse_RelationDefinitionDto",
    );
    roundtrip::<models::PagedResponseRelationTupleDto>(
        &document,
        "/components/schemas/PagedResponse_RelationTupleDto",
    );
    roundtrip::<models::PagedResponseResourceDto>(
        &document,
        "/components/schemas/PagedResponse_ResourceDto",
    );
    roundtrip::<models::PagedResponseResourceTypeDto>(
        &document,
        "/components/schemas/PagedResponse_ResourceTypeDto",
    );
    roundtrip::<models::PagedResponseRoleDto>(
        &document,
        "/components/schemas/PagedResponse_RoleDto",
    );
    roundtrip::<models::PagedResponseUserDto>(
        &document,
        "/components/schemas/PagedResponse_UserDto",
    );
    roundtrip::<models::PagedResponseValue>(&document, "/components/schemas/PagedResponse_Value");
    roundtrip::<models::PatchAttributeRequest>(
        &document,
        "/components/schemas/PatchAttributeRequest",
    );
    roundtrip::<models::PatchPolicyBindingRequest>(
        &document,
        "/components/schemas/PatchPolicyBindingRequest",
    );
    roundtrip::<models::PatchPolicyRequest>(&document, "/components/schemas/PatchPolicyRequest");
    roundtrip::<models::PatchResourceRequest>(
        &document,
        "/components/schemas/PatchResourceRequest",
    );
    roundtrip::<models::PatchResourceTypeRequest>(
        &document,
        "/components/schemas/PatchResourceTypeRequest",
    );
    roundtrip::<models::PatchRoleRequest>(&document, "/components/schemas/PatchRoleRequest");
    roundtrip::<models::PatchUserRequest>(&document, "/components/schemas/PatchUserRequest");
    roundtrip::<models::PermissionDto>(&document, "/components/schemas/PermissionDto");
    roundtrip::<models::PlanDetailDto>(&document, "/components/schemas/PlanDetailDto");
    roundtrip::<models::PoliciesStatsDto>(&document, "/components/schemas/PoliciesStatsDto");
    roundtrip::<models::PoliciesSummaryDto>(&document, "/components/schemas/PoliciesSummaryDto");
    roundtrip::<models::PolicyBindingDto>(&document, "/components/schemas/PolicyBindingDto");
    roundtrip::<models::PolicyDetailDto>(&document, "/components/schemas/PolicyDetailDto");
    roundtrip::<models::PolicyDto>(&document, "/components/schemas/PolicyDto");
    roundtrip::<models::PolicyIssueDto>(&document, "/components/schemas/PolicyIssueDto");
    roundtrip::<models::PolicyTestCaseDto>(&document, "/components/schemas/PolicyTestCaseDto");
    roundtrip::<models::PolicyTestCaseInputDto>(
        &document,
        "/components/schemas/PolicyTestCaseInputDto",
    );
    roundtrip::<models::PolicyTestCaseWriteDto>(
        &document,
        "/components/schemas/PolicyTestCaseWriteDto",
    );
    roundtrip::<models::PolicyValidationDto>(&document, "/components/schemas/PolicyValidationDto");
    roundtrip::<models::PolicyVersionDto>(&document, "/components/schemas/PolicyVersionDto");
    roundtrip::<models::PublicationDiagnostics>(
        &document,
        "/components/schemas/PublicationDiagnostics",
    );
    roundtrip::<models::PublicationFailureStage>(
        &document,
        "/components/schemas/PublicationFailureStage",
    );
    roundtrip::<models::PublicationRefusal>(&document, "/components/schemas/PublicationRefusal");
    roundtrip::<models::PublishPolicyRequest>(
        &document,
        "/components/schemas/PublishPolicyRequest",
    );
    roundtrip::<models::PublishPolicyResultDto>(
        &document,
        "/components/schemas/PublishPolicyResultDto",
    );
    roundtrip::<models::ReadinessCheck>(&document, "/components/schemas/ReadinessCheck");
    roundtrip::<models::ReadinessSummaryDto>(&document, "/components/schemas/ReadinessSummaryDto");
    roundtrip::<models::ReadyFailureBody>(&document, "/components/schemas/ReadyFailureBody");
    roundtrip::<models::ReadyResponse>(&document, "/components/schemas/ReadyResponse");
    roundtrip::<models::ReauthProof>(&document, "/components/schemas/ReauthProof");
    roundtrip::<models::RelationDefinitionDto>(
        &document,
        "/components/schemas/RelationDefinitionDto",
    );
    roundtrip::<models::RelationTupleDto>(&document, "/components/schemas/RelationTupleDto");
    roundtrip::<models::RelationTupleSpecDto>(
        &document,
        "/components/schemas/RelationTupleSpecDto",
    );
    roundtrip::<models::ReplaceAttributesRequest>(
        &document,
        "/components/schemas/ReplaceAttributesRequest",
    );
    roundtrip::<models::ReplaceRolePermissionsRequest>(
        &document,
        "/components/schemas/ReplaceRolePermissionsRequest",
    );
    roundtrip::<models::ReplaceUserRolesRequest>(
        &document,
        "/components/schemas/ReplaceUserRolesRequest",
    );
    roundtrip::<models::RequestSpecDto>(&document, "/components/schemas/RequestSpecDto");
    roundtrip::<models::ResetPasswordRequest>(
        &document,
        "/components/schemas/ResetPasswordRequest",
    );
    roundtrip::<models::ResourceDto>(&document, "/components/schemas/ResourceDto");
    roundtrip::<models::ResourceFactDto>(&document, "/components/schemas/ResourceFactDto");
    roundtrip::<models::ResourceTypeDto>(&document, "/components/schemas/ResourceTypeDto");
    roundtrip::<models::ResourceTypesStatsDto>(
        &document,
        "/components/schemas/ResourceTypesStatsDto",
    );
    roundtrip::<models::ResourcesStatsDto>(&document, "/components/schemas/ResourcesStatsDto");
    roundtrip::<models::RetainedStateCounts>(&document, "/components/schemas/RetainedStateCounts");
    roundtrip::<models::RevisionResponse>(&document, "/components/schemas/RevisionResponse");
    roundtrip::<models::RevisionStatus>(&document, "/components/schemas/RevisionStatus");
    roundtrip::<models::RewriteRuleDto>(&document, "/components/schemas/RewriteRuleDto");
    roundtrip::<models::RiskAssessmentDto>(&document, "/components/schemas/RiskAssessmentDto");
    roundtrip::<models::RiskReasonDto>(&document, "/components/schemas/RiskReasonDto");
    roundtrip::<models::RoleDto>(&document, "/components/schemas/RoleDto");
    roundtrip::<models::RolePermissionsDto>(&document, "/components/schemas/RolePermissionsDto");
    roundtrip::<models::RolesStatsDto>(&document, "/components/schemas/RolesStatsDto");
    roundtrip::<models::SessionDto>(&document, "/components/schemas/SessionDto");
    roundtrip::<models::SessionsStatsDto>(&document, "/components/schemas/SessionsStatsDto");
    roundtrip::<models::SetUserMfaPolicyRequest>(
        &document,
        "/components/schemas/SetUserMfaPolicyRequest",
    );
    roundtrip::<models::SetupAdminDto>(&document, "/components/schemas/SetupAdminDto");
    roundtrip::<models::SetupAdminRequest>(&document, "/components/schemas/SetupAdminRequest");
    roundtrip::<models::SetupStatusDto>(&document, "/components/schemas/SetupStatusDto");
    roundtrip::<models::TokenPair>(&document, "/components/schemas/TokenPair");
    roundtrip::<models::TypedValueDto>(&document, "/components/schemas/TypedValueDto");
    roundtrip::<models::UpsertPolicyTestCaseRequest>(
        &document,
        "/components/schemas/UpsertPolicyTestCaseRequest",
    );
    roundtrip::<models::UserDto>(&document, "/components/schemas/UserDto");
    roundtrip::<models::UserMfaPolicyDto>(&document, "/components/schemas/UserMfaPolicyDto");
    roundtrip::<models::UserRolesDto>(&document, "/components/schemas/UserRolesDto");
    roundtrip::<models::UsersStatsDto>(&document, "/components/schemas/UsersStatsDto");
    roundtrip::<models::ValidatePolicyRequest>(
        &document,
        "/components/schemas/ValidatePolicyRequest",
    );
    roundtrip::<models::Violation>(&document, "/components/schemas/Violation");
}

#[test]
fn every_open_json_exception_roundtrips() {
    let document = spec();
    exception_roundtrip::<models::ApiErrorPayload>(
        &document,
        "#/components/schemas/ApiErrorPayload/properties/details",
    );
    exception_roundtrip::<models::ApiResponseValue>(
        &document,
        "#/components/schemas/ApiResponse_Value/properties/data",
    );
    exception_roundtrip::<models::AttributeAssignmentDto>(
        &document,
        "#/components/schemas/AttributeAssignmentDto/properties/value",
    );
    exception_roundtrip::<models::AuditEventDto>(
        &document,
        "#/components/schemas/AuditEventDto/properties/metadata/additionalProperties",
    );
    exception_roundtrip::<models::BatchCheckRequest>(
        &document,
        "#/components/schemas/BatchCheckRequest/properties/context/additionalProperties",
    );
    exception_roundtrip::<models::CheckRequest>(
        &document,
        "#/components/schemas/CheckRequest/properties/context/additionalProperties",
    );
    exception_roundtrip::<models::CompilePreviewDto>(
        &document,
        "#/components/schemas/CompilePreviewDto/properties/warnings/items",
    );
    exception_roundtrip::<models::CreatePolicyRequest>(
        &document,
        "#/components/schemas/CreatePolicyRequest/properties/expression",
    );
    exception_roundtrip::<models::ExplainRequest>(
        &document,
        "#/components/schemas/ExplainRequest/properties/context/additionalProperties",
    );
    exception_roundtrip::<models::ListResourcesRequest>(
        &document,
        "#/components/schemas/ListResourcesRequest/properties/context/additionalProperties",
    );
    exception_roundtrip::<models::PagedResponseValue>(
        &document,
        "#/components/schemas/PagedResponse_Value/properties/data/items",
    );
    exception_roundtrip::<models::PatchPolicyRequest>(
        &document,
        "#/components/schemas/PatchPolicyRequest/properties/expression",
    );
    exception_roundtrip::<models::PolicyValidationDto>(
        &document,
        "#/components/schemas/PolicyValidationDto/properties/warnings/items",
    );
    exception_roundtrip::<models::PolicyVersionDto>(
        &document,
        "#/components/schemas/PolicyVersionDto/properties/expression",
    );
    exception_roundtrip::<models::ValidatePolicyRequest>(
        &document,
        "#/components/schemas/ValidatePolicyRequest/properties/expression",
    );
}
