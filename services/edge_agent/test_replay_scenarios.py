import tempfile
import unittest

from services.edge_agent.replay_scenarios import SCENARIO_NAMES, build_scenario
from services.edge_agent.session_store import SessionStore


class ReplayScenarioTests(unittest.TestCase):
    def test_all_named_scenarios_are_available_and_have_evidence(self):
        self.assertEqual(
            SCENARIO_NAMES,
            ("valid", "pec_error", "partial", "timeout_stale", "disconnected_reconnect", "af", "ef", "mixed_model"),
        )
        for name in SCENARIO_NAMES:
            records = build_scenario(name)
            self.assertTrue(records, name)
            for record in records:
                self.assertEqual(record["scenario"], name)
                self.assertIn("sequence", record)
                self.assertIn("snapshot", record)
                self.assertIn("frames", record)

    def test_quality_scenarios_preserve_invalid_value_and_diagnostics(self):
        pec = build_scenario("pec_error")[0]["snapshot"]
        self.assertFalse(pec["cells"][18]["valid"])
        self.assertEqual(pec["cells"][18]["quality"], "pec_error")
        self.assertTrue(pec["diagnostics"])

        partial = build_scenario("partial")[-1]["snapshot"]
        self.assertEqual(partial["cells"][10]["quality"], "partial")
        self.assertFalse(partial["cells"][10]["valid"])

        timeout = build_scenario("timeout_stale")[-1]["snapshot"]
        self.assertEqual(timeout["totalVoltage"]["quality"], "timeout")
        self.assertEqual(timeout["cells"][0]["quality"], "stale")

    def test_disconnect_then_reconnect_does_not_claim_fresh_value_while_offline(self):
        records = build_scenario("disconnected_reconnect")
        self.assertEqual(records[1]["snapshot"]["totalVoltage"]["quality"], "disconnected")
        self.assertFalse(records[1]["snapshot"]["totalVoltage"]["valid"])
        self.assertEqual(records[-1]["snapshot"]["totalVoltage"]["quality"], "valid")
        self.assertTrue(records[-1]["snapshot"]["totalVoltage"]["valid"])

    def test_pack_status_keeps_previous_evidence_during_timeout_and_disconnect(self):
        for scenario, quality in [("timeout_stale", "stale"), ("disconnected_reconnect", "disconnected")]:
            records = build_scenario(scenario)
            first, missing = records[0]["snapshot"], records[1]["snapshot"]
            self.assertEqual(missing["frames"], [])
            for name, value in first["packStatus"].items():
                with self.subTest(scenario=scenario, name=name):
                    for field in ["value", "timestamp", "sequence", "rawFrameIds"]:
                        self.assertEqual(missing["packStatus"][name][field], value[field])
                    self.assertEqual(missing["packStatus"][name]["quality"], quality)
                    self.assertFalse(missing["packStatus"][name]["valid"])

    def test_accumulated_quality_tracks_partial_cells_but_adc_is_independent(self):
        for scenario in ["pec_error", "partial"]:
            status = build_scenario(scenario)[-1]["snapshot"]["packStatus"]
            self.assertEqual(status["totalVoltageAccumulated"]["quality"], "partial")
            self.assertFalse(status["totalVoltageAccumulated"]["valid"])
            self.assertTrue(status["totalVoltageAdc"]["valid"])

    def test_model_scenarios_and_session_store_are_isolated(self):
        self.assertEqual(build_scenario("af")[0]["snapshot"]["modelDetection"]["model"], "af")
        self.assertEqual(build_scenario("ef")[0]["snapshot"]["modelDetection"]["model"], "ef")
        self.assertEqual(build_scenario("mixed_model")[0]["snapshot"]["modelDetection"]["model"], "unknown")
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            store.append(build_scenario("pec_error")[0]["snapshot"])
            store.append(build_scenario("valid")[0]["snapshot"])
            self.assertEqual(store.list_snapshots()[0]["cells"][18]["quality"], "pec_error")
            self.assertEqual(store.list_snapshots()[1]["cells"][18]["quality"], "valid")


if __name__ == "__main__":
    unittest.main()
