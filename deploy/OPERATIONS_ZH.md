# SukaSeafood 审核系统生产运维

本指南对应固定服务器目录 `/opt/sukaseafood-review`。生产数据库只使用 PostgreSQL；网页、API、数据库、备份与导入文件由独立 Compose 管理。服务器不下载、不代理、不缓存、不保存候选原图：浏览器和 Mao 的 Windows 本地同步工具直接访问经过批准的外部图片来源。

## 安全边界

- 线上入口是 `https://findai.top/sukaseafood/review`，API 前缀是 `https://findai.top/sukaseafood/api/v1`。
- `review-postgres` 只加入内部网络，不发布端口；只有 `review-api` 与 `review-web` 加入外部 `sukaseafood-edge`。
- `deploy/.env` 只在服务器生成，权限必须为 `0600`。不要复制、粘贴或记录其中的 `POSTGRES_PASSWORD`、`SESSION_SECRET`、`CSRF_SECRET`、`RECEIPT_SECRET`。
- PostgreSQL 保存审核业务数据、CSV 快照和回执状态，不保存图片字节。Windows SQLite 只保存断点续传/索引状态，不保存图片 URL、批次令牌或图片字节。
- 图片域名采用可配置的精确域名/后缀白名单；每次跳转都在本地下载前验证。配置代理只能为已经批准的主机名提供 DNS 与传输，不能扩展白名单。

## 首次初始化

确认部署包已经安全同步到固定目录后，在服务器终端执行：

```bash
sudo /opt/sukaseafood-review/deploy/scripts/first_deploy.sh <review-git-revision>
```

脚本先检查 `/opt` 至少有 5 GiB 可用空间、内存至少 1 GiB、Docker/Compose 可用，并检查或创建 `sukaseafood-edge`。它以 `umask 077` 在服务器本机生成四个独立密钥，写入 `deploy/.env` 并设为 `0600`，不会输出密钥值，也不会覆盖已有文件或删除数据库卷。

固定账号恰好六个：Hassan、Xinhui、Wahid、Sharmaa、Yiming 为 reviewer，Mao 为唯一 admin。首次初始化只在当前 SSH 终端显示一次临时密码；立即存入密码管理器。再次执行时显示 `accounts already initialized`，不重置密码。

## 日常发布与预检

Windows 端从已提交的 Git HEAD 发布：

```powershell
powershell -NoProfile -File deploy/scripts/deploy_from_windows.ps1 -SshHost dianshu-prod
```

脚本仅归档 Git HEAD，核对本地/远程 SHA-256，在明确的 `/tmp/sukaseafood-review-<revision>.tar` 与 stage 目录展开，并用 rsync 保留 `deploy/.env`、`backups`、`imports`。服务器发布依次执行 Compose 校验、PostgreSQL 备份、镜像构建、Alembic 迁移、服务切换及内容健康检查。`-WhatIf` 只打印计划，不读取密钥、不连接网络。

内部预检：

```bash
/opt/sukaseafood-review/deploy/scripts/production_preflight.sh
```

只有在网关发布已获明确授权后，才运行公开检查：

```bash
/opt/sukaseafood-review/deploy/scripts/production_preflight.sh --public
```

## 备份与恢复

每次迁移/发布前自动运行备份；也可以手动执行：

```bash
/opt/sukaseafood-review/deploy/scripts/backup_postgres.sh
```

备份使用 PostgreSQL custom format，先写临时文件，经 `pg_restore --list` 验证后原子改名。保留最近 14 个每日备份和 8 个每周备份。输出只包含备份路径，不包含连接密码。

恢复必须指定 `backups` 内经过 `realpath` 验证的单一文件，不接受目录、通配符或目录外路径。恢复前脚本再次备份当前数据库：

```bash
/opt/sukaseafood-review/deploy/scripts/restore_postgres.sh \
  /opt/sukaseafood-review/backups/review-YYYYMMDDTHHMMSSZ-revision-daily.sql.gz \
  --confirm-restore
```

恢复结束后重新运行预检。任何迁移前备份都必须先用 `pg_restore --list` 验证。

## 导入 1,221 条候选

候选源文件只从受控 Windows 路径读取。先 dry-run：

```powershell
powershell -NoProfile -File deploy/scripts/import_candidates_from_windows.ps1
```

脚本拒绝非 `.csv`、目录和 reparse point，计算 SHA-256，并上传为 `/opt/sukaseafood-review/imports/<sha256>.csv`；原始文件名不会进入远程命令。报告必须显示 `total=1221`、blocking errors 为 0，并列出各鱼种、Fish-Vista、iNaturalist、GBIF、Commons 来源、无效项和重复项。dry-run 不写 Candidate。

人工复核报告后显式提交：

```powershell
powershell -NoProfile -File deploy/scripts/import_candidates_from_windows.ps1 -Commit
```

提交命令在一个数据库事务内重新验证；首次应插入 1,221 条，所有 `current_reviewer_id` 均为 NULL。重复运行只报告 exact duplicates，不重复插入。抽样只读取 source_url、preview_url、original_url、license、attribution；服务器不会请求这些外部图片地址。

## 批次与离线回执

单个增量 CSV 最多 10,000 行且不超过 20 MiB；超过任一边界会拆成后续非重叠批次。在线回执与后台离线回执 JSON 都最多 20 MiB，每项保持七个字段。CSV 含批次令牌，下载响应禁止缓存；令牌不得进入日志、工单或聊天。取消后，本地工具先把已成功项目合并到 `canonical_manifest.csv`，再保存可上传的部分回执。

## 故障处理

1. 记录当前 review 与 YGF revision，不记录任何密钥。
2. 运行内部预检并查看容器健康与受限日志。
3. 应用回滚先恢复上一 review revision；若迁移改变了数据库，再恢复迁移前已验证备份。
4. 网关回滚恢复上一 Caddy revision，但继续让 `/project`、`/project/*`、`/project-assets/*` 返回 404，不恢复 Ocean 文件。
5. 再次执行 review 与 YGF 全部预检。生产 SSH、Caddy reload、公开验收均需要单独明确授权。
