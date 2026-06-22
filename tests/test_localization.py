import math
import unittest

from uwb_capture.localization import (
    LOCALIZATION_ALGORITHM,
    LocalizationReading,
    build_square_simulation,
    solve_position,
)


class LocalizationSolverTests(unittest.TestCase):
    def test_exact_square_anchor_ranges_solve_known_position(self) -> None:
        true_x = 3.1
        true_y = 4.2
        anchors = [
            ("A1", 0.0, 0.0),
            ("A2", 7.0, 0.0),
            ("A3", 7.0, 7.0),
            ("A4", 0.0, 7.0),
        ]
        readings = [
            LocalizationReading(
                anchor_id=anchor_id,
                x_m=x_m,
                y_m=y_m,
                range_m=math.hypot(true_x - x_m, true_y - y_m),
            )
            for anchor_id, x_m, y_m in anchors
        ]

        result = solve_position(readings)

        self.assertAlmostEqual(result.x_m, true_x, places=4)
        self.assertAlmostEqual(result.y_m, true_y, places=4)
        self.assertLess(result.rmse_m, 1e-4)
        self.assertEqual(result.confidence, "High")

    def test_requires_at_least_three_valid_anchors(self) -> None:
        readings = [
            LocalizationReading("A1", 0.0, 0.0, 1.0),
            LocalizationReading("A2", 1.0, 0.0, 1.0),
        ]

        with self.assertRaisesRegex(ValueError, "At least three"):
            solve_position(readings)

    def test_offset_correction_is_applied_before_solving(self) -> None:
        true_x = 2.0
        true_y = 2.0
        bias = 0.25
        anchors = [
            ("A1", 0.0, 0.0),
            ("A2", 5.0, 0.0),
            ("A3", 5.0, 5.0),
            ("A4", 0.0, 5.0),
        ]
        readings = [
            LocalizationReading(
                anchor_id=anchor_id,
                x_m=x_m,
                y_m=y_m,
                range_m=math.hypot(true_x - x_m, true_y - y_m) + bias,
                offset_m=bias,
            )
            for anchor_id, x_m, y_m in anchors
        ]

        result = solve_position(readings)

        self.assertAlmostEqual(result.x_m, true_x, places=4)
        self.assertAlmostEqual(result.y_m, true_y, places=4)

    def test_square_simulation_reports_position_error_metric(self) -> None:
        scenario = build_square_simulation(
            width_m=7.0,
            height_m=7.0,
            clicker_x_m=3.1,
            clicker_y_m=4.2,
        )

        result = solve_position(list(scenario.readings))
        position_error = math.hypot(result.x_m - scenario.clicker_x_m, result.y_m - scenario.clicker_y_m)

        self.assertIn("weighted least squares", LOCALIZATION_ALGORITHM.lower())
        self.assertLess(position_error, 1e-4)
        self.assertLess(result.rmse_m, 1e-4)

    def test_noisy_square_simulation_still_solves_near_true_point(self) -> None:
        scenario = build_square_simulation(
            width_m=8.0,
            height_m=5.0,
            clicker_x_m=2.75,
            clicker_y_m=2.25,
            noise_m=0.08,
        )

        result = solve_position(list(scenario.readings))
        position_error = math.hypot(result.x_m - scenario.clicker_x_m, result.y_m - scenario.clicker_y_m)

        self.assertLess(position_error, 0.12)
        self.assertLess(result.rmse_m, 0.08)


if __name__ == "__main__":
    unittest.main()
