import unittest

from packages.bms_protocol.decoder import decode_can1_frame
from services.edge_agent.replay_scenarios import SCENARIO_NAMES, build_scenario


class EvidenceConsistencyTests(unittest.TestCase):
    def test_pec_quality_is_simulated_metadata_and_decoded_consistently(self):
        snapshot = build_scenario("pec_error")[0]["snapshot"]
        cell = snapshot["cells"][18]
        frame = next(f for f in snapshot["frames"] if f["id"] == cell["rawFrameIds"][0])
        self.assertEqual(frame.get("qualityMetadataSource"), "simulation")
        signal = next(s for s in decode_can1_frame(frame) if s["name"] == "cellVoltage[18]")
        self.assertEqual(signal["quality"], "pec_error")
        self.assertEqual(signal["value"], cell["value"])
    def test_all_model_scenarios_match_raw_frames_and_accumulated_voltage(self):
        for name in ("valid", "af", "ef", "mixed_model"):
            with self.subTest(scenario=name):
                snapshot = build_scenario(name)[0]["snapshot"]
                decoded = {item["name"]: item for frame in snapshot["frames"]
                           for item in decode_can1_frame(frame)}
                for cell in snapshot["cells"]:
                    self.assertEqual(cell["value"], decoded[f"cellVoltage[{cell['cellIndex']}]"]["value"])
                self.assertEqual(snapshot["totalVoltage"]["value"], sum(c["value"] for c in snapshot["cells"]))

    def test_software_slots_keep_physical_mapping_and_synthetic_temperature_source(self):
        for name in SCENARIO_NAMES:
            snapshot = build_scenario(name)[0]["snapshot"]
            with self.subTest(scenario=name):
                self.assertEqual(len(snapshot["cells"]), 144)
                self.assertEqual(sum(c["physicalValid"] for c in snapshot["cells"]), 136)
                self.assertTrue(all(c["physicalValid"] == (i % 18 != 17) for i, c in enumerate(snapshot["cells"])))
                self.assertTrue(all(t["source"] == "synthetic-model-evidence" for t in snapshot["temperatures"]))

    def test_invalid_scenarios_do_not_keep_high_confidence_model(self):
        for name in ("pec_error", "partial", "timeout_stale", "disconnected_reconnect"):
            records = build_scenario(name)
            with self.subTest(scenario=name):
                invalid = [r["snapshot"] for r in records if r["snapshot"]["totalVoltage"]["valid"] is False]
                self.assertTrue(invalid, "故障场景必须使累压无效")
                snapshot = invalid[0]
                self.assertNotEqual(snapshot["modelDetection"]["confidence"], "high")

    def test_partial_update_keeps_old_group_evidence_and_omits_received_frame(self):
        records = build_scenario("partial")
        self.assertEqual(len(records), 2)
        before, after = [r["snapshot"] for r in records]
        cell = after["cells"][10]
        self.assertEqual(cell["timestamp"], before["cells"][10]["timestamp"])
        self.assertEqual(cell["sequence"], before["cells"][10]["sequence"])
        self.assertLess(cell["timestamp"], after["timestamp"])
        self.assertNotIn(cell["rawFrameIds"][0], [f["id"] for f in after["frames"]])

    def test_timeout_and_disconnect_do_not_invent_new_received_values(self):
        for name, quality in (("timeout_stale", "stale"), ("disconnected_reconnect", "disconnected")):
            records = build_scenario(name)
            snapshot = next(r["snapshot"] for r in records if not r["snapshot"]["totalVoltage"]["valid"])
            with self.subTest(scenario=name):
                self.assertEqual(snapshot["frames"], [])
                for value in snapshot["cells"] + snapshot["temperatures"] + [snapshot["current"]]:
                    self.assertFalse(value["valid"])
                    self.assertEqual(value["quality"], quality)
                    self.assertLess(value["timestamp"], snapshot["timestamp"])
                    self.assertLess(value["sequence"], next(r["sequence"] for r in records if r["snapshot"] is snapshot))


if __name__ == "__main__":
    unittest.main()
