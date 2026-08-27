# SukaSeafood 审核系统发布与回滚清单

本文件是待执行清单，不表示当前分支已经 SSH 部署、更新 Caddy 或通过公开验收。所有线上动作都需要操作者另行明确授权。

## 发布前

- [ ] 主仓库与 YGF 网关仓库均记录精确 Git revision，工作区无非预期改动。
- [ ] API 全量测试/compileall、Web 测试/typecheck/生产 build、本地同步全量测试/compileall/冻结构建全部通过。
- [ ] `docker compose ... config --quiet` 通过；若可用，API/Web 镜像 build 通过。
- [ ] 确认生产 `.env` 位于 `/opt/sukaseafood-review/deploy/.env`，模式 `0600`，未进入 Git/发布日志。
- [ ] 确认 `review-postgres` 无发布端口，原图/CSV/SQLite/日志不在镜像上下文或 Git 中。
- [ ] 运行 `backup_postgres.sh`，并用 `pg_restore --list` 验证本次备份。

## 先发布审核服务（不改网关）

- [ ] 运行 `first_deploy.sh`（仅首次）或 Windows 发布脚本；保存首次六账号临时密码到密码管理器。
- [ ] Alembic 成功后才切换 API；等待 review-postgres、review-api、review-web 健康。
- [ ] 从容器网络验证 `review-api:8000/v1/health` 内容为 `{"status":"ok"}`，`review-web:8080/healthz` 含 `SukaSeafood`。
- [ ] 如需导入，按“采集与导入”四步流程生成当前 CSV；CLI 路径先确认 dry-run 的 `blocking_errors=0` 和 `can_commit=true`，再显式 `-Commit`；核对 commit report 的 `file_sha256` 并记录实际 `total`、`inserted`、`skipped_exact`、`skipped_url_duplicates` 与 `skipped_blocking`。网页路径可批准观察到的精确图片主机，或二次确认后跳过阻断行。

## 再发布 YGF 网关

- [ ] 记录并备份 YGF 当前 revision/数据库；确认审核容器已健康。
- [ ] 发布已准备的 YGF 变更，但不恢复 Ocean 静态目录。
- [ ] Caddy reload 成功后运行所有既有 YGF preflight，再运行新增 review/API/legacy 404 检查。
- [ ] 根域名 `/project`、`/project/*`、`/project-assets/*` 均为 404；www 对这些路径也为 404。
- [ ] www `/sukaseafood` 跳转根域名；review 页面和 API health 均为 200 且内容正确。
- [ ] 根目录、admin、merchant、card、mobile、docs 与 YGF API 既有公开路由保持健康。

## 浏览器验收

- [ ] 六账号分别完成首次改密；刷新后登录仍有效，退出后会话失效。
- [ ] 两名审核员并发取得不同图片；保存即时持久化；个人历史隔离；全员进度正确。
- [ ] Mao 是唯一可见“管理后台”的账号；鱼种、候选、复核、账号、导入与训练集同步均可用。
- [ ] 姓名/拒绝原因使用椭圆按钮；图片有加载转圈和外链失败状态。
- [ ] 浏览器直接加载批准的外部图片；服务器没有图片代理/缓存端点。
- [ ] 增量 CSV、取消后的部分离线回执与 20 MiB 上传边界按契约工作。

## 回滚

- [ ] 记录失败 revision、症状与时间，不记录密钥/令牌/原图 URL。
- [ ] review 应用恢复上一 revision；若数据库迁移已生效，使用发布前明确备份恢复。
- [ ] YGF 网关恢复上一健康 Caddy revision，但持续保留 Ocean 路径 404；不恢复已删除旧项目。
- [ ] 重新运行 review 内部/公开预检和全部 YGF 既有预检。
- [ ] 确认 PostgreSQL 备份仍可 `pg_restore --list`，并记录回滚后的 revision。
