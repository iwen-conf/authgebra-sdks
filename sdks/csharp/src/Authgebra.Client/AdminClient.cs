namespace Authgebra;

/// <summary>管理面：按资源分组暴露契约里的全部操作（方法名 = operationId 的 PascalCase + Async）。</summary>
public sealed class AdminClient
{
    internal AdminClient(AuthgebraClient core)
    {
        Users = new UsersApi(core);
        Roles = new RolesApi(core);
        Permissions = new PermissionsApi(core);
        Resources = new ResourcesApi(core);
        ResourceTypes = new ResourceTypesApi(core);
        Attributes = new AttributesApi(core);
        Policies = new PoliciesApi(core);
        PolicyBindings = new PolicyBindingsApi(core);
        RelationDefinitions = new RelationDefinitionsApi(core);
        RelationTuples = new RelationTuplesApi(core);
        Audit = new AuditApi(core);
        Dashboard = new DashboardApi(core);
        Auth = new AuthApi(core);
        Setup = new SetupApi(core);
        Health = new HealthApi(core);
    }

    public UsersApi Users { get; }
    public RolesApi Roles { get; }
    public PermissionsApi Permissions { get; }
    public ResourcesApi Resources { get; }
    public ResourceTypesApi ResourceTypes { get; }
    public AttributesApi Attributes { get; }
    public PoliciesApi Policies { get; }
    public PolicyBindingsApi PolicyBindings { get; }
    public RelationDefinitionsApi RelationDefinitions { get; }
    public RelationTuplesApi RelationTuples { get; }
    public AuditApi Audit { get; }
    public DashboardApi Dashboard { get; }

    /// <summary>登录、刷新、MFA、会话等认证面。</summary>
    public AuthApi Auth { get; }

    public SetupApi Setup { get; }
    public HealthApi Health { get; }
}
