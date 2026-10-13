import unittest
from copy import deepcopy

from packages.bms_protocol.pack_status import pack_status_from_frames


def frame(frame_id, data):
    return {"id": frame_id, "bus": "can1", "direction": "rx", "ide": "standard", "rtr": False,
            "data": data, "dlc": len(data), "timestamp": 1234.5, "sequence": 9}


class PackStatusTests(unittest.TestCase):
    def test_units_signed_current_and_sampling_evidence_are_preserved(self):
        frames = [frame(0x100, [0x06, 0xF6, 0xFF, 0xFF, 0xC8, 0x13, 0xD0, 0x13]),
                  frame(0x101, [0x94, 0x13, 0x38, 0x03, 0, 0, 0, 0])]
        original = deepcopy(frames)
        status = pack_status_from_frames(frames)
        for name, expected, unit, raw_id in [
                ("current", -2.554, "A", 0x100),
                ("totalVoltageAccumulated", 506.4, "V", 0x100),
                ("totalVoltageAdc", 507.2, "V", 0x100),
                ("prechargeVoltage", 501.2, "V", 0x101),
                ("soc", 82.4, "%", 0x101)]:
            with self.subTest(name=name):
                self.assertEqual(status[name]["value"], expected)
                self.assertEqual(status[name]["unit"], unit)
                self.assertEqual(status[name]["timestamp"], 1234.5)
                self.assertEqual(status[name]["sequence"], 9)
                self.assertEqual(status[name]["rawFrameIds"], [raw_id])
                self.assertTrue(status[name]["valid"])
        self.assertEqual(frames, original)

    def test_missing_frames_remain_absent_and_short_frames_keep_null_quality(self):
        self.assertEqual(pack_status_from_frames([]), {})
        status = pack_status_from_frames([frame(0x101, [0x94, 0x13])])
        self.assertNotIn("totalVoltageAdc", status)
        self.assertEqual(status["prechargeVoltage"]["value"], 501.2)
        self.assertIsNone(status["soc"]["value"])
        self.assertFalse(status["soc"]["valid"])
        self.assertEqual(status["soc"]["quality"], "partial")

    def test_simulation_quality_and_legacy_unknown_are_not_upgraded(self):
        raw = frame(0x100, [0, 0, 0, 0, 0xC8, 0x13, 0xD0, 0x13])
        raw.update(qualityMetadataSource="simulation", signalQualities={"totalVoltageAccumulated": "partial"})
        status = pack_status_from_frames([raw], legacy_simulation=True)
        self.assertEqual(status["totalVoltageAccumulated"]["quality"], "partial")
        self.assertEqual(status["totalVoltageAdc"]["quality"], "valid")
        raw.pop("qualityMetadataSource")
        self.assertEqual(pack_status_from_frames([raw], legacy_simulation=True)["current"]["quality"], "unknown")


if __name__ == "__main__":
    unittest.main()
