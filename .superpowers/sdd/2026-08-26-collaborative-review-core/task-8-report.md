# Task 8 report — 增量清单和回执 API

## 交付摘要

- 管理员可读取批次历史和 pending counts、创建增量批次、下载不可变 CSV，并通过 token JSON 或 Mao 手工 JSON 回执同步结果。
- delta 以每个 candidate 最后成功回执项为本地事实，以 active Candidate/Species + 当前 APPROVED Review 为期望事实，生成 ADD、REMOVE、MOVE 或 refresh ADD。
- CSV 固定 16 列、RFC 4180、UTF-8 BOM、稳定排序，字段来自 ExportItem 创建快照；GET CSV、历史和 pending counts 均不写数据库。
- 回执 token 是 batch id 的域分离 HMAC，数据库只存 SHA-256 digest；JSON、历史、审计、错误和日志不暴露原 token。
- JSON 与手工文件回执共用行锁、全量预校验和单事务服务，支持 partial、失败保留 pending、幂等成功重试与冲突检测。
- 未读取、下载、代理、解码或保存图片字节；未实现 Windows 下载器或 Task 9+。

## 评审修复

- species code 明确定义为 1–32 位 Windows-safe ASCII 标识符：首字符 `A-Z`，其余仅 `A-Z0-9_-`，并拒绝 `CON`、`PRN`、`AUX`、`NUL`、`COM1`–`COM9`、`LPT1`–`LPT9`。规则统一用于 admin create、export filter、import normalize、export path 防线及数据库约束。
- SQLite 使用 `GLOB`、PostgreSQL 使用正则约束；05 upgrade 在任何 schema 修改前扫描并拒绝已有不安全 code。实际 PostgreSQL 插入 `../outside` 被 `ck_species_code_safe` 拒绝。
- species 与 original URL 同时变化时生成 refresh `ADD`，新路径在 `target_relative_path`，旧路径保留在 `previous_relative_path` 供本地工具清理；只有 original fingerprint 未变化才生成 `MOVE`，避免复用陈旧字节。
- 历史 GET 只计算响应中的 effective `expired`，不持久化 status/expired_at、不写 audit、不 commit；pending counts 同样只读，并把已过期但尚未由写路径落库的批次视为不再占用调度。
- SQLite 04 数据的 32 位 UUID storage 经 `UUID(...)` 规范化为与运行时一致的 36 位连字符 scope key；已覆盖 populated 04→05→04→05 和升级后同 scope 复用。
- Mao 手工回执改为 bounded raw `application/json`，认证/CSRF 依赖先执行，随后按 Content-Type、Content-Length 和 ASGI stream 累计上限 128 KiB；不再触发 multipart parser/spool。OpenAPI 仅声明 `application/json`。

### 第二次复审修复

- FAILED receipt error 在持久化前统一经过敏感值检查。服务用 batch id 与配置 secret 重算 expected token，并拒绝包含 token、`RECEIPT_SECRET`、大小写变体，以及标准/URL-safe Base64（含去 padding）或 hex 显见编码的错误文本；固定返回 `RECEIPT_ERROR_SENSITIVE`，不回显命中值，整笔回滚。
- sync 与 Mao 手工 JSON 两条路都显式把同一配置 secret 传入 `apply_receipt`；回归测试直接检查 `ExportItem.error`、AuditEvent、API response 与捕获日志，确认 token/secret/offending variant 均未出现。
- 成功回执允许的 decoded extension 现在成为 canonical local path。original fingerprint 未变化时，同鱼种以实际 `local_relative_path` 比较，因此 unchanged 为 NO_WORK；跨鱼种 MOVE 只替换目标 species 目录并保留实际 filename/suffix。original 改变仍采用新 URL 推断 suffix，并在路径变化时保留 previous cleanup。
- 下游本地同步计划明确服务器 `target_relative_path` 是 ADD/MOVE/REMOVE 唯一目标；composite ADD 必须先成功提交新 target 再清理旧路径，失败保留旧文件；REMOVE 不再自建 timestamp destination，receipt 报告实际服务器 target。仅更新契约与未来验收措辞，没有实现 Task 9 下载器。

## 数据库与迁移

- 仍只使用 `20260826_05_export_snapshots.py`，01–04 未修改。Task 8 尚未部署，且 brief 指定新增 05，因此直接修正 05，而不是追加 06；upgrade/downgrade 均同步维护 species 约束。
- ExportBatch 保存 canonical scope key、完成/过期时间、受约束状态、pending-scope 部分唯一索引和 expiry 查询索引。
- ExportItem 保存 candidate/review 版本、来源/鱼种/路径/授权快照、original/metadata fingerprint、成功回执路径和受约束状态。
- PostgreSQL 批次创建使用事务 advisory lock，并由 pending-scope 唯一索引兜底；all/species overlap 在同一临界区检查。
- 回执严格锁 batch/items；并发相同成功回执收敛为一个 succeeded 状态和一个 receipt audit。

## API 与安全契约

- `GET /v1/admin/exports`
- `GET /v1/admin/exports/pending-counts`
- `POST /v1/admin/exports`
- `GET /v1/admin/exports/{batch_id}.csv`
- `POST /v1/sync/batches/{batch_id}/receipt`
- `POST /v1/admin/exports/{batch_id}/receipt-file`（raw `application/json`）

管理员读取要求完成首次改密的 Mao，管理员 POST 另要求 CSRF。sync 仅接受 `Authorization: Batch ...`，权限严格绑定 URL batch。token 回执 audit actor 固定为 batch creator，不采信同请求中的浏览器 cookie。无工作不创建空批次；同 scope 复用未过期批次；跨 scope overlap 返回稳定 409 引用。

成功 ADD/MOVE/REMOVE 要求 64-hex SHA-256 和匹配 action/candidate stem 的安全相对路径。batch/token/expiry/item triple/version、重复项、越界项、path/hash 任一无效都会在写入前整批拒绝。失败项保持 pending；相同成功重试幂等，SHA/path 冲突返回 409。

FAILED error 仍要求非空、去控制空白后不超过 2000 字符；若包含 batch token、配置 secret 或上述显见编码/大小写变体，则以固定 422 拒绝且不保存 error、不新增 receipt audit。合法失败文本仍保持 pending 并可供重试。

生产 API 校验 `RECEIPT_SECRET`：缺失、过短、低熵或复用其他 secret 均拒绝启动。原报告曾把低熵 secret 测试描述为独立 RED，但该测试实际随实现提交 `2f79017` 加入；本报告纠正该历史，不把它计为严格 test-first 证据。

## TDD 与提交证据

原 Task 8：

1. `fa765bd test(review): specify incremental export state`
2. `2f79017 feat(review): add incremental export and receipt API`

本轮评审修复严格先提交测试：

1. `98ab713 test(review): cover export review findings`
2. RED 命令覆盖 composite change、unsafe schema/DB/export/import、只读 GET、populated migration、raw receipt、inactive Species 和 audit actor：`21 failed, 2 passed, 74 deselected`。失败分别重现 MOVE 误判、路径穿越、DB 未约束、GET 落库、scope hex 回填及 multipart 预解析。
3. `07848f9 fix(review): harden incremental export boundaries`
4. 新增/修正场景 GREEN：`24 passed, 73 deselected in 10.30s`。
5. exports/receipts/imports 聚焦集：`97 passed in 68.64s`。
6. admin catalog、model constraints 与迁移聚焦集：`59 passed in 24.08s`。

RFC 4180 现在实际断言逗号、双引号和换行的 round trip 及原始 quoting；另覆盖 inactive Species REMOVE、token-vs-browser audit actor、unsafe existing migration rejection，以及 raw JSON 的 auth/CSRF/media type/body cap/OpenAPI。

第二次复审同样严格 test-first：

1. `78f2098 test(review): cover receipt secrecy and canonical paths`
2. RED：敏感 FAILED error 与 decoded suffix 聚焦命令得到 `14 failed, 12 deselected in 6.54s`。12 个 sync/manual × token/secret/大小写/Base64 场景均证明原文写入 DB；另两项分别证明 unchanged 错建 201 ADD、species MOVE 从 `.png` 退回 `.jpg`。
3. `dd6d338 fix(review): secure receipts and canonicalize local paths`
4. GREEN：同一命令 `14 passed, 12 deselected in 5.59s`；完整 exports/receipts `55 passed in 23.51s`。
5. SQLite 全 API：`332 passed, 16 skipped in 176.65s`，16 项均为未提供 PostgreSQL URL 时的预期 integration skip。

## 最终验证

- SQLite 全 API：`319 passed, 16 skipped in 173.01s`；16 项全部是缺少显式 PostgreSQL URL 时预期跳过的 integration。
- 唯一临时容器 `sukaseafood-task8-fix-07848f9`（PostgreSQL 16）中全 API + integration：`335 passed in 185.05s`，`0 skipped`。
- Task 8 PostgreSQL race 单独复验：`2 passed in 1.47s`，证明 simultaneous same-scope create 和 concurrent identical receipt 收敛。
- PostgreSQL Alembic：fresh→head、`alembic check`、05→04→05 均成功；`No new upgrade operations detected.`。
- PostgreSQL offline DDL 成功包含 `20260826_05`、`ck_species_code_safe`、`uq_export_batches_pending_scope` 和 `original_fingerprint`。
- SQLite populated 04→05→04→05、unsafe preflight rollback、同 scope reuse 均通过聚焦测试。
- `python -m compileall -q app tests`、`git diff --check` 通过；定向 token/secret 扫描与最终 clean status 在报告提交后复验。
- 临时容器已用精确名称删除，并确认 `docker ps -a` 中不存在。

第二次复审最终验证：

- 唯一 PostgreSQL 16 容器 `sukaseafood-task8-rereview-dd6d338` 中 fresh→head、`alembic check`、05→04→05 与 offline DDL 全部成功；offline DDL 包含 05、species constraint、pending scope index 与 snapshot fingerprint。
- 第一次真实 PostgreSQL 全套运行得到 `347 passed, 1 failed`；唯一失败是未修改的 Task 7 SQLite `test_concurrent_retry_converges_on_one_result` 偶发 `IMPORT_PREVIEW_STALE`。独立循环在前 5 次通过、第 6 次复现，且 `6e48664..HEAD` 对 import 实现/测试无差异，因此未越界修改 Task 7。
- 相同代码与容器完整重跑：`348 passed in 162.78s`，`0 skipped`。
- Task 8 PostgreSQL race 单独复验：`2 passed in 1.87s`，simultaneous create 与 concurrent receipt 均收敛。
- 原临时容器已按精确名称删除；最终 compileall、diff、secret scan 与 clean status 在本报告提交后再次执行。
