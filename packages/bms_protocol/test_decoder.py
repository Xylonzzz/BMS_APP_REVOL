import unittest

from packages.bms_protocol.decoder import decode_can1_frame


class ProtocolDecoderTests(unittest.TestCase):
    def test_decodes_status_frame_little_endian_and_signed_current(self):
        frame = {"id": 0x100, "data": [0x18, 0xFC, 0xFF, 0xFF, 0xD8, 0x16, 0xE0, 0x16], "dlc": 8}
        signals = decode_can1_frame(frame)
        self.assertEqual(signals[0]["name"], "totalCurrent")
        self.assertEqual(signals[0]["value"], -1000)
        self.assertEqual(signals[1]["value"], 5848)
        self.assertEqual(signals[2]["unit"], "0.1V")

    def test_decodes_cell_frame_using_compatibility_slot_index(self):
        frame = {"id": 0x105, "data": [0x70, 0x0E, 0x72, 0x0E, 0x74, 0x0E, 0x76, 0x0E], "dlc": 8}
        signals = decode_can1_frame(frame)
        self.assertEqual(signals[0]["name"], "cellVoltage[4]")
        self.assertEqual(signals[0]["value"], 3696)
        self.assertEqual(signals[-1]["name"], "cellVoltage[7]")

    def test_short_frame_is_partial_and_does_not_invent_values(self):
        signals = decode_can1_frame({"id": 0x139, "data": [1, 0], "dlc": 2})
        self.assertTrue(all(signal["quality"] == "partial" for signal in signals))
        self.assertIsNone(signals[2]["value"])

    def test_same_id_downlink_is_not_decoded_as_uplink(self):
        signals = decode_can1_frame({"id": 0x101, "data": [1, 2, 3, 4, 5, 6, 7, 8], "dlc": 8}, direction="down")
        self.assertEqual(signals[0]["name"], "unknownFrame")
        self.assertEqual(signals[0]["quality"], "unknown")


if __name__ == "__main__":
    unittest.main()
