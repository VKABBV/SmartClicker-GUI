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

    def test_live_tracking_applies_complete_batch_once(self) -> None:
        app = ExtendedUwbCaptureApp.__new__(ExtendedUwbCaptureApp)
        app.live_tracking_event_seq = None
        app.live_tracking_expected_sample_count = None
        app.live_tracking_received_sample_count = 0
        app.live_tracking_ranges_by_anchor = {}
        app.anchor_measured_distances = {}
        app.anchor_distance_history = {}
        app.live_tracking_status_var = FakeStatusVar()
        app.applied_ranges = {}
        app.solve_count = 0
        app._update_localization_range = lambda anchor_id, distance_m: app.applied_ranges.__setitem__(
            anchor_id, distance_m
        )

        def solve_once() -> bool:
            app.solve_count += 1
            return True

        app._try_live_tracking_solve = solve_once
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
        self.assertEqual(app.applied_ranges, {})
        self.assertEqual(app.solve_count, 0)

        self.assertTrue(app._record_live_tracking_sample(records[2]))

        self.assertEqual(app.applied_ranges, {"A1": 1.0, "A2": 2.0, "A3": 3.0})
        self.assertEqual(app.solve_count, 1)
        self.assertEqual(app.live_tracking_received_sample_count, 0)

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
