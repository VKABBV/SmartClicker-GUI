"""Small 2D UWB localization solver used by the capture GUI.

The GUI already owns the live measured ranges. This module keeps the position
solver dependency-free so the capture app does not need to launch the separate
simulation/localization project or install plotting libraries.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


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
    """Validated range measurement prepared for weighted least squares."""

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


def solve_position(
    readings: list[LocalizationReading],
    *,
    min_sigma_m: float = 0.01,
    min_range_m: float = 0.05,
    max_iterations: int = 30,
    tolerance_m: float = 1e-5,
) -> LocalizationResult:
    """Solve a 2D position from at least three anchor range readings."""

    processed = _preprocess_readings(
        readings,
        min_sigma_m=min_sigma_m,
        min_range_m=min_range_m,
    )
    seed_x, seed_y = _radical_axis_seed(processed)
    x, y = _weighted_least_squares(
        processed,
        seed_x=seed_x,
        seed_y=seed_y,
        max_iterations=max_iterations,
        tolerance_m=tolerance_m,
    )
    residuals, rmse, confidence, warnings = _analyze_residuals(processed, x, y)
    return LocalizationResult(
        seed_x_m=seed_x,
        seed_y_m=seed_y,
        x_m=x,
        y_m=y,
        rmse_m=rmse,
        confidence=confidence,
        processed_readings=tuple(processed),
        residuals_m=residuals,
        warnings=tuple(warnings),
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


def _radical_axis_seed(readings: list[ProcessedReading]) -> tuple[float, float]:
    a11 = a12 = a22 = 0.0
    b1 = b2 = 0.0
    row_count = 0
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
            weight = min(first.weight, second.weight)
            a11 += weight * rx * rx
            a12 += weight * rx * ry
            a22 += weight * ry * ry
            b1 += weight * rx * rhs
            b2 += weight * ry * rhs
            row_count += 1
    if row_count < 2:
        raise ValueError("At least two independent anchor pairs are required.")
    return _solve_2x2(a11, a12, a22, b1, b2, "anchor geometry is singular")


def _weighted_least_squares(
    readings: list[ProcessedReading],
    *,
    seed_x: float,
    seed_y: float,
    max_iterations: int,
    tolerance_m: float,
) -> tuple[float, float]:
    x = float(seed_x)
    y = float(seed_y)
    for _ in range(max_iterations):
        a11 = a12 = a22 = 0.0
        b1 = b2 = 0.0
        for reading in readings:
            dx = x - reading.x_m
            dy = y - reading.y_m
            predicted = max(math.hypot(dx, dy), 1e-9)
            residual = predicted - reading.corrected_range_m
            jx = dx / predicted
            jy = dy / predicted
            weight = reading.weight
            a11 += weight * jx * jx
            a12 += weight * jx * jy
            a22 += weight * jy * jy
            b1 += -weight * jx * residual
            b2 += -weight * jy * residual

        step_x, step_y = _solve_2x2(a11, a12, a22, b1, b2, "WLS solve failed")
        x += step_x
        y += step_y
        if math.hypot(step_x, step_y) < tolerance_m:
            break
    return x, y


def _analyze_residuals(
    readings: list[ProcessedReading],
    x_m: float,
    y_m: float,
    *,
    good_rmse_m: float = 0.15,
    weak_rmse_m: float = 0.35,
) -> tuple[dict[str, float], float, str, list[str]]:
    residuals: dict[str, float] = {}
    residual_values: list[float] = []
    warnings: list[str] = []
    for reading in readings:
        predicted = math.hypot(x_m - reading.x_m, y_m - reading.y_m)
        residual = predicted - reading.corrected_range_m
        residuals[reading.anchor_id] = residual
        residual_values.append(residual)
        if abs(residual) > weak_rmse_m:
            warnings.append(f"{reading.anchor_id} residual is {residual:.3f} m.")

    rmse = math.sqrt(sum(value * value for value in residual_values) / len(residual_values))
    if rmse <= good_rmse_m:
        confidence = "High"
    elif rmse <= weak_rmse_m:
        confidence = "Medium"
    else:
        confidence = "Low"
        warnings.append("Residual RMSE is high; check anchor positions, NLOS, or bad ranges.")
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
