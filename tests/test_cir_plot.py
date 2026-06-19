import unittest

from uwb_capture.base_gui import _cir_first_path_local_index, _cir_record_matches_diag
from uwb_capture.common import ParsedRecord


class CirPlotTests(unittest.TestCase):
    def test_first_path_absolute_index_maps_into_visible_window(self) -> None:
        record = ParsedRecord(
            kind="sample",
            cir_first_path_index=700,
            cir_start_index=640,
        )

        self.assertEqual(_cir_first_path_local_index(record, 256), 60)

    def test_first_path_without_start_index_is_already_local(self) -> None:
        record = ParsedRecord(kind="sample", cir_first_path_index=4)

        self.assertEqual(_cir_first_path_local_index(record, 16), 4)

    def test_first_path_outside_window_is_not_drawn(self) -> None:
        record = ParsedRecord(
            kind="sample",
            cir_first_path_index=700,
            cir_start_index=640,
        )

        self.assertIsNone(_cir_first_path_local_index(record, 32))

    def test_cir_record_matching_rejects_old_trigger_event(self) -> None:
        old_record = ParsedRecord(kind="sample", anchor_id="anchor-1", event_seq=10)
        current_record = ParsedRecord(kind="sample", anchor_id="anchor-1", event_seq=11)

        self.assertFalse(_cir_record_matches_diag(old_record, "anchor-1", 11))
        self.assertTrue(_cir_record_matches_diag(current_record, "anchor-1", 11))

    def test_cir_record_matching_allows_current_trigger_without_event_seq(self) -> None:
        record = ParsedRecord(kind="sample", anchor_id="anchor-1")

        self.assertTrue(_cir_record_matches_diag(record, "anchor-1", None))


if __name__ == "__main__":
    unittest.main()
