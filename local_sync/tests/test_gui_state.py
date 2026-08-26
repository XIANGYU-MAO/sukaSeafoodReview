from __future__ import annotations

from pathlib import Path

import pytest


def test_gui_state_model_has_exact_states_controls_and_legal_transitions() -> None:
    from sukaseafood_sync.gui import GUI_STATES, GuiStateModel

    assert GUI_STATES == ("idle", "running", "cancelling", "complete", "failed")
    model = GuiStateModel()
    assert model.state == "idle"
    assert model.selection_enabled is True
    assert model.start_enabled is True
    assert model.cancel_enabled is False

    model.transition("running")
    assert model.selection_enabled is False
    assert model.start_enabled is False
    assert model.cancel_enabled is True
    model.transition("cancelling")
    assert model.selection_enabled is False
    assert model.start_enabled is False
    assert model.cancel_enabled is False
    model.transition("complete")
    assert model.selection_enabled is True
    assert model.start_enabled is True

    model.transition("running")
    model.transition("failed")
    assert model.selection_enabled is True
    assert model.start_enabled is True

    with pytest.raises(ValueError, match="INVALID_GUI_TRANSITION"):
        model.transition("idle")


def test_gui_cancel_only_sets_shared_event_and_never_kills_worker(tmp_path: Path) -> None:
    from sukaseafood_sync.gui import GuiController
    from sukaseafood_sync.service import SyncRequest

    threads: list[object] = []

    class Thread:
        def __init__(self, *, target, args, daemon: bool) -> None:
            self.target = target
            self.args = args
            self.daemon = daemon
            self.started = 0
            threads.append(self)

        def start(self) -> None:
            self.started += 1

    controller = GuiController(
        lambda *_args: pytest.fail("worker should not run synchronously"),
        thread_factory=Thread,
    )
    controller.start(SyncRequest(tmp_path / "batch.csv", tmp_path / "training"))
    cancel_event = controller.cancel_event

    assert controller.model.state == "running"
    assert threads[0].started == 1
    assert threads[0].daemon is True
    assert cancel_event is not None and not cancel_event.is_set()

    controller.cancel()

    assert controller.model.state == "cancelling"
    assert cancel_event.is_set()
    assert not hasattr(threads[0], "kill")
    assert not hasattr(threads[0], "terminate")


def test_worker_only_queues_messages_and_main_poll_applies_fifo_chinese_summary(
    tmp_path: Path,
) -> None:
    from sukaseafood_sync.engine import ProgressEvent
    from sukaseafood_sync.gui import GuiController
    from sukaseafood_sync.service import SyncOutcome, SyncRequest

    threads: list[object] = []
    candidate_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    candidate_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    unsafe_internal_message = "proxy-user:proxy-password@private.example"
    offline_path = tmp_path / "download_receipt.json"

    class Thread:
        def __init__(self, *, target, args, daemon: bool) -> None:
            self.target = target
            self.args = args
            threads.append(self)

        def start(self) -> None:
            return None

    def run(request, cancel_event, progress):
        progress(ProgressEvent(1, 2, candidate_a, "SF006", "DOWNLOADING", unsafe_internal_message))
        progress(ProgressEvent(2, 2, candidate_b, "SF007", "SUCCEEDED", unsafe_internal_message))
        return SyncOutcome(
            4,
            f"回执未完整上传，已保存：{offline_path}",
            {"succeeded": 1, "failed": 0, "skipped": 1},
            offline_path,
        )

    controller = GuiController(run, thread_factory=Thread)
    controller.start(SyncRequest(tmp_path / "batch.csv", tmp_path / "training"))
    thread = threads[0]

    thread.target(*thread.args)

    assert controller.model.state == "running"
    assert controller.logs == ()
    assert controller.current_candidate is None

    processed = controller.poll()

    assert processed == 3
    assert controller.model.state == "complete"
    assert controller.current_candidate == candidate_b
    assert controller.progress == (2, 2)
    assert candidate_a in controller.logs[0]
    assert candidate_b in controller.logs[1]
    assert unsafe_internal_message not in "".join(controller.logs)
    assert controller.summary == (
        f"成功 1，失败 0，已跳过 1；离线回执：{offline_path}；可在管理后台上传"
    )


def test_restart_uses_fresh_worker_state_and_close_requests_recoverable_cancel(
    tmp_path: Path,
) -> None:
    from sukaseafood_sync.gui import GuiController
    from sukaseafood_sync.service import SyncOutcome, SyncRequest

    threads: list[object] = []

    class Thread:
        def __init__(self, *, target, args, daemon: bool) -> None:
            self.target = target
            self.args = args
            threads.append(self)

        def start(self) -> None:
            return None

    def run(request, cancel_event, progress):
        return SyncOutcome(
            0,
            "同步完成",
            {"succeeded": 1, "failed": 0, "skipped": 0},
        )

    controller = GuiController(run, thread_factory=Thread)
    request = SyncRequest(tmp_path / "batch.csv", tmp_path / "training")
    controller.start(request)
    first_event = controller.cancel_event
    threads[0].target(*threads[0].args)
    controller.poll()
    assert controller.model.state == "complete"

    controller.start(request)
    second_event = controller.cancel_event
    assert second_event is not first_event
    assert controller.model.state == "running"

    assert controller.request_close() is False
    assert second_event is not None and second_event.is_set()
    assert controller.model.state == "cancelling"
    assert controller.request_close() is False

    threads[1].target(*threads[1].args)
    controller.poll()
    assert controller.model.state == "complete"
    assert controller.request_close() is True


@pytest.mark.parametrize(
    ("csv_path", "root_path", "api_base", "message"),
    [
        ("", "C:/training", "https://findai.top/sukaseafood/api/v1", "请选择增量 CSV"),
        ("C:/batch.csv", "", "https://findai.top/sukaseafood/api/v1", "请选择训练集目录"),
        ("C:/batch.csv", "C:/training", "", "请输入 API 地址"),
    ],
)
def test_gui_build_request_validates_required_selections_before_worker(
    csv_path: str, root_path: str, api_base: str, message: str
) -> None:
    from sukaseafood_sync.gui import GuiValidationError, build_sync_request

    with pytest.raises(GuiValidationError, match=message):
        build_sync_request(csv_path, root_path, api_base, "", "", "")


def test_gui_build_request_keeps_proxy_overrides_memory_only() -> None:
    from sukaseafood_sync.gui import build_sync_request

    proxy_secret = "http://private-user:private-password@proxy.example:8080"
    request = build_sync_request(
        "C:/batch.csv",
        "C:/training",
        "https://findai.top/sukaseafood/api/v1",
        proxy_secret,
        "",
        "",
    )

    assert request.http_proxy == proxy_secret
    assert request.https_proxy is None
    assert request.no_proxy is None
    assert proxy_secret not in repr(request)


def test_gui_build_request_rejects_noncanonical_api_before_worker() -> None:
    from sukaseafood_sync.gui import GuiValidationError, build_sync_request

    with pytest.raises(GuiValidationError, match="API 地址无效") as caught:
        build_sync_request(
            "C:/batch.csv",
            "C:/training",
            "https://example.test/wrong/path",
            "",
            "",
            "",
        )
    assert caught.value.__context__ is None


def test_gui_visible_text_catalog_is_complete_and_chinese_without_display() -> None:
    from sukaseafood_sync.gui import GUI_LABELS, WINDOW_TITLE

    assert WINDOW_TITLE == "SukaSeafood 训练图片同步工具"
    assert GUI_LABELS == {
        "manifest": "增量 CSV",
        "root": "训练集目录",
        "api": "API 地址",
        "http_proxy": "HTTP 代理（可选）",
        "https_proxy": "HTTPS 代理（可选）",
        "no_proxy": "不使用代理的地址（可选）",
        "browse": "选择",
        "start": "开始同步",
        "cancel": "取消",
        "progress": "同步进度",
        "candidate": "当前候选图片",
        "log": "安全日志",
        "summary": "完成摘要",
    }


def test_gui_main_can_launch_with_injected_headless_root_and_app() -> None:
    from sukaseafood_sync import gui

    events: list[object] = []

    class Root:
        def title(self, value: str) -> None:
            events.append(("title", value))

        def protocol(self, name: str, callback) -> None:
            events.append(("protocol", name, callback))

        def mainloop(self) -> None:
            events.append("mainloop")

    root = Root()

    class App:
        def __init__(self, selected_root: Root) -> None:
            assert selected_root is root
            self.request_close = lambda: None
            events.append("app")

    exit_code = gui.main(root_factory=lambda: root, app_factory=App)

    assert exit_code == 0
    assert events[0] == ("title", gui.WINDOW_TITLE)
    assert events[1] == "app"
    assert events[2][0:2] == ("protocol", "WM_DELETE_WINDOW")
    assert events[3] == "mainloop"


def test_worker_start_failure_restores_controls_without_raw_exception(
    tmp_path: Path,
) -> None:
    from sukaseafood_sync.gui import GuiController, GuiValidationError
    from sukaseafood_sync.service import SyncRequest

    secret = "proxy-user:proxy-password@private.example"

    class Thread:
        def __init__(self, *, target, args, daemon: bool) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError(secret)

    controller = GuiController(lambda *_args: None, thread_factory=Thread)

    with pytest.raises(GuiValidationError) as caught:
        controller.start(
            SyncRequest(tmp_path / "batch.csv", tmp_path / "training")
        )

    assert controller.model.state == "failed"
    assert controller.model.selection_enabled is True
    assert secret not in str(caught.value)
    assert caught.value.__context__ is None


def test_every_illegal_gui_state_transition_is_rejected() -> None:
    from sukaseafood_sync.gui import GUI_STATES, GuiStateModel

    legal = {
        "idle": {"running"},
        "running": {"cancelling", "complete", "failed"},
        "cancelling": {"complete", "failed"},
        "complete": {"running"},
        "failed": {"running"},
    }
    for source in GUI_STATES:
        for target in GUI_STATES:
            if target in legal[source]:
                continue
            model = GuiStateModel(source)
            with pytest.raises(ValueError, match="INVALID_GUI_TRANSITION"):
                model.transition(target)


def test_worker_error_is_queue_delivered_as_safe_chinese_failure(tmp_path: Path) -> None:
    from sukaseafood_sync.gui import GuiController
    from sukaseafood_sync.service import SyncRequest

    threads: list[object] = []
    secret = "proxy-user:proxy-password@private.example"

    class Thread:
        def __init__(self, *, target, args, daemon: bool) -> None:
            self.target = target
            self.args = args
            threads.append(self)

        def start(self) -> None:
            return None

    def fail(*_args):
        raise RuntimeError(secret)

    controller = GuiController(fail, thread_factory=Thread)
    controller.start(SyncRequest(tmp_path / "batch.csv", tmp_path / "training"))
    threads[0].target(*threads[0].args)
    assert controller.model.state == "running"

    controller.poll()

    assert controller.model.state == "failed"
    assert controller.summary == "同步失败，请检查选择和网络设置"
    assert secret not in controller.summary + "".join(controller.logs)
