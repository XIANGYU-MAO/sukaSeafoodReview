# SukaSeafood 审核系统生产部署实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 把独立审核系统安全部署到 dianshu-prod，通过 https://findai.top/sukaseafood/review 提供服务，并删除旧 /project 页面而不影响 YGF 其他线上功能。

**Architecture:** sukaSeafoodReview 使用自己的 Docker Compose、PostgreSQL 数据卷和备份卷。review-web 与 review-api 加入外部 sukaseafood-edge 网络，现有 YGF Caddy 也加入该网络并按路径代理。部署顺序为审核服务先上线并完成内网健康检查，再更新 YGF 网关；原图始终不经过服务器。

**Tech Stack:** Docker Engine 27+、Docker Compose 2.29+、PostgreSQL 16、Python 3.12、Node 22+、Nginx Alpine、Caddy 2.10、PowerShell、Bash

**Spec:** docs/superpowers/specs/2026-08-26-collaborative-review-system-design.md

## Global Constraints

- 目标 SSH 别名为 dianshu-prod；不得读取、复制、提交或输出私钥内容。
- 审核服务远程目录固定为 /opt/sukaseafood-review。
- 生产入口固定为 https://findai.top/sukaseafood/review。
- API 外部路径固定为 /sukaseafood/api/v1。
- review-postgres 不映射公网端口。
- 图片不代理、不缓存、不保存；服务器只处理网页、API、CSV 和回执。
- YGF 现有根目录、www、admin、merchant、card、mobile、docs 和 API 必须保持健康。
- 删除 YGF 的 server/deploy/ocean-project 文件和 Caddy 静态挂载。
- /project、/project/*、/project-assets/* 在根域名和 www 域名都返回 404。
- 只修改 YGF 当前干净的部署文件，不覆盖无关未提交改动。
- 每次数据库迁移和生产发布前先创建可验证的 PostgreSQL 备份。
- secrets、初始密码和生产 .env 不进入 Git 或部署日志。

---

## 涉及仓库

sukaSeafoodReview：

- Dockerfile、Compose、Nginx、备份、部署脚本和生产环境示例。

YGF sibling checkout：

- C:/Users/86166/Desktop/ygf/server/deploy/Caddyfile
- C:/Users/86166/Desktop/ygf/docker-compose.caddy.yml
- C:/Users/86166/Desktop/ygf/server/scripts/production_preflight.sh
- C:/Users/86166/Desktop/ygf/server/deploy/ocean-project/**

## Task 1: 容器化 API、网页和 PostgreSQL

**Files:**
- Create: api/Dockerfile
- Create: api/.dockerignore
- Create: web/Dockerfile
- Create: web/nginx.conf
- Create: web/.dockerignore
- Create: docker-compose.yml
- Create: docker-compose.production.yml
- Create: deploy/.env.example
- Create: tests/test_compose_config.py

**Interfaces:**
- Produces: review-api:8000
- Produces: review-web:8080
- Produces: review-postgres:5432，仅内部网络
- Produces: external Docker network sukaseafood-edge

- [ ] **Step 1: 写 Compose 配置测试**

~~~python
def test_production_compose_does_not_publish_postgres(compose_config):
    postgres = compose_config["services"]["review-postgres"]
    assert "ports" not in postgres
    assert "review-internal" in postgres["networks"]
    assert "sukaseafood-edge" not in postgres["networks"]

def test_only_web_and_api_join_edge(compose_config):
    assert "sukaseafood-edge" in compose_config["services"]["review-web"]["networks"]
    assert "sukaseafood-edge" in compose_config["services"]["review-api"]["networks"]
~~~

- [ ] **Step 2: 运行测试确认失败**

Run: pytest tests/test_compose_config.py -q

Expected: FAIL，Compose 文件尚不存在。

- [ ] **Step 3: 实现多阶段构建**

API 镜像安装生产依赖，以非 root 用户运行 uvicorn app.main:app --host 0.0.0.0 --port 8000，并以 /v1/health 作为健康检查。

Web 镜像在 Node 阶段执行 npm ci、typecheck、test 和 build；Nginx 阶段只复制 dist。nginx.conf 对剥离 /sukaseafood/review 前缀后的路径使用 try_files $uri /index.html，并提供 /healthz。

- [ ] **Step 4: 实现开发和生产 Compose**

生产服务：

~~~yaml
services:
  review-postgres:
    image: postgres:16-alpine
    networks: [review-internal]
  review-api:
    build: ./api
    networks: [review-internal, sukaseafood-edge]
  review-web:
    build: ./web
    networks: [sukaseafood-edge]

networks:
  review-internal:
    internal: true
  sukaseafood-edge:
    external: true
    name: sukaseafood-edge
~~~

数据库、会话、CSRF 和回执密钥从 deploy/.env 读取，不提供生产默认值。

- [ ] **Step 5: 运行容器测试并提交**

Run: docker compose -f docker-compose.yml config --quiet

Expected: exit 0。

Run: docker compose -f docker-compose.production.yml --env-file deploy/.env.example config --quiet

Expected: exit 0，示例值只用于 config 验证。

Run: pytest tests/test_compose_config.py -q

Expected: PASS。

~~~bash
git add api/Dockerfile web/Dockerfile web/nginx.conf docker-compose*.yml deploy/.env.example tests/test_compose_config.py
git commit -m "build: containerize collaborative review system"
~~~

## Task 2: 数据库迁移、备份和服务发布脚本

**Files:**
- Create: deploy/scripts/backup_postgres.sh
- Create: deploy/scripts/restore_postgres.sh
- Create: deploy/scripts/deploy_cloud.sh
- Create: deploy/scripts/production_preflight.sh
- Create: deploy/scripts/deploy_from_windows.ps1
- Create: deploy/scripts/first_deploy.sh
- Create: tests/test_deploy_scripts.py

**Interfaces:**
- Produces: deploy/scripts/deploy_from_windows.ps1 -SshHost dianshu-prod
- Produces: /opt/sukaseafood-review/backups/*.sql.gz
- Produces: review service internal health preflight

- [ ] **Step 1: 写脚本安全测试**

测试脚本文本必须：

- 使用固定 REMOTE_ROOT=/opt/sukaseafood-review；
- 不包含 rm -rf /opt 或未限定通配删除；
- 部署前调用 backup_postgres.sh；
- Alembic 成功后才切换 API；
- secrets 和 data 从 rsync --delete 中排除；
- SSH 默认 BatchMode=yes、ServerAliveInterval 和连接超时。

- [ ] **Step 2: 实现备份和恢复**

backup_postgres.sh 使用 pg_dump --format=custom 写临时文件，pg_restore --list 验证后原子改名。文件名包含 UTC 时间和 schema revision。保留最近 14 个每日备份和 8 个每周备份。

restore_postgres.sh 只接受 /opt/sukaseafood-review/backups 下经 realpath 验证的明确文件，恢复前再次备份当前库，不接受目录或 glob。

- [ ] **Step 3: 实现服务器内部发布**

deploy_cloud.sh：

1. docker compose config --quiet；
2. 运行数据库备份；
3. build review-api 和 review-web；
4. run --rm review-api alembic upgrade head；
5. up -d；
6. 等待容器健康；
7. curl review-api:8000/v1/health 和 review-web:8080/healthz；
8. 输出部署 revision。

- [ ] **Step 4: 实现 Windows SSH 发布**

deploy_from_windows.ps1 打包 Git HEAD 的 api、web、local_sync、deploy 和 Compose 文件；核对本地/远程 SHA-256；上传到 /tmp/sukaseafood-review-{revision}.tar；远程创建明确 stage 目录；同步到 /opt/sukaseafood-review；调用 deploy_cloud.sh；成功后删除本次明确命名的临时文件。

- [ ] **Step 5: 运行脚本测试并提交**

Run: pytest tests/test_deploy_scripts.py -q

Expected: PASS。

Run: powershell -NoProfile -File deploy/scripts/deploy_from_windows.ps1 -WhatIf

Expected: 只输出将打包和执行的步骤，不连接服务器。

~~~bash
git add deploy/scripts tests/test_deploy_scripts.py
git commit -m "ops: add safe review deployment and backups"
~~~

## Task 3: 给 YGF Caddy 加路径代理并删除旧项目

**Files:**
- Modify: C:/Users/86166/Desktop/ygf/server/deploy/Caddyfile
- Modify: C:/Users/86166/Desktop/ygf/docker-compose.caddy.yml
- Modify: C:/Users/86166/Desktop/ygf/server/scripts/production_preflight.sh
- Delete: C:/Users/86166/Desktop/ygf/server/deploy/ocean-project/**
- Create: tests/test_ygf_gateway_contract.py

**Interfaces:**
- Consumes: review-api、review-web on sukaseafood-edge
- Produces: root-domain review/API path routes
- Produces: legacy Ocean paths HTTP 404

- [ ] **Step 1: 写 YGF 网关契约测试**

~~~python
def test_caddy_routes_review_before_main_fallback(caddy_text):
    api_pos = caddy_text.index("/sukaseafood/api/*")
    web_pos = caddy_text.index("/sukaseafood/review*")
    fallback_pos = caddy_text.index("reverse_proxy admin-web:8080")
    assert api_pos < fallback_pos
    assert web_pos < fallback_pos

def test_legacy_project_is_404_and_not_mounted(caddy_text, compose_text):
    assert "respond @legacyOcean 404" in caddy_text
    assert "/srv/ocean-project" not in compose_text
~~~

- [ ] **Step 2: 验证删除目标和 YGF 工作区**

Run: git -C C:/Users/86166/Desktop/ygf status --short

Expected: Caddyfile、docker-compose.caddy.yml、production_preflight.sh 和 server/deploy/ocean-project 当前没有用户未提交改动。若其中任何目标有改动，停止并人工合并，不覆盖。

Run: git -C C:/Users/86166/Desktop/ygf ls-files server/deploy/ocean-project

Expected: 只列出要删除的旧 Ocean Project 文件。

- [ ] **Step 3: 修改 Caddy 和共享网络**

根域名处理顺序：

~~~caddy
@legacyOcean path /project /project/* /project-assets/*
respond @legacyOcean 404

handle_path /sukaseafood/api/* {
    reverse_proxy review-api:8000
}

handle_path /sukaseafood/review* {
    reverse_proxy review-web:8080
}

handle {
    redir https://{$DIANSHU_WEB_DOMAIN}{uri} permanent
}
~~~

www 域名先对 legacyOcean 返回 404，再把 /sukaseafood 和 /sukaseafood/* 永久跳转到 https://findai.top{uri}，然后才进入 YGF 其他处理。

docker-compose.caddy.yml 删除 ocean-project volume mount；gateway 同时加入原 YGF 网络和外部 sukaseafood-edge 网络。

- [ ] **Step 4: 删除旧项目并扩展生产预检**

先验证解析后的绝对路径严格等于 C:/Users/86166/Desktop/ygf/server/deploy/ocean-project，再从 Git 删除该目录。Git 历史仍可恢复，但线上发布包不再包含文件。

production_preflight.sh 新增：

~~~bash
curl --fail --silent --show-error https://findai.top/sukaseafood/review > /dev/null
test "$(curl --silent --output /dev/null --write-out '%{http_code}' https://findai.top/project)" = "404"
test "$(curl --silent --output /dev/null --write-out '%{http_code}' https://www.findai.top/project/seafood)" = "404"
~~~

保留现有所有 YGF 预检。

- [ ] **Step 5: 验证并提交 YGF 变更**

Run: docker compose --env-file server/.env -f docker-compose.cloud.yml -f docker-compose.caddy.yml config --quiet

Expected: exit 0。

Run: pytest C:/Users/86166/Desktop/sukaSeafoodReview/tests/test_ygf_gateway_contract.py -q

Expected: PASS。

~~~bash
git -C C:/Users/86166/Desktop/ygf add server/deploy/Caddyfile docker-compose.caddy.yml server/scripts/production_preflight.sh server/deploy/ocean-project
git -C C:/Users/86166/Desktop/ygf commit -m "feat(gateway): route SukaSeafood review and remove project"
~~~

## Task 4: 首次服务器初始化和生产密钥

**Files:**
- Modify: deploy/scripts/first_deploy.sh
- Create: deploy/OPERATIONS_ZH.md
- Create: tests/test_first_deploy.py

**Interfaces:**
- Produces: external network sukaseafood-edge
- Produces: /opt/sukaseafood-review/deploy/.env mode 0600
- Produces: six temporary passwords exactly once

- [ ] **Step 1: 写初始化幂等测试**

测试 first_deploy.sh 第二次运行不能重建数据库卷、覆盖 .env 或重置已有用户密码。缺少 SESSION_SECRET、CSRF_SECRET、RECEIPT_SECRET 或 POSTGRES_PASSWORD 时必须在服务器本机使用 openssl rand 生成。

- [ ] **Step 2: 实现服务器容量和依赖预检**

首次部署只读检查：

~~~bash
df -h /opt
free -m
docker version
docker compose version
docker network inspect sukaseafood-edge
~~~

网络不存在时 docker network create sukaseafood-edge。磁盘可用空间低于 5 GiB 或内存低于 1 GiB 时停止，不开始构建。

- [ ] **Step 3: 实现密钥和目录初始化**

创建 /opt/sukaseafood-review、backups、imports，所有权限定部署用户。服务器本机生成密钥并以 umask 077 写 deploy/.env；脚本输出密钥文件位置但不输出密钥值。

- [ ] **Step 4: 初始化固定账号**

数据库迁移后运行 python -m app.commands.seed_users --print-once。临时密码只在当前 SSH 终端显示一次，不写日志文件；操作者立即保存到密码管理器。再次运行返回“accounts already initialized”。

- [ ] **Step 5: 测试并提交**

Run: pytest tests/test_first_deploy.py -q

Expected: PASS。

~~~bash
git add deploy/scripts/first_deploy.sh deploy/OPERATIONS_ZH.md tests/test_first_deploy.py
git commit -m "ops: add idempotent production initialization"
~~~

## Task 5: 导入 1,221 条候选数据

**Files:**
- Create: deploy/scripts/import_candidates_from_windows.ps1
- Create: tests/test_import_deploy.py
- Modify: deploy/OPERATIONS_ZH.md

**Interfaces:**
- Consumes: C:/Users/86166/Desktop/SukaSeafood_CV_Dataset_Collector/output/candidates.csv
- Produces: production Candidate rows with current_reviewer_id NULL

- [ ] **Step 1: 写上传路径和 dry-run 测试**

脚本只允许 .csv 文件，计算 SHA-256，上传到 /opt/sukaseafood-review/imports/{sha256}.csv，不使用原始文件名拼接远程命令。

- [ ] **Step 2: 实现服务器 dry-run**

运行：

~~~bash
docker compose --env-file deploy/.env -f docker-compose.production.yml run --rm review-api +  python -m app.commands.import_candidates /imports/{sha256}.csv --dry-run --json-report /imports/{sha256}.report.json
~~~

下载报告到本机并显示 total=1221、各鱼种、各来源、无效行和重复行。dry-run 不写 Candidate。

- [ ] **Step 3: 实现显式 commit 参数**

只有传入 -Commit 且 dry-run invalid_count=0 时才执行正式导入。正式导入后查询数据库总数，预期首次为 1221；所有 current_reviewer_id 为 NULL。

- [ ] **Step 4: 验证抽样地址**

从 Fish-Vista、iNaturalist、GBIF、Commons 各抽取至少一条，API 返回 source_url、preview_url、original_url、license 和 attribution。服务器不请求这些 URL。

- [ ] **Step 5: 测试并提交**

Run: pytest tests/test_import_deploy.py -q

Expected: PASS。

~~~bash
git add deploy/scripts/import_candidates_from_windows.ps1 deploy/OPERATIONS_ZH.md tests/test_import_deploy.py
git commit -m "ops: add production candidate import workflow"
~~~

## Task 6: 分阶段上线、浏览器验收和回滚

**Files:**
- Create: deploy/RELEASE_CHECKLIST_ZH.md
- Create: tests/test_public_routes.py

**Interfaces:**
- Produces: 可重复执行的上线和回滚清单。

- [ ] **Step 1: 部署审核服务但暂不修改网关**

在服务器创建 sukaseafood-edge，运行 first_deploy.sh 和 review deploy。通过 docker exec gateway 或临时加入网络，从 Caddy 容器验证 review-api:8000/v1/health 和 review-web:8080/healthz。

- [ ] **Step 2: 备份并发布 YGF 网关变更**

记录 YGF 当前 revision，运行 YGF 现有 deploy_from_windows.ps1。发布脚本先备份 YGF PostgreSQL 和源码。Caddy reload 成功后执行全部原有 YGF preflight 和新增审核路径检查。

- [ ] **Step 3: 运行公开路由测试**

~~~python
@pytest.mark.parametrize("url", [
    "https://findai.top/project",
    "https://findai.top/project/seafood",
    "https://findai.top/project-assets/app.js",
    "https://www.findai.top/project",
])
def test_removed_project_is_404(url):
    assert requests.get(url, timeout=15, allow_redirects=False).status_code == 404
~~~

还要验证 review=200、API health=200、www SukaSeafood 路径跳转到根域名，以及全部 YGF 既有公开路由。

- [ ] **Step 4: 使用真实浏览器完成验收**

按六账号逐项验证首次改密、刷新保持登录、两人同时取得不同图片、即时保存、全员进度、个人历史隔离、Mao 中文后台、姓名和拒绝原因椭圆按钮、图片加载转圈、外链失败状态、增量 CSV。

- [ ] **Step 5: 验证备份和回滚**

执行一次 review pg_dump 并用 pg_restore --list 验证。回滚审核应用时恢复上一 review revision 和迁移前数据库备份；回滚 YGF 网关时恢复上一 Caddy revision，但继续保留 /project 404，不恢复已删除旧项目。再次运行 YGF 和 review preflight。

- [ ] **Step 6: 提交上线清单**

~~~bash
git add deploy/RELEASE_CHECKLIST_ZH.md tests/test_public_routes.py
git commit -m "docs: add production release and rollback checklist"
~~~
