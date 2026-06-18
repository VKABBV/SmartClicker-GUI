"""IMEC UWB/BLE packet codec and report conversion helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import Any, Iterable

from .common import ParsedRecord

MAGIC = 0xC1
VERSION = 0x01
HEADER_SIZE = 28
CRC_SIZE = 2
MAX_PAYLOAD_SIZE = 255
BROADCAST_ID = 0
DEFAULT_TTL = 4


class ProtocolError(ValueError):
    """Raised when a packet cannot be encoded or decoded safely."""


class MessageType(IntEnum):
    CLICK_REPORT = 0x20
    SELF_TEST_REPORT = 0x21
    ANCHOR_HEARTBEAT = 0x22
    COMMAND = 0x40
    COMMAND_RESULT = 0x41
    SURVEY_REACH_REQ = 0x50
    SURVEY_REACH_REPORT = 0x51
    SURVEY_PAIR_PREPARE = 0x52
    SURVEY_PAIR_RESULT = 0x53
    MSG_ERROR = 0x7F


class CommandId(IntEnum):
    GET_STATUS = 0x0002
    START_HEARTBEAT = 0x0009
    STOP_HEARTBEAT = 0x000A
    SURVEY_REACHABILITY = 0x0100
    SURVEY_PREPARE_PAIR = 0x0101
    SURVEY_START_PAIR = 0x0102
    SURVEY_ABORT = 0x0103


class CommandStatus(IntEnum):
    COMMAND_OK = 0
    COMMAND_UNSUPPORTED_COMMAND = 1
    COMMAND_MALFORMED_PAYLOAD = 2
    COMMAND_BUSY = 3
    COMMAND_DENIED = 4
    COMMAND_TIMEOUT = 5
    COMMAND_RADIO_ERROR = 6
    COMMAND_INVALID_STATE = 7
    COMMAND_INTERNAL_ERROR = 8


class RangeStatus(IntEnum):
    RANGE_OK = 0
    RANGE_RX_TIMEOUT = 1
    RANGE_RX_ERROR = 2
    RANGE_BAD_FRAME = 3
    RANGE_WRONG_TARGET = 4
    RANGE_STS_QUALITY_FAIL = 5
    RANGE_DELAYED_TX_MISSED = 6
    RANGE_INTERNAL_ERROR = 7
    RANGE_TIMING_INVALID = 8


class TlvId(IntEnum):
    DEVICE_ROLE = 0x01
    BATTERY_MV = 0x02
    STATUS_BITS = 0x03
    ERROR_CODE = 0x04
    ERROR_DETAIL = 0x05
    EVENT_SEQ = 0x06
    TIMESTAMP_MS = 0x07
    RSSI_DBM = 0x08
    UWB_SHORT_ADDR = 0x09
    ANCHOR_ID = 0x0A
    CLICKER_ID = 0x0B
    DISTANCE_MM = 0x0C
    QUALITY = 0x0D
    SAMPLE_INDEX = 0x0E
    SAMPLE_COUNT = 0x0F
    COMMAND_ID = 0x10
    COMMAND_STATUS = 0x11
    REQUESTED_MSG_SEQ = 0x12
    NEXT_HOP_ID = 0x13
    GATEWAY_ID = 0x14
    SURVEY_ID = 0x15
    PEER_ID_LIST = 0x16
    REACHABILITY_ENTRY = 0x17
    RANGE_FLAGS = 0x18
    LED_PATTERN_ID = 0x19
    DURATION_MS = 0x1A
    RETRY_COUNT = 0x1B
    FW_VERSION = 0x1C
    UPTIME_MS = 0x1D
    REASON = 0x1E
    INITIATOR_ID = 0x1F
    RESPONDER_ID = 0x20
    RANGE_STATUS = 0x21
    ROUTE_EPOCH = 0x22
    HOP_COUNT = 0x23
    UWB_RSL_DBM = 0x24
    DISTANCE_SAMPLES_MM = 0x25
    TIME_SYNC_AGE_MS = 0x27
    RANGE_ROUND_INDICES = 0x28
    SEQUENCE_START_TIMESTAMPS_MS = 0x29
    BURST_ID = 0x34


@dataclass(frozen=True)
class ImecPacket:
    msg_type: int
    flags: int
    source_id: int
    destination_id: int
    session_id: int
    sequence: int
    ttl: int
    payload: bytes


class ImecPacketStream:
    """Accumulates notification fragments and extracts complete packets."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        packets: list[bytes] = []
        while True:
            if not self._buffer:
                return packets
            magic_index = self._buffer.find(bytes([MAGIC]))
            if magic_index < 0:
                self._buffer.clear()
                return packets
            if magic_index:
                del self._buffer[:magic_index]
            if len(self._buffer) < HEADER_SIZE + CRC_SIZE:
                return packets
            payload_len = self._buffer[27]
            total_len = HEADER_SIZE + payload_len + CRC_SIZE
            if len(self._buffer) < total_len:
                return packets
            packets.append(bytes(self._buffer[:total_len]))
            del self._buffer[:total_len]


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode_tlv(tlv_id: int | TlvId, value: bytes) -> bytes:
    if len(value) > 255:
        raise ProtocolError(f"TLV {int(tlv_id):#x} is longer than 255 bytes")
    return bytes([int(tlv_id), len(value)]) + value


def encode_tlvs(items: Iterable[tuple[int | TlvId, bytes]]) -> bytes:
    return b"".join(encode_tlv(tlv_id, value) for tlv_id, value in items)


def decode_tlvs(payload: bytes) -> dict[int, list[bytes]]:
    tlvs: dict[int, list[bytes]] = {}
    index = 0
    while index < len(payload):
        if index + 2 > len(payload):
            raise ProtocolError("Truncated TLV header")
        tlv_id = payload[index]
        length = payload[index + 1]
        index += 2
        if index + length > len(payload):
            raise ProtocolError(f"Truncated TLV {tlv_id:#x}")
        tlvs.setdefault(tlv_id, []).append(payload[index : index + length])
        index += length
    return tlvs


def encode_packet(packet: ImecPacket) -> bytes:
    if len(packet.payload) > MAX_PAYLOAD_SIZE:
        raise ProtocolError("Packet payload is longer than 255 bytes")
    header = struct.pack(
        "<BBBBQQIHB",
        MAGIC,
        VERSION,
        int(packet.msg_type) & 0xFF,
        int(packet.flags) & 0xFF,
        packet.source_id & 0xFFFFFFFFFFFFFFFF,
        packet.destination_id & 0xFFFFFFFFFFFFFFFF,
        packet.session_id & 0xFFFFFFFF,
        packet.sequence & 0xFFFF,
        packet.ttl & 0xFF,
    ) + bytes([len(packet.payload)])
    body = header + packet.payload
    return body + struct.pack("<H", crc16_ccitt_false(body))


def decode_packet(data: bytes) -> ImecPacket:
    if len(data) < HEADER_SIZE + CRC_SIZE:
        raise ProtocolError("Packet is too short")
    if data[0] != MAGIC:
        raise ProtocolError("Bad packet magic")
    if data[1] != VERSION:
        raise ProtocolError(f"Unsupported packet version: {data[1]}")
    payload_len = data[27]
    expected_len = HEADER_SIZE + payload_len + CRC_SIZE
    if len(data) != expected_len:
        raise ProtocolError(f"Packet length mismatch: expected {expected_len}, got {len(data)}")
    actual_crc = struct.unpack_from("<H", data, expected_len - CRC_SIZE)[0]
    expected_crc = crc16_ccitt_false(data[: expected_len - CRC_SIZE])
    if actual_crc != expected_crc:
        raise ProtocolError("Packet CRC mismatch")
    fields = struct.unpack("<BBBBQQIHB", data[: HEADER_SIZE - 1])
    return ImecPacket(
        msg_type=fields[2],
        flags=fields[3],
        source_id=fields[4],
        destination_id=fields[5],
        session_id=fields[6],
        sequence=fields[7],
        ttl=fields[8],
        payload=data[HEADER_SIZE : HEADER_SIZE + payload_len],
    )


def u8(value: int) -> bytes:
    return struct.pack("<B", value & 0xFF)


def u16(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)


def u32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def u64(value: int) -> bytes:
    return struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF)


def i32(value: int) -> bytes:
    return struct.pack("<i", int(value))


def first_tlv(tlvs: dict[int, list[bytes]], tlv_id: int | TlvId) -> bytes | None:
    values = tlvs.get(int(tlv_id), [])
    return values[0] if values else None


def read_uint(data: bytes | None) -> int | None:
    if data is None:
        return None
    if len(data) == 1:
        return struct.unpack("<B", data)[0]
    if len(data) == 2:
        return struct.unpack("<H", data)[0]
    if len(data) == 4:
        return struct.unpack("<I", data)[0]
    if len(data) == 8:
        return struct.unpack("<Q", data)[0]
    return int.from_bytes(data, "little", signed=False)


def read_int(data: bytes | None) -> int | None:
    if data is None:
        return None
    if len(data) == 1:
        return struct.unpack("<b", data)[0]
    if len(data) == 2:
        return struct.unpack("<h", data)[0]
    if len(data) == 4:
        return struct.unpack("<i", data)[0]
    if len(data) == 8:
        return struct.unpack("<q", data)[0]
    return int.from_bytes(data, "little", signed=True)


def parse_device_id(value: str, *, default: int | None = None, bits: int = 64) -> int:
    text = str(value or "").strip()
    if not text:
        if default is not None:
            return default
        raise ProtocolError("Missing device ID")
    cleaned = text.replace("_", "").replace(":", "").replace("-", "")
    try:
        if cleaned.lower().startswith("0x"):
            number = int(cleaned, 16)
        elif any(char in "abcdefABCDEF" for char in cleaned):
            number = int(cleaned, 16)
        else:
            number = int(cleaned, 10)
    except ValueError as exc:
        raise ProtocolError(f"Invalid device ID: {value}") from exc
    max_value = (1 << bits) - 1
    if number < 0 or number > max_value:
        raise ProtocolError(f"Device ID is outside {bits}-bit range: {value}")
    return number


def format_device_id(value: int | None) -> str:
    if value is None:
        return ""
    return f"0x{int(value) & 0xFFFFFFFFFFFFFFFF:016X}"


def next_sequence(sequence: int) -> int:
    return 1 if sequence >= 0xFFFF else sequence + 1


def build_command_packet(
    *,
    command_id: int | CommandId,
    source_id: int,
    destination_id: int,
    session_id: int,
    sequence: int,
    extra_tlvs: Iterable[tuple[int | TlvId, bytes]] = (),
) -> bytes:
    payload = encode_tlvs(
        [(TlvId.COMMAND_ID, u16(int(command_id))), *list(extra_tlvs)]
    )
    packet = ImecPacket(
        msg_type=MessageType.COMMAND,
        flags=0,
        source_id=source_id,
        destination_id=destination_id,
        session_id=session_id,
        sequence=sequence,
        ttl=DEFAULT_TTL,
        payload=payload,
    )
    return encode_packet(packet)


def build_command_tlvs(
    command_id: CommandId,
    *,
    heartbeat_interval_ms: int | None = None,
    survey_id: int | None = None,
    initiator_id: int | None = None,
    responder_id: int | None = None,
    sample_count: int | None = None,
) -> list[tuple[TlvId, bytes]]:
    tlvs: list[tuple[TlvId, bytes]] = []
    if command_id == CommandId.START_HEARTBEAT and heartbeat_interval_ms:
        tlvs.append((TlvId.DURATION_MS, u32(heartbeat_interval_ms)))
    if command_id in {
        CommandId.SURVEY_REACHABILITY,
        CommandId.SURVEY_PREPARE_PAIR,
        CommandId.SURVEY_START_PAIR,
        CommandId.SURVEY_ABORT,
    } and survey_id is not None:
        tlvs.append((TlvId.SURVEY_ID, u32(survey_id)))
    if command_id in {CommandId.SURVEY_PREPARE_PAIR, CommandId.SURVEY_START_PAIR}:
        if initiator_id is not None:
            tlvs.append((TlvId.INITIATOR_ID, u64(initiator_id)))
        if responder_id is not None:
            tlvs.append((TlvId.RESPONDER_ID, u64(responder_id)))
        if sample_count is not None:
            tlvs.append((TlvId.SAMPLE_COUNT, u16(sample_count)))
    return tlvs


def packet_summary(packet: ImecPacket) -> str:
    try:
        name = MessageType(packet.msg_type).name
    except ValueError:
        name = f"0x{packet.msg_type:02X}"
    return (
        f"{name} src={format_device_id(packet.source_id)} "
        f"dst={format_device_id(packet.destination_id)} "
        f"session={packet.session_id} seq={packet.sequence}"
    )


def command_result_summary(packet: ImecPacket) -> str:
    tlvs = decode_tlvs(packet.payload)
    command_id = read_uint(first_tlv(tlvs, TlvId.COMMAND_ID))
    status = read_uint(first_tlv(tlvs, TlvId.COMMAND_STATUS))
    command_name = _enum_name(CommandId, command_id, "CMD")
    status_name = _enum_name(CommandStatus, status, "COMMAND_STATUS")
    parts = [f"{command_name}: {status_name}"]
    role = read_uint(first_tlv(tlvs, TlvId.DEVICE_ROLE))
    battery = read_uint(first_tlv(tlvs, TlvId.BATTERY_MV))
    uptime = read_uint(first_tlv(tlvs, TlvId.UPTIME_MS))
    status_bits = read_uint(first_tlv(tlvs, TlvId.STATUS_BITS))
    if role is not None:
        parts.append(f"role={role}")
    if battery is not None:
        parts.append(f"battery={battery}mV")
    if uptime is not None:
        parts.append(f"uptime={uptime}ms")
    if status_bits is not None:
        parts.append(f"status=0x{status_bits:08X}")
    return ", ".join(parts)


def status_report_summary(packet: ImecPacket) -> str:
    tlvs = decode_tlvs(packet.payload)
    role = read_uint(first_tlv(tlvs, TlvId.DEVICE_ROLE))
    battery = read_uint(first_tlv(tlvs, TlvId.BATTERY_MV))
    uptime = read_uint(first_tlv(tlvs, TlvId.UPTIME_MS))
    status_bits = read_uint(first_tlv(tlvs, TlvId.STATUS_BITS))
    parts = [f"Heartbeat from {format_device_id(packet.source_id)}"]
    if role is not None:
        parts.append(f"role={role}")
    if battery is not None:
        parts.append(f"battery={battery}mV")
    if uptime is not None:
        parts.append(f"uptime={uptime}ms")
    if status_bits is not None:
        parts.append(f"status=0x{status_bits:08X}")
    return ", ".join(parts)


def survey_reach_summary(packet: ImecPacket) -> str:
    tlvs = decode_tlvs(packet.payload)
    peer_list = first_tlv(tlvs, TlvId.PEER_ID_LIST)
    if not peer_list:
        return f"Reachability report from {format_device_id(packet.source_id)}"
    peers = [
        format_device_id(struct.unpack_from("<Q", peer_list, index)[0])
        for index in range(0, len(peer_list) - 7, 8)
    ]
    return f"Reachability report from {format_device_id(packet.source_id)}: {', '.join(peers)}"


def records_from_packet(packet: ImecPacket) -> list[ParsedRecord]:
    if packet.msg_type == MessageType.CLICK_REPORT:
        return _click_report_records(packet)
    if packet.msg_type == MessageType.SURVEY_PAIR_RESULT:
        return _survey_pair_records(packet)
    return []


def _click_report_records(packet: ImecPacket) -> list[ParsedRecord]:
    tlvs = decode_tlvs(packet.payload)
    anchor_id = read_uint(first_tlv(tlvs, TlvId.ANCHOR_ID)) or packet.source_id
    sample_index = read_uint(first_tlv(tlvs, TlvId.SAMPLE_INDEX)) or 0
    range_status = read_uint(first_tlv(tlvs, TlvId.RANGE_STATUS))
    status = _range_status_text(range_status)
    rx_power_dbm = read_int(first_tlv(tlvs, TlvId.UWB_RSL_DBM))
    if rx_power_dbm == 0:
        rx_power_dbm = None
    sample_bytes = first_tlv(tlvs, TlvId.DISTANCE_SAMPLES_MM)
    records: list[ParsedRecord] = []
    if sample_bytes:
        for offset in range(0, len(sample_bytes) - 3, 4):
            distance_mm = struct.unpack_from("<i", sample_bytes, offset)[0]
            records.append(
                ParsedRecord(
                    kind="sample" if distance_mm >= 0 and range_status in (None, 0) else "failure",
                    anchor_id=format_device_id(anchor_id),
                    sample_index=sample_index + (offset // 4),
                    distance_m=None if distance_mm < 0 else distance_mm / 1000.0,
                    rx_power_dbm=float(rx_power_dbm) if rx_power_dbm is not None else None,
                    status=status,
                    error_code=None if range_status in (None, 0) else str(range_status),
                    source="ble_click_report",
                    raw_line=packet_summary(packet),
                )
            )
        return records

    distance_mm = read_int(first_tlv(tlvs, TlvId.DISTANCE_MM))
    if distance_mm is None:
        return []
    records.append(
        ParsedRecord(
            kind="sample" if distance_mm >= 0 and range_status in (None, 0) else "failure",
            anchor_id=format_device_id(anchor_id),
            sample_index=sample_index,
            distance_m=None if distance_mm < 0 else distance_mm / 1000.0,
            rx_power_dbm=float(rx_power_dbm) if rx_power_dbm is not None else None,
            status=status,
            error_code=None if range_status in (None, 0) else str(range_status),
            source="ble_click_report",
            raw_line=packet_summary(packet),
        )
    )
    return records


def _survey_pair_records(packet: ImecPacket) -> list[ParsedRecord]:
    tlvs = decode_tlvs(packet.payload)
    responder_id = read_uint(first_tlv(tlvs, TlvId.RESPONDER_ID)) or packet.source_id
    sample_index = read_uint(first_tlv(tlvs, TlvId.SAMPLE_INDEX))
    distance_mm = read_int(first_tlv(tlvs, TlvId.DISTANCE_MM))
    range_status = read_uint(first_tlv(tlvs, TlvId.RANGE_STATUS))
    if distance_mm is None:
        return []
    return [
        ParsedRecord(
            kind="sample" if distance_mm >= 0 and range_status in (None, 0) else "failure",
            anchor_id=format_device_id(responder_id),
            sample_index=sample_index,
            distance_m=None if distance_mm < 0 else distance_mm / 1000.0,
            status=f"survey_{_range_status_text(range_status)}",
            error_code=None if range_status in (None, 0) else str(range_status),
            source="ble_survey_pair_result",
            raw_line=packet_summary(packet),
        )
    ]


def _range_status_text(status: int | None) -> str:
    if status is None or status == 0:
        return "ok"
    return _enum_name(RangeStatus, status, "range_status").lower()


def _enum_name(enum: Any, value: int | None, prefix: str) -> str:
    if value is None:
        return f"{prefix}_UNKNOWN"
    try:
        return enum(value).name
    except ValueError:
        return f"{prefix}_{value}"
