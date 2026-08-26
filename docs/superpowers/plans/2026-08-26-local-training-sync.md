# SukaSeafood Windows 本地训练集同步工具实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 构建可双击使用的 Windows 工具，根据 Mao 后台导出的增量 CSV，只把尚未成功同步的 APPROVED 原图下载到本地训练集，并把小型回执返回服务器。

**Architecture:** local_sync 是独立 Python 包。核心同步引擎读取签名批次 CSV，使用本地 SQLite 索引保证重复运行幂等，直接从外部 original_url 下载和验证图片，安全应用 ADD、MOVE、REMOVE，再通过 HTTP 或 JSON 文件提交回执。Tkinter GUI 和 CLI 复用同一同步引擎。

**Tech Stack:** Python 3.12、Tkinter、requests、Pillow、ImageHash、SQLite、pytest、responses、PyInstaller

**Spec:** docs/superpowers/specs/2026-08-26-collaborative-review-system-design.md

## Global Constraints

- 原图由 Mao 的 Windows 电脑直接访问外部来源，不经过生产服务器。
- 只下载当前增量 CSV 中 action=ADD 且本地尚未成功处理的项目。
- 所有操作的目标和成功回执路径都必须使用服务器 CSV 的精确 `target_relative_path`；客户端不得自行重算目录、扩展名或 REMOVE 位置。
- 下载先写 .part 文件，验证和哈希成功后再原子改名。
- 每项计算 SHA-256 和感知哈希；完全相同字节不重复保存。
- MOVE 不重新下载相同图片并移动到精确 `target_relative_path`；REMOVE 从 `previous_relative_path` 移到服务器给出的精确 `_removed/...` target，不永久删除。
- 带 `previous_relative_path` 的 composite ADD 必须先下载并原子提交新 target，只有新文件成功后才清理/移动旧路径；下载或验证失败必须保留旧文件。
- 下载失败不记成功，并保留给下一次增量同步。
- 重复运行同一 CSV 不重复下载已经成功的 candidate_id + review_id + review_version。
- 回执上传失败时必须保存 download_receipt.json。
- 默认读取 HTTP_PROXY、HTTPS_PROXY 和 NO_PROXY；GUI 允许本次覆盖。
- Wikimedia 等来源遵守 Retry-After；无 Retry-After 时指数退避。
- 所有路径必须解析并验证位于用户选择的训练集根目录内。

---

## 文件结构

- local_sync/pyproject.toml：包元数据、依赖和 CLI 入口。
- local_sync/src/sukaseafood_sync/manifest.py：CSV 契约和校验。
- local_sync/src/sukaseafood_sync/index.py：本地 SQLite 同步索引。
- local_sync/src/sukaseafood_sync/downloader.py：网络下载、重试、验证和哈希。
- local_sync/src/sukaseafood_sync/operations.py：ADD、MOVE、REMOVE。
- local_sync/src/sukaseafood_sync/engine.py：批次编排、进度和取消。
- local_sync/src/sukaseafood_sync/receipt.py：在线和离线回执。
- local_sync/src/sukaseafood_sync/gui.py：Tkinter 界面。
- local_sync/src/sukaseafood_sync/cli.py：命令行入口。
- local_sync/tests/*：单元、恢复和端到端测试。

## Task 1: 定义增量 CSV 契约和本地索引

**Files:**
- Create: local_sync/pyproject.toml
- Create: local_sync/src/sukaseafood_sync/__init__.py
- Create: local_sync/src/sukaseafood_sync/manifest.py
- Create: local_sync/src/sukaseafood_sync/index.py
- Create: local_sync/tests/conftest.py
- Create: local_sync/tests/fixtures/export_batch.csv
- Create: local_sync/tests/test_manifest.py
- Create: local_sync/tests/test_index.py

**Interfaces:**
- Produces: ManifestRow dataclass
- Produces: load_manifest(path: Path) -> ExportManifest
- Produces: SyncIndex(root: Path)
- Produces: SyncIndex.is_completed(candidate_id: str, review_id: UUID, review_version: int, action: str) -> bool
- Produces: SyncIndex.record_success(result: SyncResult) -> None

- [ ] **Step 1: 写 CSV 必填列和路径攻击测试**

~~~python
def test_manifest_rejects_missing_original_url(tmp_path):
    path = write_csv(tmp_path, rows=[valid_row(original_url="")])
    with pytest.raises(ManifestError, match="original_url"):
        load_manifest(path)

@pytest.mark.parametrize("target", ["../outside.jpg", "C:/Windows/file.jpg", "/tmp/file.jpg"])
def test_manifest_rejects_unsafe_relative_path(tmp_path, target):
    path = write_csv(tmp_path, rows=[valid_row(target_relative_path=target)])
    with pytest.raises(ManifestError, match="relative path"):
        load_manifest(path)
~~~

- [ ] **Step 2: 运行测试确认失败**

Run: cd local_sync && python -m pytest tests/test_manifest.py tests/test_index.py -q

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现强类型清单解析**

~~~python
@dataclass(frozen=True)
class ManifestRow:
    batch_id: UUID
    receipt_token: str
    action: Literal["ADD", "REMOVE", "MOVE"]
    candidate_id: str
    review_id: UUID
    review_version: int
    species_code: str
    target_relative_path: PurePosixPath
    previous_relative_path: PurePosixPath | None
    original_url: str
    source_url: str
    license: str
    attribution: str
~~~

同一文件所有行必须拥有相同 batch_id 和 receipt_token。original_url 必须为 HTTPS；ADD 必须有 original_url，且 composite refresh ADD 可以有 previous_relative_path；MOVE/REMOVE 必须有 previous_relative_path。三种 action 都把服务器 `target_relative_path` 作为唯一目标，成功回执必须报告该精确 target（仅 ADD 下载解码时允许服务器契约认可的受控图片扩展调整）。

- [ ] **Step 4: 实现本地 SQLite 索引**

数据库固定为训练集根目录下的 .sukaseafood-sync.sqlite3。表：

~~~sql
CREATE TABLE synced_items (
  candidate_id TEXT NOT NULL,
  review_id TEXT NOT NULL,
  review_version INTEGER NOT NULL,
  action TEXT NOT NULL,
  relative_path TEXT,
  sha256 TEXT,
  perceptual_hash TEXT,
  completed_at TEXT NOT NULL,
  PRIMARY KEY (candidate_id, review_id, review_version, action)
);
~~~

连接启用 WAL、foreign_keys 和 busy_timeout。所有相对路径再次解析验证位于 root 内。

- [ ] **Step 5: 运行测试并提交**

Run: cd local_sync && python -m pytest tests/test_manifest.py tests/test_index.py -q

Expected: PASS。

~~~bash
git add local_sync
git commit -m "feat(sync): add manifest validation and local index"
~~~

## Task 2: 实现可靠的外部原图下载

**Files:**
- Create: local_sync/src/sukaseafood_sync/downloader.py
- Create: local_sync/tests/test_downloader.py
- Create: local_sync/tests/fixtures/valid_fish.jpg
- Create: local_sync/tests/fixtures/not_an_image.txt

**Interfaces:**
- Produces: DownloadPolicy
- Produces: DownloadResult
- Produces: download_image(session, row, destination, policy, progress, cancel) -> DownloadResult

- [ ] **Step 1: 写 Retry-After 和指数退避测试**

~~~python
def test_429_honors_retry_after(http, fake_sleep, valid_jpeg, tmp_path):
    http.add(responses.GET, IMAGE_URL, status=429, headers={"Retry-After": "3"})
    http.add(responses.GET, IMAGE_URL, body=valid_jpeg, status=200)
    result = download_image(session(), add_row(), tmp_path / "fish.jpg", policy(), noop, never_cancel)
    assert result.sha256
    fake_sleep.assert_called_once_with(3.0)
~~~

再写无 Retry-After 时 10、20、40 秒退避，最多 4 次；非重试 404 立即失败；取消立即停止。

- [ ] **Step 2: 写图片验证和 .part 清理测试**

~~~python
def test_invalid_image_is_not_promoted(http, tmp_path):
    http.add(responses.GET, IMAGE_URL, body=b"<html>blocked</html>", status=200)
    target = tmp_path / "fish.jpg"
    with pytest.raises(DownloadError, match="decodable image"):
        download_image(session(), add_row(), target, policy(), noop, never_cancel)
    assert not target.exists()
    assert not target.with_suffix(".jpg.part").exists()
~~~

- [ ] **Step 3: 实现流式下载和大小限制**

requests.Session 使用 trust_env=True。以 256 KiB 块写入 .part，同时累计 SHA-256。默认单图上限 100 MiB；Content-Length 超限提前拒绝，实际流量超限立即删除 .part。

- [ ] **Step 4: 实现 Pillow 验证和感知哈希**

先 Image.verify()，再重新打开、执行 EXIF transpose、转换 RGB 并计算 imagehash.phash。保留来源原始字节，不重新编码。扩展名优先依据验证后的图像格式映射 JPEG/PNG/WEBP。

- [ ] **Step 5: 运行测试并提交**

Run: cd local_sync && python -m pytest tests/test_downloader.py -q

Expected: PASS。

~~~bash
git add local_sync/src/sukaseafood_sync/downloader.py local_sync/tests
git commit -m "feat(sync): add resilient original image downloader"
~~~

## Task 3: 安全应用 ADD、MOVE 和 REMOVE

**Files:**
- Create: local_sync/src/sukaseafood_sync/operations.py
- Create: local_sync/tests/test_operations.py

**Interfaces:**
- Produces: apply_add(root, row, download_result, index) -> SyncResult
- Produces: apply_move(root, row, index) -> SyncResult
- Produces: apply_remove(root, row, index) -> SyncResult

- [ ] **Step 1: 写 ADD 幂等和重复字节测试**

~~~python
def test_add_does_not_download_or_copy_completed_revision(sync_root, index, completed_row):
    result = apply_add(sync_root, completed_row, unexpected_download_result(), index)
    assert result.status == "SKIPPED_ALREADY_COMPLETED"

def test_identical_sha_uses_existing_file(sync_root, index, add_row, existing_file):
    result = apply_add(sync_root, add_row, result_with_sha(existing_file.sha256), index)
    assert result.status == "SUCCEEDED"
    assert result.relative_path == existing_file.relative_path

def test_composite_add_cleans_previous_only_after_new_target_succeeds(sync_root, index, composite_add_row):
    result = apply_add(sync_root, composite_add_row, verified_download(), index)
    assert result.relative_path == composite_add_row.target_relative_path
    assert not resolve_inside(sync_root, composite_add_row.previous_relative_path).exists()

def test_failed_composite_add_preserves_previous(sync_root, index, composite_add_row):
    with pytest.raises(DownloadError):
        apply_add(sync_root, composite_add_row, failed_download(), index)
    assert resolve_inside(sync_root, composite_add_row.previous_relative_path).exists()
~~~

- [ ] **Step 2: 写 MOVE 和可恢复 REMOVE 测试**

MOVE 使用 os.replace 从 previous 移到服务器给出的精确 target；目标已存在且 SHA 相同则只更新索引，SHA 不同则报冲突。REMOVE 同样使用服务器给出的精确 `_removed/...` target，不生成客户端 timestamp 路径，并记录该恢复位置。ADD/MOVE/REMOVE 的成功 `SyncResult.relative_path` 与回执都必须等于实际使用的服务器 target（仅受控 decoded-extension ADD 例外，且该实际路径随后成为服务器 canonical local state）。

- [ ] **Step 3: 实现根目录边界检查**

~~~python
def resolve_inside(root: Path, relative: PurePosixPath) -> Path:
    candidate = (root / Path(*relative.parts)).resolve()
    candidate.relative_to(root.resolve())
    return candidate
~~~

所有移动、改名和创建目录只能使用该函数返回的路径。不能使用 CSV 直接提供的绝对路径。

- [ ] **Step 4: 实现操作日志**

每次运行在 logs/sync-{batch_id}-{timestamp}.jsonl 追加 candidate_id、action、status、旧路径、新路径、sha256、错误和时间。日志不包含 receipt_token。

- [ ] **Step 5: 运行测试并提交**

Run: cd local_sync && python -m pytest tests/test_operations.py -q

Expected: PASS。

~~~bash
git add local_sync/src/sukaseafood_sync/operations.py local_sync/tests/test_operations.py
git commit -m "feat(sync): apply safe incremental dataset operations"
~~~

## Task 4: 编排批次、续跑和进度

**Files:**
- Create: local_sync/src/sukaseafood_sync/engine.py
- Create: local_sync/tests/test_engine.py
- Create: local_sync/tests/test_resume.py

**Interfaces:**
- Produces: SyncEngine.run(manifest, root, callbacks, cancel_event) -> BatchResult
- Produces: ProgressEvent
- Produces: BatchResult.receipt_items

- [ ] **Step 1: 写混合批次测试**

~~~python
def test_engine_processes_add_move_remove_and_keeps_failures_pending(engine, manifest):
    result = engine.run(manifest, ROOT, callbacks(), Event())
    assert result.counts == {
        "succeeded": 3,
        "failed": 1,
        "skipped": 1,
    }
    assert [item.status for item in result.receipt_items].count("FAILED") == 1
~~~

- [ ] **Step 2: 写中断续跑测试**

第一次在第二项设置 cancel_event；确认第一项已写入索引、第二项没有成功。第二次运行相同 CSV 时第一项不发起网络请求，继续第二项。

- [ ] **Step 3: 实现串行、来源友好的编排**

默认单线程，避免对来源站点造成突发压力。每项前检查本地索引。iNaturalist、GBIF、Fish-Vista 默认最小间隔 1 秒；Wikimedia Commons 默认 6.5 秒。Retry-After 优先级高于最小间隔。

- [ ] **Step 4: 实现线程安全进度回调**

ProgressEvent 包含 current、total、candidate_id、species_code、phase、message。engine 不直接操作 Tkinter；GUI 通过 queue.Queue 接收事件。

- [ ] **Step 5: 运行测试并提交**

Run: cd local_sync && python -m pytest tests/test_engine.py tests/test_resume.py -q

Expected: PASS。

~~~bash
git add local_sync/src/sukaseafood_sync/engine.py local_sync/tests/test_engine.py local_sync/tests/test_resume.py
git commit -m "feat(sync): orchestrate resumable incremental batches"
~~~

## Task 5: 在线回执和离线回执文件

**Files:**
- Create: local_sync/src/sukaseafood_sync/receipt.py
- Create: local_sync/tests/test_receipt.py

**Interfaces:**
- Produces: build_receipt(manifest, batch_result) -> Receipt
- Produces: submit_receipt(receipt, api_base, token, timeout) -> SubmitResult
- Produces: save_receipt_file(receipt, path) -> Path

- [ ] **Step 1: 写只回传允许字段的测试**

~~~python
def test_receipt_does_not_expose_original_urls_or_token(batch_result):
    payload = build_receipt(manifest(), batch_result).to_dict()
    serialized = json.dumps(payload)
    assert "original_url" not in serialized
    assert "receipt_token" not in serialized
    assert set(payload["items"][0]) == {
        "candidate_id", "review_id", "review_version", "action", "status",
        "relative_path", "sha256", "perceptual_hash", "error"
    }
~~~

- [ ] **Step 2: 写上传失败保存 JSON 测试**

模拟 DNS/timeout。submit_receipt 返回失败，不删除本地结果；save_receipt_file 原子写入 download_receipt-{batch_id}.json。

- [ ] **Step 3: 实现回执提交**

POST 到 CSV 指定的同源 API 路径 /sukaseafood/api/v1/sync/batches/{batch_id}/receipt，Authorization 使用 Batch {receipt_token}。只重试连接失败、429 和 5xx，最多 3 次。

- [ ] **Step 4: 实现已提交标记**

服务器返回 accepted candidate IDs 后，本地 index 写 receipt_submitted_at。重复提交相同回执必须安全；冲突项保留离线回执并显示人工处理提示。

- [ ] **Step 5: 运行测试并提交**

Run: cd local_sync && python -m pytest tests/test_receipt.py -q

Expected: PASS。

~~~bash
git add local_sync/src/sukaseafood_sync/receipt.py local_sync/tests/test_receipt.py
git commit -m "feat(sync): add online and offline receipts"
~~~

## Task 6: Tkinter 小窗口和 CLI

**Files:**
- Create: local_sync/src/sukaseafood_sync/gui.py
- Create: local_sync/src/sukaseafood_sync/cli.py
- Create: local_sync/src/sukaseafood_sync/__main__.py
- Create: local_sync/tests/test_cli.py
- Create: local_sync/tests/test_gui_state.py

**Interfaces:**
- Produces: suka-seafood-sync CLI
- Produces: python -m sukaseafood_sync GUI

- [ ] **Step 1: 写 CLI dry-run 和退出码测试**

~~~python
def test_cli_dry_run_makes_no_network_calls(runner, manifest_path, sync_root):
    result = runner.invoke(["sync", str(manifest_path), str(sync_root), "--dry-run"])
    assert result.exit_code == 0
    assert "ADD 3, MOVE 1, REMOVE 1" in result.stdout
~~~

退出码：0 全部成功或跳过；2 清单/参数错误；3 有下载失败；4 回执未上传但已保存离线文件；130 用户取消。

- [ ] **Step 2: 写 GUI 状态机测试**

状态为 idle、running、cancelling、complete、failed。running 时禁用 CSV 和目录选择；Cancel 只设置 Event，不强杀线程；complete 显示成功、失败和跳过数量。

- [ ] **Step 3: 实现 GUI**

窗口包含 CSV 选择、训练集目录选择、代理覆盖、开始、取消、进度条、当前 candidate_id、滚动日志和完成摘要。后台线程运行 SyncEngine，主线程通过 after() 轮询 Queue 更新控件。

- [ ] **Step 4: 实现 CLI**

子命令：

~~~text
suka-seafood-sync inspect BATCH.csv
suka-seafood-sync sync BATCH.csv DATASET_ROOT
suka-seafood-sync submit-receipt download_receipt.json
~~~

CLI 与 GUI 调用相同 load_manifest、SyncEngine 和 receipt 函数。

- [ ] **Step 5: 运行测试并提交**

Run: cd local_sync && python -m pytest tests/test_cli.py tests/test_gui_state.py -q

Expected: PASS。

~~~bash
git add local_sync/src local_sync/tests
git commit -m "feat(sync): add Windows GUI and CLI"
~~~

## Task 7: 打包 Windows 可执行文件并整体验证

**Files:**
- Create: local_sync/packaging/suka-seafood-sync.spec
- Create: local_sync/scripts/build_windows.ps1
- Create: local_sync/README_ZH.md
- Create: local_sync/tests/test_end_to_end.py
- Create: .github/workflows/windows-sync.yml

**Interfaces:**
- Produces: dist/SukaSeafoodTrainingSync/SukaSeafoodTrainingSync.exe
- Produces: Windows CI 测试和打包产物。

- [ ] **Step 1: 写本地端到端测试**

使用 responses 提供两张有效图、一张 429 后成功图、一张无效图，并包含带 previous 的 composite ADD、保留 decoded suffix 的 MOVE 与服务器指定 `_removed/...` target 的 REMOVE。运行两次同一 CSV；断言第二次不请求已成功项目、失败项仍请求、composite ADD 只在新 target 成功后清理旧路径、所有操作与 receipt 使用服务器精确 target，且 canonical_manifest.csv 正确。

- [ ] **Step 2: 编写 PyInstaller 配置**

收集 Pillow 图像插件、certifi CA、Tkinter 资源和包元数据。使用 onedir 而不是 onefile，减少杀毒误报和启动延迟。exe 不包含服务器账号或永久令牌。

- [ ] **Step 3: 编写 PowerShell 构建脚本**

脚本创建隔离 venv、安装锁定依赖、运行 pytest、运行 PyInstaller、启动 exe --version smoke test，并生成 SHA256SUMS.txt。

- [ ] **Step 4: 编写中文使用说明和 CI**

README_ZH 明确后台导出 CSV、双击 exe、选择训练集目录、代理、失败重试、离线回执上传、_removed 恢复和本地备份。GitHub Actions windows-latest 运行测试并上传构建 artifact。

- [ ] **Step 5: 运行全量验证并提交**

Run: cd local_sync && python -m pytest -q

Expected: 全部 PASS。

Run: powershell -ExecutionPolicy Bypass -File local_sync/scripts/build_windows.ps1

Expected: exe smoke test 通过，SHA256SUMS.txt 存在。

~~~bash
git add local_sync .github/workflows/windows-sync.yml
git commit -m "build(sync): package Windows training sync tool"
~~~
