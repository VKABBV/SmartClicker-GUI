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
    DEFAULT_DB_NAME,
    INSTABILITY_WINDOW_SIZE,
    MAX_TABLE_ROWS,
    ParsedRecord,
    now_local_iso,
    safe_filename,
    safe_float,
    safe_int,
)
from .bluetooth_io import BluetoothDeviceInfo, BluetoothScanner, BluetoothWorker
from .parser import parse_serial_line
from .protocol import (
    CommandId,
    ImecPacket,
    ProtocolError,
    build_command_packet,
    build_command_tlvs,
    command_result_summary,
    decode_packet,
    format_device_id,
    next_sequence,
    packet_summary,
    parse_device_id,
    records_from_packet,
    status_report_summary,
    survey_reach_summary,
)
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
        self.bluetooth_worker: BluetoothWorker | None = None
        self.connected_device: str | None = None
        self.device_infos: dict[str, BluetoothDeviceInfo] = {}
        self.protocol_sequence = 0
        self.protocol_session_seed = int(time.time()) & 0xFFFFFFFF
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
        self.after(80, self.process_events)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_variables(self) -> None:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.output_dir_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.db_path_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR / DEFAULT_DB_NAME))
        self.device_var = tk.StringVar()
        self.notify_uuid_var = tk.StringVar()
        self.write_uuid_var = tk.StringVar()
        self.auto_connect_var = tk.BooleanVar(value=False)
        self.gateway_id_var = tk.StringVar(value="0")
        self.target_id_var = tk.StringVar(value="0")
        self.heartbeat_interval_ms_var = tk.StringVar(value="60000")
        self.survey_id_var = tk.StringVar(value=str(self.protocol_session_seed))
        self.survey_initiator_id_var = tk.StringVar()
        self.survey_responder_id_var = tk.StringVar()
        self.survey_sample_count_var = tk.StringVar(value="10")
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
        frame = ttk.LabelFrame(parent, text="Bluetooth Connection", padding=8)
        frame.pack(fill=tk.X, pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Device").grid(row=0, column=0, sticky="w", pady=2)
        self.device_combo = ttk.Combobox(frame, textvariable=self.device_var, state="normal")
        self.device_combo.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Button(frame, text="Scan", command=self.refresh_devices).grid(row=0, column=2, padx=(6, 0), pady=2)

        ttk.Label(frame, text="Notify UUID").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(frame, textvariable=self.notify_uuid_var).grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=2,
        )
        ttk.Label(frame, text="Write UUID").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(frame, textvariable=self.write_uuid_var).grid(
            row=2,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=2,
        )
        ttk.Checkbutton(frame, text="Auto-connect", variable=self.auto_connect_var).grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="w",
            pady=2,
        )

        button_row = ttk.Frame(frame)
        button_row.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        button_row.columnconfigure(0, weight=1)
        button_row.columnconfigure(1, weight=1)
        self.connect_button = ttk.Button(button_row, text="Connect", command=self.connect_selected_device)
        self.connect_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(button_row, text="Disconnect", command=self.disconnect_transport).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(5, 0),
        )

        ttk.Label(frame, textvariable=self.status_var, style="Status.TLabel").grid(
            row=5,
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

        self._build_serial_control(frame, 5)

        action_row = ttk.Frame(frame)
        action_row.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        action_row.columnconfigure(0, weight=1)
        action_row.columnconfigure(1, weight=1)
        self.start_button = ttk.Button(action_row, text="Start Logging", style="Accent.TButton", command=self.start_capture)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.stop_button = ttk.Button(action_row, text="Stop Logging", command=self.stop_capture)
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        ttk.Label(frame, textvariable=self.session_status_var, style="Status.TLabel").grid(
            row=7,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )

    def _build_serial_control(self, parent: ttk.Frame, row: int) -> None:
        control = ttk.LabelFrame(parent, text="Bluetooth protocol commands", padding=6)
        control.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        control.columnconfigure(1, weight=1)
        control.columnconfigure(3, weight=1)

        ttk.Label(control, text="Gateway ID").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(control, textvariable=self.gateway_id_var, width=18).grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(6, 0),
            pady=2,
        )
        ttk.Label(control, text="Target ID").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(control, textvariable=self.target_id_var, width=18).grid(
            row=1,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(6, 0),
            pady=2,
        )

        command_row = ttk.Frame(control)
        command_row.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        command_row.columnconfigure(0, weight=1)
        command_row.columnconfigure(1, weight=1)
        ttk.Button(
            command_row,
            text="Get Status",
            command=lambda: self.send_protocol_command(CommandId.GET_STATUS),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            command_row,
            text="Start Heartbeat",
            command=lambda: self.send_protocol_command(CommandId.START_HEARTBEAT),
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(
            command_row,
            text="Stop Heartbeat",
            command=lambda: self.send_protocol_command(CommandId.STOP_HEARTBEAT),
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        ttk.Label(control, text="Heartbeat ms").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(control, textvariable=self.heartbeat_interval_ms_var, width=12).grid(
            row=3,
            column=1,
            sticky="ew",
            padx=(6, 0),
            pady=(8, 0),
        )

        ttk.Label(control, text="Survey ID").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(control, textvariable=self.survey_id_var, width=12).grid(
            row=4,
            column=1,
            sticky="ew",
            padx=(6, 12),
            pady=(8, 0),
        )
        ttk.Label(control, text="Samples").grid(row=4, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(control, textvariable=self.survey_sample_count_var, width=8).grid(
            row=4,
            column=3,
            sticky="ew",
            padx=(6, 0),
            pady=(8, 0),
        )

        ttk.Label(control, text="Initiator").grid(row=5, column=0, sticky="w", pady=2)
        ttk.Entry(control, textvariable=self.survey_initiator_id_var, width=18).grid(
            row=5,
            column=1,
            sticky="ew",
            padx=(6, 12),
            pady=2,
        )
        ttk.Label(control, text="Responder").grid(row=5, column=2, sticky="w", pady=2)
        ttk.Entry(control, textvariable=self.survey_responder_id_var, width=18).grid(
            row=5,
            column=3,
            sticky="ew",
            padx=(6, 0),
            pady=2,
        )

        survey_row = ttk.Frame(control)
        survey_row.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        for index in range(2):
            survey_row.columnconfigure(index, weight=1)
        ttk.Button(
            survey_row,
            text="Reachability Survey",
            command=lambda: self.send_protocol_command(CommandId.SURVEY_REACHABILITY),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            survey_row,
            text="Prepare Pair",
            command=lambda: self.send_protocol_command(CommandId.SURVEY_PREPARE_PAIR),
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(
            survey_row,
            text="Start Pair",
            command=lambda: self.send_protocol_command(CommandId.SURVEY_START_PAIR),
        ).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(6, 0))
        ttk.Button(
            survey_row,
            text="Abort Survey",
            command=lambda: self.send_protocol_command(CommandId.SURVEY_ABORT),
        ).grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(6, 0))

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
        notebook.add(raw_frame, text="Raw data view")

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

    def refresh_devices(self) -> None:
        self.status_var.set("Scanning for Bluetooth devices...")
        BluetoothScanner(self.events).start()

    def selected_device(self) -> str:
        return self.device_var.get().split(" - ", 1)[0].strip()

    def connect_selected_device(self) -> None:
        if self.bluetooth_worker is not None:
            return
        device = self.selected_device()
        if not device:
            messagebox.showwarning("No device selected", "Select or enter a BLE device before connecting.", parent=self)
            return
        self.bluetooth_worker = BluetoothWorker(
            device=device,
            notify_uuid=self.notify_uuid_var.get(),
            write_uuid=self.write_uuid_var.get(),
            events=self.events,
        )
        self.bluetooth_worker.start()
        self.status_var.set(f"Connecting to {device}...")

    def disconnect_transport(self) -> None:
        if self.bluetooth_worker is not None:
            self.bluetooth_worker.stop()
            self.bluetooth_worker = None
        self.connected_device = None
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
        self.log_raw("# Waiting for BLE click reports and survey results.")

    def stop_capture(self) -> None:
        if not self.capture_active:
            return
        self.capture_active = False
        if self.store is not None and self.current_session_id is not None:
            self.store.finish_session(self.current_session_id)
            counts = self.store.counts(self.current_session_id)
            self.session_status_var.set(f"Stopped: {counts['samples']} samples, {counts['alerts']} alerts")
            self.log_raw(f"# Stopped session {self.current_session_id}")

    def send_protocol_command(self, command_id: CommandId) -> None:
        if self.bluetooth_worker is None:
            self.log_raw(f"# Did not send {command_id.name}; Bluetooth is not connected.")
            return
        try:
            extra_tlvs = self.command_extra_tlvs(command_id)
            self.protocol_sequence = next_sequence(self.protocol_sequence)
            packet = build_command_packet(
                command_id=command_id,
                source_id=parse_device_id(self.gateway_id_var.get(), default=0),
                destination_id=parse_device_id(self.target_id_var.get(), default=0),
                session_id=self.command_session_id(command_id),
                sequence=self.protocol_sequence,
                extra_tlvs=extra_tlvs,
            )
        except ProtocolError as exc:
            messagebox.showerror("Invalid protocol command", str(exc), parent=self)
            return
        sent = self.bluetooth_worker.send_packet(packet)
        if sent:
            self.log_raw(f"# Queued {command_id.name} to {self.target_id_var.get().strip() or 'broadcast'}")
        else:
            self.log_raw(f"# Did not send {command_id.name}; Bluetooth is not ready.")

    def command_extra_tlvs(self, command_id: CommandId) -> list[tuple[Any, bytes]]:
        heartbeat_interval = None
        survey_id = None
        sample_count = None
        initiator_id = None
        responder_id = None

        if command_id == CommandId.START_HEARTBEAT:
            heartbeat_interval = safe_int(self.heartbeat_interval_ms_var.get())
            if heartbeat_interval is not None and not (5000 <= heartbeat_interval <= 3600000):
                raise ProtocolError("Heartbeat interval must be from 5000 ms to 3600000 ms.")

        if command_id in {
            CommandId.SURVEY_REACHABILITY,
            CommandId.SURVEY_PREPARE_PAIR,
            CommandId.SURVEY_START_PAIR,
            CommandId.SURVEY_ABORT,
        }:
            survey_id = self.survey_id()

        if command_id in {CommandId.SURVEY_PREPARE_PAIR, CommandId.SURVEY_START_PAIR}:
            sample_count = safe_int(self.survey_sample_count_var.get())
            if sample_count is None or not (1 <= sample_count <= 65535):
                raise ProtocolError("Survey sample count must be from 1 to 65535.")
            initiator_id = self.optional_device_id(self.survey_initiator_id_var.get())
            responder_id = self.optional_device_id(self.survey_responder_id_var.get())
        return build_command_tlvs(
            command_id,
            heartbeat_interval_ms=heartbeat_interval,
            survey_id=survey_id,
            initiator_id=initiator_id,
            responder_id=responder_id,
            sample_count=sample_count,
        )

    def command_session_id(self, command_id: CommandId) -> int:
        if command_id in {
            CommandId.SURVEY_REACHABILITY,
            CommandId.SURVEY_PREPARE_PAIR,
            CommandId.SURVEY_START_PAIR,
            CommandId.SURVEY_ABORT,
        }:
            return self.survey_id()
        return self.protocol_session_seed

    def survey_id(self) -> int:
        value = safe_int(self.survey_id_var.get())
        if value is None or value < 0 or value > 0xFFFFFFFF:
            raise ProtocolError("Survey ID must be a 32-bit unsigned integer.")
        return value

    def optional_device_id(self, value: str) -> int | None:
        if not value.strip():
            return None
        return parse_device_id(value)

    def handle_protocol_packet(self, data: bytes) -> None:
        try:
            packet = decode_packet(data)
        except ProtocolError as exc:
            self.log_alert(f"Protocol packet rejected: {exc}")
            return

        summary = packet_summary(packet)
        self.log_raw(f"# RX {summary}")
        try:
            self.log_protocol_message(packet)
            records = records_from_packet(packet)
        except ProtocolError as exc:
            self.log_alert(f"Could not parse {summary}: {exc}")
            records = []

        if self.capture_active and self.store is not None and self.current_session_id is not None:
            self.store.insert_raw_line(self.current_session_id, f"{summary} {data.hex(' ')}", bool(records))
        if not self.capture_active:
            return
        self.handle_records(records)

    def log_protocol_message(self, packet: ImecPacket) -> None:
        if packet.msg_type == 0x41:
            self.log_raw(f"# Command result: {command_result_summary(packet)}")
        elif packet.msg_type == 0x22:
            self.log_raw(f"# {status_report_summary(packet)}")
        elif packet.msg_type == 0x51:
            self.log_raw(f"# {survey_reach_summary(packet)}")
        elif packet.msg_type == 0x7F:
            self.log_alert(f"Protocol error from {format_device_id(packet.source_id)}")

    def session_metadata(self) -> dict[str, Any]:
        ground_truth = safe_float(self.ground_truth_var.get())
        threshold = safe_float(self.threshold_var.get())
        return {
            "port": self.selected_device(),
            "baud": None,
            "tag_id": "",
            "building_label": "",
            "constellation_label": self.constellation_var.get().strip(),
            "condition_los": self.los_var.get(),
            "condition_nlos": self.nlos_var.get(),
            "ground_truth_m": ground_truth,
            "outlier_threshold_m": threshold,
            "notes": "BLE protocol capture",
            "send_start_stop": False,
            "start_command": "",
            "stop_command": "",
        }

    def process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "devices":
                    infos = list(payload)
                    self.device_infos = {info.address: info for info in infos}
                    values = [info.label for info in infos]
                    self.device_combo.configure(values=values)
                    if values and not self.device_var.get().strip():
                        self.device_var.set(values[0])
                    self.status_var.set(f"Found {len(values)} Bluetooth device(s)")
                    if self.auto_connect_var.get() and values and self.bluetooth_worker is None:
                        self.connect_selected_device()
                elif event == "connected":
                    self.connected_device = str(payload)
                    self.status_var.set(f"Connected to {payload}")
                    self.log_raw(f"# Connected to {payload}")
                elif event == "disconnected":
                    self.bluetooth_worker = None
                    self.connected_device = None
                    self.status_var.set("Disconnected")
                    self.log_raw("# Disconnected")
                elif event == "error":
                    self.status_var.set(str(payload))
                    self.log_alert(str(payload))
                    self.bluetooth_worker = None
                elif event == "command_sent":
                    self.log_raw(f"# TX {payload}")
                elif event == "packet":
                    self.handle_protocol_packet(bytes(payload))
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

        self.handle_records(records)

    def handle_records(self, records: list[ParsedRecord]) -> None:
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
        self.disconnect_transport()
        if self.store is not None:
            self.store.close()
        self.destroy()


def main() -> None:
    app = UwbCaptureApp()
    app.mainloop()


if __name__ == "__main__":
    main()
