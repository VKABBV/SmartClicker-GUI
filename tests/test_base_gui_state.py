import queue
import tempfile
import unittest
from pathlib import Path

from uwb_capture import base_gui
from uwb_capture import extended_gui
from uwb_capture.extended_gui import ExtendedUwbCaptureApp
from uwb_capture.store import MeasurementStore


class FakeButton:
    def __init__(self) -> None:
        self.state = ""

    def configure(self, **kwargs: object) -> None:
        if "state" in kwargs:
            self.state = str(kwargs["state"])


class FakeStatusVar:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class FakeVar:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class FakeCombo:
    def __init__(self) -> None:
        self.values: list[str] = []

    def configure(self, **kwargs: object) -> None:
        if "values" in kwargs:
            self.values = list(kwargs["values"])  # type: ignore[arg-type]

    def cget(self, key: str) -> object:
        if key == "values":
            return tuple(self.values)
        return ""


class FakeWorker:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeBluetoothWorker:
    def __init__(self) -> None:
        self.packets: list[bytes] = []

    def send_packet(self, packet: bytes) -> bool:
        self.packets.append(packet)
        return True


class FakeSampleTable:
    def __init__(self) -> None:
        self.values = {
            "row1": ["12:00:00", "A1", "0", "1.000", "", "", "", "", "OK", "ml", "", ""],
        }

    def get_children(self) -> list[str]:
        return list(self.values)

    def item(self, item_id: str, option: str | None = None, **kwargs: object) -> object:
        if "values" in kwargs:
            self.values[item_id] = list(kwargs["values"])  # type: ignore[arg-type]
            return None
        if option == "values":
            return tuple(self.values[item_id])
        return None

    def insert(self, _parent: str, _index: object, *, values: tuple[object, ...], tags: tuple[str, ...]) -> None:
        self.values[f"row{len(self.values) + 1}"] = list(values)

    def delete(self, item_id: str) -> None:
        self.values.pop(item_id, None)

    def yview_moveto(self, _fraction: float) -> None:
        return None


class FakeDbSessionTable:
    def __init__(self, selected: list[str] | None = None) -> None:
        self.rows: dict[str, tuple[object, ...]] = {}
        self._selection = list(selected or [])

    def get_children(self) -> list[str]:
        return list(self.rows)

    def delete(self, item_id: str) -> None:
        self.rows.pop(item_id, None)
        self._selection = [item for item in self._selection if item != item_id]

    def insert(self, _parent: str, _index: object, *, iid: str, values: tuple[object, ...]) -> None:
        self.rows[iid] = tuple(values)

    def selection(self) -> tuple[str, ...]:
        return tuple(self._selection)

    def selection_set(self, *items: str) -> None:
        self._selection = list(items)

    def exists(self, item_id: str) -> bool:
        return item_id in self.rows


def make_command_app() -> base_gui.UwbCaptureApp:
    app = base_gui.UwbCaptureApp.__new__(base_gui.UwbCaptureApp)
    app.ml_command_in_flight = True
    app.ml_pending_session = 10
    app.ml_pending_seq = 20
    app.ml_pending_mode = base_gui.ML_COLLECTION_MODE_FAST
    app.ml_timeout_after_id = "timeout1"
    app.ml_expected_sample_notifications = 4
    app.ml_received_sample_notifications = 2
    app.bluetooth_manual_disconnect_pending = False
    app.ml_collect_button = FakeButton()
    app.ml_status_var = FakeStatusVar()
    app.cancelled_after_ids = []
    app.log_lines = []
    app.collection_sample_rows = [1]
    app.collection_sample_rows_by_anchor = {"A1": [1]}
    app.collection_table_items = ["row1"]
    app.collection_table_items_by_anchor = {"A1": ["row1"]}
    app.collection_cir_records_by_anchor = {"A1": [object()]}
    app.collection_event_seq = 7
    app.pending_diagnostics = [object()]
    app.cir_reassembly_groups = {"A1": object()}
    app.after_cancel = lambda after_id: app.cancelled_after_ids.append(after_id)
    app.log_raw = lambda text: app.log_lines.append(text)
    app._flush_diagnostics_to_all_samples = lambda: None
    return app


def make_autosave_app(tmp: str, *, constellation: str = "bench") -> ExtendedUwbCaptureApp:
    app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
    app.constellation_var = FakeVar(constellation)
    app.output_dir_var = FakeVar(tmp)
    app.db_path_var = FakeVar(str(Path(tmp) / "measurements.sqlite"))
    app.device_var = FakeVar("clicker")
    app.los_var = FakeVar(False)
    app.nlos_var = FakeVar(False)
    app.ground_truth_var = FakeVar("")
    app.threshold_var = FakeVar("0")
    app.instability_threshold_var = FakeVar("")
    app.range_static_offset_var = FakeVar("0")
    app.store = None
    app.current_session_id = None
    app.capture_active = False
    app.sample_count_var = FakeStatusVar()
    app.alert_count_var = FakeStatusVar()
    app.raw_count_var = FakeStatusVar()
    app.session_status_var = FakeStatusVar()
    app.constellation_status_var = FakeStatusVar()
    app.last_alert_prompt = {}
    app.last_instability_prompt = {}
    app.anchor_distance_windows = {}
    app.last_sample_row_by_anchor = {}
    app.collection_sample_rows = []
    app.collection_sample_rows_by_anchor = {}
    app.collection_table_items = []
    app.collection_table_items_by_anchor = {}
    app.collection_cir_records_by_anchor = {}
    app.collection_event_seq = None
    app.pending_diagnostics = []
    app.cir_reassembly_groups = {}
    app.anchor_true_distances = {}
    app.anchor_los_nlos = {}
    app.anchor_measured_distances = {}
    app.anchor_distance_history = {}
    app.detected_anchor_ids = set()
    app.ml_command_in_flight = False
    app.ml_pending_mode = None
    app.ml_received_sample_notifications = 0
    app.ml_expected_sample_notifications = None
    app.live_tracking_active = False
    app.live_tracking_finish_pending = False
    app.sample_table = FakeSampleTable()
    app.table_rows = 0
    app.log_lines = []
    app.log_raw = lambda text: app.log_lines.append(text)
    app.log_alert = lambda text: app.log_lines.append(text)
    app._add_cir_sample = lambda _record: None
    app._refresh_db_manager_sessions = lambda **_kwargs: None
    app._register_detected_anchor = lambda anchor_id: app.detected_anchor_ids.add(str(anchor_id).strip())
    app._refresh_anchor_truth_table = lambda: None
    app._update_localization_range = lambda _anchor_id, _distance_m: None
    return app


class CommandStateTests(unittest.TestCase):
    def test_abort_ml_command_clears_in_flight_guard_and_timeout(self) -> None:
        app = make_command_app()

        app._abort_ml_command("Bluetooth disconnected")

        self.assertFalse(app.ml_command_in_flight)
        self.assertIsNone(app.ml_pending_session)
        self.assertIsNone(app.ml_pending_seq)
        self.assertIsNone(app.ml_pending_mode)
        self.assertIsNone(app.ml_timeout_after_id)
        self.assertIsNone(app.ml_expected_sample_notifications)
        self.assertEqual(app.ml_received_sample_notifications, 0)
        self.assertEqual(app.cancelled_after_ids, ["timeout1"])
        self.assertEqual(app.ml_collect_button.state, "normal")
        self.assertIn("Bluetooth disconnected", app.ml_status_var.value)
        self.assertIn("Bluetooth disconnected", app.log_lines[-1])
        self.assertEqual(app.collection_sample_rows, [])
        self.assertEqual(app.pending_diagnostics, [])

    def test_disconnect_transport_aborts_in_flight_command(self) -> None:
        app = make_command_app()
        worker = FakeWorker()
        app.bluetooth_worker = worker
        app.connected_device = "clicker"
        app.status_var = FakeStatusVar()

        app.disconnect_transport()

        self.assertTrue(worker.stopped)
        self.assertIsNone(app.bluetooth_worker)
        self.assertIsNone(app.connected_device)
        self.assertEqual(app.status_var.value, "Disconnected")
        self.assertFalse(app.ml_command_in_flight)

    def test_unexpected_disconnected_event_reconnects_immediately(self) -> None:
        app = make_command_app()
        app.events = queue.Queue()
        app.events.put(("disconnected", "clicker-address"))
        app.bluetooth_worker = FakeWorker()
        app.connected_device = "clicker-address"
        app.status_var = FakeStatusVar()
        app.after = lambda _ms, _callback: None
        app.reconnects = []
        app.selected_device = lambda: "clicker-address"
        app.connect_selected_device = lambda: app.reconnects.append("connect")

        app.process_events()

        self.assertIsNone(app.bluetooth_worker)
        self.assertIsNone(app.connected_device)
        self.assertEqual(app.reconnects, ["connect"])
        self.assertFalse(app.bluetooth_manual_disconnect_pending)
        self.assertIn("reconnecting immediately", app.log_lines[-1])

    def test_manual_disconnected_event_does_not_reconnect(self) -> None:
        app = make_command_app()
        worker = FakeWorker()
        app.bluetooth_worker = worker
        app.connected_device = "clicker-address"
        app.status_var = FakeStatusVar()
        app.events = queue.Queue()
        app.after = lambda _ms, _callback: None
        app.reconnects = []
        app.selected_device = lambda: "clicker-address"
        app.connect_selected_device = lambda: app.reconnects.append("connect")

        app.disconnect_transport()
        app.events.put(("disconnected", "clicker-address"))
        app.process_events()

        self.assertTrue(worker.stopped)
        self.assertEqual(app.reconnects, [])
        self.assertFalse(app.bluetooth_manual_disconnect_pending)

    def test_scan_results_include_active_ble_connection(self) -> None:
        app = base_gui.UwbCaptureApp.__new__(base_gui.UwbCaptureApp)
        app.connected_device = "AA:BB:CC:DD:EE:FF"
        app.device_infos = {}
        app.device_var = FakeVar("")
        app.device_combo = FakeCombo()
        app.status_var = FakeStatusVar()
        app.auto_connect_var = FakeVar(False)
        app.bluetooth_worker = FakeWorker()

        app._handle_device_scan_results([])

        self.assertEqual(app.device_combo.values, ["AA:BB:CC:DD:EE:FF - IMEC ML Clicker (connected)"])
        self.assertEqual(app.device_var.get(), "AA:BB:CC:DD:EE:FF - IMEC ML Clicker (connected)")
        self.assertIn("active connection", app.status_var.value)

    def test_connect_button_reselects_active_ble_connection(self) -> None:
        app = base_gui.UwbCaptureApp.__new__(base_gui.UwbCaptureApp)
        app.connected_device = "AA:BB:CC:DD:EE:FF"
        app.bluetooth_worker = FakeWorker()
        app.device_infos = {}
        app.device_var = FakeVar("")
        app.device_combo = FakeCombo()
        app.status_var = FakeStatusVar()
        app.log_lines = []
        app.log_raw = lambda text: app.log_lines.append(text)

        app.connect_selected_device()

        self.assertEqual(app.device_var.get(), "AA:BB:CC:DD:EE:FF - IMEC ML Clicker (connected)")
        self.assertEqual(app.status_var.value, "Already connected to AA:BB:CC:DD:EE:FF")
        self.assertIn("Already connected", app.log_lines[-1])

    def test_flush_diagnostics_updates_visible_cir_without_recording(self) -> None:
        app = base_gui.UwbCaptureApp.__new__(base_gui.UwbCaptureApp)
        sample = base_gui.ParsedRecord(
            kind="sample",
            anchor_id="A1",
            sample_index=0,
            event_seq=7,
            status="OK",
        )
        diag = base_gui.ParsedRecord(
            kind="diagnostic_fragment",
            anchor_id="A1",
            event_seq=7,
            rx_power_dbm=-88.25,
            cir_first_path_index=700,
            cir_start_index=640,
        )
        app.store = None
        app.collection_sample_rows = []
        app.collection_sample_rows_by_anchor = {}
        app.collection_table_items = ["row1"]
        app.collection_table_items_by_anchor = {"A1": ["row1"]}
        app.collection_cir_records_by_anchor = {"A1": [sample]}
        app.collection_event_seq = 7
        app.pending_diagnostics = [diag]
        app.cir_reassembly_groups = {
            (7, "A1", None, 640, 700, 6): {
                "anchor_id": "A1",
                "event_seq": 7,
                "burst_id": None,
                "cir_start_index": 640,
                "cir_first_path_index": 700,
                "total": 6,
                "bytes": bytearray(b"\x01\x00\x00\x02\x00\x00"),
            },
        }
        app.sample_table = FakeSampleTable()
        app.draw_count = 0
        app._draw_cir_plot = lambda: setattr(app, "draw_count", app.draw_count + 1)

        app._flush_diagnostics_to_all_samples()

        self.assertEqual(sample.cir_raw, "010000020000")
        self.assertEqual(sample.cir_first_path_index, 700)
        self.assertEqual(sample.cir_start_index, 640)
        self.assertEqual(app.sample_table.values["row1"][5], "-88.25")
        self.assertEqual(app.sample_table.values["row1"][7], "6B")
        self.assertEqual(app.pending_diagnostics, [])
        self.assertEqual(app.cir_reassembly_groups, {})
        self.assertEqual(app.draw_count, 1)

    def test_stop_live_tracking_completes_locally_without_bluetooth(self) -> None:
        app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
        app.live_tracking_active = True
        app.live_tracking_stopping = False
        app.live_tracking_after_id = "live1"
        app.live_tracking_event_seq = 1
        app.live_tracking_expected_sample_count = 4
        app.live_tracking_received_sample_count = 2
        app.live_tracking_ranges_by_anchor = {"A1": [1.0]}
        app.live_tracking_finish_pending = True
        app.live_tracking_finish_after_id = "idle1"
        app.live_tracking_run_finish_pending = False
        app.live_tracking_final_status = None
        app.live_tracking_source_id = None
        app.live_tracking_destination_id = None
        app.live_tracking_session_id = None
        app.live_tracking_start_sequence = None
        app.live_tracking_last_range_monotonic = None
        app.live_tracking_restart_retry_count = 0
        app.live_tracking_status_var = FakeStatusVar()
        app.bluetooth_worker = None
        app.ml_command_in_flight = True
        app.ml_pending_mode = base_gui.ML_COLLECTION_MODE_FAST
        app.ml_timeout_after_id = None
        app.aborted_reasons = []
        app.cancelled_live_after_ids = []
        app._abort_ml_command = lambda reason: app.aborted_reasons.append(reason)
        app.after_cancel = lambda after_id: app.cancelled_live_after_ids.append(after_id)
        app._set_live_tracking_button_state = lambda: None

        app.stop_live_tracking("Stopped by test.")

        self.assertFalse(app.live_tracking_active)
        self.assertEqual(app.aborted_reasons, [])
        self.assertEqual(app.cancelled_live_after_ids, ["live1"])
        self.assertEqual(
            app.live_tracking_status_var.value,
            "Live tracking stopped locally; Bluetooth is disconnected.",
        )
        self.assertEqual(app.live_tracking_ranges_by_anchor, {})
        self.assertFalse(app.live_tracking_finish_pending)

    def test_db_manager_refresh_shows_committed_session_counts_and_preserves_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "measurements.sqlite"
            store = MeasurementStore(db_path)
            session_a = store.create_session({"constellation_label": "bench_a"})
            session_b = store.create_session({"constellation_label": "bench_b"})
            store.insert_sample(
                session_a,
                base_gui.ParsedRecord(kind="sample", anchor_id="A1", distance_m=1.0),
            )
            store.insert_sample(
                session_b,
                base_gui.ParsedRecord(kind="sample", anchor_id="A2", distance_m=2.0),
            )

            app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
            app.store = store
            app.capture_active = False
            app.db_path_var = FakeVar(str(db_path))
            app.db_session_table = FakeDbSessionTable(selected=[session_a])
            app.db_manager_status_var = FakeStatusVar()

            app._refresh_db_manager_sessions()
            store.close()

        self.assertEqual(set(app.db_session_table.rows), {session_a, session_b})
        self.assertEqual(app.db_session_table.selection(), (session_a,))
        self.assertEqual(app.db_session_table.rows[session_a][4], 1)
        self.assertEqual(app.db_manager_status_var.value, "2 saved session(s).")

    def test_db_manager_delete_removes_all_selected_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "measurements.sqlite"
            store = MeasurementStore(db_path)
            session_a = store.create_session({"constellation_label": "bench_a"})
            session_b = store.create_session({"constellation_label": "bench_b"})
            session_c = store.create_session({"constellation_label": "bench_c"})
            for session_id, anchor_id in ((session_a, "A1"), (session_b, "A2"), (session_c, "A3")):
                store.insert_sample(
                    session_id,
                    base_gui.ParsedRecord(kind="sample", anchor_id=anchor_id, distance_m=1.0),
                )

            app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
            app.store = store
            app.capture_active = False
            app.current_session_id = None
            app.db_path_var = FakeVar(str(db_path))
            app.db_session_table = FakeDbSessionTable(selected=[session_a, session_b])
            app.db_manager_status_var = FakeStatusVar()
            original_askyesno = extended_gui.messagebox.askyesno
            extended_gui.messagebox.askyesno = lambda *_args, **_kwargs: True
            try:
                app._delete_selected_db_session()
            finally:
                extended_gui.messagebox.askyesno = original_askyesno
            remaining = [row["id"] for row in store.list_sessions()]
            store.close()

        self.assertEqual(remaining, [session_c])
        self.assertEqual(app.db_manager_status_var.value, "Deleted 2 session(s).")

    def test_named_constellation_autosaves_first_measurement_without_start_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = make_autosave_app(tmp, constellation="bench")
            app.anchor_true_distances["A1"] = {
                "anchor_id": "A1",
                "true_distance_m": 3.5,
                "distance_source": extended_gui.SOURCE_DIRECT,
                "pythagorean_side_a_m": None,
                "pythagorean_side_b_m": None,
            }

            app.handle_records([
                base_gui.ParsedRecord(
                    kind="sample",
                    anchor_id="A1",
                    sample_index=0,
                    distance_m=1.25,
                    status="ok",
                    source="test",
                )
            ])

            self.assertTrue(app.capture_active)
            session_id = app.current_session_id
            self.assertIsNotNone(session_id)
            counts = app.store.counts(session_id)
            session = app.store.session_row(session_id)
            sample = app.store.conn.execute(
                "SELECT true_distance_m FROM samples WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            app.store.close()

        self.assertEqual(session["constellation_label"], "bench")
        self.assertEqual(counts["samples"], 1)
        self.assertAlmostEqual(sample["true_distance_m"], 3.5)
        self.assertEqual(app.sample_table.values["row2"][10], "3.500")
        self.assertEqual(app.sample_count_var.value, "1")

    def test_ml_collection_prompts_for_constellation_name_before_sending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = make_autosave_app(tmp, constellation="")
            app.bluetooth_worker = FakeBluetoothWorker()
            app.ml_collection_mode_var = FakeVar(base_gui.ML_COLLECTION_MODE_FAST)
            app.ml_sample_count_var = FakeVar("1")
            app.ml_discovery_slot_count_var = FakeVar("1")
            app.ml_session_id_var = FakeVar("1")
            app.host_id_var = FakeVar("1")
            app.clicker_id_var = FakeVar("2")
            app.protocol_sequence = 0
            app.ml_timeout_after_id = None
            app.ml_collect_button = FakeButton()
            app.ml_status_var = FakeStatusVar()
            app.after = lambda _ms, _callback: "after1"
            original_askstring = extended_gui.simpledialog.askstring
            extended_gui.simpledialog.askstring = lambda *_args, **_kwargs: "prompted_bench"
            try:
                sent = app.send_ml_start_collection()
            finally:
                extended_gui.simpledialog.askstring = original_askstring

            self.assertTrue(sent)
            self.assertEqual(app.constellation_var.get(), "prompted_bench")
            self.assertEqual(len(app.bluetooth_worker.packets), 1)
            session = app.store.session_row(app.current_session_id)
            app.store.close()

        self.assertEqual(session["constellation_label"], "prompted_bench")


if __name__ == "__main__":
    unittest.main()
