# SukaSeafood 多人协作审核系统

[English](README.md)

## 系统成果

本仓库提供完整的 SukaSeafood 多人协作审核 Web/API 和 Windows 本地同步工具：六个固定账号 Hassan、Mao、Xinhui、Wahid、Sharmaa、Yiming 共享同一待审核池，其中 Mao 是唯一管理员。系统不设个人配额，同一候选不会交给已经审核过它的成员；每次选择 KEEP、REJECT 或 UNSURE 后立即写入数据库，收到数据库确认后才取下一张。

成员只能查看和修改自己的当前审核历史，但都能看汇总进度。Mao 使用标题为“管理后台”的固定中文七标签后台管理进度、候选图片、动态鱼种、审核历史、CSV 导入、训练集同步批次和账号。服务器只保存结构化候选信息、外部 URL、审核结果、受限 CSV 与回执，不保存、缓存、代理或下载原图字节。Windows 本地同步工具已经实现，由 Mao 的电脑直接访问获准的外部来源；详见 [`local_sync/README_ZH.md`](local_sync/README_ZH.md)。

## 仓库与当前开发上下文

截至 2026-08-27，当前实现仅存在于本地，尚未发布：分支是 `codex/collaborative-review`，工作树位于 `C:\Users\86166\Desktop\sukaSeafoodReview\.worktrees\collaborative-review`。远端 `origin` 当前没有已发布引用；`https://github.com/XIANGYU-MAO/sukaSeafoodReview.git` 只是目标地址。`.worktrees` 是本机 Git 隔离实现细节，不是运行时要求。

只有该分支被显式合并、推送和发布后，其他使用者才可以执行以下 clone；当前不能把它当成可运行的获取方式：

```powershell
git clone https://github.com/XIANGYU-MAO/sukaSeafoodReview.git
Set-Location .\sukaSeafoodReview
```

在此之前，本机命令必须从上述当前 checkout 的仓库根目录运行。不要复制或依赖另一台机器的 `.worktrees` 目录。本文描述当前已实现代码，不表示已合并、已推送或已生产发布。

## 架构与数据流

- `web/`：React 19、TypeScript、Vite；生产构建基址和浏览器路由基址固定为 `/sukaseafood/review/`。
- `api/`：FastAPI、SQLAlchemy async、Alembic；应用内部路由从 `/v1` 开始。
- `collector/`：Mao 的 Windows 本地候选元数据采集器，读取当前鱼种配置并写入 `collector/output/candidates.csv` 供审核导入。
- `local_sync/`：独立的已批准原图下载器，包含 CLI/Tkinter Windows 同步器、可恢复索引与冻结构建；使用说明见 [`local_sync/README_ZH.md`](local_sync/README_ZH.md)。
- `deploy/` 与两个 Compose 文件：固定路径的生产备份、恢复、首次部署、预检、导入和回滚构件。
- 开发浏览器入口：`http://localhost:5173/sukaseafood/review/`。Vite 只把 `/sukaseafood/api` 重写到本机 FastAPI 根路径，因此网页仍使用 `/sukaseafood/api/v1`。
- 规划中的生产入口：`https://findai.top/sukaseafood/review`；外部 API 前缀固定为 `/sukaseafood/api/v1`。
- 浏览器从 API 取得候选元数据，再通过外部 HTTPS URL 直接加载图片。图片字节不经过中国服务器，也不写入服务器数据库或磁盘。
- 审核提交带 CSRF 和 Idempotency-Key；API 在数据库事务确认后返回回执，网页验证回执再刷新汇总并取下一张。

本系统没有图片上传、原图代理或原图下载 API。已准备的 YGF 发布会删除 `/project`、`/project/*` 与 `/project-assets/*`，同时保留其他 YGF 页面；这些网关改动尚未部署，线上路由在获得显式授权并发布前不会改变。

## 环境要求

- Windows PowerShell 7（Windows PowerShell 5.1 也可执行下列基础命令）。
- Python 3.12。
- Node.js 22.12 或更高版本，配套 npm。
- API 测试/开发可使用 SQLite；生产业务数据库只使用 PostgreSQL。本地同步器另有一个小型 SQLite 恢复索引，只保存候选图片同步代次、相对路径、哈希、回执状态和恢复意图，不保存图片字节、原图 URL 或批次 token。
- 生产必须使用 PostgreSQL 16、HTTPS，并把 `SECURE_COOKIE` 设为 `true`。生产配置会拒绝 SQLite 和不安全 Cookie。

如需运行真实 PostgreSQL 并发测试，请准备一个可清空的独立 PostgreSQL 16 测试数据库。绝不能把测试指向生产数据库。

## Windows 本地快速启动

以下命令都从仓库根目录开始。先创建 API 虚拟环境并复制安全示例：

```powershell
Set-Location .\api
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
Set-Location ..
Copy-Item .\api\.env.example .\api\.env
```

`api/.env` 只供本机使用。把三个 `change-me-*` 值替换为彼此不同的本地随机值，不要提交该文件，也不要复用生产值。

终端 1：加载 `.env` 到当前 PowerShell 进程，迁移、初始化六账号并启动 API：

```powershell
Get-Content .\api\.env | Where-Object { $_ -match '^[^#][^=]*=' } | ForEach-Object {
    $envEntry = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($envEntry[0].Trim(), $envEntry[1].Trim(), 'Process')
}
Set-Location .\api
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.commands.seed_users --print-once
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

新数据库包含六个账号，但初始鱼种目录为空。Mao 在中文管理后台创建并维护任意当前鱼种后，再采集或导入候选。账号首次 seed 会在终端中各显示一次六个临时密码；立即安全保存并分发。相同数据库再次运行不会输出或替换密码。

终端 2：安装网页依赖并启动 Vite：

```powershell
Set-Location .\web
npm install
npm run dev
```

浏览器打开 `http://localhost:5173/sukaseafood/review/`。开发示例明确使用 `APP_ENV=development`、SQLite 和 `SECURE_COOKIE=false`，因为普通 `http://localhost` 不会发送 Secure Cookie；此设置只能用于本机 HTTP 开发。

## 账号、密码与会话

公开姓名顺序固定为 Hassan、Mao、Xinhui、Wahid、Sharmaa、Yiming；除 Mao 为 `admin` 外，其余均为 `reviewer`。不支持注册、社交登录或自定义账号。首次登录必须修改临时密码，修改成功会撤销全部会话并返回登录页；管理员重置成员密码也会撤销该成员会话。

会话保存在数据库，浏览器只接收 HttpOnly、SameSite=Lax、`Path=/sukaseafood` Cookie。刷新页面通过 `/sukaseafood/api/v1/auth/me` 恢复会话。生产必须在 HTTPS 下使用 `SECURE_COOKIE=true`；`SECURE_COOKIE=false` 会被生产配置拒绝。登录是未认证入口；登录成功后，浏览器发起的已认证状态变更使用会话和同一会话派生的 CSRF，审核提交还要求每个具体操作独立的 Idempotency-Key。本地同步工具向 `/v1/sync/batches/{batch_id}/receipt` 提交回执时使用批次 token，不使用浏览器会话或 CSRF。

临时密码和重置密码只显示一次。不要把密码、会话 Cookie、CSRF、回执密钥、数据库 URL 或 SSH 凭据写进 Git、截图、日志或问题单。

## 采集与导入

Mao 的正常四步流程是：（1）在中文管理后台维护当前鱼种，（2）下载采集器和当前配置，（3）在本机运行 `collector/`，生成 `C:\Users\86166\Desktop\sukaSeafoodReview\collector\output\candidates.csv`，（4）预检查并显式提交 CSV。初始目录为空；Mao 可以在采集前创建任意有效的当前鱼种。

采集器输出可以有任意有效行数和任意受支持来源组合。在已经加载 API 环境变量的终端执行只读 dry-run：

```powershell
Set-Location .\api
.\.venv\Scripts\python.exe -m app.commands.import_candidates 'C:\Users\86166\Desktop\sukaSeafoodReview\collector\output\candidates.csv' --dry-run
```

dry-run 不写候选、不生成可提交预览令牌。它必须报告零个 blocking error 且 `can_commit=true`，Mao 才能显式提交。确认报告后，Mao 可以在中文后台“采集与导入”标签先预检查再显式确认 commit；生产帮助脚本同样先 dry-run，再以 `--commit` 事务性复核并写入，随后把返回的 `file_sha256` 与本机 CSV 核对，并打印 `total`、`inserted`、`skipped_exact` 和 `possible_url_duplicates`。CLI 精确重复导入是幂等的。网页预览令牌仅保存在当前页面内存并有过期时间；新文件、终端冲突或成功提交会使旧令牌失效。

## 审核成员工作流

1. 选择固定姓名并登录；首次登录先改密码。
2. 首页从共享池恢复或取得一张尚未由该成员审核的候选图。
3. 图片加载期间显示转圈；失败会进入有限错误状态，可重试或选择“图片链接不可用”。
4. 查看中英文鱼种名、学名、来源、来源记录、许可与安全外链。应用只把 URL 交给浏览器，不抓取图片。
5. 选择 KEEP、REJECT 或 UNSURE。REJECT 必须选择椭圆形原因；“其他”必须输入说明。K/R/U 快捷键不会劫持输入控件。
6. 网页立即提交并等待数据库回执；回执身份、内容和版本验证成功后才刷新进度并取下一张，没有额外保存按钮。
7. “历史记录”只请求当前成员自己的记录，URL 不带 reviewer 查询参数。只有当前版本可编辑；旧版本只读，409 冲突不会静默覆盖。

汇总进度只包含数量和六名成员的聚合，不包含备注、图片 URL、候选 ID、审核 ID或个人历史条目。成员工作量按所有已提交尝试计数；总体进度描述当前活跃数据，因此 Mao 重新打开记录后两种总计可能不同。

## 七标签中文管理后台

管理页面的通用标题是“管理后台”，其唯一授权账号仍是 Mao。Mao 登录后看到固定中文界面，普通成员直接访问 `/admin` 会在发出任何管理请求前返回审核首页。七个标签是：

1. 审核进度：全组聚合和当前占用。
2. 候选图片：筛选、修正安全元数据、释放或转交尚未提交的当前图片。
3. 鱼种管理：新增、编辑、停用和重新启用 Windows 安全的不可变鱼种代码。初始目录为空；导入 SF006 或任何其他当前鱼种的行之前，Mao 应先在此添加符合安全代码规则的目录项。
4. 审核历史：跨成员筛选、受版本保护的修正，以及指定从未审核该候选的活跃成员重新审核。
5. 采集与导入：四步采集器流程、CSV 预检查与原子 commit。
6. 训练集同步：查看待处理数、创建不可变增量批次、下载小型 CSV、上传 JSON 回执文件。
7. 账号：查看固定目录并重置普通成员密码；不在网页重置 Mao。

浏览器中的管理写操作需要 Mao 会话、CSRF 和各 API 规定的必要确认。只有候选、鱼种、审核历史、账号等请求模型包含 `reason` 的管理数据操作才要求并审计明确原因；导入预览/commit、导出批次和回执按各自的 token、确认及认证合同执行，不虚构 `reason`。后台不会显示原始服务器错误、失败回执自由文本、导入令牌或已关闭的一次性密码。

## 增量 CSV 与本地下载边界

服务器按一个统一封套生成增量批次：每批最多 10,000 行，精确 16 列 CSV 序列化后最多 20 MiB，在线回执与离线回执上传也最多 20 MiB。超过 10,000 个待处理项会被拆成互不重叠的后续批次；任意一行本身导致超限时会在持久化批次前失败。CSV 按同一 PostgreSQL 快照产生 ADD、REMOVE 或 MOVE，并携带服务器决定的精确目标相对路径和单调的**候选图片同步代次**。为兼容现有格式，CSV 固定列名仍为 `review_version`；它表示候选图片同步代次，不是审核成员的编辑次数。CSV 下载是同源认证、`no-store` 的附件响应；回执是有界 `application/json` POST。

Alembic 修订 `20260827_07` 在创建导出所使用的同一 PostgreSQL 串行化边界内开启新的同步纪元。它把每个候选的代次提高到大于该候选当前值以及审核、审核修订和导出项中的全部历史值；整数空间耗尽时拒绝迁移，并使所有修订前的待处理批次过期，避免同一批次混用新旧语义。已有本地训练集无需破坏性重置：升级后的第一个代次自然大于任何合法的升级前本地值。

独立 `local_sync` 包、CLI/Tkinter 和 Windows 可执行文件已经实现。工具在 Mao 的电脑上直接访问每个获准的 `original_url`，验证每次重定向、图片内容和哈希，使用 `.part` 加原子改名幂等续跑，并把 REMOVE 移到可恢复 `_removed` 路径。只允许配置的精确主机/域名后缀；可用 `IMAGE_ORIGIN_ALLOWLIST` 配置服务器，用 `SUKASEAFOOD_IMAGE_ORIGIN_ALLOWLIST` 配置本机同步器。禁止 localhost、IP 字面量和未批准来源。配置代理只代表信任该代理去连接经过批准的主机名；工具不会把 Cookie 或凭据发送到图片来源。中国服务器从不向图片来源发起 HEAD/GET，也没有图片缓存或代理。

取消或网络中断时，已经安全完成的操作会留在本地索引；工具保存 `download_receipt-{batch_id}.json` 离线回执，网络恢复后可重传。较旧的候选图片同步代次重放不能覆盖较新的图片、索引行或规范清单行。本地 SQLite schema v3 只记录同步代次、哈希、路径和有界的替换恢复意图。同路径替换只有在 SQLite 证明候选拥有该精确路径、且磁盘 SHA-256 仍与上一代次一致时才允许；否则工具保持文件不变并报告冲突。中断后，工具会利用已验证的暂存图片和 `_removed/{batch_id}/` 备份继续恢复；新暂存不可用时则恢复经过验证的旧图片。具体操作和安全恢复流程见 [`local_sync/README_ZH.md`](local_sync/README_ZH.md)。不要手工伪造成功回执。

## 验证命令

SQLite 后端全量、编译和迁移检查：

```powershell
Set-Location .\api
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall app tests alembic
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic check
```

只在可丢弃的开发/测试数据库上验证 downgrade/re-upgrade：

```powershell
.\.venv\Scripts\python.exe -m alembic downgrade -1
.\.venv\Scripts\python.exe -m alembic upgrade head
```

真实 PostgreSQL 测试（占位符必须替换为隔离测试数据库，不能使用生产库）：

```powershell
$env:TEST_POSTGRES_URL = 'postgresql+asyncpg://<test-user>:<test-password>@127.0.0.1:<port>/<test-db>'
.\.venv\Scripts\python.exe -m pytest -q
```

网页全量、类型检查和生产构建：

```powershell
Set-Location ..\web
npm test
npm run typecheck
npm run build
```

本地同步器测试、编译和锁定构建：

```powershell
Set-Location ..\local_sync
python -m pytest -q
python -m compileall src tests
Set-Location ..
powershell -NoProfile -ExecutionPolicy Bypass -File local_sync/scripts/build_windows.ps1
```

生产构建资产必须保持在 `/sukaseafood/review/assets/`。部署构件已实现并在本机验证，但未执行生产 SSH 部署或公开验收。未执行线上部署；上线、Caddy reload、六账号浏览器验收和回滚演练都需要显式授权。

## 故障排查

- 本机登录后仍是 401：确认 API 和 Vite 都在运行，浏览器使用 `http://localhost:5173/sukaseafood/review/`，本地 `.env` 是 `APP_ENV=development` 与 `SECURE_COOKIE=false`。重启 API 前要在同一终端重新加载 `.env`。
- 生产配置因 Cookie 启动失败：生产只能使用 HTTPS 和 `SECURE_COOKIE=true`；不要为了绕过检查关闭安全 Cookie。
- 401 表示没有有效会话或会话已撤销；403 通常表示角色、首次改密或 CSRF 边界不满足。刷新后重新登录，不要复制另一个会话的 CSRF。
- 外部图片被屏蔽或损坏：检查浏览器网络、来源站、HTTPS 和内容拦截器。服务器不会代理图片；使用页面的重试或“图片链接不可用”，必要时由 Mao 修正 URL。
- 来源采集遇到 429：Wikimedia/GBIF/iNaturalist 等原始采集与重试属于本地 `collector/`，不是审核服务器职责。登录 API 自身的 429 是认证限流，也应等待后重试。
- PostgreSQL 集成测试显示 skipped：设置 `TEST_POSTGRES_URL` 指向独立 PostgreSQL 16 测试库；SQLite 无法证明行锁、SKIP LOCKED 或竞争语义。
- 导入返回 409：预览可能过期、已提交、属于另一个会话或文件状态已变化；重新选择文件并预检查，不要复用旧令牌。
- 回执返回 409/422：确认 batch、review ID、版本、状态和服务器给出的精确路径匹配；重新取得当前批次，不要把冲突当成功。

## 仓库结构与后续阶段

```text
api/                         FastAPI、模型、迁移、CLI 与后端测试
web/                         React/Vite 网页与 Web 测试
collector/                   Windows 候选元数据采集器与测试
local_sync/                  独立的已批准原图下载器、测试、构建与中文手册
deploy/                      生产脚本、环境模板、操作与回滚清单
docs/superpowers/specs/      已批准系统设计
docs/superpowers/plans/      核心、本地同步和生产部署计划
```

- 系统设计：`docs/superpowers/specs/2026-08-26-collaborative-review-system-design.md`
- 当前采集器权威：`docs/superpowers/specs/2026-08-27-dynamic-collector-admin-integration-design.md`
- 动态采集器集成计划：`docs/superpowers/plans/2026-08-27-dynamic-collector-admin-integration.md`
- 核心实施计划：`docs/superpowers/plans/2026-08-26-collaborative-review-core.md`
- Windows 本地同步实施计划：`docs/superpowers/plans/2026-08-26-local-training-sync.md`（代码与冻结构建流程已实现）
- 生产部署与 YGF 路由计划：`docs/superpowers/plans/2026-08-26-production-deployment.md`（构件和隔离网关提交已准备，未上线）

生产 Compose、镜像、备份/恢复、首次部署、预检、导入和回滚构件均已准备并完成本机静态/构建验证；隔离的 YGF 发布也已准备删除 `/project` 并接入 `/sukaseafood/review` 与 `/sukaseafood/api/v1`。当前分支没有执行 SSH、推送、部署、Caddy reload、生产数据导入或公开验收；所有外部操作仍须用户显式授权。本文不包含真实服务器、SSH、数据库、生产密码或密钥值。
