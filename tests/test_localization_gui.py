import math
import unittest

from uwb_capture.common import ParsedRecord
from uwb_capture.extended_gui import ExtendedUwbCaptureApp
from uwb_capture.localization import LocalizationReading, solve_position


class FakeStatusVar:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class FakeVar:
    def __init__(self, value: object) -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class FakeCanvas:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height

    def winfo_width(self) -> int:
        return self.width

    def winfo_height(self) -> int:
        return self.height


class FakeStopEvent:
    def __init__(self, stopped: bool) -> None:
        self.stopped = stopped

    def is_set(self) -> bool:
        return self.stopped


class FakeWorker:
    def __init__(self, stopped: bool = False) -> None:
        self.stop_event = FakeStopEvent(stopped)


class LocalizationGuiTests(unittest.TestCase):
    def test_plot_bounds_use_room_size_not_estimated_position(self) -> None:
        app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
        app.sim_width_var = FakeVar("7")
        app.sim_height_var = FakeVar("7")
        result = solve_position(
            [
                LocalizationReading("A1", 0.0, 0.0, math.hypot(1.0, 1.0)),
                LocalizationReading("A2", 0.3, 0.8, math.hypot(0.7, 0.2)),
                LocalizationReading("A3", 1.5, 1.0, 0.5),
            ]
        )

        self.assertEqual(result.confidence, "High")
        self.assertEqual(
            app._localization_plot_bounds(result),
            (-0.28, 7.28, -0.28, 7.28),
        )

    def test_manual_plot_view_uses_requested_center_and_scale(self) -> None:
        app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
        app.sim_width_var = FakeVar("7")
        app.sim_height_var = FakeVar("7")
        app.localization_view_center_x_var = FakeVar("2")
        app.localization_view_center_y_var = FakeVar("3")
        app.localization_view_scale_var = FakeVar("50")
        result = solve_position(
            [
                LocalizationReading("A1", 0.0, 0.0, math.hypot(1.0, 1.0)),
                LocalizationReading("A2", 0.3, 0.8, math.hypot(0.7, 0.2)),
                LocalizationReading("A3", 1.5, 1.0, 0.5),
            ]
        )

        center_x, center_y, scale, min_x, max_x, min_y, max_y = app._localization_plot_view(
            result,
            width=500,
            height=300,
            margin=32,
        )

        self.assertEqual(center_x, 2.0)
        self.assertEqual(center_y, 3.0)
        self.assertEqual(scale, 50.0)
        self.assertAlmostEqual(min_x, -2.36)
        self.assertAlmostEqual(max_x, 6.36)
        self.assertAlmostEqual(min_y, 0.64)
        self.assertAlmostEqual(max_y, 5.36)

    def test_relative_plot_controls_pan_and_zoom_from_current_view(self) -> None:
        app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
        app.sim_width_var = FakeVar("7")
        app.sim_height_var = FakeVar("7")
        app.localization_view_center_x_var = FakeVar("")
        app.localization_view_center_y_var = FakeVar("")
        app.localization_view_scale_var = FakeVar("")
        app.localization_status_var = FakeStatusVar()
        app.localization_canvas = FakeCanvas(500, 300)
        app.redraw_count = 0
        app._redraw_localization_views = lambda: setattr(app, "redraw_count", app.redraw_count + 1)
        app.localization_result = solve_position(
            [
                LocalizationReading("A1", 0.0, 0.0, math.hypot(1.0, 1.0)),
                LocalizationReading("A2", 0.3, 0.8, math.hypot(0.7, 0.2)),
                LocalizationReading("A3", 1.5, 1.0, 0.5),
            ]
        )
        auto_center_x, auto_center_y, auto_scale, min_x, max_x, min_y, max_y = app._localization_plot_view(
            app.localization_result,
            width=500,
            height=300,
            margin=32,
        )

        app.pan_localization_view(1.0, -1.0)

        expected_center_x = auto_center_x + (max_x - min_x) * 0.15
        expected_center_y = auto_center_y - (max_y - min_y) * 0.15
        self.assertAlmostEqual(float(app.localization_view_center_x_var.get()), expected_center_x, places=5)
        self.assertAlmostEqual(float(app.localization_view_center_y_var.get()), expected_center_y, places=5)
        self.assertAlmostEqual(float(app.localization_view_scale_var.get()), auto_scale, delta=1e-4)
        self.assertEqual(app.redraw_count, 1)

        app.zoom_localization_view(1.25)

        self.assertAlmostEqual(float(app.localization_view_center_x_var.get()), expected_center_x, places=5)
        self.assertAlmostEqual(float(app.localization_view_center_y_var.get()), expected_center_y, places=5)
        self.assertAlmostEqual(float(app.localization_view_scale_var.get()), auto_scale * 1.25, delta=1e-4)
        self.assertEqual(app.redraw_count, 2)

    def test_solved_anchor_layout_populates_localization_coordinates(self) -> None:
        app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
        app.anchor_layout_positions = {
            "A2": (1.234, 5.678),
            "A1": (-0.5, 0.25),
        }
        app.localization_rows = {}
        app.localization_row_order = []
        app.localization_anchor_positions = {}
        app.localization_status_var = FakeStatusVar()
        app.redraw_count = 0

        def ensure_row(anchor_id: str) -> dict[str, FakeVar]:
            if anchor_id not in app.localization_rows:
                app.localization_rows[anchor_id] = {
                    "enabled_var": FakeVar(False),
                    "anchor_var": FakeVar(anchor_id),
                    "x_var": FakeVar(""),
                    "y_var": FakeVar(""),
                    "range_var": FakeVar(""),
                    "offset_var": FakeVar("0"),
                    "sigma_var": FakeVar("0.05"),
                }
                app.localization_row_order.append(anchor_id)
            return app.localization_rows[anchor_id]

        app._ensure_localization_row = ensure_row
        app._redraw_localization_views = lambda: setattr(app, "redraw_count", app.redraw_count + 1)

        self.assertTrue(app.populate_localization_from_anchor_layout())

        self.assertEqual(app.localization_row_order, ["A1", "A2"])
        self.assertTrue(app.localization_rows["A1"]["enabled_var"].get())
        self.assertEqual(app.localization_rows["A1"]["x_var"].get(), "-0.5")
        self.assertEqual(app.localization_rows["A1"]["y_var"].get(), "0.25")
        self.assertEqual(app.localization_rows["A2"]["x_var"].get(), "1.234")
        self.assertEqual(app.localization_rows["A2"]["y_var"].get(), "5.678")
        self.assertEqual(app.localization_anchor_positions["A2"], ("1.234", "5.678"))
        self.assertEqual(app.redraw_count, 1)
        self.assertIn("Loaded 2 anchor coordinate", app.localization_status_var.value)

    def make_live_tracking_app(self) -> ExtendedUwbCaptureApp:
        app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
        app.live_tracking_event_seq = None
        app.live_tracking_expected_sample_count = None
        app.live_tracking_received_sample_count = 0
        app.live_tracking_ranges_by_anchor = {}
        app.live_tracking_finish_pending = False
        app.live_tracking_finish_after_id = None
        app.anchor_measured_distances = {}
        app.anchor_distance_history = {}
        app.live_tracking_status_var = FakeStatusVar()
        app.applied_ranges = {}
        app.solve_count = 0
        app.after_idle_callbacks = []
        app.after_idle = lambda callback: app.after_idle_callbacks.append(callback) or "idle1"
        app._update_localization_range = lambda anchor_id, distance_m: app.applied_ranges.__setitem__(
            anchor_id, distance_m
        )

        def solve_once() -> bool:
            app.solve_count += 1
            return True

        app._try_live_tracking_solve = solve_once
        return app

    def test_live_tracking_waits_for_command_result_to_apply_batch(self) -> None:
        app = self.make_live_tracking_app()
        records = [
            ParsedRecord(
                kind="sample",
                anchor_id="A1",
                distance_m=1.0,
                event_seq=10,
                scheduled_sample_count=3,
            ),
            ParsedRecord(
                kind="sample",
                anchor_id="A2",
                distance_m=2.0,
                event_seq=10,
                scheduled_sample_count=3,
            ),
            ParsedRecord(
                kind="sample",
                anchor_id="A3",
                distance_m=3.0,
                event_seq=10,
                scheduled_sample_count=3,
            ),
        ]

        self.assertFalse(app._record_live_tracking_sample(records[0]))
        self.assertFalse(app._record_live_tracking_sample(records[1]))
        self.assertFalse(app._record_live_tracking_sample(records[2]))
        self.assertEqual(app.applied_ranges, {})
        self.assertEqual(app.solve_count, 0)

        self.assertTrue(app._finish_live_tracking_batch(force=True))

        self.assertEqual(app.applied_ranges, {"A1": 1.0, "A2": 2.0, "A3": 3.0})
        self.assertEqual(app.solve_count, 1)
        self.assertEqual(app.live_tracking_received_sample_count, 0)

    def test_live_tracking_keeps_rows_after_expected_count_and_event_changes(self) -> None:
        app = self.make_live_tracking_app()
        records = [
            ParsedRecord(
                kind="sample",
                anchor_id="A1",
                distance_m=1.0,
                event_seq=10,
                scheduled_sample_count=2,
            ),
            ParsedRecord(
                kind="sample",
                anchor_id="A2",
                distance_m=2.0,
                event_seq=11,
                scheduled_sample_count=2,
            ),
            ParsedRecord(
                kind="summary",
                anchor_id="A3",
                mean_distance_m=3.0,
                event_seq=12,
                scheduled_sample_count=2,
            ),
        ]

        for record in records:
            self.assertFalse(app._record_live_tracking_sample(record))

        self.assertEqual(app.live_tracking_received_sample_count, 3)
        self.assertEqual(set(app.live_tracking_ranges_by_anchor), {"A1", "A2", "A3"})

        self.assertTrue(app._finish_live_tracking_batch(force=True))

        self.assertEqual(app.applied_ranges, {"A1": 1.0, "A2": 2.0, "A3": 3.0})
        self.assertEqual(app.solve_count, 1)

    def test_live_tracking_pending_finish_waits_for_failure_row_from_result_packet(self) -> None:
        app = self.make_live_tracking_app()
        records = [
            ParsedRecord(
                kind="sample",
                anchor_id="A1",
                distance_m=1.0,
                scheduled_sample_count=4,
            ),
            ParsedRecord(
                kind="sample",
                anchor_id="A2",
                distance_m=2.0,
                scheduled_sample_count=4,
            ),
            ParsedRecord(
                kind="sample",
                anchor_id="A3",
                distance_m=3.0,
                scheduled_sample_count=4,
            ),
            ParsedRecord(
                kind="failure",
                anchor_id="A4",
                scheduled_sample_count=4,
            ),
        ]

        app._request_live_tracking_batch_finish()
        self.assertTrue(app.live_tracking_finish_pending)
        self.assertEqual(app.solve_count, 0)

        for record in records:
            self.assertFalse(app._record_live_tracking_sample(record))

        self.assertTrue(app._finish_pending_live_tracking_batch())

        self.assertEqual(app.applied_ranges, {"A1": 1.0, "A2": 2.0, "A3": 3.0})
        self.assertEqual(app.solve_count, 1)
        self.assertFalse(app.live_tracking_finish_pending)
        self.assertEqual(app.live_tracking_received_sample_count, 0)

    def make_live_tick_app(self) -> ExtendedUwbCaptureApp:
        app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
        app.live_tracking_active = True
        app.live_tracking_after_id = None
        app.live_tracking_interval_var = FakeVar("2")
        app.live_tracking_status_var = FakeStatusVar()
        app.ml_command_in_flight = False
        app.after_calls = []
        app.after = lambda interval_ms, callback: app.after_calls.append((interval_ms, callback)) or "after1"
        app._set_live_tracking_button_state = lambda: None
        app._reset_live_tracking_batch = lambda: None
        return app

    def test_live_tracking_retries_when_bluetooth_is_temporarily_missing(self) -> None:
        app = self.make_live_tick_app()
        app.bluetooth_worker = None

        app._live_tracking_tick()

        self.assertTrue(app.live_tracking_active)
        self.assertEqual(app.live_tracking_after_id, "after1")
        self.assertEqual(app.after_calls[0][0], 2000)
        self.assertIn("retrying", app.live_tracking_status_var.value)

    def test_live_tracking_retries_when_worker_is_stopping(self) -> None:
        app = self.make_live_tick_app()
        app.bluetooth_worker = FakeWorker(stopped=True)

        app._live_tracking_tick()

        self.assertTrue(app.live_tracking_active)
        self.assertEqual(app.live_tracking_after_id, "after1")
        self.assertIn("reconnecting", app.live_tracking_status_var.value)

    def test_live_tracking_retries_when_range_request_is_not_sent(self) -> None:
        app = self.make_live_tick_app()
        app.bluetooth_worker = FakeWorker()
        app.send_ml_start_collection = lambda collection_mode=None: False

        app._live_tracking_tick()

        self.assertTrue(app.live_tracking_active)
        self.assertEqual(app.live_tracking_after_id, "after1")
        self.assertIn("not sent, retrying", app.live_tracking_status_var.value)

    def test_static_range_offset_is_added_to_each_anchor_offset(self) -> None:
        app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
        app.range_static_offset_var = FakeVar("0.25")
        app.localization_row_order = ["A1", "A2", "A3"]
        app.localization_rows = {
            anchor_id: {
                "enabled_var": FakeVar(True),
                "anchor_var": FakeVar(anchor_id),
                "x_var": FakeVar(str(index)),
                "y_var": FakeVar("0"),
                "range_var": FakeVar("3"),
                "offset_var": FakeVar("0.05"),
                "sigma_var": FakeVar("0.05"),
            }
            for index, anchor_id in enumerate(app.localization_row_order)
        }

        readings = app._read_localization_form()

        for reading in readings:
            self.assertAlmostEqual(reading.offset_m, 0.30)

    def test_static_range_offset_must_be_numeric(self) -> None:
        app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
        app.range_static_offset_var = FakeVar("bad")
        app.localization_row_order = []
        app.localization_rows = {}

        with self.assertRaisesRegex(ValueError, "Static range offset"):
            app._read_localization_form()


if __name__ == "__main__":
    unittest.main()
