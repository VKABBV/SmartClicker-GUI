#!/usr/bin/env python3
"""Original-style UWB capture GUI used by serial_to_excel_extended_gui.py."""

from __future__ import annotations

import math
import queue
import re
import sqlite3
import statistics
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import serial
    from serial import SerialException
    from serial.tools import list_ports
except Exception:  # pragma: no cover - lets the GUI open even without pyserial.
    serial = None
    list_ports = None

    class SerialException(Exception):
        pass


APP_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = APP_DIR / "Measurements" / "GUI_Captures"
DEFAULT_DB_NAME = "uwb_measurements.sqlite"
DEFAULT_BAUD = 115200
MAX_TABLE_ROWS = 400
ALERT_PROMPT_COOLDOWN_SECONDS = 15
INSTABILITY_WINDOW_SIZE = 12


def now_local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return cleaned.strip("._") or "session"


@dataclass
class ParsedRecord:
    kind: str
    anchor_id: str | None = None
    sample_index: int | None = None
    distance_m: float | None = None
    mean_distance_m: float | None = None
    good_count: int | None = None
    rx_power_dbm: float | None = None
    fp_power_dbm: float | None = None
    cir_power: float | None = None
    cir_raw: str | None = None
    status: str | None = None
    error_code: str | None = None
    source: str | None = None
    raw_line: str | None = None


def _float_at(parts: list[str], index: int) -> float | None:
    return safe_float(parts[index]) if len(parts) > index else None


def _int_at(parts: list[str], index: int) -> int | None:
    return safe_int(parts[index]) if len(parts) > index else None


def parse_serial_line(line: str) -> list[ParsedRecord]:
    raw_line = line.rstrip("\r\n")
    stripped = raw_line.strip()
    if not stripped:
        return []

    parts = [part.strip() for part in stripped.split(",")]
    tag = parts[0].upper() if parts else ""
    records: list[ParsedRecord] = []

    try:
        if tag == "S" and len(parts) >= 4:
            records.append(
                ParsedRecord(
                    kind="sample",
                    anchor_id=parts[1],
                    sample_index=_int_at(parts, 2),
                    distance_m=_float_at(parts, 3),
                    status="ok",
                    source=parts[4] if len(parts) > 4 and parts[4] else "serial_csv",
                    raw_line=raw_line,
                )
            )
        elif tag == "F" and len(parts) >= 3:
            records.append(
                ParsedRecord(
                    kind="failure",
                    anchor_id=parts[1],
                    sample_index=_int_at(parts, 2),
                    status="failure",
                    error_code=parts[3] if len(parts) > 3 else "",
                    source="serial_csv",
                    raw_line=raw_line,
                )
            )
        elif tag == "M" and len(parts) >= 4:
            records.append(
                ParsedRecord(
                    kind="summary",
                    anchor_id=parts[1],
                    mean_distance_m=_float_at(parts, 2),
                    good_count=_int_at(parts, 3),
                    distance_m=_float_at(parts, 6),
                    status="summary",
                    source="serial_csv",
                    raw_line=raw_line,
                )
            )
        elif tag == "Q" and len(parts) >= 3:
            status = "quality_invalid" if len(parts) > 2 and parts[2].lower() == "invalid" else "quality"
            records.append(
                ParsedRecord(
                    kind="quality",
                    anchor_id=parts[1] if len(parts) > 1 else None,
                    rx_power_dbm=_float_at(parts, 2) if status == "quality" else None,
                    fp_power_dbm=_float_at(parts, 3) if status == "quality" else None,
                    cir_power=_float_at(parts, 11) if status == "quality" else None,
                    status=status,
                    source="serial_quality",
                    raw_line=raw_line,
                )
            )
        elif tag == "CIR" and len(parts) >= 5:
            records.append(
                ParsedRecord(
                    kind="cir",
                    anchor_id=parts[1],
                    cir_raw=raw_line,
                    status="cir",
                    source="serial_cir",
                    raw_line=raw_line,
                )
            )
        elif tag == "FAIL" and len(parts) >= 2:
            records.append(
                ParsedRecord(
                    kind="failure",
                    anchor_id=parts[1],
                    status="failure",
                    error_code="FAIL",
                    source="serial_csv",
                    raw_line=raw_line,
                )
            )
        else:
            # A small compatibility parser for older text logs.
            distance_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*m", stripped, re.I)
            anchor_match = re.search(r"anchor\s*[:#-]?\s*([A-Za-z0-9_.:-]+)", stripped, re.I)
            if distance_match and anchor_match:
                records.append(
                    ParsedRecord(
                        kind="sample",
                        anchor_id=anchor_match.group(1),
                        distance_m=float(distance_match.group(1)),
                        status="ok",
                        source="serial_text",
                        raw_line=raw_line,
                    )
                )
    except Exception:
        records.append(
            ParsedRecord(
                kind="failure",
                status="parse_error",
                error_code="parse_error",
                source="parser",
                raw_line=raw_line,
            )
        )

    return records


class MeasurementStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                stopped_at TEXT,
                port TEXT,
                baud INTEGER,
                tag_id TEXT,
                building_label TEXT,
                constellation_label TEXT,
                condition_los INTEGER NOT NULL DEFAULT 0,
                condition_nlos INTEGER NOT NULL DEFAULT 0,
                ground_truth_m REAL,
                outlier_threshold_m REAL,
                notes TEXT,
                send_start_stop INTEGER NOT NULL DEFAULT 0,
                start_command TEXT,
                stop_command TEXT
            );

            CREATE TABLE IF NOT EXISTS raw_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                raw_line TEXT NOT NULL,
                parsed INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                anchor_id TEXT,
                sample_index INTEGER,
                distance_m REAL,
                rx_power_dbm REAL,
                fp_power_dbm REAL,
                cir_power REAL,
                cir_raw TEXT,
                status TEXT,
                error_code TEXT,
                source TEXT,
                raw_line TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                anchor_id TEXT,
                mean_distance_m REAL,
                good_count INTEGER,
                status TEXT,
                source TEXT,
                raw_line TEXT,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                anchor_id TEXT,
                distance_m REAL,
                ground_truth_m REAL,
                absolute_error_m REAL,
                message TEXT NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_samples_session ON samples(session_id);
            CREATE INDEX IF NOT EXISTS idx_samples_anchor ON samples(session_id, anchor_id);
            CREATE INDEX IF NOT EXISTS idx_raw_lines_session ON raw_lines(session_id);
            CREATE INDEX IF NOT EXISTS idx_alerts_session ON alerts(session_id);
            """
        )
        self.conn.commit()

    def create_session(self, metadata: dict[str, Any]) -> str:
        session_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO sessions (
                id, started_at, port, baud, tag_id, building_label, constellation_label,
                condition_los, condition_nlos, ground_truth_m, outlier_threshold_m, notes,
                send_start_stop, start_command, stop_command
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                now_local_iso(),
                metadata.get("port"),
                metadata.get("baud"),
                metadata.get("tag_id"),
                metadata.get("building_label"),
                metadata.get("constellation_label"),
                int(bool(metadata.get("condition_los"))),
                int(bool(metadata.get("condition_nlos"))),
                metadata.get("ground_truth_m"),
                metadata.get("outlier_threshold_m"),
                metadata.get("notes"),
                int(bool(metadata.get("send_start_stop"))),
                metadata.get("start_command"),
                metadata.get("stop_command"),
            ),
        )
        self.conn.commit()
        return session_id

    def finish_session(self, session_id: str) -> None:
        self.conn.execute("UPDATE sessions SET stopped_at = ? WHERE id = ?", (now_local_iso(), session_id))
        self.conn.commit()

    def insert_raw_line(self, session_id: str, raw_line: str, parsed: bool) -> int:
        cur = self.conn.execute(
            "INSERT INTO raw_lines (session_id, timestamp, raw_line, parsed) VALUES (?, ?, ?, ?)",
            (session_id, now_local_iso(), raw_line, int(bool(parsed))),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def insert_sample(self, session_id: str, record: ParsedRecord) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO samples (
                session_id, timestamp, anchor_id, sample_index, distance_m,
                rx_power_dbm, fp_power_dbm, cir_power, cir_raw, status,
                error_code, source, raw_line
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                now_local_iso(),
                record.anchor_id,
                record.sample_index,
                record.distance_m,
                record.rx_power_dbm,
                record.fp_power_dbm,
                record.cir_power,
                record.cir_raw,
                record.status,
                record.error_code,
                record.source,
                record.raw_line,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def insert_summary(self, session_id: str, record: ParsedRecord) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO summaries (
                session_id, timestamp, anchor_id, mean_distance_m, good_count,
                status, source, raw_line
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                now_local_iso(),
                record.anchor_id,
                record.mean_distance_m,
                record.good_count,
                record.status,
                record.source,
                record.raw_line,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def insert_alert(
        self,
        session_id: str,
        anchor_id: str | None,
        distance_m: float | None,
        ground_truth_m: float | None,
        absolute_error_m: float | None,
        message: str,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO alerts (
                session_id, timestamp, anchor_id, distance_m, ground_truth_m, absolute_error_m, message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, now_local_iso(), anchor_id, distance_m, ground_truth_m, absolute_error_m, message),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def counts(self, session_id: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for table in ("samples", "summaries", "alerts", "raw_lines"):
            row = self.conn.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            result[table] = int(row["count"]) if row else 0
        return result

    def session_row(self, session_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown session id: {session_id}")
        return row

    def export_session_to_excel(self, session_id: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        wb = openpyxl.Workbook()
        session_sheet = wb.active
        session_sheet.title = "Session"
        session = self.session_row(session_id)
        session_sheet.append(["field", "value"])
        for key in session.keys():
            session_sheet.append([key, session[key]])
        session_sheet.freeze_panes = "A2"

        self._write_table_sheet(wb, "Samples", "SELECT * FROM samples WHERE session_id = ? ORDER BY id", session_id)
        self._write_table_sheet(wb, "Summaries", "SELECT * FROM summaries WHERE session_id = ? ORDER BY id", session_id)
        self._write_table_sheet(wb, "Alerts", "SELECT * FROM alerts WHERE session_id = ? ORDER BY id", session_id)
        self._write_table_sheet(
            wb,
            "Raw Lines",
            "SELECT id, timestamp, parsed, raw_line FROM raw_lines WHERE session_id = ? ORDER BY id",
            session_id,
        )

        for sheet in wb.worksheets:
            for column_cells in sheet.columns:
                header = column_cells[0].value
                if header is None:
                    continue
                max_len = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
                sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 12), 42)
        wb.save(output_path)

    def _write_table_sheet(self, wb: openpyxl.Workbook, title: str, sql: str, session_id: str) -> None:
        sheet = wb.create_sheet(title)
        rows = self.conn.execute(sql, (session_id,)).fetchall()
        if not rows:
            sheet.append(["No rows"])
            return
        headers = list(rows[0].keys())
        sheet.append(headers)
        for row in rows:
            sheet.append([row[key] for key in headers])
        sheet.freeze_panes = "A2"

    def close(self) -> None:
        self.conn.close()


class SerialWorker(threading.Thread):
    def __init__(self, port: str, baud: int, events: queue.Queue[tuple[str, Any]]) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.events = events
        self.stop_event = threading.Event()
        self._serial = None
        self._lock = threading.Lock()

    def run(self) -> None:
        if serial is None:
            self.events.put(("error", "pyserial is not installed. Install it with: pip install pyserial"))
            return
        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.2)
            with self._lock:
                self._serial = ser
            self.events.put(("connected", self.port))
            while not self.stop_event.is_set():
                try:
                    data = ser.readline()
                except SerialException as exc:
                    self.events.put(("error", str(exc)))
                    break
                if not data:
                    continue
                line = data.decode("utf-8", errors="replace").strip()
                if line:
                    self.events.put(("line", line))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            with self._lock:
                try:
                    if self._serial is not None and self._serial.is_open:
                        self._serial.close()
                except SerialException:
                    pass
                self._serial = None
            self.events.put(("disconnected", self.port))

    def stop(self) -> None:
        self.stop_event.set()
        with self._lock:
            try:
                if self._serial is not None and self._serial.is_open:
                    self._serial.close()
            except SerialException:
                pass

    def send_command(self, command: str) -> bool:
        text = command.strip()
        if not text:
            return False
        with self._lock:
            if self._serial is None or not self._serial.is_open:
                return False
            self._serial.write((text + "\n").encode("utf-8"))
            self._serial.flush()
            return True


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
