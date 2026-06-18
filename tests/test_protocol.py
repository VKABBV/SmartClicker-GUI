import unittest

from uwb_capture.protocol import (
    CommandId,
    ImecPacket,
    ImecPacketStream,
    MessageType,
    TlvId,
    build_command_packet,
    command_result_summary,
    decode_packet,
    encode_packet,
    encode_tlvs,
    i32,
    records_from_packet,
    u8,
    u16,
    u32,
    u64,
)


class ProtocolTests(unittest.TestCase):
    def test_build_get_status_command_packet(self) -> None:
        raw = build_command_packet(
            command_id=CommandId.GET_STATUS,
            source_id=0x100,
            destination_id=0x200,
            session_id=1234,
            sequence=7,
            extra_tlvs=[],
        )

        packet = decode_packet(raw)

        self.assertEqual(packet.msg_type, MessageType.COMMAND)
        self.assertEqual(packet.source_id, 0x100)
        self.assertEqual(packet.destination_id, 0x200)
        self.assertEqual(packet.session_id, 1234)
        self.assertEqual(packet.sequence, 7)
        self.assertIn(bytes([TlvId.COMMAND_ID, 2, 0x02, 0x00]), packet.payload)

    def test_packet_stream_reassembles_fragmented_notifications(self) -> None:
        raw = build_command_packet(
            command_id=CommandId.STOP_HEARTBEAT,
            source_id=1,
            destination_id=2,
            session_id=3,
            sequence=4,
            extra_tlvs=[],
        )
        stream = ImecPacketStream()

        self.assertEqual(stream.feed(raw[:10]), [])
        self.assertEqual(stream.feed(raw[10:]), [raw])

    def test_click_report_distance_samples_become_records(self) -> None:
        payload = encode_tlvs(
            [
                (TlvId.ANCHOR_ID, u64(0xAABBCCDD)),
                (TlvId.SAMPLE_INDEX, u16(5)),
                (TlvId.RANGE_STATUS, u8(0)),
                (TlvId.DISTANCE_SAMPLES_MM, i32(1250) + i32(1300)),
            ]
        )
        packet = ImecPacket(
            msg_type=MessageType.CLICK_REPORT,
            flags=0,
            source_id=0xAABBCCDD,
            destination_id=0,
            session_id=99,
            sequence=1,
            ttl=4,
            payload=payload,
        )

        records = records_from_packet(decode_packet(encode_packet(packet)))

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].kind, "sample")
        self.assertEqual(records[0].anchor_id, "0x00000000AABBCCDD")
        self.assertEqual(records[0].sample_index, 5)
        self.assertEqual(records[0].distance_m, 1.25)
        self.assertEqual(records[1].sample_index, 6)
        self.assertEqual(records[1].distance_m, 1.3)

    def test_command_result_summary_includes_status_fields(self) -> None:
        payload = encode_tlvs(
            [
                (TlvId.COMMAND_ID, u16(CommandId.GET_STATUS)),
                (TlvId.COMMAND_STATUS, u8(0)),
                (TlvId.DEVICE_ROLE, u8(2)),
                (TlvId.BATTERY_MV, u16(3010)),
                (TlvId.UPTIME_MS, u32(45000)),
                (TlvId.STATUS_BITS, u32(0x81)),
            ]
        )
        packet = ImecPacket(
            msg_type=MessageType.COMMAND_RESULT,
            flags=0,
            source_id=1,
            destination_id=0,
            session_id=1,
            sequence=1,
            ttl=4,
            payload=payload,
        )

        summary = command_result_summary(packet)

        self.assertIn("GET_STATUS", summary)
        self.assertIn("battery=3010mV", summary)
        self.assertIn("status=0x00000081", summary)

    def test_survey_pair_result_becomes_record(self) -> None:
        payload = encode_tlvs(
            [
                (TlvId.RESPONDER_ID, u64(0x44)),
                (TlvId.SAMPLE_INDEX, u16(2)),
                (TlvId.RANGE_STATUS, u8(0)),
                (TlvId.DISTANCE_MM, i32(2750)),
            ]
        )
        packet = ImecPacket(
            msg_type=MessageType.SURVEY_PAIR_RESULT,
            flags=0,
            source_id=0x33,
            destination_id=0,
            session_id=101,
            sequence=1,
            ttl=4,
            payload=payload,
        )

        record = records_from_packet(decode_packet(encode_packet(packet)))[0]

        self.assertEqual(record.anchor_id, "0x0000000000000044")
        self.assertEqual(record.sample_index, 2)
        self.assertEqual(record.distance_m, 2.75)
        self.assertEqual(record.source, "ble_survey_pair_result")


if __name__ == "__main__":
    unittest.main()
