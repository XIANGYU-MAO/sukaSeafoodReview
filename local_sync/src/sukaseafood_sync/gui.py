from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from typing import Any, Callable, Literal

from .receipt import ReceiptError, validate_api_base
from .service import DEFAULT_API_BASE, SyncOutcome, SyncRequest, run_sync


GuiState = Literal["idle", "running", "cancelling", "complete", "failed"]
GUI_STATES = ("idle", "running", "cancelling", "complete", "failed")
WINDOW_TITLE = "SukaSeafood 训练图片同步工具"
GUI_LABELS = {
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
_TRANSITIONS: dict[str, frozenset[str]] = {
    "idle": frozenset({"running"}),
    "running": frozenset({"cancelling", "complete", "failed"}),
    "cancelling": frozenset({"complete", "failed"}),
    "complete": frozenset({"running"}),
    "failed": frozenset({"running"}),
}
_PHASE_LABELS = {
    "RECOVERING": "正在恢复",
    "WAITING": "正在等待来源站点",
    "DOWNLOADING": "正在下载",
    "APPLYING": "正在写入训练集",
    "SUCCEEDED": "处理成功",
    "SKIPPED": "已跳过",
    "FAILED": "处理失败",
    "CANCELLED": "正在取消",
    "COMPLETED": "批次处理完成",
}


@dataclass(frozen=True, slots=True)
class _ProgressMessage:
    event: object


@dataclass(frozen=True, slots=True)
class _ResultMessage:
    exit_code: int
    succeeded: int
    failed: int
    skipped: int
    offline_receipt_path: Path | None


@dataclass(frozen=True, slots=True)
class _ErrorMessage:
    message: str = "同步失败，请检查选择和网络设置"


class GuiValidationError(ValueError):
    pass


def build_sync_request(
    manifest_path: str,
    dataset_root: str,
    api_base: str,
    http_proxy: str,
    https_proxy: str,
    no_proxy: str,
) -> SyncRequest:
    csv_value = manifest_path.strip()
    root_value = dataset_root.strip()
    api_value = api_base.strip()
    if not csv_value:
        raise GuiValidationError("请选择增量 CSV")
    if not root_value:
        raise GuiValidationError("请选择训练集目录")
    if not api_value:
        raise GuiValidationError("请输入 API 地址")
    api_invalid = False
    try:
        checked_api = validate_api_base(api_value)
    except ReceiptError:
        api_invalid = True
        checked_api = ""
    if api_invalid:
        raise GuiValidationError("API 地址无效")
    return SyncRequest(
        manifest_path=Path(csv_value),
        dataset_root=Path(root_value),
        api_base=checked_api,
        http_proxy=http_proxy or None,
        https_proxy=https_proxy or None,
        no_proxy=no_proxy or None,
    )


@dataclass(slots=True)
class GuiStateModel:
    state: GuiState = "idle"

    @property
    def selection_enabled(self) -> bool:
        return self.state in {"idle", "complete", "failed"}

    @property
    def start_enabled(self) -> bool:
        return self.selection_enabled

    @property
    def cancel_enabled(self) -> bool:
        return self.state == "running"

    def transition(self, state: GuiState) -> None:
        if state not in _TRANSITIONS.get(self.state, frozenset()):
            raise ValueError("INVALID_GUI_TRANSITION")
        self.state = state


class GuiController:
    def __init__(
        self,
        service: Callable[..., SyncOutcome],
        *,
        thread_factory: Callable[..., object] = Thread,
    ) -> None:
        self.model = GuiStateModel()
        self._service = service
        self._thread_factory = thread_factory
        self._cancel_event: Event | None = None
        self._worker_thread: object | None = None
        self._queue: Queue[object] = Queue()
        self._logs: list[str] = []
        self.current_candidate: str | None = None
        self.progress = (0, 0)
        self.summary = ""

    @property
    def cancel_event(self) -> Event | None:
        return self._cancel_event

    @property
    def logs(self) -> tuple[str, ...]:
        return tuple(self._logs)

    def _worker(
        self,
        request: SyncRequest,
        cancel_event: Event,
        messages: Queue[object],
    ) -> None:
        try:
            outcome = self._service(
                request,
                cancel_event,
                lambda event: messages.put(_ProgressMessage(event)),
            )
            counts = outcome.counts
            messages.put(
                _ResultMessage(
                    outcome.exit_code,
                    int(counts["succeeded"]),
                    int(counts["failed"]),
                    int(counts["skipped"]),
                    outcome.offline_receipt_path,
                )
            )
        except Exception:
            messages.put(_ErrorMessage())

    def start(self, request: SyncRequest) -> None:
        self.model.transition("running")
        cancel_event = Event()
        messages: Queue[object] = Queue()
        self._cancel_event = cancel_event
        self._queue = messages
        self._logs.clear()
        self.current_candidate = None
        self.progress = (0, 0)
        self.summary = ""
        start_failed = False
        try:
            worker = self._thread_factory(
                target=self._worker,
                args=(request, cancel_event, messages),
                daemon=True,
            )
            self._worker_thread = worker
            worker.start()
        except Exception:
            start_failed = True
        if start_failed:
            cancel_event.set()
            self._worker_thread = None
            self.summary = "无法启动后台同步"
            self.model.transition("failed")
            raise GuiValidationError("无法启动后台同步")

    def cancel(self) -> None:
        if self.model.state != "running" or self._cancel_event is None:
            raise ValueError("INVALID_GUI_TRANSITION")
        self._cancel_event.set()
        self.model.transition("cancelling")

    def request_close(self) -> bool:
        if self.model.state == "running":
            self.cancel()
            return False
        if self.model.state == "cancelling":
            return False
        return True

    def poll(self) -> int:
        processed = 0
        while True:
            try:
                message = self._queue.get_nowait()
            except Empty:
                return processed
            processed += 1
            if isinstance(message, _ProgressMessage):
                event = message.event
                self.current_candidate = getattr(event, "candidate_id", None)
                self.progress = (
                    int(getattr(event, "current", 0)),
                    int(getattr(event, "total", 0)),
                )
                phase = str(getattr(event, "phase", ""))
                label = _PHASE_LABELS.get(phase, "正在处理")
                candidate = self.current_candidate
                self._logs.append(f"{label}：{candidate}" if candidate else label)
            elif isinstance(message, _ResultMessage):
                self.summary = (
                    f"成功 {message.succeeded}，失败 {message.failed}，"
                    f"已跳过 {message.skipped}"
                )
                if message.offline_receipt_path is not None:
                    self.summary += (
                        f"；离线回执：{message.offline_receipt_path}；"
                        "可在管理后台上传"
                    )
                self.model.transition(
                    "failed" if message.exit_code == 2 else "complete"
                )
            elif isinstance(message, _ErrorMessage):
                self.summary = message.message
                self.model.transition("failed")


class TrainingSyncApp:
    """Tk view; all synchronization work remains in GuiController's worker."""

    def __init__(
        self,
        root: Any,
        *,
        tk_module: Any,
        ttk_module: Any,
        filedialog_module: Any,
        messagebox_module: Any,
        scrolledtext_module: Any,
    ) -> None:
        self.root = root
        self._tk = tk_module
        self._ttk = ttk_module
        self._filedialog = filedialog_module
        self._messagebox = messagebox_module
        self.controller = GuiController(run_sync)
        self._close_pending = False
        self._rendered_logs = 0

        self.manifest_var = tk_module.StringVar()
        self.root_var = tk_module.StringVar()
        self.api_var = tk_module.StringVar(value=DEFAULT_API_BASE)
        self.http_proxy_var = tk_module.StringVar()
        self.https_proxy_var = tk_module.StringVar()
        self.no_proxy_var = tk_module.StringVar()
        self.candidate_var = tk_module.StringVar(value="-")
        self.summary_var = tk_module.StringVar(value="尚未开始同步")

        frame = ttk_module.Frame(root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        self._selection_widgets: list[Any] = []
        row = 0
        self.manifest_entry, self.manifest_button = self._path_row(
            frame,
            row,
            GUI_LABELS["manifest"],
            self.manifest_var,
            self._choose_manifest,
        )
        row += 1
        self.root_entry, self.root_button = self._path_row(
            frame,
            row,
            GUI_LABELS["root"],
            self.root_var,
            self._choose_root,
        )
        row += 1
        self.api_entry = self._entry_row(frame, row, GUI_LABELS["api"], self.api_var)
        row += 1
        self.http_proxy_entry = self._entry_row(
            frame, row, GUI_LABELS["http_proxy"], self.http_proxy_var
        )
        row += 1
        self.https_proxy_entry = self._entry_row(
            frame, row, GUI_LABELS["https_proxy"], self.https_proxy_var
        )
        row += 1
        self.no_proxy_entry = self._entry_row(
            frame, row, GUI_LABELS["no_proxy"], self.no_proxy_var
        )
        row += 1

        button_frame = ttk_module.Frame(frame)
        button_frame.grid(row=row, column=0, columnspan=3, pady=(8, 6))
        self.start_button = ttk_module.Button(
            button_frame, text=GUI_LABELS["start"], command=self._start
        )
        self.start_button.grid(row=0, column=0, padx=4)
        self.cancel_button = ttk_module.Button(
            button_frame, text=GUI_LABELS["cancel"], command=self._cancel
        )
        self.cancel_button.grid(row=0, column=1, padx=4)
        row += 1

        ttk_module.Label(frame, text=GUI_LABELS["progress"]).grid(
            row=row, column=0, sticky="w"
        )
        self.progress_bar = ttk_module.Progressbar(
            frame, mode="determinate", maximum=1, value=0
        )
        self.progress_bar.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        row += 1
        ttk_module.Label(frame, text=GUI_LABELS["candidate"]).grid(
            row=row, column=0, sticky="w"
        )
        ttk_module.Label(frame, textvariable=self.candidate_var).grid(
            row=row, column=1, columnspan=2, sticky="w"
        )
        row += 1
        ttk_module.Label(frame, text=GUI_LABELS["log"]).grid(
            row=row, column=0, sticky="nw"
        )
        self.log = scrolledtext_module.ScrolledText(
            frame, height=10, wrap="word", state="disabled"
        )
        self.log.grid(row=row, column=1, columnspan=2, sticky="nsew", pady=3)
        frame.rowconfigure(row, weight=1)
        row += 1
        ttk_module.Label(frame, text=GUI_LABELS["summary"]).grid(
            row=row, column=0, sticky="nw"
        )
        ttk_module.Label(
            frame,
            textvariable=self.summary_var,
            wraplength=640,
            justify="left",
        ).grid(row=row, column=1, columnspan=2, sticky="w")

        self._selection_widgets.extend(
            [
                self.manifest_entry,
                self.manifest_button,
                self.root_entry,
                self.root_button,
                self.api_entry,
                self.http_proxy_entry,
                self.https_proxy_entry,
                self.no_proxy_entry,
            ]
        )
        self._sync_widgets()
        root.after(100, self._poll)

    def _entry_row(self, frame: Any, row: int, label: str, variable: Any) -> Any:
        self._ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=2)
        entry = self._ttk.Entry(frame, textvariable=variable)
        entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)
        return entry

    def _path_row(
        self,
        frame: Any,
        row: int,
        label: str,
        variable: Any,
        command: Callable[[], None],
    ) -> tuple[Any, Any]:
        self._ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=2)
        entry = self._ttk.Entry(frame, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=2)
        button = self._ttk.Button(frame, text=GUI_LABELS["browse"], command=command)
        button.grid(row=row, column=2, padx=(6, 0), pady=2)
        return entry, button

    def _choose_manifest(self) -> None:
        selected = self._filedialog.askopenfilename(
            title="选择增量 CSV",
            filetypes=(("CSV 文件", "*.csv"), ("所有文件", "*.*")),
        )
        if selected:
            self.manifest_var.set(selected)

    def _choose_root(self) -> None:
        selected = self._filedialog.askdirectory(title="选择训练集目录")
        if selected:
            self.root_var.set(selected)

    def _start(self) -> None:
        try:
            request = build_sync_request(
                self.manifest_var.get(),
                self.root_var.get(),
                self.api_var.get(),
                self.http_proxy_var.get(),
                self.https_proxy_var.get(),
                self.no_proxy_var.get(),
            )
            self.controller.start(request)
        except (GuiValidationError, ValueError):
            self._messagebox.showerror("输入无效", "请检查增量 CSV、训练集目录和 API 地址")
            return
        self._rendered_logs = 0
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.summary_var.set("正在同步")
        self._sync_widgets()

    def _cancel(self) -> None:
        try:
            self.controller.cancel()
        except ValueError:
            return
        self.summary_var.set("正在安全取消，请稍候")
        self._sync_widgets()

    def _sync_widgets(self) -> None:
        selection_state = "normal" if self.controller.model.selection_enabled else "disabled"
        for widget in self._selection_widgets:
            widget.configure(state=selection_state)
        self.start_button.configure(
            state="normal" if self.controller.model.start_enabled else "disabled"
        )
        self.cancel_button.configure(
            state="normal" if self.controller.model.cancel_enabled else "disabled"
        )

    def _poll(self) -> None:
        self.controller.poll()
        current, total = self.controller.progress
        self.progress_bar.configure(maximum=max(total, 1), value=current)
        self.candidate_var.set(self.controller.current_candidate or "-")
        new_logs = self.controller.logs[self._rendered_logs :]
        if new_logs:
            self.log.configure(state="normal")
            for entry in new_logs:
                self.log.insert("end", entry + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
            self._rendered_logs += len(new_logs)
        if self.controller.summary:
            self.summary_var.set(self.controller.summary)
        self._sync_widgets()
        if self._close_pending and self.controller.request_close():
            self.root.destroy()
            return
        self.root.after(100, self._poll)

    def request_close(self) -> None:
        if self.controller.request_close():
            self.root.destroy()
            return
        self._close_pending = True
        self.summary_var.set("正在安全取消，完成后关闭窗口")
        self._sync_widgets()


def main(
    *,
    root_factory: Callable[[], Any] | None = None,
    app_factory: Callable[[Any], Any] | None = None,
) -> int:
    if root_factory is None or app_factory is None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk

        root_factory = tk.Tk

        def create_app(root: Any) -> TrainingSyncApp:
            return TrainingSyncApp(
                root,
                tk_module=tk,
                ttk_module=ttk,
                filedialog_module=filedialog,
                messagebox_module=messagebox,
                scrolledtext_module=scrolledtext,
            )

        app_factory = create_app
    root = root_factory()
    root.title(WINDOW_TITLE)
    app = app_factory(root)
    root.protocol("WM_DELETE_WINDOW", app.request_close)
    root.mainloop()
    return 0
