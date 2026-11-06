# 受审契约与生成规则

`openapi.json` 由 Rust `interface::contract::openapi` 导出，人工核对源头校验后更新受审文件与 SHA。
生成器只读取受审文件，`--check` 不更新它，也不修复工作区。HTTP 路径、方法、Envelope 与服务端接受行为不因生成而变化。

本轮补齐已有规则，不增加服务端拒绝分支：

| 字段范围 | 机器信息 | 校验来源 |
|---|---|---|
| BatchCheck.resources | 1～10000 | authz 批量请求入口 |
| ListResources.limit | 1～100 | authz 分页入口 |
| 管理 limit | IAM/facts/policy 1～200；audit/relations 1～100 | 各 usecase 的分页常量与校验函数 |
| 新建/重置/修改密码 | 12～128 Unicode 字符，writeOnly | domain/authn/password.rs:hash_password_with |
| IAM/facts 展示名 | 1～64 Unicode 字符 | iam/service_common、facts/service_attribute |
| role/resource type/policy code | ASCII 小写开头，至多64字符，后续可数字、点、下划线、减号 | 对应 usecase code validator |
| 初始 role_ids、角色/权限替换集合 | 最大512；JSON值唯一 | iam/service_common.rs:ReplaceSet::parse |
| resource.external_id / attributes | ID 1～128；属性集合最大512 | facts/service_resource、service_attribute |
| policy.description / binding.priority | 描述最大2000；优先级0～10000 | policy/service_common |
| 四个双层 Option PATCH 字段 | `x-authgebra-clearable: true` | email 与 role/resource-type/policy description 的 double_option |

`x-authgebra-normalized-value` 记录先规范化、再验证的规则，包含操作顺序、规范化后的 Schema 和源码符号。
例如 username 先 Unicode trim / lowercase，再检查3～32字符；Policy name 先 trim，再检查1～200。
这些范围不能误标成原始输入的 maxLength。SDK保留原始 payload，不擅自 trim/lowercase；服务端继续执行规范化规则。
SDK `Validate` 的直接本地检查针对原始值的标准 Schema 断言，规范化扩展供审计使用，不代替服务端校验。

`type-coverage.json` 从受审契约与实际生成源码独立构建。它记录每个 operation、响应状态、Schema / 字段 Pointer、
required/nullable/约束、六端类型/文件/位置、动态例外及测试和源码摘要。生成器 provenance 只是候选位置，
独立检查器另外核对真实声明；未知断言不被当成任意 JSON。例外只允许真实开放 JSON，不能为生成失败兜底。

标准 `uniqueItems` 比较原始 JSON 值。不同文本表示但指向同一个 UUID、属性 ID 唯一、实体存在性、授权与生命周期
仍由服务端执行；SDK 不宣称本地验证覆盖所有数据库/业务状态。

Seven DELETE operations now share MutationAckDto for their existing data.replayed response. An independent test pins the original JSON bytes. ApiResponse_Value and PagedResponse_Value remain source-compatible legacy models; no current operation uses them.
