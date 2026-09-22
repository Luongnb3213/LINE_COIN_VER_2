"""GUI Tkinter cho LINE_COIN_VER_2, bám layout LineViet."""

from __future__ import annotations

import concurrent.futures
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import openpyxl
except Exception:  # pragma: no cover
    openpyxl = None

from flows.phase2 import CHROME_PACKAGE, LINE_PACKAGE
from flows.google_prepare import GoogleCredential, next_prepare_names, prepare_google_instance
from flows.xlsx_store import XlsxStore, XlsxStoreError
from device.controller import DeviceController
from ldplayer.adapter import LDPlayerAdapter, LDPlayerConfig
from phase1_test import run_phase1_once
from phase2_test import run_phase2_once
from smoke_test import run_smoke_test_once
from xiaowei.client import XiaoweiClient
from xiaowei.transport import WebSocketTransport

ROOT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = ROOT_DIR / "config.json"
DEFAULT_XIAOWEI_WS = "ws://127.0.0.1:22222/"


def _load_config_dict() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class Phase2GUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LineViet — đăng ký LINE trên máy ảo")
        self.geometry("1180x820")
        self.running = False
        self.stopping = False
        self.config_data = _load_config_dict()
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.vars: dict[str, tk.Variable] = {}
        self.stop_event: threading.Event | None = None

        self._build()
        if not str(self.vars["ld_path"].get()).strip():
            self.after(300, lambda: self.detect_ld_path(im_lang=True))
        self.after(200, self._drain_logs)
        self.after(2000, self._refresh_stats)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log_queue.put(f"Đã mở {CONFIG_PATH}")

    def _var(self, name, value, kind="str"):
        var = {"bool": tk.BooleanVar, "int": tk.IntVar}.get(kind, tk.StringVar)(value=value)
        self.vars[name] = var
        return var

    def _build(self):
        data = self.config_data
        pad = {"padx": 6, "pady": 3}
        top = ttk.LabelFrame(self, text="Cấu hình", padding=8)
        top.pack(fill="x", padx=8, pady=6)

        ttk.Label(top, text="File Excel").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(top, textvariable=self._var("xlsx_path", data.get("xlsx_path", "")), width=64)\
            .grid(row=0, column=1, columnspan=3, sticky="we", **pad)
        ttk.Button(top, text="Chọn...", command=self.choose_xlsx).grid(row=0, column=4, **pad)
        ttk.Button(top, text="Tạo file mẫu", command=self.create_template).grid(row=0, column=5, **pad)

        ttk.Label(top, text="Số instance chạy cùng lúc").grid(row=1, column=0, sticky="e", **pad)
        ttk.Spinbox(top, from_=1, to=8, width=6,
                    textvariable=self._var("worker_count", int(data.get("worker_count", 1) or 1), "int"))\
            .grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(top, text="(mỗi máy 2-4GB RAM, boot 30-60s)",
                  foreground="#707070").grid(row=1, column=2, sticky="w", **pad)

        ttk.Label(top, text="Tổng số tài khoản chạy").grid(row=1, column=3, sticky="e", **pad)
        ttk.Spinbox(top, from_=0, to=100000, width=8,
                    textvariable=self._var("run_limit", int(data.get("run_limit", 1) or 0), "int"))\
            .grid(row=1, column=4, sticky="w", **pad)
        ttk.Label(top, text="(0 = chạy hết file Excel)",
                  foreground="#707070").grid(row=1, column=5, sticky="w", **pad)

        ttk.Label(top, text="Thư mục LDPlayer").grid(row=2, column=0, sticky="e", **pad)
        ttk.Entry(top, textvariable=self._var("ld_path", data.get("ld_path", "")), width=64)\
            .grid(row=2, column=1, columnspan=3, sticky="we", **pad)
        ttk.Button(top, text="Chọn...", command=self.choose_ld_path).grid(row=2, column=4, **pad)
        ttk.Button(top, text="Tự tìm", command=self.detect_ld_path).grid(row=2, column=5, **pad)

        ttk.Label(top, text="Instance mẫu").grid(row=3, column=0, sticky="e", **pad)
        ttk.Entry(top, textvariable=self._var("ld_template", data.get("ld_template", "LDPlayer")), width=28)\
            .grid(row=3, column=1, sticky="w", **pad)
        ttk.Button(top, text="Xem instance", command=self.list_instances).grid(row=3, column=2, sticky="w", **pad)
        ttk.Label(top, text="Email chạy").grid(row=3, column=3, sticky="e", **pad)
        ttk.Entry(top, textvariable=self._var("email", ""), width=28)\
            .grid(row=3, column=4, columnspan=2, sticky="we", **pad)

        ttk.Label(top, text="Mật khẩu chung").grid(row=4, column=0, sticky="e", **pad)
        ttk.Entry(top, textvariable=self._var("default_password", data.get("default_password", "LineViet@2026")),
                  width=26).grid(row=4, column=1, sticky="w", **pad)
        ttk.Label(top, text="dùng cho MỌI account — LINE đòi ≥ 8 ký tự",
                  foreground="#707070").grid(row=4, column=2, columnspan=3, sticky="w", **pad)
        ttk.Label(top, text="Source Phase 2").grid(row=4, column=3, sticky="e", **pad)
        ttk.Combobox(top, textvariable=self._var("source", "both"),
                     values=("both", "airwallet", "ministop"), state="readonly", width=12)\
            .grid(row=4, column=4, sticky="w", **pad)

        ttk.Label(top, text="Năm sinh từ").grid(row=5, column=0, sticky="e", **pad)
        ttk.Spinbox(top, from_=1900, to=2100, width=8,
                    textvariable=self._var("dob_year_min", int(data.get("dob_year_min", 1980) or 1980), "int"))\
            .grid(row=5, column=1, sticky="w", **pad)
        ttk.Label(top, text="đến").grid(row=5, column=2, sticky="w", **pad)
        ttk.Spinbox(top, from_=1900, to=2100, width=8,
                    textvariable=self._var("dob_year_max", int(data.get("dob_year_max", 2000) or 2000), "int"))\
            .grid(row=5, column=3, sticky="w", **pad)
        ttk.Label(top, text="mỗi email một ngày cố định",
                  foreground="#707070").grid(row=5, column=4, sticky="w", **pad)

        ttk.Checkbutton(top, text="Dùng Proxy",
                        variable=self._var("use_proxy", _as_bool(data.get("use_proxy")), "bool"))\
            .grid(row=6, column=0, sticky="w", **pad)
        ttk.Checkbutton(top, text="Máy sạch mỗi account (nhân bản instance mẫu)",
                        variable=self._var("device_fresh_clone", _as_bool(data.get("device_fresh_clone"), True), "bool"),
                        command=self._hint_clone)\
            .grid(row=6, column=1, columnspan=2, sticky="w", **pad)

        ttk.Label(top, text="Luồng chạy").grid(row=7, column=0, sticky="e", **pad)
        ttk.Label(top, text="Bắt đầu = Phase 1 rồi tự chạy Phase 2, ghi mã về Excel",
                  foreground="#204080").grid(row=7, column=1, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(top, text="Xoá instance khi SUCCESS",
                        variable=self._var("remove_instance", False, "bool"))\
            .grid(row=7, column=3, columnspan=2, sticky="w", **pad)

        ttk.Label(top, text="WebSocket Xiaowei").grid(row=8, column=0, sticky="e", **pad)
        ttk.Entry(top, textvariable=self._var("xiaowei_ws_url", data.get("xiaowei_ws_url", DEFAULT_XIAOWEI_WS)),
                  width=30).grid(row=8, column=1, sticky="w", **pad)
        ttk.Label(top, text="Instance index").grid(row=8, column=2, sticky="e", **pad)
        ttk.Entry(top, textvariable=self._var("instance_index", "0"), width=8).grid(row=8, column=3, sticky="w", **pad)
        ttk.Label(top, text="hoặc name").grid(row=8, column=4, sticky="e", **pad)
        ttk.Entry(top, textvariable=self._var("instance_name", ""), width=20).grid(row=8, column=5, sticky="w", **pad)
        top.columnconfigure(1, weight=1)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8)
        self.start_button = ttk.Button(bar, text="▶ Bắt đầu", command=self.start_run)
        self.start_button.pack(side="left", padx=4)
        self.stop_button = ttk.Button(bar, text="■ Dừng tất cả máy", command=self.stop_bot)
        self.stop_button.pack(side="left", padx=4)
        ttk.Button(bar, text="Lưu cấu hình", command=self.save_settings).pack(side="left", padx=4)
        ttk.Button(bar, text="Test kết nối", command=self.start_smoke_test).pack(side="left", padx=4)
        self.stats_label = ttk.Label(bar, text="", foreground="#204080")
        self.stats_label.pack(side="left", padx=16)

        self._build_google_like_panel(self)

        self.log_frame = ttk.LabelFrame(self, text="Nhật ký", padding=4)
        self.log_frame.pack(fill="both", expand=True, padx=8, pady=6)
        self.log_text = tk.Text(self.log_frame, height=18, wrap="none")
        scroll = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_google_like_panel(self, root):
        panel = ttk.LabelFrame(
            root,
            text="Máy mẫu Google — tự lấy email/password từ Excel, bạn chỉ xác nhận 2FA nếu Google hỏi",
            padding=6,
        )
        panel.pack(fill="x", padx=8, pady=(0, 4))
        controls = ttk.Frame(panel)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Label(controls, text="Số máy chuẩn bị").pack(side="left", padx=(0, 6))
        ttk.Spinbox(controls, from_=1, to=100000, width=7,
                    textvariable=self._var("google_prepare_count", int(self.config_data.get("google_prepare_count", 1) or 1), "int"))\
            .pack(side="left", padx=(0, 12))
        ttk.Label(controls, text="Chạy song song").pack(side="left", padx=(0, 6))
        ttk.Spinbox(controls, from_=1, to=8, width=6,
                    textvariable=self._var("google_prepare_parallel", int(self.config_data.get("google_prepare_parallel", 1) or 1), "int"))\
            .pack(side="left", padx=(0, 12))
        ttk.Button(controls, text="Chuẩn bị Google", command=self.start_prepare_google).pack(side="left", padx=4)
        ttk.Button(controls, text="Làm mới kho", command=self.refresh_instance_panel).pack(side="left", padx=4)
        ttk.Button(controls, text="Xóa instance", command=self.delete_selected_instance).pack(side="left", padx=4)
        self.google_status = ttk.Label(controls, text="", foreground="#204080")
        self.google_status.pack(side="left", padx=12)

        columns = ("index", "instance", "status", "google", "assigned")
        labels = {"index": "Index", "instance": "Instance", "status": "Trạng thái", "google": "Google", "assigned": "Dùng cho"}
        widths = {"index": 70, "instance": 180, "status": 100, "google": 260, "assigned": 260}
        self.google_tree = ttk.Treeview(panel, columns=columns, show="headings", height=4, selectmode="browse")
        for column in columns:
            self.google_tree.heading(column, text=labels[column])
            self.google_tree.column(column, width=widths[column], anchor="w")
        self.google_tree.pack(fill="x")
        self.google_tree.bind("<<TreeviewSelect>>", self._on_instance_selected)
        self.after(400, self.refresh_instance_panel)

    def _hint_clone(self):
        if not bool(self.vars["device_fresh_clone"].get()):
            messagebox.showwarning(
                "Máy sạch",
                "Ver 2 hiện ưu tiên chạy Phase 2 trên instance đã chọn; không tự clear app data.",
            )

    def choose_xlsx(self):
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx")])
        if path:
            self.vars["xlsx_path"].set(path)

    def create_template(self):
        if openpyxl is None:
            messagebox.showerror("Tạo file mẫu", "Thiếu openpyxl. Chạy: py -m pip install -r requirements.txt")
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Mails"
        ws.append(["email", "email_password", "display_name", "account_password", "status", "error_details",
                   "google_instance_status", "gift_code", "gift_code_ministop", "phase2_status",
                   "phase2_message", "phase2_updated_at"])
        wb.save(path)
        self.vars["xlsx_path"].set(path)
        messagebox.showinfo("LineViet", f"Đã tạo {path}")

    def choose_ld_path(self):
        path = filedialog.askdirectory(title="Chọn thư mục cài LDPlayer")
        if path:
            self.vars["ld_path"].set(path)

    def detect_ld_path(self, im_lang: bool = False) -> bool:
        for folder in [Path("C:/LDPlayer/LDPlayer14"), Path("C:/LDPlayer/LDPlayer9"),
                       Path("D:/LDPlayer/LDPlayer14"), Path("D:/LDPlayer/LDPlayer9")]:
            if (folder / "ldconsole.exe").exists():
                self.vars["ld_path"].set(str(folder).replace("\\", "/"))
                if not im_lang:
                    messagebox.showinfo("Tự tìm LDPlayer", f"Đã chọn:\n{folder}")
                return True
        if not im_lang:
            messagebox.showwarning("Tự tìm LDPlayer", "Không thấy LDPlayer. Bấm 'Chọn...' và trỏ vào thư mục có ldconsole.exe.")
        return False

    def _ldplayer_adapter(self) -> LDPlayerAdapter:
        ld_path = str(self.vars["ld_path"].get()).strip().rstrip("\\/")
        return LDPlayerAdapter(LDPlayerConfig(
            ld_console=f"{ld_path}/ldconsole.exe" if ld_path else "ldconsole.exe",
            adb_path=f"{ld_path}/adb.exe" if ld_path else "adb",
            boot_timeout=int(self.config_data.get("emulator_boot_timeout", 180) or 180),
        ))

    def list_instances(self):
        try:
            rows = self._ldplayer_adapter().list_instances()
        except Exception as exc:
            messagebox.showerror("LDPlayer", f"Không gọi được ldconsole:\n{exc}")
            return
        if not rows:
            messagebox.showwarning("LDPlayer", "Không thấy instance nào.")
            return
        lines = [f"[{row.index}] {row.name}{'  (đang chạy)' if row.running else ''}" for row in rows]
        messagebox.showinfo("Instance trong LDPlayer", "\n".join(lines))

    def refresh_instance_panel(self):
        try:
            self.google_tree.delete(*self.google_tree.get_children())
            rows = self._ldplayer_adapter().list_instances()
            prepared = self._google_prepare_by_instance()
            for row in rows:
                account = prepared.get(row.name)
                self.google_tree.insert(
                    "",
                    "end",
                    iid=str(row.index),
                    values=(
                        row.index,
                        row.name,
                        "running" if row.running else "stopped",
                        account.google_prepare_status if account else "",
                        account.email if account else "",
                    ),
                )
            running = sum(1 for row in rows if row.running)
            self.google_status.configure(text=f"instance: tổng {len(rows)} | đang chạy {running}")
        except Exception as exc:
            self.google_status.configure(text=f"không đọc được instance: {exc}")

    def _google_prepare_by_instance(self):
        return {}

    def _on_instance_selected(self, _event=None):
        selected = self.google_tree.selection()
        if not selected:
            return
        item = self.google_tree.item(selected[0])
        values = item.get("values") or []
        if len(values) >= 2:
            self.vars["instance_index"].set(str(values[0]))
            self.vars["instance_name"].set("")

    def delete_selected_instance(self):
        if self.running or self.stopping:
            messagebox.showwarning("Xóa instance", "Tool đang chạy, dừng xong rồi hãy xóa instance.")
            return
        selected = self.google_tree.selection()
        if not selected:
            messagebox.showwarning("Xóa instance", "Chọn một instance trong danh sách trước.")
            return
        item = self.google_tree.item(selected[0])
        values = item.get("values") or []
        if len(values) < 2:
            messagebox.showerror("Xóa instance", "Không đọc được instance đã chọn.")
            return
        try:
            index = int(values[0])
        except (TypeError, ValueError):
            messagebox.showerror("Xóa instance", f"Index instance không hợp lệ: {values[0]!r}")
            return
        name = str(values[1])
        template_name = str(self.vars["ld_template"].get()).strip()
        if template_name and name == template_name:
            messagebox.showwarning(
                "Không thể xóa instance mẫu",
                f"Instance `{name}` đang được cấu hình là Instance mẫu nên không được xóa.",
            )
            self.log_queue.put(f"Chặn xóa instance mẫu {index} ({name}).")
            return
        if not messagebox.askyesno(
            "Xóa hẳn instance",
            f"Xóa hẳn LDPlayer instance này?\n\nIndex: {index}\nTên: {name}\n\nThao tác này không hoàn tác được.",
        ):
            return
        try:
            self._ldplayer_adapter().remove_instance(index)
        except Exception as exc:
            messagebox.showerror("Xóa instance", f"Xóa instance thất bại:\n{exc}")
            self.log_queue.put(f"Xóa instance {index} ({name}) thất bại: {exc}")
            return
        self.log_queue.put(f"Đã xóa hẳn instance {index} ({name}).")
        if str(self.vars["instance_index"].get()).strip() == str(index):
            self.vars["instance_index"].set("")
        if str(self.vars["instance_name"].get()).strip() == name:
            self.vars["instance_name"].set("")
        self.refresh_instance_panel()

    def save_settings(self, show_message: bool = True):
        data = dict(self.config_data)
        for key, var in self.vars.items():
            if key in {"instance_index", "instance_name", "email", "source", "remove_instance"}:
                continue
            data[key] = var.get()
        data["ld_path"] = str(data.get("ld_path", "")).strip().rstrip("\\/")
        data["ld_console"] = str(data.get("ld_console", "") or "")
        data["adb_path"] = str(data.get("adb_path", "") or "")
        data["run_limit"] = max(0, int(data.get("run_limit") or 0))
        data["worker_count"] = max(1, int(data.get("worker_count") or 1))
        data["ld_lanes"] = max(1, int(data.get("worker_count") or 1))
        lo = int(data.get("dob_year_min") or 1980)
        hi = int(data.get("dob_year_max") or 2000)
        if lo > hi:
            lo, hi = hi, lo
        data["dob_year_min"], data["dob_year_max"] = lo, hi
        data["phone_source"] = "sim"
        data["xiaowei_ws_url"] = str(data.get("xiaowei_ws_url") or DEFAULT_XIAOWEI_WS)
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.config_data = data
        self.log_queue.put(f"Đã lưu cấu hình vào {CONFIG_PATH}")
        if show_message:
            messagebox.showinfo("LineViet", "Đã lưu cấu hình.")

    def _xiaowei_client(self) -> XiaoweiClient:
        ws_url = str(self.vars["xiaowei_ws_url"].get()).strip() or DEFAULT_XIAOWEI_WS
        transport = WebSocketTransport(
            ws_url,
            connect_timeout=float(self.config_data.get("xiaowei_connect_timeout") or 10.0),
            request_timeout=float(self.config_data.get("xiaowei_request_timeout") or 20.0),
        )
        return XiaoweiClient(
            transport,
            max_retries=int(self.config_data.get("xiaowei_max_retries") or 2),
            retry_backoff=float(self.config_data.get("xiaowei_retry_backoff") or 0.5),
            screenshot_dir=str(self.config_data.get("xiaowei_screenshot_dir") or ""),
        )

    def _read_google_credentials(self) -> list[GoogleCredential]:
        xlsx_path = str(self.vars["xlsx_path"].get()).strip()
        if not xlsx_path:
            raise XlsxStoreError("Chưa chọn file Excel.")
        active_sheet = str(self.config_data.get("active_sheet") or "Mails")
        limit = max(1, int(self.vars["google_prepare_count"].get() or 1))
        store = XlsxStore(xlsx_path, active_sheet)
        accounts = store.get_pending_accounts(limit=0)

        credentials: list[GoogleCredential] = []
        for account in accounts:
            if not account.email or not account.email_password:
                self.log_queue.put(f"Bỏ qua {account.email or '(trống)'}: thiếu email/email_password.")
                continue
            prepared_ok = account.google_prepare_status.strip().upper() == "SUCCESS"
            if prepared_ok:
                self.log_queue.put(f"Bỏ qua {account.email}: google_instance_status=SUCCESS.")
                continue
            credentials.append(GoogleCredential(email=account.email, password=account.email_password))
            if len(credentials) >= limit:
                break
        return credentials

    def start_prepare_google(self):
        if self.running or self.stopping:
            return
        template_name = str(self.vars["ld_template"].get()).strip()
        if not template_name:
            messagebox.showerror("Chuẩn bị Google", "Cần nhập Instance mẫu.")
            return
        try:
            credentials = self._read_google_credentials()
        except XlsxStoreError as exc:
            messagebox.showerror("Chuẩn bị Google", str(exc))
            return
        if not credentials:
            messagebox.showerror("Chuẩn bị Google", "Không đọc được email/email_password pending nào từ Excel.")
            return

        self.save_settings(show_message=False)
        self.running = True
        self.stopping = False
        self.stop_event = threading.Event()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        threading.Thread(
            target=self._run_prepare_google,
            args=(template_name, credentials, self.stop_event),
            daemon=True,
        ).start()

    def _run_prepare_google(self, template_name: str, credentials: list[GoogleCredential], stop_event: threading.Event):
        code = 0
        try:
            prefix = str(self.config_data.get("template_prefix") or "LineViet-g")
            names = next_prepare_names(self._ldplayer_adapter(), prefix, len(credentials))
            parallel = min(
                len(credentials),
                max(1, int(self.vars["google_prepare_parallel"].get() or 1)),
            )
            jobs = list(enumerate(zip(credentials, names), start=1))
            self.log_queue.put(
                f"Chuẩn bị Google: {len(credentials)} account, chạy song song tối đa {parallel} máy."
            )

            with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
                futures = {}
                for offset, (credential, new_name) in jobs:
                    if stop_event.is_set():
                        self.log_queue.put("Dừng chuẩn bị Google theo yêu cầu.")
                        code = 1
                        break
                    self.log_queue.put(
                        f"Chuẩn bị Google {offset}/{len(credentials)}: {credential.email} -> {new_name}"
                    )
                    future = executor.submit(
                        self._prepare_google_one,
                        template_name,
                        credential,
                        prefix,
                        new_name,
                        stop_event,
                    )
                    futures[future] = (credential, new_name)

                for future in concurrent.futures.as_completed(futures):
                    credential, new_name = futures[future]
                    try:
                        prepared = future.result()
                        self._xlsx_store().update_google_prepare_success(credential.email, prepared.name)
                        self.log_queue.put(
                            f"Chuẩn bị Google xong cho {credential.email}: {prepared.name} (index {prepared.index})"
                        )
                    except Exception as exc:
                        code = 1
                        try:
                            self._xlsx_store().update_google_prepare_failed(credential.email, new_name, str(exc))
                        except Exception as write_exc:
                            self.log_queue.put(f"Ghi Excel lỗi cho {credential.email}: {write_exc}")
                        self.log_queue.put(f"Chuẩn bị Google lỗi cho {credential.email} ({new_name}): {exc}")
                if stop_event.is_set():
                    self.log_queue.put("Dừng chuẩn bị Google theo yêu cầu.")
                    code = 1
            self.log_queue.put("Chuẩn bị Google xong.")
        except Exception as exc:
            code = 1
            self.log_queue.put(f"Chuẩn bị Google lỗi: {exc}")
        self.after(0, lambda: self._finish_run(code))

    def _xlsx_store(self) -> XlsxStore:
        return XlsxStore(
            str(self.vars["xlsx_path"].get()).strip(),
            str(self.config_data.get("active_sheet") or "Mails"),
        )

    def _prepare_google_one(
        self,
        template_name: str,
        credential: GoogleCredential,
        prefix: str,
        new_name: str,
        stop_event: threading.Event,
    ):
        ldplayer = self._ldplayer_adapter()
        controller = DeviceController(ldplayer, self._xiaowei_client())
        return prepare_google_instance(
            ldplayer=ldplayer,
            controller=controller,
            template_name=template_name,
            credential=credential,
            prefix=prefix,
            new_name=new_name,
            stop_event=stop_event,
            log=self.log_queue.put,
        )

    def _resolve_target(self):
        index_raw = str(self.vars["instance_index"].get()).strip()
        name_raw = str(self.vars["instance_name"].get()).strip()
        if index_raw and name_raw:
            messagebox.showerror("Instance", "Chỉ điền MỘT trong hai ô: Instance index HOẶC instance name.")
            return None
        if name_raw:
            return None, name_raw
        if index_raw:
            try:
                return int(index_raw), None
            except ValueError:
                messagebox.showerror("Instance", f"Instance index phải là số nguyên, đang là `{index_raw}`.")
            return None
        return None, None

    def _resolve_email(self) -> str:
        email = str(self.vars["email"].get()).strip()
        if email:
            return email

        xlsx_path = str(self.vars["xlsx_path"].get()).strip()
        if not xlsx_path:
            raise XlsxStoreError("Chưa chọn file Excel và ô email đang trống.")
        active_sheet = str(self.config_data.get("active_sheet") or "Mails")
        accounts = XlsxStore(xlsx_path, active_sheet).get_pending_accounts(limit=1)
        if not accounts:
            raise XlsxStoreError(
                "Không có account pending trong Excel. Nếu muốn chạy lại một email đã SUCCESS, nhập email đó vào ô Email chạy."
            )
        picked = accounts[0].email
        self.vars["email"].set(picked)
        self.log_queue.put(f"Tự chọn account pending đầu tiên từ Excel: {picked}")
        return picked

    def start_run(self):
        """Handler của nút "Bắt đầu": luôn chạy full flow Phase 1 + Phase 2.

        Nếu email đã `line_status=SUCCESS` trong Excel, bỏ qua Phase 1 và
        chạy thẳng Phase 2 để tránh đăng ký lại một tài khoản đã xong.
        """
        if self.running or self.stopping:
            return
        try:
            email = self._resolve_email()
        except XlsxStoreError as exc:
            messagebox.showerror("Bắt đầu", str(exc))
            return
        target = self._resolve_target()
        if target is None:
            return

        xlsx_path = str(self.vars["xlsx_path"].get()).strip()
        skip_phase1 = False
        if xlsx_path:
            active_sheet = str(self.config_data.get("active_sheet") or "Mails")
            try:
                account = XlsxStore(xlsx_path, active_sheet).get_account(email)
            except XlsxStoreError as exc:
                messagebox.showerror("Excel", f"Không đọc được Excel:\n{exc}")
                return
            skip_phase1 = account is not None and account.line_status.strip().upper() == "SUCCESS"

        if skip_phase1:
            self.log_queue.put(f"[{email}] line_status đã SUCCESS — bỏ qua Phase 1, chạy thẳng Phase 2.")
            self._start_phase2_only(email, target)
            return

        self.save_settings(show_message=False)
        self.running = True
        self.stopping = False
        self.stop_event = threading.Event()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        index, name = target
        threading.Thread(
            target=self._run_full_flow,
            args=(index, name, email, self.vars["source"].get(), bool(self.vars["remove_instance"].get()), self.stop_event),
            daemon=True,
        ).start()

    def _run_full_flow(self, index, name, email, source, remove_instance, stop_event):
        try:
            code = run_phase1_once(
                str(CONFIG_PATH), index=index, name=name, email=email,
                remove_instance=remove_instance,
                run_phase2_after_success=True,
                source=source, clear_app_data=False, log_dir=str(ROOT_DIR / "logs"),
                stop_event=stop_event, log_callback=self.log_queue.put,
            )
        except Exception as exc:
            self.log_queue.put(f"Phase 1 lỗi: {exc}")
            code = 1
        self.after(0, lambda: self._finish_run(code))

    def _start_phase2_only(self, email: str, target):
        self.save_settings(show_message=False)
        self.running = True
        self.stopping = False
        self.stop_event = threading.Event()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        index, name = target
        threading.Thread(target=self._run_phase2,
                         args=(index, name, email, self.vars["source"].get(), bool(self.vars["remove_instance"].get()), self.stop_event),
                         daemon=True).start()

    def _run_phase2(self, index, name, email, source, remove_instance, stop_event):
        try:
            code = run_phase2_once(str(CONFIG_PATH), index=index, name=name, email=email, source=source,
                                   remove_instance=remove_instance, log_dir=str(ROOT_DIR / "logs"),
                                   stop_event=stop_event, log_callback=self.log_queue.put)
        except Exception as exc:
            self.log_queue.put(f"Phase 2 lỗi: {exc}")
            code = 1
        self.after(0, lambda: self._finish_run(code))

    def start_smoke_test(self):
        if self.running or self.stopping:
            return
        target = self._resolve_target()
        if target is None:
            return
        self.save_settings(show_message=False)
        self.running = True
        self.stopping = False
        self.stop_event = threading.Event()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        index, name = target
        threading.Thread(target=self._run_smoke, args=(index, name, self.stop_event), daemon=True).start()

    def _run_smoke(self, index, name, stop_event):
        try:
            code = run_smoke_test_once(str(CONFIG_PATH), index=index, name=name,
                                       screenshot=str(ROOT_DIR / "smoke_screenshot.png"),
                                       stop_event=stop_event, log_callback=self.log_queue.put)
        except Exception as exc:
            self.log_queue.put(f"Test kết nối lỗi: {exc}")
            code = 1
        self.after(0, lambda: self._finish_run(code))

    def stop_bot(self):
        if self.stopping:
            return
        self.stopping = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        if self.stop_event is not None:
            self.stop_event.set()
        self.log_queue.put("Đã gửi yêu cầu dừng.")
        threading.Thread(target=self._force_stop_apps, daemon=True).start()

    def _force_stop_apps(self):
        stop_event = self.stop_event
        controller = getattr(stop_event, "controller", None) if stop_event is not None else None
        bound = getattr(stop_event, "bound", None) if stop_event is not None else None
        if controller is not None and bound is not None:
            try:
                controller.stop_app(bound, CHROME_PACKAGE)
                controller.stop_app(bound, LINE_PACKAGE)
                self.log_queue.put("Đã force-stop Chrome/LINE (KHÔNG xoá app data).")
            except Exception as exc:
                self.log_queue.put(f"Không force-stop được Chrome/LINE: {exc}")
        else:
            self.log_queue.put("Chưa bind được thiết bị — không có Chrome/LINE để force-stop.")
        self.after(0, lambda: self._finish_run(1))

    def _finish_run(self, code: int):
        self.running = False
        self.stopping = False
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="normal")
        self.log_queue.put("Hoàn tất." if code == 0 else "Kết thúc với lỗi hoặc đã dừng.")
        self.refresh_instance_panel()

    def _drain_logs(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
        except queue.Empty:
            pass
        finally:
            self.after(200, self._drain_logs)

    def _refresh_stats(self):
        status = "đang chạy 1" if self.running else "đang chạy 0"
        self.stats_label.configure(text=f"Chờ 0 | {status} | xong 0 | lỗi 0")
        self.after(2000, self._refresh_stats)

    def on_close(self):
        if self.running and not messagebox.askyesno("Thoát", "Tool đang chạy. Thoát luôn?"):
            return
        if self.stop_event is not None:
            self.stop_event.set()
        self.destroy()


def run():
    Phase2GUI().mainloop()


if __name__ == "__main__":
    run()
