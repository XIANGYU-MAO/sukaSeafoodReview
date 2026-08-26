# Task 8 report — 增量清单和回执 API

## 交付摘要

- 新增管理员批次历史、pending counts、批次创建、不可变 CSV、token 回执和 Mao 手动回执文件接口。
- delta 以“每个 candidate 最后一个成功回执项”为本地事实，以当前 active Candidate/Species + 当前 APPROVED Review 为期望事实，生成 ADD、REMOVE、MOVE 或 metadata/original refresh ADD。
- CSV 固定 16 列、RFC 4180、UTF-8 BOM、稳定排序；所有字段来自 ExportItem 创建快照，GET CSV 不修改状态。
- 回执令牌是 batch id 的域分离 HMAC；数据库仅保存 SHA-256 digest。JSON、历史、审计、错误和日志不包含原始 token。
- JSON 与文件回执复用同一个行锁 + 全量预校验 + 单事务服务；支持 partial、失败保留 pending、幂等成功重试和冲突检测。
- 未读取、下载、代理、解码或保存任何图片字节；未实现 Windows 下载器或 Task 9+。

## 数据库与并发

- 新增且只新增 `20260826_05_export_snapshots.py`；01–04 未改动。
- ExportBatch 新增稳定 scope key、completed/expired 时间、状态约束、pending scope 部分唯一索引和 expiry 查询索引。
- ExportItem 新增 candidate/review 版本、来源/鱼种/路径/授权快照、original/metadata fingerprint、成功回执实际路径和状态约束/索引。
- PostgreSQL 批次创建使用事务级 advisory lock，加 pending-scope 唯一索引兜底；all/species 交叉 scope 在同一临界区检查 candidate overlap。
- 回执严格锁 batch/items；两个并发相同成功回执收敛为一个 succeeded 状态和一个 receipt audit。

## API 契约

- `GET /v1/admin/exports`
- `GET /v1/admin/exports/pending-counts`
- `POST /v1/admin/exports`
- `GET /v1/admin/exports/{batch_id}.csv`
- `POST /v1/sync/batches/{batch_id}/receipt`
- `POST /v1/admin/exports/{batch_id}/receipt-file`

管理员读取要求已完成首次改密的 Mao；管理员 POST 另要求 CSRF。sync 路由只接受 `Authorization: Batch ...`，且权限只覆盖 URL 中的精确 batch 回执。无工作返回 `NO_WORK` 且不创建 token-bearing 空批次；同 scope 返回现有批次；跨 scope overlap 返回稳定 409 引用。

## TDD 证据

1. 先新增 export/receipt/PostgreSQL race 测试和独立测试 `RECEIPT_SECRET`。
2. RED：`pytest tests/test_exports.py::test_new_approved_export_is_immutable_rfc4180_snapshot_and_csv_get_stays_pending -q` 因 Settings 尚无 RECEIPT_SECRET 支持而失败。
3. 测试提交：`fa765bd test(review): specify incremental export state`。
4. GREEN focused：`pytest tests/test_exports.py tests/test_receipts.py -q` → `26 passed`。
5. 原图 URL 扩展名变化最初误判 MOVE；既有 failing test 将其固定为重新下载语义 ADD，修复后 focused 全绿。
6. 完整套件暴露旧 auth fixture 的结构兼容回归；读取完整 traceback 后保留原 trusted-proxy 校验顺序，并让无状态 secret 校验兼容该 fixture。`pytest tests/test_auth.py -q` → `47 passed`。
7. 补充低熵长 secret RED（`"x" * 64` 未被拒绝），实现强度边界后目标测试 `2 passed`。

## 验证结果

- Focused exports/receipts：`26 passed in 13.70s`。
- Affected admin/history/import/model：`182 passed in 73.96s`。
- Auth regression：`47 passed in 31.76s`。
- Task 8 PostgreSQL races（独立临时容器）：`2 passed in 1.57s`，容器已删除。
- 全部 PostgreSQL integration（新独立临时容器）：`16 passed in 12.75s`，0 skip，容器已删除。
- 全 API + 全 PostgreSQL integration（第三个唯一临时容器）：`313 passed in 141.72s`，0 skip，容器 `sukaseafood-task8-all-776ca462a7be4eed93bd6136f856cec0` 已由 finally 精确删除。
- Alembic SQLite：upgrade head、`alembic check`、downgrade 04、re-upgrade head 全部成功；check 输出 `No new upgrade operations detected.`。
- PostgreSQL offline DDL 成功生成，包含 `20260826_05`、`uq_export_batches_pending_scope` 与 `original_fingerprint`。
- 最终还执行 `python -m compileall app tests`、`git diff --check`、定向 secret/token scan 和工作树检查；结果记录在实现提交前的最终验证输出中。

## 安全与原子性说明

- 生产 API 缺少、短、低熵或复用其他 secret 的 RECEIPT_SECRET 时拒绝启动；CLI 仅构造 Settings 时不被强制要求该值。
- receipt body/file 上限 128 KiB；item 数量、错误、hash、路径均有边界。路径只允许预期 action 目录、candidate stem 和受控图片扩展调整。
- batch id/token/expiry/item triple/version 任一不匹配都会在写入前拒绝；重复条目与 out-of-batch 项整批回滚。
- AuditEvent 只记录 bounded IDs/count/status，不记录 token、token digest、SHA 明文列表、URL 或错误正文。
