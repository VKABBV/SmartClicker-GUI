"""CIR decoding helpers for export paths."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

CIR_EXPORT_COLUMNS = [
    "cir_block_id",
    "source_sample_id",
    "timestamp",
    "session_id",
    "anchor_id",
    "clicker_id",
    "sample_index",
    "event_seq",
    "burst_id",
    "diag_source",
    "cir_start_index",
    "cir_first_path_index",
    "cir_window_sample_index",
    "cir_abs_sample_index",
    "cir_byte_offset",
    "cir_real",
    "cir_imag",
    "cir_magnitude",
    "cir_power_db",
]


def decode_cir_iq(cir_hex: str | None) -> list[tuple[int, int]]:
    """Return signed 24-bit little-endian real/imaginary CIR pairs."""

    if not cir_hex:
        return []
    try:
        raw = bytes.fromhex(cir_hex)
    except ValueError:
        return []

    values: list[tuple[int, int]] = []
    for offset in range(0, len(raw) - 5, 6):
        real = int.from_bytes(raw[offset:offset + 3], "little", signed=True)
        imag = int.from_bytes(raw[offset + 3:offset + 6], "little", signed=True)
        values.append((real, imag))
    return values


def build_cir_export_rows(sample_rows: Iterable[Any]) -> list[list[Any]]:
    """Build one normalized export row per CIR complex sample.

    Full-diagnostic firmware stores the same post-burst anchor CIR block on each
    range sample for that anchor. The export de-duplicates those blocks and keeps
    the first sample row id as the source pointer.
    """

    rows: list[list[Any]] = []
    seen_blocks: set[tuple[Any, ...]] = set()
    cir_block_id = 0

    for sample_row in sample_rows:
        cir_hex = _row_value(sample_row, "cir_raw")
        iq_values = decode_cir_iq(cir_hex)
        if not iq_values:
            continue

        block_key = _cir_block_key(sample_row, cir_hex)
        if block_key in seen_blocks:
            continue
        seen_blocks.add(block_key)
        cir_block_id += 1

        start_index = _row_value(sample_row, "cir_start_index")
        first_path_index = _row_value(sample_row, "cir_first_path_index")
        for sample_index, (real, imag) in enumerate(iq_values):
            abs_index = start_index + sample_index if isinstance(start_index, int) else None
            power = real * real + imag * imag
            rows.append([
                cir_block_id,
                _row_value(sample_row, "id"),
                _row_value(sample_row, "timestamp"),
                _row_value(sample_row, "session_id"),
                _row_value(sample_row, "anchor_id"),
                _row_value(sample_row, "clicker_id"),
                _row_value(sample_row, "sample_index"),
                _row_value(sample_row, "event_seq"),
                _row_value(sample_row, "burst_id"),
                _row_value(sample_row, "diag_source"),
                start_index,
                first_path_index,
                sample_index,
                abs_index,
                sample_index * 6,
                real,
                imag,
                math.sqrt(power),
                10.0 * math.log10(max(power, 1)),
            ])

    return rows


def _cir_block_key(sample_row: Any, cir_hex: str) -> tuple[Any, ...]:
    full_block = (
        _row_value(sample_row, "cir_start_index") is not None
        or _row_value(sample_row, "cir_first_path_index") is not None
        or _row_value(sample_row, "diag_source") is not None
    )
    source_row_id = None if full_block else _row_value(sample_row, "id")
    return (
        _row_value(sample_row, "session_id"),
        _row_value(sample_row, "anchor_id"),
        _row_value(sample_row, "clicker_id"),
        _row_value(sample_row, "event_seq"),
        _row_value(sample_row, "burst_id"),
        _row_value(sample_row, "diag_source"),
        _row_value(sample_row, "cir_start_index"),
        _row_value(sample_row, "cir_first_path_index"),
        source_row_id,
        cir_hex,
    )


def _row_value(row: Any, key: str) -> Any:
    try:
        if hasattr(row, "keys") and key not in row.keys():
            return None
        return row[key]
    except (IndexError, KeyError, TypeError):
        return None
