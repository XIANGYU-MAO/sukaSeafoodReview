# SukaSeafood 线上协作审核核心实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 构建带固定账号、共享待审核池、即时保存、全员进度、个人历史和 Mao 中文后台的线上审核 API 与网页。

**Architecture:** 在独立 sukaSeafoodReview 仓库中新增 api 和 web。FastAPI 负责会话、权限、候选图片、审核、进度、管理和增量清单接口；React 网页通过同源 /sukaseafood/api/v1 调用 API。PostgreSQL 保存唯一当前审核和当前查看人，外部图片由浏览器直接加载。

**Tech Stack:** Python 3.12、FastAPI 0.116+、SQLAlchemy 2、Alembic、PostgreSQL 16、Argon2id、pytest、React 19、TypeScript、Vite、Vitest、Testing Library

**Spec:** docs/superpowers/specs/2026-08-26-collaborative-review-system-design.md

## Global Constraints

- 固定账号严格为 Hassan、Mao、Xinhui、Wahid、Sharmaa、Yiming；Mao 是唯一管理员。
- 不开放注册；随机初始密码首次登录强制修改；正常刷新保持登录；会话默认 12 小时。
- 所有候选图片进入共享待审核池，不平均分配、不设置个人配额、不使用自动租约或心跳。
- 每名成员同一时间最多保留一张当前图片；不同成员不能同时取得同一 candidate_id。
- KEEP、REJECT、UNSURE 点击后立即保存；数据库成功前不能进入下一张。
- 全员可见总进度和每位成员的汇总数字；普通成员看不到他人的具体历史。
- 登录姓名和拒绝原因使用椭圆单选按钮；鱼种、来源、状态筛选继续使用下拉框。
- 审核页支持中文和英文；Mao 后台固定中文。
- review preview_url 由浏览器直连外部 HTTPS 地址；API 不代理、不缓存图片。
- 原图不在本计划下载；本计划只生成小型增量 CSV 并接收回执。
- 所有业务代码位于独立 sukaSeafoodReview 仓库，不修改消费者 Flutter App 或现有 SukaSeafood backend。

---

## 文件结构

新增后端：

- api/app/main.py：FastAPI 工厂和中间件。
- api/app/config.py：环境配置。
- api/app/database.py：异步数据库引擎和会话。
- api/app/models/*.py：账号、候选图、审核、审计和导出表。
- api/app/schemas/*.py：API 输入输出契约。
- api/app/services/*.py：认证、共享池、审核、进度、后台、导入和导出业务规则。
- api/app/api/dependencies.py：当前用户、管理员和 CSRF 依赖。
- api/app/api/routes/*.py：HTTP 路由。
- api/app/commands/*.py：初始化账号和导入旧清单命令。
- api/alembic/*：数据库迁移。
- api/tests/*：后端单元和接口测试。

新增网页：

- web/src/api/*：类型和 HTTP 客户端。
- web/src/auth/*：登录状态。
- web/src/components/*：椭圆单选组、图片加载区、进度和布局。
- web/src/pages/*：登录、审核、历史和后台。
- web/src/i18n/*：稳定代码到中英文显示值。
- web/src/**/*.test.tsx：组件和页面测试。

## Task 1: 建立独立 FastAPI 骨架

**Files:**
- Create: api/app/__init__.py
- Create: api/app/main.py
- Create: api/app/config.py
- Create: api/app/database.py
- Create: api/app/api/__init__.py
- Create: api/app/api/routes/__init__.py
- Create: api/app/api/routes/health.py
- Create: api/tests/conftest.py
- Create: api/tests/test_health.py
- Create: api/requirements.txt
- Create: api/requirements-dev.txt
- Create: api/pytest.ini

**Interfaces:**
- Produces: create_app(settings: Settings | None = None) -> FastAPI
- Produces: get_db() -> AsyncIterator[AsyncSession]
- Produces: GET /v1/health -> {"status": "ok"}

- [ ] **Step 1: 写失败的健康检查测试**

~~~python
def test_health(client):
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
~~~

- [ ] **Step 2: 运行测试确认失败**

Run: cd api && pytest tests/test_health.py -q

Expected: FAIL，原因是 app.main 或 create_app 尚不存在。

- [ ] **Step 3: 实现最小应用工厂和配置**

~~~python
def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = settings or get_settings()
    app = FastAPI(title=runtime.app_name)
    app.include_router(health.router, prefix="/v1")
    return app
~~~

Settings 必须包含 DATABASE_URL、SESSION_COOKIE_NAME、SESSION_HOURS、SESSION_SECRET、CSRF_SECRET 和 APP_ENV。测试 fixture 使用独立 SQLite 文件，生产配置拒绝 SQLite。

- [ ] **Step 4: 运行健康检查和静态导入验证**

Run: cd api && pytest tests/test_health.py -q

Expected: 1 passed。

Run: cd api && python -m compileall app

Expected: exit 0。

- [ ] **Step 5: 提交骨架**

~~~bash
git add api
git commit -m "feat(review): scaffold isolated review API"
~~~

## Task 2: 建立数据库模型和首个 Alembic 迁移

**Files:**
- Create: api/app/models/__init__.py
- Create: api/app/models/auth.py
- Create: api/app/models/catalog.py
- Create: api/app/models/review.py
- Create: api/app/models/audit.py
- Create: api/app/models/export.py
- Create: api/alembic.ini
- Create: api/alembic/env.py
- Create: api/alembic/versions/20260826_01_initial.py
- Create: api/tests/test_model_constraints.py

**Interfaces:**
- Produces: User、Session、Species、Candidate、Review、ReviewRevision、AuditEvent、IdempotencyCommand、ExportBatch、ExportItem ORM 类。
- Candidate.current_reviewer_id: UUID | None
- Review.is_current: bool；每个 candidate_id 最多一条 is_current=true
- Candidate 唯一键: (source_dataset, source_record_id)

- [ ] **Step 1: 写模型约束测试**

~~~python
async def test_candidate_has_only_one_current_review(session, candidate, reviewer):
    session.add(Review(candidate_id=candidate.id, reviewer_id=reviewer.id, decision="APPROVED", is_current=True))
    await session.commit()
    session.add(Review(candidate_id=candidate.id, reviewer_id=reviewer.id, decision="REJECTED", is_current=True))
    with pytest.raises(IntegrityError):
        await session.commit()
~~~

再写测试验证旧 Review 设置 is_current=false 后可以插入新的当前 Review、相同 source_dataset 与 source_record_id 不能重复，以及 current_reviewer_id 可以为空。

- [ ] **Step 2: 运行约束测试确认失败**

Run: cd api && pytest tests/test_model_constraints.py -q

Expected: FAIL，原因是 ORM 类尚不存在。

- [ ] **Step 3: 实现模型和枚举**

使用字符串枚举：

~~~python
class Decision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    UNSURE = "UNSURE"

class ExportAction(StrEnum):
    ADD = "ADD"
    REMOVE = "REMOVE"
    MOVE = "MOVE"
~~~

Candidate 保存 preview_url、original_url、source_url、license、attribution、metadata_json、current_reviewer_id、current_started_at、active 和 version。Review 保存 rejection_reason、notes、whole_fish、exact_species_verified、is_current 和 version。数据库使用部分唯一索引保证每个 candidate_id 最多一条 is_current=true。ReviewRevision 自身保存 candidate_id、review_id、reviewer_id 和完整快照，不能因当前 Review 状态变化而丢失。

- [ ] **Step 4: 编写并执行首个迁移**

Run: cd api && alembic upgrade head

Expected: 所有表、唯一键和外键创建成功。

Run: cd api && pytest tests/test_model_constraints.py -q

Expected: PASS。

- [ ] **Step 5: 提交数据模型**

~~~bash
git add api/app/models api/alembic api/tests/test_model_constraints.py
git commit -m "feat(review): add review database schema"
~~~

## Task 3: 固定账号、密码和会话认证

**Files:**
- Create: api/app/schemas/auth.py
- Create: api/app/services/auth.py
- Create: api/app/api/dependencies.py
- Create: api/app/api/routes/auth.py
- Create: api/app/commands/seed_users.py
- Create: api/app/commands/reset_password.py
- Create: api/tests/test_auth.py

**Interfaces:**
- Produces: hash_password(password: str) -> str
- Produces: verify_password(password: str, encoded: str) -> bool
- Produces: POST /v1/auth/login
- Produces: POST /v1/auth/change-password
- Produces: POST /v1/auth/logout
- Produces: GET /v1/auth/me
- Produces: GET /v1/auth/names

- [ ] **Step 1: 写固定名单和首次改密测试**

~~~python
def test_login_names_are_fixed(client):
    response = client.get("/v1/auth/names")
    assert [item["name"] for item in response.json()] == [
        "Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming"
    ]

def test_temporary_password_requires_change(client, seeded_users):
    response = client.post("/v1/auth/login", json={"name": "Hassan", "password": seeded_users["Hassan"]})
    assert response.status_code == 200
    assert response.json()["must_change_password"] is True
~~~

再写错误密码限流、Cookie 属性、改密撤销旧会话、Mao 角色和无注册路由测试。

- [ ] **Step 2: 运行认证测试确认失败**

Run: cd api && pytest tests/test_auth.py -q

Expected: FAIL，路由返回 404。

- [ ] **Step 3: 实现 Argon2id、会话和 CSRF**

~~~python
FIXED_USERS = (
    ("Hassan", "reviewer"),
    ("Mao", "admin"),
    ("Xinhui", "reviewer"),
    ("Wahid", "reviewer"),
    ("Sharmaa", "reviewer"),
    ("Yiming", "reviewer"),
)

def session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
~~~

登录成功设置 Secure、HttpOnly、SameSite=Lax、Path=/sukaseafood 的 Cookie。测试环境允许 secure_cookie=False。CSRF 值随会话返回并要求写请求通过 X-CSRF-Token 提交。

- [ ] **Step 4: 实现账号初始化和密码重置命令**

Run: cd api && python -m app.commands.seed_users --print-once

Expected: 首次执行输出 6 个随机临时密码；第二次执行不改密码且不再次输出。

Run: cd api && python -m app.commands.reset_password Mao

Expected: 输出一个新的临时密码，Mao 下次登录必须修改。

- [ ] **Step 5: 运行认证测试并提交**

Run: cd api && pytest tests/test_auth.py -q

Expected: PASS。

~~~bash
git add api/app/schemas/auth.py api/app/services/auth.py api/app/api api/app/commands api/tests/test_auth.py
git commit -m "feat(review): add fixed-account authentication"
~~~

## Task 4: 共享待审核池和即时审核

**Files:**
- Create: api/app/schemas/review.py
- Create: api/app/services/pool.py
- Create: api/app/services/reviews.py
- Create: api/app/api/routes/reviews.py
- Create: api/tests/test_shared_pool.py
- Create: api/tests/test_review_submission.py
- Create: api/tests/integration/test_postgres_concurrency.py

**Interfaces:**
- Produces: get_or_open_current(session, user_id, filters) -> Candidate | None
- Produces: submit_decision(session, user_id, candidate_id, command_id, payload) -> Review
- Produces: POST /v1/reviews/current
- Produces: POST /v1/reviews/{candidate_id}/decision

- [ ] **Step 1: 写“同一人恢复当前图片”和“不同人不重复”测试**

~~~python
async def test_current_candidate_is_restored(session, hassan, candidates):
    first = await get_or_open_current(session, hassan.id, ReviewFilters())
    second = await get_or_open_current(session, hassan.id, ReviewFilters())
    assert first.id == second.id

async def test_two_users_receive_different_candidates(postgres_sessions, hassan, mao):
    first, second = await asyncio.gather(
        get_or_open_current(postgres_sessions[0], hassan.id, ReviewFilters()),
        get_or_open_current(postgres_sessions[1], mao.id, ReviewFilters()),
    )
    assert first.id != second.id
~~~

- [ ] **Step 2: 运行共享池测试确认失败**

Run: cd api && pytest tests/test_shared_pool.py tests/integration/test_postgres_concurrency.py -q

Expected: FAIL，pool 服务尚不存在。

- [ ] **Step 3: 实现简单当前图片标记**

~~~python
async def get_or_open_current(session, user_id, filters):
    current = await find_current_for_user(session, user_id)
    if current:
        return current
    candidate = await session.scalar(
        eligible_candidate_query(filters)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if candidate is None:
        return None
    candidate.current_reviewer_id = user_id
    candidate.current_started_at = utcnow()
    await session.commit()
    return candidate
~~~

用户同一时间只允许一张 current。生产 PostgreSQL 通过行级选择避免同时取得同一行；产品层不暴露“抢单、租约、心跳”概念。

- [ ] **Step 4: 写并实现审核幂等测试**

~~~python
def test_retrying_same_command_creates_one_review(auth_client, candidate):
    headers = {"Idempotency-Key": "decision-001"}
    payload = {"decision": "APPROVED", "notes": ""}
    first = auth_client.post(f"/v1/reviews/{candidate.id}/decision", json=payload, headers=headers)
    second = auth_client.post(f"/v1/reviews/{candidate.id}/decision", json=payload, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
~~~

submit_decision 验证 current_reviewer_id、保存 is_current=true 的 Review、追加 ReviewRevision、清除 current_reviewer_id，并保存 IdempotencyCommand 响应摘要。REJECT 必须带 rejection_reason；OTHER 必须带非空 notes。

- [ ] **Step 5: 运行测试并提交**

Run: cd api && pytest tests/test_shared_pool.py tests/test_review_submission.py tests/integration/test_postgres_concurrency.py -q

Expected: PASS。

~~~bash
git add api/app/schemas/review.py api/app/services/pool.py api/app/services/reviews.py api/app/api/routes/reviews.py api/tests
git commit -m "feat(review): add shared review pool and immediate decisions"
~~~

## Task 5: 全员进度和个人历史

**Files:**
- Create: api/app/schemas/progress.py
- Create: api/app/schemas/history.py
- Create: api/app/services/progress.py
- Create: api/app/services/history.py
- Create: api/app/api/routes/progress.py
- Create: api/app/api/routes/history.py
- Create: api/tests/test_progress.py
- Create: api/tests/test_history.py

**Interfaces:**
- Produces: GET /v1/progress
- Produces: GET /v1/history
- Produces: PATCH /v1/history/{review_id}

- [ ] **Step 1: 写全员进度隐私测试**

~~~python
def test_reviewer_sees_member_counts_but_not_other_history(hassan_client, reviewed_dataset):
    progress = hassan_client.get("/v1/progress").json()
    assert progress["total"] == 12
    assert {row["name"] for row in progress["members"]} == {
        "Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming"
    }
    other = hassan_client.get("/v1/history", params={"reviewer": "Mao"})
    assert other.status_code == 403
~~~

- [ ] **Step 2: 写历史版本冲突测试**

~~~python
def test_history_patch_rejects_stale_version(hassan_client, hassan_review):
    response = hassan_client.patch(
        f"/v1/history/{hassan_review.id}",
        json={"version": hassan_review.version - 1, "decision": "UNSURE", "notes": ""}
    )
    assert response.status_code == 409
~~~

- [ ] **Step 3: 实现汇总和本人历史查询**

progress 返回 total、reviewed、pending、currently_open、completion_percent、decision_counts、today_count 和六名成员的 completed/approved/rejected/unsure/today 数字，不返回备注或候选详情。

history 查询始终从 current_user.id 派生 reviewer_id，不接受普通成员覆盖；返回本人所有审核尝试，is_current=false 的旧尝试只读。Mao 的管理员路由另行实现。

- [ ] **Step 4: 实现带版本的历史修改**

PATCH 要求 version 且 Review.is_current=true。成功时更新 Review、version + 1，并追加 ReviewRevision；普通成员只能改自己的当前记录。修改为非 APPROVED 时，由导出服务在后续任务生成 REMOVE。

- [ ] **Step 5: 运行测试并提交**

Run: cd api && pytest tests/test_progress.py tests/test_history.py -q

Expected: PASS。

~~~bash
git add api/app/schemas api/app/services/progress.py api/app/services/history.py api/app/api/routes api/tests/test_progress.py api/tests/test_history.py
git commit -m "feat(review): add shared progress and private history"
~~~

## Task 6: Mao 管理接口

**Files:**
- Create: api/app/schemas/admin.py
- Create: api/app/services/admin.py
- Create: api/app/api/routes/admin.py
- Create: api/tests/test_admin_permissions.py
- Create: api/tests/test_admin_catalog.py
- Create: api/tests/test_admin_current.py

**Interfaces:**
- Produces: GET/PATCH/POST /v1/admin/species
- Produces: GET/PATCH /v1/admin/candidates
- Produces: GET/PATCH /v1/admin/reviews
- Produces: GET /v1/admin/current
- Produces: POST /v1/admin/current/{candidate_id}/release
- Produces: POST /v1/admin/current/{candidate_id}/transfer
- Produces: POST /v1/admin/reviews/{review_id}/reopen
- Produces: POST /v1/admin/users/{user_id}/reset-password

- [ ] **Step 1: 写管理员权限边界测试**

~~~python
@pytest.mark.parametrize("path", [
    "/v1/admin/species",
    "/v1/admin/candidates",
    "/v1/admin/reviews",
    "/v1/admin/current",
])
def test_reviewer_cannot_open_admin_routes(hassan_client, path):
    assert hassan_client.get(path).status_code == 403
~~~

- [ ] **Step 2: 写当前图片释放和鱼种停用测试**

验证 release 只清除尚无 Review 的 current_reviewer_id；验证被 Candidate 引用的 Species 只能 active=False，DELETE 路由不存在。

- [ ] **Step 3: 实现 Mao 管理服务**

所有修改输入都包含 reason。服务使用：

~~~python
async def audited_change(session, *, actor_id, object_type, object_id, reason, before, after):
    session.add(AuditEvent(
        actor_id=actor_id,
        object_type=object_type,
        object_id=str(object_id),
        reason=reason,
        before_json=before,
        after_json=after,
    ))
~~~

候选鱼种改变或管理员重新开放时，先追加包含原因的 ReviewRevision，再把当前 Review.is_current 设为 false，生成后续 MOVE 或 REMOVE 事件，并把图片放回共享池。旧审核尝试在管理员历史中保持只读可见。

- [ ] **Step 4: 实现密码重置和管理员历史修改**

重置密码返回一次性临时密码、设置 must_change_password=True、撤销该用户全部 Session。管理员修改 Review 必须带 version 和 reason。

- [ ] **Step 5: 运行测试并提交**

Run: cd api && pytest tests/test_admin_permissions.py tests/test_admin_catalog.py tests/test_admin_current.py -q

Expected: PASS。

~~~bash
git add api/app/schemas/admin.py api/app/services/admin.py api/app/api/routes/admin.py api/tests/test_admin_*.py
git commit -m "feat(review): add Mao administration API"
~~~

## Task 7: 预检查并导入现有候选 CSV

**Files:**
- Create: api/app/schemas/imports.py
- Create: api/app/services/imports.py
- Create: api/app/api/routes/imports.py
- Create: api/app/commands/import_candidates.py
- Create: api/tests/fixtures/candidates_sample.csv
- Create: api/tests/test_imports.py

**Interfaces:**
- Produces: preview_candidate_csv(content: bytes) -> ImportPreview
- Produces: commit_candidate_csv(session, preview_token, actor_id) -> ImportResult
- Produces: POST /v1/admin/imports/preview
- Produces: POST /v1/admin/imports/commit
- Produces: python -m app.commands.import_candidates PATH --dry-run

- [ ] **Step 1: 写旧字段映射和 iNaturalist 原图测试**

~~~python
def test_import_maps_inaturalist_preview_and_original(sample_inat_row):
    candidate = normalize_legacy_row(sample_inat_row)
    assert candidate.preview_url.endswith("/large.jpg")
    assert candidate.original_url.endswith("/original.jpg")
~~~

再验证 Fish-Vista、GBIF、Commons；缺少 image_url、未知 seafood_code、无许可证、完全重复和 URL 可疑重复分别出现在预检查报告。

- [ ] **Step 2: 运行导入测试确认失败**

Run: cd api && pytest tests/test_imports.py -q

Expected: FAIL，normalize_legacy_row 尚不存在。

- [ ] **Step 3: 实现不可部分提交的预检查和导入**

ImportPreview 返回 total、new_rows、exact_duplicates、possible_url_duplicates、invalid_species、missing_urls、invalid_licenses 和 preview_token。commit 重新校验文件摘要和数据库版本后在一个事务中插入新记录；current_reviewer_id 保持为空。

- [ ] **Step 4: 对真实 1,221 条清单执行 dry-run**

Run: cd api && python -m app.commands.import_candidates "C:/Users/86166/Desktop/SukaSeafood_CV_Dataset_Collector/output/candidates.csv" --dry-run --json-report import-report.json

Expected: total=1221；报告列出每个来源和鱼种数量；数据库行数不变。

- [ ] **Step 5: 运行测试并提交**

Run: cd api && pytest tests/test_imports.py -q

Expected: PASS。

~~~bash
git add api/app/schemas/imports.py api/app/services/imports.py api/app/api/routes/imports.py api/app/commands/import_candidates.py api/tests
git commit -m "feat(review): add validated candidate import"
~~~

## Task 8: 增量清单和回执 API

**Files:**
- Create: api/app/schemas/exports.py
- Create: api/app/services/exports.py
- Create: api/app/api/routes/exports.py
- Create: api/app/api/routes/sync.py
- Create: api/tests/test_exports.py
- Create: api/tests/test_receipts.py

**Interfaces:**
- Produces: POST /v1/admin/exports
- Produces: GET /v1/admin/exports/{batch_id}.csv
- Produces: POST /v1/sync/batches/{batch_id}/receipt
- Produces: POST /v1/admin/exports/{batch_id}/receipt-file

- [ ] **Step 1: 写“生成 CSV 不算下载成功”测试**

~~~python
def test_export_remains_pending_until_receipt(mao_client, approved_candidate):
    created = mao_client.post("/v1/admin/exports", json={"species_code": approved_candidate.species.code})
    batch_id = created.json()["id"]
    mao_client.get(f"/v1/admin/exports/{batch_id}.csv")
    progress = mao_client.get("/v1/admin/exports/pending-counts").json()
    assert progress[approved_candidate.species.code] == 1
~~~

- [ ] **Step 2: 写 ADD、REMOVE、MOVE 和失败回执测试**

成功 ADD 从 pending 变 succeeded；失败 ADD 保持 pending；已成功 APPROVED 后改为 REJECTED 生成 REMOVE；鱼种变化生成 MOVE；元数据变化不重复下载字节。

- [ ] **Step 3: 实现批次和签名回执令牌**

CSV 固定列：

~~~python
EXPORT_COLUMNS = [
    "batch_id", "receipt_token", "action", "candidate_id", "review_id", "review_version",
    "species_code", "target_relative_path", "previous_relative_path",
    "preview_url", "original_url", "source_url", "creator", "license",
    "license_url", "attribution",
]
~~~

receipt_token 只允许提交该批次列出的 candidate_id、review_id 和 review_version，默认 7 天过期；数据库保存令牌摘要。批次过期后，未成功项目回到 pending，并可生成新批次。

- [ ] **Step 4: 实现 JSON 和文件回执**

本地工具使用 Authorization: Batch <receipt_token> POST JSON；Mao 可上传 download_receipt.json。每项接收 review_id、status、sha256、relative_path 和 error。服务拒绝批次外 ID、重复冲突 SHA 和过期令牌。

- [ ] **Step 5: 运行测试并提交**

Run: cd api && pytest tests/test_exports.py tests/test_receipts.py -q

Expected: PASS。

~~~bash
git add api/app/schemas/exports.py api/app/services/exports.py api/app/api/routes api/tests/test_exports.py api/tests/test_receipts.py
git commit -m "feat(review): add incremental export and receipt API"
~~~

## Task 9: 建立 React 网页和认证状态

**Files:**
- Create: web/package.json
- Create: web/package-lock.json
- Create: web/tsconfig.json
- Create: web/vite.config.ts
- Create: web/index.html
- Create: web/src/main.tsx
- Create: web/src/App.tsx
- Create: web/src/api/types.ts
- Create: web/src/api/client.ts
- Create: web/src/auth/AuthProvider.tsx
- Create: web/src/components/PillChoiceGroup.tsx
- Create: web/src/pages/LoginPage.tsx
- Create: web/src/styles/global.css
- Create: web/src/test/setup.ts
- Create: web/src/pages/LoginPage.test.tsx
- Create: web/src/components/PillChoiceGroup.test.tsx

**Interfaces:**
- Produces: api.request<T>(path, options) -> Promise<T>
- Produces: AuthProvider/useAuth
- Produces: PillChoiceGroup<T>
- Produces: /sukaseafood/review 登录页面

- [ ] **Step 1: 写姓名椭圆按钮测试**

~~~tsx
it("renders six names as selectable pills instead of a select", async () => {
  render(<LoginPage />);
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.getAllByRole("radio")).toHaveLength(6);
  await user.click(screen.getByRole("radio", { name: "Mao" }));
  expect(screen.getByRole("radio", { name: "Mao" })).toHaveAttribute("aria-checked", "true");
});
~~~

- [ ] **Step 2: 运行网页测试确认失败**

Run: cd web && npm test -- --run src/pages/LoginPage.test.tsx

Expected: FAIL，组件尚不存在。

- [ ] **Step 3: 实现基础路由、HTTP 客户端和认证状态**

Vite base 设置为 /sukaseafood/review/。api.client 使用 /sukaseafood/api/v1，credentials 为 include，并从 AuthProvider 加 X-CSRF-Token。

- [ ] **Step 4: 实现椭圆单选组和登录页**

PillChoiceGroup 使用 role=radiogroup 和 role=radio。选中类使用 transform: scale(1.06)，不改变布局；同时显示边框、背景和文字勾选标记。支持方向键、Home、End 和空格。

- [ ] **Step 5: 运行测试并提交**

Run: cd web && npm test -- --run

Expected: PASS。

Run: cd web && npm run typecheck

Expected: exit 0。

~~~bash
git add web
git commit -m "feat(review-web): add authentication shell and name pills"
~~~

## Task 10: 实现审核页、图片转圈和拒绝原因椭圆按钮

**Files:**
- Create: web/src/i18n/catalog.ts
- Create: web/src/i18n/I18nProvider.tsx
- Create: web/src/components/ImageStage.tsx
- Create: web/src/components/DecisionPanel.tsx
- Create: web/src/components/ProgressSummary.tsx
- Create: web/src/pages/ReviewPage.tsx
- Create: web/src/components/ImageStage.test.tsx
- Create: web/src/components/DecisionPanel.test.tsx
- Create: web/src/pages/ReviewPage.test.tsx

**Interfaces:**
- Produces: ImageStage({previewUrl, sourceUrl})
- Produces: DecisionPanel({onSubmit, pending})
- Produces: ReviewPage 调用 POST /reviews/current 和 /decision

- [ ] **Step 1: 写图片加载状态测试**

~~~tsx
it("shows spinner until load and finite error actions after error", async () => {
  render(<ImageStage previewUrl="https://example.test/fish.jpg" sourceUrl="https://example.test/record" />);
  expect(screen.getByRole("status", { name: "正在加载图片" })).toBeVisible();
  fireEvent.error(screen.getByRole("img"));
  expect(screen.queryByRole("status", { name: "正在加载图片" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新加载图片" })).toBeVisible();
  expect(screen.getByRole("button", { name: "图片链接失效" })).toBeVisible();
});
~~~

- [ ] **Step 2: 写拒绝原因不是下拉框的测试**

~~~tsx
it("requires one rejection reason selected from pills", async () => {
  const onSubmit = vi.fn();
  render(<DecisionPanel onSubmit={onSubmit} pending={false} />);
  await user.click(screen.getByRole("button", { name: "拒绝" }));
  expect(screen.queryByRole("combobox", { name: "拒绝原因" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("radio", { name: "鱼种错误" }));
  await user.click(screen.getByRole("button", { name: "确认拒绝" }));
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ rejection_reason: "WRONG_SPECIES" }));
});
~~~

- [ ] **Step 3: 实现中英文稳定代码翻译**

catalog 覆盖状态、来源、全部拒绝原因、加载错误、按钮、进度、历史和筛选项。数据库代码保持英文，不把中文写入 decision 或 rejection_reason。

- [ ] **Step 4: 实现即时保存和键盘快捷键**

K/R/U 只在非输入框且 pending=false 时工作。提交生成 crypto.randomUUID() 作为 Idempotency-Key。“图片链接失效”提交 REJECTED + IMAGE_URL_UNAVAILABLE。pending 时禁用按钮；失败保留图片和选择；成功后重新请求 current。

- [ ] **Step 5: 运行测试并提交**

Run: cd web && npm test -- --run src/components src/pages/ReviewPage.test.tsx

Expected: PASS。

~~~bash
git add web/src
git commit -m "feat(review-web): add bilingual immediate review flow"
~~~

## Task 11: 实现全员进度和个人历史页面

**Files:**
- Create: web/src/components/TeamProgress.tsx
- Create: web/src/pages/HistoryPage.tsx
- Create: web/src/components/TeamProgress.test.tsx
- Create: web/src/pages/HistoryPage.test.tsx
- Modify: web/src/App.tsx

**Interfaces:**
- Consumes: GET /v1/progress、GET/PATCH /v1/history
- Produces: /sukaseafood/review/history

- [ ] **Step 1: 写全员进度展示测试**

~~~tsx
it("shows total and every member without exposing notes", () => {
  render(<TeamProgress data={progressFixture} />);
  expect(screen.getByText("总完成率")).toBeVisible();
  for (const name of ["Hassan", "Mao", "Xinhui", "Wahid", "Sharmaa", "Yiming"]) {
    expect(screen.getByText(name)).toBeVisible();
  }
  expect(screen.queryByText("private review note")).not.toBeInTheDocument();
});
~~~

- [ ] **Step 2: 写本人历史修改和冲突测试**

HistoryPage 请求不发送 reviewer 参数。PATCH 409 时显示“记录已被更新”，用响应中的最新版本替换表单。

- [ ] **Step 3: 实现进度组件和历史筛选**

进度显示 total、reviewed、pending、currently_open、completion_percent、每人成果和当日数量。历史筛选使用鱼种、来源、状态下拉框和日期输入。

- [ ] **Step 4: 实现历史编辑**

决定使用按钮；拒绝原因复用 PillChoiceGroup；提交携带 version。成功后刷新该行并显示修改时间。

- [ ] **Step 5: 运行测试并提交**

Run: cd web && npm test -- --run src/components/TeamProgress.test.tsx src/pages/HistoryPage.test.tsx

Expected: PASS。

~~~bash
git add web/src
git commit -m "feat(review-web): add team progress and private history"
~~~

## Task 12: 实现 Mao 中文后台

**Files:**
- Create: web/src/pages/AdminPage.tsx
- Create: web/src/admin/ProgressTab.tsx
- Create: web/src/admin/CandidatesTab.tsx
- Create: web/src/admin/SpeciesTab.tsx
- Create: web/src/admin/ReviewsTab.tsx
- Create: web/src/admin/ImportsTab.tsx
- Create: web/src/admin/ExportsTab.tsx
- Create: web/src/admin/UsersTab.tsx
- Create: web/src/pages/AdminPage.test.tsx
- Modify: web/src/App.tsx

**Interfaces:**
- Consumes: /v1/admin/*
- Produces: /sukaseafood/review/admin

- [ ] **Step 1: 写角色路由和中文固定界面测试**

~~~tsx
it("redirects reviewers and renders Chinese tabs for Mao", async () => {
  renderApp({ user: maoFixture, route: "/admin" });
  for (const label of ["审核进度", "候选图片", "鱼种管理", "审核历史", "导入", "训练集同步", "账号"]) {
    expect(await screen.findByRole("tab", { name: label })).toBeVisible();
  }
});
~~~

另写 reviewerFixture 访问 /admin 后跳回 /review 的测试。

- [ ] **Step 2: 实现进度、当前图片释放和审核管理标签**

当前图片列表显示成员、candidate_id、打开时间、来源；释放和转交必须填写原因。审核修改和重新开放要求 version 与 reason。

- [ ] **Step 3: 实现候选、鱼种和 CSV 导入标签**

候选编辑支持 preview_url、original_url、鱼种和启用状态。鱼种只支持停用不硬删。导入必须先展示预检查结果，再允许提交。

- [ ] **Step 4: 实现账号和增量导出标签**

密码重置前二次确认，只显示新临时密码一次。导出标签显示每种鱼 pending 数量、创建/下载 CSV、批次历史和手动上传 download_receipt.json。

- [ ] **Step 5: 运行测试并提交**

Run: cd web && npm test -- --run src/pages/AdminPage.test.tsx

Expected: PASS。

Run: cd web && npm run typecheck && npm run build

Expected: exit 0，产物引用 /sukaseafood/review/assets/*。

~~~bash
git add web/src
git commit -m "feat(review-web): add Mao Chinese administration"
~~~

## Task 13: 核心系统整体验证和文档

**Files:**
- Create: README_ZH.md
- Create: api/tests/test_openapi_contract.py
- Create: web/src/App.integration.test.tsx
- Create: README.md

**Interfaces:**
- Consumes: Task 1 至 Task 12 全部接口。
- Produces: 本地可运行、接口契约稳定的审核核心。

- [ ] **Step 1: 添加 OpenAPI 契约快照测试**

测试必须断言固定路由存在、/register 不存在、普通进度模型不含 notes/source_url/review_id。

- [ ] **Step 2: 添加登录到提交再看进度的网页集成测试**

Mock API 流程：选择 Hassan 椭圆按钮、登录、加载图片转圈、选择 KEEP、等待保存、取得下一张、打开历史、查看总进度。

- [ ] **Step 3: 编写中文本地开发说明**

README_ZH 包含 Python/Node 安装、环境变量、迁移、初始化六账号、导入 dry-run、API 启动、网页启动、测试命令和不代理图片说明。

- [ ] **Step 4: 运行全量验证**

Run: cd api && pytest -q

Expected: 全部 PASS。

Run: cd web && npm test -- --run && npm run typecheck && npm run build

Expected: 全部 PASS。

- [ ] **Step 5: 提交核心系统**

~~~bash
git add api web README.md
git commit -m "docs(review): document collaborative review system"
~~~
