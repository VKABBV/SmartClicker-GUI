"""Small 2D UWB localization solver used by the capture GUI.

The GUI already owns the live measured ranges. This module keeps the position
solver dependency-free so the capture app does not need to launch the separate
simulation/localization project or install plotting libraries.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

LOCALIZATION_ALGORITHM = "Radical-axis line least squares (range-difference LS)"


@dataclass(frozen=True)
class LocalizationReading:
    """One enabled range measurement from the tag to a fixed anchor."""

    anchor_id: str
    x_m: float
    y_m: float
    range_m: float
    sigma_m: float = 0.05
    offset_m: float = 0.0
    enabled: bool = True


@dataclass(frozen=True)
class ProcessedReading:
    """Validated range measurement prepared for radical-axis solving."""

    anchor_id: str
    x_m: float
    y_m: float
    measured_range_m: float
    corrected_range_m: float
    sigma_m: float
    weight: float


@dataclass(frozen=True)
class LocalizationResult:
    """Final position estimate and diagnostics."""

    seed_x_m: float
    seed_y_m: float
    x_m: float
    y_m: float
    rmse_m: float
    confidence: str
    processed_readings: tuple[ProcessedReading, ...]
    residuals_m: dict[str, float]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SimulatedLocalizationScenario:
    """Deterministic fake anchor ranges for GUI and regression testing."""

    readings: tuple[LocalizationReading, ...]


def solve_position(
    readings: list[LocalizationReading],
    *,
    min_sigma_m: float = 0.01,
    min_range_m: float = 0.05,
) -> LocalizationResult:
    """Solve a 2D position from radical-axis line least squares.

    Subtracting squared range equations turns each anchor pair into a line. A
    common vertical height term appears in every squared range, so it cancels
    before the least-squares solve.
    """

    processed = _preprocess_readings(
        readings,
        min_sigma_m=min_sigma_m,
        min_range_m=min_range_m,
    )
    x, y = _solve_radical_axis_lines(processed)
    residuals, rmse, confidence, warnings = _analyze_radical_axis_residuals(processed, x, y)
    return LocalizationResult(
        seed_x_m=x,
        seed_y_m=y,
        x_m=x,
        y_m=y,
        rmse_m=rmse,
        confidence=confidence,
        processed_readings=tuple(processed),
        residuals_m=residuals,
        warnings=tuple(warnings),
    )


def build_square_simulation(
    *,
    width_m: float = 7.0,
    height_m: float = 7.0,
    sigma_m: float = 0.05,
    noise_m: float = 0.0,
) -> SimulatedLocalizationScenario:
    """Build a simple four-anchor floor-plan simulation in metric units.

    The fake ranges come from a deterministic hidden tag point so the GUI can
    exercise the solver without asking the operator for the clicker position.
    The optional noise is deterministic so test output and GUI demos are
    repeatable. Distances remain physically plausible for small noise values.
    """

    if width_m <= 0 or height_m <= 0:
        raise ValueError("Simulation width and height must be greater than 0.")
    if sigma_m <= 0:
        raise ValueError("Simulation sigma must be greater than 0.")
    if noise_m < 0:
        raise ValueError("Simulation noise must be 0 or greater.")

    reference_x_m = width_m * 0.443
    reference_y_m = height_m * 0.600
    anchors = (
        ("A1", 0.0, 0.0, 0.0),
        ("A2", width_m, 0.0, 0.65),
        ("A3", width_m, height_m, -0.45),
        ("A4", 0.0, height_m, 0.25),
    )
    readings = []
    for anchor_id, x_m, y_m, noise_scale in anchors:
        true_range = math.hypot(reference_x_m - x_m, reference_y_m - y_m)
        measured_range = max(true_range + noise_m * noise_scale, 0.05)
        readings.append(
            LocalizationReading(
                anchor_id=anchor_id,
                x_m=x_m,
                y_m=y_m,
                range_m=measured_range,
                sigma_m=sigma_m,
            )
        )
    return SimulatedLocalizationScenario(
        readings=tuple(readings),
    )


def _preprocess_readings(
    readings: list[LocalizationReading],
    *,
    min_sigma_m: float,
    min_range_m: float,
) -> list[ProcessedReading]:
    processed: list[ProcessedReading] = []
    for reading in readings:
        if not reading.enabled:
            continue
        values = (reading.x_m, reading.y_m, reading.range_m, reading.sigma_m, reading.offset_m)
        if not all(math.isfinite(float(value)) for value in values):
            continue
        if reading.range_m <= min_range_m:
            continue
        sigma = max(abs(float(reading.sigma_m)), min_sigma_m)
        corrected_range = max(float(reading.range_m) - float(reading.offset_m), min_range_m)
        processed.append(
            ProcessedReading(
                anchor_id=str(reading.anchor_id),
                x_m=float(reading.x_m),
                y_m=float(reading.y_m),
                measured_range_m=float(reading.range_m),
                corrected_range_m=corrected_range,
                sigma_m=sigma,
                weight=1.0 / (sigma * sigma),
            )
        )
    if len(processed) < 3:
        raise ValueError("At least three valid enabled anchor readings are required.")
    return processed


def _solve_radical_axis_lines(readings: list[ProcessedReading]) -> tuple[float, float]:
    a11 = a12 = a22 = 0.0
    b1 = b2 = 0.0
    row_count = 0
    for first, second, rx, ry, rhs, weight in _radical_axis_rows(readings):
        a11 += weight * rx * rx
        a12 += weight * rx * ry
        a22 += weight * ry * ry
        b1 += weight * rx * rhs
        b2 += weight * ry * rhs
        row_count += 1
    if row_count < 2:
        raise ValueError("At least two independent anchor pairs are required.")
    return _solve_2x2(a11, a12, a22, b1, b2, "anchor geometry is singular")


def _radical_axis_rows(
    readings: list[ProcessedReading],
):
    for i, first in enumerate(readings):
        for second in readings[i + 1 :]:
            rx = 2.0 * (second.x_m - first.x_m)
            ry = 2.0 * (second.y_m - first.y_m)
            rhs = (
                first.corrected_range_m**2
                - second.corrected_range_m**2
                + second.x_m**2
                + second.y_m**2
                - first.x_m**2
                - first.y_m**2
            )
            if math.hypot(rx, ry) <= 1e-12:
                continue
            yield first, second, rx, ry, rhs, min(first.weight, second.weight)


def _analyze_radical_axis_residuals(
    readings: list[ProcessedReading],
    x_m: float,
    y_m: float,
    *,
    good_rmse_m: float = 0.15,
    weak_rmse_m: float = 0.35,
) -> tuple[dict[str, float], float, str, list[str]]:
    residuals: dict[str, float] = {}
    weighted_residual_sum = 0.0
    weight_total = 0.0
    warnings: list[str] = []
    for first, second, rx, ry, rhs, weight in _radical_axis_rows(readings):
        line_length = math.hypot(rx, ry)
        residual = (rx * x_m + ry * y_m - rhs) / line_length
        pair_id = f"{first.anchor_id}-{second.anchor_id}"
        residuals[pair_id] = residual
        weighted_residual_sum += weight * residual * residual
        weight_total += weight
        if abs(residual) > weak_rmse_m:
            warnings.append(f"{pair_id} radical-axis residual is {residual:.3f} m.")

    if weight_total <= 0.0:
        raise ValueError("At least two independent anchor pairs are required.")
    rmse = math.sqrt(weighted_residual_sum / weight_total)
    if rmse <= good_rmse_m:
        confidence = "High"
    elif rmse <= weak_rmse_m:
        confidence = "Medium"
    else:
        confidence = "Low"
        warnings.append("Radical-axis RMSE is high; check anchor positions, NLOS, or bad ranges.")
    return residuals, rmse, confidence, warnings


def _solve_2x2(
    a11: float,
    a12: float,
    a22: float,
    b1: float,
    b2: float,
    error_message: str,
) -> tuple[float, float]:
    determinant = a11 * a22 - a12 * a12
    if abs(determinant) < 1e-12:
        raise ValueError(error_message)
    return (
        (b1 * a22 - b2 * a12) / determinant,
        (a11 * b2 - a12 * b1) / determinant,
    )
