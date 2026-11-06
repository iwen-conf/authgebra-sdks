# Authgebra 官方 SDK

SDK `0.1.0` 的公开分发仓库为 [authgebra-sdks](https://github.com/iwen-conf/authgebra-sdks)，
保留专有商业许可证；各语言安装方法与 registry 边界见 [DISTRIBUTION.md](DISTRIBUTION.md)。
This public SDK-only mirror contains SDKs, contracts and fixtures; it contains no server source.

五个语言的 SDK 是同一套 `/api/v1` HTTP API 的薄封装：**不做任何授权计算**（不实现 RBAC/ABAC/ReBAC）。

唯一契约来源：`Rust DTO → OpenAPI`，受审导出物 `contracts/openapi.json`（`contracts/openapi.sha256` 记录摘要）。禁止在 SDK 里另写协议定义；模型以它为准，更新它必须人工评审，工具不得自动覆盖。

| 目录 | 内容 |
|---|---|
| `contracts/` | OpenAPI 导出物与摘要 |
| `fixtures/cases/*.json` | 跨语言一致性夹具（本文件「夹具格式」） |
| `go/ rust/ python/ java/ zig/` | 各语言 SDK |

## 夹具格式（每个 SDK 的测试必须逐个跑通 `fixtures/cases/*.json`）

```
{ id, description, operation, request{method,path,idempotency_key,body},
  response{status,headers,body | body_raw}  或  fault: "timeout"|"reset",
  expect{...} }
```
`body_raw` 是**字节原样**的响应体（用于大整数/非 JSON）；SDK 必须从原始字节解析，不得先经浮点数。
`expect.kind`：`ok` | `api_error` | `transport_error` | `protocol_error`。字段为 `null` 表示“必须缺席/为空”，不是“不检查”。

## 统一语义（所有语言等价）

**结果与错误是两个世界**
- HTTP 2xx → `Result`：`data`、`meta`（可缺席）、`page`、`request_id`、`trace_id`。`decision=deny` 是**成功结果**，绝不抛错。
- 服务端错误体（`{"error":{...}}`）→ `ApiError`：`status`、`code`（**原始字符串**）、`known_code`（是否在 SDK 已知枚举内）、`message`、`request_id`、`trace_id`、`details`、`violations`（来自 `details.violations`，保序）、`retryable`（三态：true/false/未声明）、`retry_after_seconds`、`commit_state`。
- 传输失败（超时/连接中断/TLS）→ `TransportError`；响应无法理解（非 JSON、缺必需字段）→ `ProtocolError`，保留 HTTP 状态码。
- **不得**用 `message` 的内容判断类型；**不得**把未知错误码映射成任何已知码。
- `503 NOT_READY` 是“系统未完成可信判断”，**不得**转换成 Deny。业务方自行决定安全拒绝，但必须保留原始错误。

**追踪**
- 每次请求可注入 `X-Request-ID`（合法性同服务端：`^[A-Za-z0-9._-]{8,128}$` 之外的值丢弃，由服务端生成）与 W3C `traceparent`（调用方给出或 SDK 新建；`trace_id` 32 位小写十六进制、非全零）。
- 响应侧：`request_id` 以响应头 `X-Request-ID` 为准，头缺失/非法时回退 body；二者不一致时保留头值并暴露 `request_id_mismatch`。`trace_id` 取 body `error.trace_id`，缺席时取合法的 `X-Trace-ID` 头；非法头一律忽略。三个标识（request_id / trace_id / span_id）不混用。

**版本与精度**
- `authz_revision` / `snapshot_generation` 是 64 位整数，**必须无损**（Go `int64`、Rust `i64`、Java `long`、Python `int`、Zig `i64`）。管理读取没有 `meta` 时暴露为“无”，**绝不填 0**。
- 请求体 UTF-8 原样；不得自动补默认值、不得把缺省字段改写成 `null`。

**重试（默认保守）** — 「读/写」按**是否改变服务端状态**划分，不按 HTTP 方法；只读 POST 的传输失败不声明 `commit_state`。
- 读（GET/HEAD，以及纯判定的只读 POST：`authz.check` / `authz.batch_check` / `authz.explain` / `authz.list_authorized_resources`）：默认最多重试 2 次（总 3 次），仅对 `RATE_LIMITED`（遵守 `Retry-After`，上限封顶）、`NOT_READY`、连接/读超时；指数退避带抖动；`CURSOR_STALE`、`FORBIDDEN`、4xx 其余**不重试**。重试次数、上限可配，**不允许无限**。
- 写（POST/PUT/PATCH/DELETE）：**默认不自动重发**。仅当调用方显式开启且请求带同一个 `Idempotency-Key`、payload 字节相同时，才对 `NOT_READY` / 传输失败 / `IDEMPOTENCY_IN_PROGRESS` 有限重试。
- `AUTHZ_RELOAD_PENDING` / `IDENTITY_RELOAD_PENDING`：**永不**重发；错误携带 `commit_state=committed_unpublished`。未确认发布前不得向上层报告“授权已生效”。
- 写请求（上述只读 POST 之外的 POST/PUT/PATCH/DELETE）超时/连接中断：`commit_state=unknown`（SDK 自己推断，服务端未声明），不得假设未提交；核对只能用同一幂等键或实体读取。

**分页**
- 提供单页调用；迭代器遇到 `CURSOR_STALE` 默认**抛出**（调用方决定是否从首页重来）。迭代器必须有页数上限，支持取消。授权 List 的 cursor 保持不透明，不跨 generation 拼接。

**安全与资源**
- TLS 默认校验证书，不提供“默认关闭”；Token 绝不进入日志/异常文本（只记录长度或前缀掩码）；超时默认有限（连接 5s，总 30s，可配）；响应体大小默认上限 8 MiB（可配）；响应体必须关闭/释放。
- 客户端可注入自定义 HTTP 传输；`User-Agent: authgebra-<lang>/<version>`。

## 状态口径
每个 SDK 的状态只能逐级提升且需证据：`DESIGNED → IMPLEMENTED → COMPILED → CONTRACT_TESTED → INTEGRATION_TESTED → RELEASE_READY → PUBLISHED`。当前状态与验证边界见 [SDK 状态](STATUS.md)，真实场景及执行方法见 [集成测试](integration/README.md)。

## 具名模型与字段来源

六端共同消费受审 OpenAPI 3.1。请求、响应、嵌套对象、枚举和已知 tagged union 都生成具名模型；
只在契约确实允许任意 JSON 的位置使用受控 JSON 值。每端 provenance 记录 Schema / 属性 JSON Pointer，
独立检查器 [sdk-type-coverage.py](../scripts/ci/sdk-type-coverage.py) 另外读取源码声明、字段类型与 JSON 名称，
验证 [type-coverage.json](contracts/type-coverage.json)，不将生成器自报数量当作覆盖证据。

可选与可空分开处理：缺席不补 null，显式 null 不丢失，false、0、空字符串及空数组均保留。
四个服务端已经支持清空的 PATCH 字段通过 Rust 导出 `x-authgebra-clearable`；描述文本不参与业务判断。
各端沿用现有错误、分页、重试及追踪合同；非法用户 Request ID 丢弃并由既有机制补齐，不抛参数异常。

```sh
bash scripts/ci/sdk-gates.sh --generated-only
bash scripts/ci/sdk-gates.sh
# 真实隔离服务需要 integration/README.md 中的显式环境变量
bash scripts/ci/sdk-integration.sh
```

所有生成检查均使用独立临时目录逐字节比较，不修复工作区；未知结构或未实现的约束明确失败，禁止
静默降级。机器约束的覆盖率指受审契约中已声明的约束，不代表把权限、唯一性、资源存在性等业务状态规则
搬进客户端。详见 [本轮类型体系验收](TYPE-SYSTEM-20261106.md)。
