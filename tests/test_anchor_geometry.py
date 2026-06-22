import math
import unittest

from uwb_capture.anchor_geometry import (
    ANCHOR_LAYOUT_ALGORITHM,
    AnchorPairDistance,
    mirror_layout,
    pair_residuals,
    rotate_layout,
    rotate_layout_to_level,
    solve_anchor_layout,
)


def pair(anchor_a: str, anchor_b: str, distance: float) -> AnchorPairDistance:
    return AnchorPairDistance(anchor_a, anchor_b, distance, sigma_m=0.03)


class AnchorGeometrySolverTests(unittest.TestCase):
    def test_exact_square_anchor_distances_solve_low_energy_layout(self) -> None:
        width = 4.0
        height = 3.0
        diagonal = math.hypot(width, height)
        result = solve_anchor_layout(
            [
                pair("A1", "A2", width),
                pair("A2", "A3", height),
                pair("A3", "A4", width),
                pair("A4", "A1", height),
                pair("A1", "A3", diagonal),
                pair("A2", "A4", diagonal),
            ],
            seed_count=8,
            basin_hops=4,
        )

        self.assertIn("basin", ANCHOR_LAYOUT_ALGORITHM.lower())
        self.assertLess(result.rmse_m, 1e-5)
        self.assertLess(result.max_residual_m, 1e-5)
        self.assertAlmostEqual(result.positions_m["A1"][1], result.positions_m["A2"][1], places=6)
        residuals = pair_residuals(result.positions_m, result.processed_pairs)
        self.assertTrue(all(abs(value) < 1e-5 for value in residuals.values()))

    def test_noisy_anchor_distances_remain_close(self) -> None:
        positions = {
            "A1": (0.0, 0.0),
            "A2": (5.0, 0.0),
            "A3": (5.5, 3.0),
            "A4": (2.0, 4.5),
            "A5": (-1.0, 2.0),
        }
        readings = []
        noise_by_pair = {
            ("A1", "A3"): 0.03,
            ("A2", "A4"): -0.02,
            ("A3", "A5"): 0.04,
        }
        anchor_ids = list(positions)
        for index, anchor_a in enumerate(anchor_ids):
            ax, ay = positions[anchor_a]
            for anchor_b in anchor_ids[index + 1 :]:
                bx, by = positions[anchor_b]
                noise = noise_by_pair.get((anchor_a, anchor_b), 0.0)
                readings.append(pair(anchor_a, anchor_b, math.hypot(ax - bx, ay - by) + noise))

        result = solve_anchor_layout(readings, seed_count=12, basin_hops=6)

        self.assertLess(result.rmse_m, 0.05)
        self.assertLess(result.max_residual_m, 0.08)

    def test_disconnected_anchor_graph_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "disconnected"):
            solve_anchor_layout(
                [
                    pair("A1", "A2", 1.0),
                    pair("A3", "A4", 1.0),
                ]
            )

    def test_rotate_layout_to_level_puts_selected_pair_on_same_y(self) -> None:
        positions = {
            "A1": (1.0, 2.0),
            "A2": (3.0, 4.0),
            "A3": (1.0, 4.0),
        }

        rotated = rotate_layout_to_level(positions, "A1", "A2")

        self.assertAlmostEqual(rotated["A1"][0], 0.0, places=6)
        self.assertAlmostEqual(rotated["A1"][1], 0.0, places=6)
        self.assertGreater(rotated["A2"][0], 0.0)
        self.assertAlmostEqual(rotated["A2"][1], 0.0, places=6)

    def test_rotate_and_mirror_preserve_pair_distances(self) -> None:
        positions = {
            "A1": (0.0, 0.0),
            "A2": (4.0, 0.0),
            "A3": (4.0, 3.0),
        }
        pairs = [
            pair("A1", "A2", 4.0),
            pair("A2", "A3", 3.0),
            pair("A1", "A3", 5.0),
        ]

        transformed = mirror_layout(rotate_layout(positions, 37.0), "x")
        residuals = pair_residuals(transformed, pairs)

        self.assertTrue(all(abs(value) < 1e-9 for value in residuals.values()))


if __name__ == "__main__":
    unittest.main()
