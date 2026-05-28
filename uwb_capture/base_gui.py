"""Base Tk GUI for UWB measurement capture."""

from __future__ import annotations

import math
import queue
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .common import (
    ALERT_PROMPT_COOLDOWN_SECONDS,
    DEFAULT_BAUD,
    DEFAULT_DB_NAME,
    INSTABILITY_WINDOW_SIZE,
    MAX_TABLE_ROWS,
    ParsedRecord,
    now_local_iso,
    safe_filename,
    safe_float,
    safe_int,
)
from .parser import parse_serial_line
from .serial_io import SerialException, SerialWorker, list_ports
from .store import MeasurementStore

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = APP_DIR / "Measurements" / "GUI_Captures"

class UwbCaptureApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("UWB Measurement Capture")
        self.geometry("1200x760")
        self.minsize(980, 620)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.serial_worker: SerialWorker | None = None
        self.connected_port: str | None = None
        self.port_infos: dict[str, Any] = {}
        self.last_port_devices: tuple[str, ...] = ()
        self.capture_active = False
        self.current_session_id: str | None = None
        self.store: MeasurementStore | None = None
        self.table_rows = 0
        self.last_alert_prompt: dict[str, float] = {}
        self.last_instability_prompt: dict[str, float] = {}
        self.anchor_distance_windows: dict[str, list[float]] = {}
        self._build_variables()
        self._build_style()
        self._build_layout()
        self.refresh_ports()
        self.after(500, self.auto_port_poll)
        self.after(80, self.process_events)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_variables(self) -> None:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.output_dir_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.db_path_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR / DEFAULT_DB_NAME))
        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value=str(DEFAULT_BAUD))
        self.auto_connect_var = tk.BooleanVar(value=True)
        self.constellation_var = tk.StringVar()
        self.los_var = tk.BooleanVar(value=True)
        self.nlos_var = tk.BooleanVar(value=False)
        self.ground_truth_var = tk.StringVar()
        self.threshold_var = tk.StringVar(value="0.50")
        self.instability_threshold_var = tk.StringVar(value="0.35")
        self.status_var = tk.StringVar(value="Disconnected")
        self.session_status_var = tk.StringVar(value="No active session")
        self.sample_count_var = tk.StringVar(value="0")
        self.alert_count_var = tk.StringVar(value="0")
        self.raw_count_var = tk.StringVar(value="0")

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 9, "bold"))

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=0)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        left = ttk.Frame(root, width=360)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_propagate(False)

        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self._build_connection_panel(left)
        self._build_session_panel(left)
        self._build_storage_panel(left)
        self._build_stats_panel(left)
        self._build_live_panel(right)
        self._build_log_panel(right)

    def _build_connection_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Serial Connection", padding=8)
        frame.pack(fill=tk.X, pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Port").grid(row=0, column=0, sticky="w", pady=2)
        self.port_combo = ttk.Combobox(frame, textvariable=self.port_var, state="normal")
        self.port_combo.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Button(frame, text="Refresh", command=self.refresh_ports).grid(row=0, column=2, padx=(6, 0), pady=2)

        ttk.Label(frame, text="Baud").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(frame, textvariable=self.baud_var, width=12).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Checkbutton(frame, text="Auto-connect", variable=self.auto_connect_var).grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="w",
            pady=2,
        )

        button_row = ttk.Frame(frame)
        button_row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        button_row.columnconfigure(0, weight=1)
        button_row.columnconfigure(1, weight=1)
        self.connect_button = ttk.Button(button_row, text="Connect", command=self.connect_selected_port)
        self.connect_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(button_row, text="Disconnect", command=self.disconnect_serial).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(5, 0),
        )

        ttk.Label(frame, textvariable=self.status_var, style="Status.TLabel").grid(
            row=4,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )

    def _build_session_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Measurement Session", padding=8)
        frame.pack(fill=tk.X, pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        rows = [
            ("Constellation", self.constellation_var),
            ("Laser ground truth m", self.ground_truth_var),
            ("LOS alert threshold m", self.threshold_var),
            ("Stability alert std m", self.instability_threshold_var),
        ]
        for row_index, (label, variable) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row_index, column=0, sticky="w", pady=2)
            ttk.Entry(frame, textvariable=variable).grid(row=row_index, column=1, columnspan=2, sticky="ew", pady=2)

        condition_row = ttk.Frame(frame)
        condition_row.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(condition_row, text="LOS", variable=self.los_var).pack(side=tk.LEFT)
        ttk.Checkbutton(condition_row, text="NLOS", variable=self.nlos_var).pack(side=tk.LEFT, padx=(14, 0))

        action_row = ttk.Frame(frame)
        action_row.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        action_row.columnconfigure(0, weight=1)
        action_row.columnconfigure(1, weight=1)
        self.start_button = ttk.Button(action_row, text="Start Logging", style="Accent.TButton", command=self.start_capture)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.stop_button = ttk.Button(action_row, text="Stop Logging", command=self.stop_capture)
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        ttk.Label(frame, textvariable=self.session_status_var, style="Status.TLabel").grid(
            row=6,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )

    def _build_storage_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Storage", padding=8)
        frame.pack(fill=tk.X, pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Output folder").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(frame, textvariable=self.output_dir_var).grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Button(frame, text="Browse", command=self.choose_output_dir).grid(row=0, column=2, padx=(6, 0), pady=2)

        ttk.Label(frame, text="SQLite DB").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(frame, textvariable=self.db_path_var).grid(row=1, column=1, columnspan=2, sticky="ew", pady=2)

        ttk.Button(frame, text="Export Current Session to Excel", command=self.export_current_session).grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(8, 0),
        )

    def _build_stats_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Current Session Counts", padding=8)
        frame.pack(fill=tk.X, pady=(0, 8))
        rows = [
            ("Samples", self.sample_count_var),
            ("Alerts", self.alert_count_var),
            ("Raw lines", self.raw_count_var),
        ]
        for index, (label, variable) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", pady=2)
            ttk.Label(frame, textvariable=variable, style="Status.TLabel").grid(row=index, column=1, sticky="e", pady=2)
        frame.columnconfigure(1, weight=1)

    def _build_live_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Current Live Table", style="Status.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Clear", command=self.clear_live_view).grid(row=0, column=1, sticky="e")

        columns = ("time", "anchor", "sample", "distance", "error", "rx", "fp", "cir", "status", "source")
        self.sample_table = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        headings = {
            "time": "Time",
            "anchor": "Anchor",
            "sample": "Sample",
            "distance": "Distance m",
            "error": "Error m",
            "rx": "RX dBm",
            "fp": "FP dBm",
            "cir": "CIR power",
            "status": "Status",
            "source": "Source",
        }
        widths = {
            "time": 80,
            "anchor": 90,
            "sample": 80,
            "distance": 100,
            "error": 90,
            "rx": 90,
            "fp": 90,
            "cir": 95,
            "status": 120,
            "source": 110,
        }
        for column in columns:
            self.sample_table.heading(column, text=headings[column])
            self.sample_table.column(column, width=widths[column], anchor="center")

        yscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.sample_table.yview)
        self.sample_table.configure(yscrollcommand=yscroll.set)
        self.sample_table.grid(row=1, column=0, sticky="nsew")
        yscroll.grid(row=1, column=1, sticky="ns")
        self.sample_table.tag_configure("alert", background="#f8d7da")
        self.sample_table.tag_configure("failure", background="#f5c2c7")
        self.sample_table.tag_configure("summary", background="#d1e7dd")

    def _build_log_panel(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.grid(row=1, column=0, sticky="nsew")

        raw_frame = ttk.Frame(notebook)
        raw_frame.rowconfigure(0, weight=1)
        raw_frame.columnconfigure(0, weight=1)
        self.raw_text = tk.Text(raw_frame, height=10, wrap="none")
        raw_scroll = ttk.Scrollbar(raw_frame, orient=tk.VERTICAL, command=self.raw_text.yview)
        self.raw_text.configure(yscrollcommand=raw_scroll.set)
        self.raw_text.grid(row=0, column=0, sticky="nsew")
        raw_scroll.grid(row=0, column=1, sticky="ns")
        notebook.add(raw_frame, text="Raw serial view")

        alert_frame = ttk.Frame(notebook)
        alert_frame.rowconfigure(0, weight=1)
        alert_frame.columnconfigure(0, weight=1)
        self.alert_text = tk.Text(alert_frame, height=10, wrap="word")
        alert_scroll = ttk.Scrollbar(alert_frame, orient=tk.VERTICAL, command=self.alert_text.yview)
        self.alert_text.configure(yscrollcommand=alert_scroll.set)
        self.alert_text.grid(row=0, column=0, sticky="nsew")
        alert_scroll.grid(row=0, column=1, sticky="ns")
        notebook.add(alert_frame, text="Alerts")

    def choose_output_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=self.output_dir_var.get(), parent=self)
        if not path:
            return
        self.output_dir_var.set(str(path))
        self.db_path_var.set(str(Path(path) / DEFAULT_DB_NAME))

    def refresh_ports(self) -> None:
        infos = list(list_ports.comports()) if list_ports is not None else []
        self.port_infos = {info.device: info for info in infos}
        values = [self.describe_port(info) for info in infos]
        self.last_port_devices = tuple(self.port_infos)
        self.port_combo.configure(values=values)
        selected = self.selected_port_device()
        if not selected and infos:
            likely = self.pick_likely_port(infos)
            if likely is not None:
                self.port_var.set(self.describe_port(likely))

    def auto_port_poll(self) -> None:
        if list_ports is not None:
            infos = list(list_ports.comports())
            devices = tuple(info.device for info in infos)
            if devices != self.last_port_devices:
                self.refresh_ports()
            if self.auto_connect_var.get() and self.serial_worker is None and infos:
                likely = self.pick_likely_port(infos)
                if likely is not None:
                    self.port_var.set(self.describe_port(likely))
                    self.connect_selected_port()
        self.after(1500, self.auto_port_poll)

    def describe_port(self, info: Any) -> str:
        description = getattr(info, "description", "") or ""
        return f"{info.device} - {description}" if description else str(info.device)

    def selected_port_device(self) -> str:
        value = self.port_var.get().strip()
        return value.split(" - ", 1)[0].strip()

    def pick_likely_port(self, infos: list[Any]) -> Any | None:
        if not infos:
            return None
        scored = []
        keywords = ("usb", "serial", "uart", "cp210", "ch340", "wch", "silicon", "arduino")
        for info in infos:
            text = " ".join(str(getattr(info, field, "")) for field in ("device", "description", "manufacturer", "hwid")).lower()
            score = sum(1 for keyword in keywords if keyword in text)
            scored.append((score, info))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    def connect_selected_port(self) -> None:
        if self.serial_worker is not None:
            return
        port = self.selected_port_device()
        if not port:
            messagebox.showwarning("No port selected", "Select a serial port before connecting.", parent=self)
            return
        baud = safe_int(self.baud_var.get(), DEFAULT_BAUD) or DEFAULT_BAUD
        self.serial_worker = SerialWorker(port, baud, self.events)
        self.serial_worker.start()
        self.status_var.set(f"Connecting to {port} @ {baud}...")

    def disconnect_serial(self) -> None:
        if self.serial_worker is not None:
            self.serial_worker.stop()
            self.serial_worker = None
        self.connected_port = None
        self.status_var.set("Disconnected")

    def start_capture(self) -> None:
        if self.capture_active:
            return
        metadata = self.session_metadata()
        if not metadata["condition_los"] and not metadata["condition_nlos"]:
            proceed = messagebox.askyesno(
                "No LOS/NLOS label",
                "Neither LOS nor NLOS is checked. Continue with an unlabeled session?",
                parent=self,
            )
            if not proceed:
                return
        output_dir = Path(self.output_dir_var.get()).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        db_path = Path(self.db_path_var.get()).expanduser()
        if not db_path.name:
            db_path = output_dir / DEFAULT_DB_NAME
            self.db_path_var.set(str(db_path))
        if self.store is not None:
            self.store.close()
        self.store = MeasurementStore(db_path)
        self.current_session_id = self.store.create_session(metadata)
        self.capture_active = True
        self.sample_count_var.set("0")
        self.alert_count_var.set("0")
        self.raw_count_var.set("0")
        self.last_alert_prompt.clear()
        self.last_instability_prompt.clear()
        self.anchor_distance_windows.clear()
        self.session_status_var.set(f"Capturing: {self.current_session_id[:8]}")
        self.log_raw(f"# Started session {self.current_session_id}")
        self.log_raw("# Waiting for button-triggered serial readings from the tag.")

    def stop_capture(self) -> None:
        if not self.capture_active:
            return
        self.capture_active = False
        if self.store is not None and self.current_session_id is not None:
            self.store.finish_session(self.current_session_id)
            counts = self.store.counts(self.current_session_id)
            self.session_status_var.set(f"Stopped: {counts['samples']} samples, {counts['alerts']} alerts")
            self.log_raw(f"# Stopped session {self.current_session_id}")

    def send_serial_command(self, command: str, label: str) -> None:
        if self.serial_worker is None:
            self.log_raw(f"# Did not send {label} command; serial port is not ready.")
            return
        try:
            sent = self.serial_worker.send_command(command)
        except SerialException as exc:
            self.log_raw(f"# Could not send {label} command: {exc}")
            return
        if sent:
            self.log_raw(f"# Sent {label} command: {command.strip()}")
        else:
            self.log_raw(f"# Did not send {label} command; serial port is not ready.")

    def session_metadata(self) -> dict[str, Any]:
        ground_truth = safe_float(self.ground_truth_var.get())
        threshold = safe_float(self.threshold_var.get())
        return {
            "port": self.selected_port_device(),
            "baud": safe_int(self.baud_var.get(), DEFAULT_BAUD),
            "tag_id": "",
            "building_label": "",
            "constellation_label": self.constellation_var.get().strip(),
            "condition_los": self.los_var.get(),
            "condition_nlos": self.nlos_var.get(),
            "ground_truth_m": ground_truth,
            "outlier_threshold_m": threshold,
            "notes": "",
            "send_start_stop": False,
            "start_command": "",
            "stop_command": "",
        }

    def process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "connected":
                    self.connected_port = str(payload)
                    self.status_var.set(f"Connected to {payload}")
                    self.log_raw(f"# Connected to {payload}")
                elif event == "disconnected":
                    self.serial_worker = None
                    self.connected_port = None
                    self.status_var.set("Disconnected")
                    self.log_raw("# Disconnected")
                elif event == "error":
                    self.status_var.set(str(payload))
                    self.log_alert(str(payload))
                    self.serial_worker = None
                elif event == "line":
                    self.handle_serial_line(str(payload))
        except queue.Empty:
            pass
        self.after(80, self.process_events)

    def handle_serial_line(self, line: str) -> None:
        self.log_raw(line)
        records = parse_serial_line(line)
        if self.capture_active and self.store is not None and self.current_session_id is not None:
            self.store.insert_raw_line(self.current_session_id, line, bool(records))
        if not self.capture_active:
            return

        for record in records:
            if self.store is not None and self.current_session_id is not None:
                if record.kind == "summary":
                    self.store.insert_summary(self.current_session_id, record)
                    alert_error = self.check_los_alert(record)
                    self.add_record_to_table(record, alert_error)
                else:
                    self.store.insert_sample(self.current_session_id, record)
                    alert_error = self.check_los_alert(record)
                    self.check_instability_alert(record)
                    self.add_record_to_table(record, alert_error)
            else:
                self.add_record_to_table(record)
        self.update_counts()

    def check_los_alert(self, record: ParsedRecord) -> float | None:
        if not self.los_var.get() or self.nlos_var.get():
            return None
        ground_truth = safe_float(self.ground_truth_var.get())
        threshold = safe_float(self.threshold_var.get()) or 0.0
        distance = record.distance_m if record.distance_m is not None else record.mean_distance_m
        if ground_truth is None or ground_truth <= 0 or distance is None:
            return None
        absolute_error = abs(float(distance) - float(ground_truth))
        if absolute_error > threshold and self.store is not None and self.current_session_id is not None:
            anchor = record.anchor_id or "unknown"
            message = (
                f"LOS measurement from anchor {anchor} is {absolute_error:.3f} m away "
                f"from ground truth ({float(distance):.3f} m measured vs {ground_truth:.3f} m true)."
            )
            self.store.insert_alert(self.current_session_id, anchor, float(distance), ground_truth, absolute_error, message)
            self.log_alert(message)
            current_time = time.monotonic()
            last_prompt = self.last_alert_prompt.get(anchor, 0.0)
            if current_time - last_prompt > ALERT_PROMPT_COOLDOWN_SECONDS:
                self.last_alert_prompt[anchor] = current_time
                messagebox.showwarning("LOS measurement warning", message, parent=self)
        return absolute_error

    def check_instability_alert(self, record: ParsedRecord) -> float | None:
        if record.kind == "summary" or record.distance_m is None:
            return None
        threshold = safe_float(self.instability_threshold_var.get())
        if threshold is None or threshold <= 0:
            return None
        anchor = record.anchor_id or "unknown"
        values = self.anchor_distance_windows.setdefault(anchor, [])
        values.append(float(record.distance_m))
        if len(values) > INSTABILITY_WINDOW_SIZE:
            del values[0 : len(values) - INSTABILITY_WINDOW_SIZE]
        if len(values) < min(5, INSTABILITY_WINDOW_SIZE):
            return None
        mean = sum(values) / len(values)
        std = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
        if std > threshold and self.store is not None and self.current_session_id is not None:
            now = time.monotonic()
            last = self.last_instability_prompt.get(anchor, 0.0)
            if now - last > ALERT_PROMPT_COOLDOWN_SECONDS:
                self.last_instability_prompt[anchor] = now
                message = f"Anchor {anchor} distance is unstable: std {std:.3f} m over {len(values)} samples."
                self.store.insert_alert(self.current_session_id, anchor, record.distance_m, None, std, message)
                self.log_alert(message)
        return std

    def update_counts(self) -> None:
        if self.store is None or self.current_session_id is None:
            return
        counts = self.store.counts(self.current_session_id)
        self.sample_count_var.set(str(counts["samples"]))
        self.alert_count_var.set(str(counts["alerts"]))
        self.raw_count_var.set(str(counts["raw_lines"]))

    def add_record_to_table(self, record: ParsedRecord, alert_error: float | None = None) -> None:
        display_time = datetime.now().strftime("%H:%M:%S")
        distance = record.distance_m if record.distance_m is not None else record.mean_distance_m
        row = (
            display_time,
            record.anchor_id or "",
            "" if record.sample_index is None else str(record.sample_index),
            "" if distance is None else f"{float(distance):.3f}",
            "" if alert_error is None else f"{float(alert_error):.3f}",
            "" if record.rx_power_dbm is None else f"{float(record.rx_power_dbm):.2f}",
            "" if record.fp_power_dbm is None else f"{float(record.fp_power_dbm):.2f}",
            "" if record.cir_power is None else f"{float(record.cir_power):.2f}",
            record.status or "",
            record.source or "",
        )
        tags: tuple[str, ...] = ()
        threshold = safe_float(self.threshold_var.get())
        if alert_error is not None and threshold is not None and alert_error > threshold:
            tags = ("alert",)
        if record.kind == "failure":
            tags = ("failure",)
        elif record.kind == "summary":
            tags = ("summary",)
        self.sample_table.insert("", tk.END, values=row, tags=tags)
        self.table_rows += 1
        if self.table_rows > MAX_TABLE_ROWS:
            first = self.sample_table.get_children()[0]
            self.sample_table.delete(first)
            self.table_rows -= 1
        self.sample_table.yview_moveto(1.0)

    def clear_live_view(self) -> None:
        for item in self.sample_table.get_children():
            self.sample_table.delete(item)
        self.table_rows = 0
        self.raw_text.delete("1.0", tk.END)
        self.alert_text.delete("1.0", tk.END)

    def log_raw(self, message: str) -> None:
        self.raw_text.insert(tk.END, f"{message}\n")
        self.raw_text.see(tk.END)

    def log_alert(self, message: str) -> None:
        self.alert_text.insert(tk.END, f"{datetime.now().strftime('%H:%M:%S')} {message}\n")
        self.alert_text.see(tk.END)

    def export_current_session(self) -> None:
        if self.store is None or self.current_session_id is None:
            messagebox.showwarning("No session", "Start a capture session before exporting.", parent=self)
            return
        if self.capture_active:
            proceed = messagebox.askyesno(
                "Session still running",
                "The session is still capturing. Export a snapshot of the data collected so far?",
                parent=self,
            )
            if not proceed:
                return
        session = self.store.session_row(self.current_session_id)
        condition_bits = []
        if session["condition_los"]:
            condition_bits.append("LOS")
        if session["condition_nlos"]:
            condition_bits.append("NLOS")
        condition = "-".join(condition_bits) or "unlabeled"
        constellation = safe_filename(session["constellation_label"] or "constellation")
        started = str(session["started_at"]).replace(":", "_")
        default_name = f"{constellation}_{condition}_{started}.xlsx"
        output_path = filedialog.asksaveasfilename(
            initialdir=self.output_dir_var.get(),
            initialfile=default_name,
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
            parent=self,
        )
        if not output_path:
            return
        try:
            self.store.export_session_to_excel(self.current_session_id, Path(output_path))
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        messagebox.showinfo("Export complete", f"Wrote {output_path}", parent=self)

    def on_close(self) -> None:
        if self.capture_active:
            proceed = messagebox.askyesno("Capture active", "Stop logging and close the app?", parent=self)
            if not proceed:
                return
            self.stop_capture()
        self.disconnect_serial()
        if self.store is not None:
            self.store.close()
        self.destroy()


def main() -> None:
    app = UwbCaptureApp()
    app.mainloop()


if __name__ == "__main__":
    main()

