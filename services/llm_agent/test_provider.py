import unittest
from copy import deepcopy

from services.llm_agent.provider import MockAiProvider
from services.edge_agent.simulator import Simulator
from services.edge_agent.replay_scenarios import build_scenario
from services.llm_agent.provider import analysis_to_dict


class AiProviderTests(unittest.TestCase):
    def test_empty_snapshot_does_not_claim_valid_sampling(self):
        result = MockAiProvider().analyze({"cells": [], "diagnostics": []})
        self.assertEqual(result.severity, "warning")
        self.assertIn("缺少", result.summary)
    def test_provider_returns_evidence_bounded_analysis(self):
        simulator = Simulator()
        simulator.inject_pec_error(18)
        result = MockAiProvider().analyze(simulator.next_snapshot())
        self.assertEqual(result.suggested_chain, "H01/A01")
        self.assertIn("CELL_PEC_ERROR", result.evidence)
        self.assertTrue(result.next_checks)

    def test_provider_does_not_claim_clean_data_when_invalid_cell_exists(self):
        snapshot = Simulator().next_snapshot()
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual(result.severity, "info")
        self.assertIsNone(result.suggested_chain)

    def test_real_source_cannot_inherit_simulation_success_claim(self):
        snapshot = Simulator().next_snapshot()
        snapshot["dataSource"] = "can"
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual("warning", result.severity)
        self.assertNotIn("模拟", result.summary)
        self.assertIn("有效性", result.summary)
        self.assertIn("dataSource=can", result.evidence)
        self.assertTrue(any("固件" in check and "有效" in check for check in result.next_checks))

    def test_unknown_quality_overrides_true_valid_flag(self):
        snapshot = Simulator().next_snapshot()
        snapshot["cells"][0].update(quality="unknown", valid=True)
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual("warning", result.severity)
        self.assertIn("未知", result.summary)
        self.assertTrue(any("quality=unknown" in item for item in result.evidence))

    def test_missing_value_is_not_valid_sampling(self):
        snapshot = Simulator().next_snapshot()
        snapshot["cells"][0].update(value=None, quality="valid", valid=True)
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual("warning", result.severity)
        self.assertIn("缺少", result.summary)

    def test_reserved_slots_do_not_count_as_failed_physical_cells(self):
        snapshot = Simulator().next_snapshot()
        for cell in snapshot["cells"]:
            if cell["cellIndex"] % 18 == 17:
                cell.update(value=None, valid=False, quality="unknown")
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual("info", result.severity)
        self.assertTrue(any("physicalSlots=136/136" in item and "softwareSlots=144" in item for item in result.evidence))

    def test_partial_slot_coverage_is_reported_even_when_received_cells_are_valid(self):
        snapshot = Simulator().next_snapshot()
        snapshot["cells"] = snapshot["cells"][:16]
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual("warning", result.severity)
        self.assertIn("缺少", result.summary)

    def test_timeout_and_disconnect_are_distinct_and_keep_old_value_evidence(self):
        for scenario, word in (("timeout_stale", "超时"), ("disconnected_reconnect", "断开")):
            snapshot = build_scenario(scenario)[1]["snapshot"]
            result = MockAiProvider().analyze(snapshot)
            self.assertIn(word, result.summary)
            self.assertTrue(any("旧值" in check for check in result.next_checks))
            self.assertTrue(any("timestamp=" in item and "sequence=" in item for item in result.evidence))

    def test_pack_quality_is_checked_even_when_cells_are_valid(self):
        snapshot = Simulator().next_snapshot()
        snapshot["packStatus"]["prechargeVoltage"].update(quality="timeout", valid=False)
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual("warning", result.severity)
        self.assertIn("超时", result.summary)
        self.assertTrue(any("prechargeVoltage" in item for item in result.evidence))

    def test_nonzero_alarm_and_mask_have_explicit_raw_evidence(self):
        snapshot = Simulator().next_snapshot()
        snapshot["signals"] = {
            "alarm.IMD_Error": {"value": 1, "quality": "valid", "valid": True, "timestamp": 10, "sequence": 2, "rawFrameIds": [0x142]},
            "alarmMask": {"value": 0x800, "quality": "valid", "valid": True, "timestamp": 10, "sequence": 2, "rawFrameIds": [0x141]},
        }
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual("warning", result.severity)
        self.assertIn("告警", result.summary)
        self.assertTrue(any("alarm.IMD_Error=1" in item for item in result.evidence))
        self.assertTrue(any("alarmMask=2048" in item for item in result.evidence))
        self.assertEqual("H07/A01", result.suggested_chain)

    def test_stale_alarm_is_not_declared_current_fault(self):
        snapshot = Simulator().next_snapshot()
        snapshot["signals"] = {"alarm.Precharge_Fail": {"value": 1, "quality": "stale", "valid": False,
                                                       "timestamp": 10, "sequence": 2, "rawFrameIds": [0x143]}}
        result = MockAiProvider().analyze(snapshot)
        self.assertIn("旧值", result.summary)
        self.assertTrue(any("不能" in check and "当前" in check for check in result.next_checks))

    def test_precharge_mirror_never_claims_physical_contact_or_requests_actuation(self):
        snapshot = Simulator().next_snapshot()
        snapshot["signals"] = {"relayPrecharge": {"value": 1, "quality": "valid", "valid": True,
                                                  "timestamp": 10, "sequence": 2, "rawFrameIds": [0x139]}}
        result = MockAiProvider().analyze(snapshot)
        self.assertTrue(any("镜像" in item for item in result.evidence))
        self.assertTrue(any("实物" in check and "不能" in check for check in result.next_checks))
        text = " ".join(result.next_checks)
        for command in ("自动闭合", "写入阈值", "下发命令", "自动清故障"):
            self.assertNotIn(command, text)

    def test_file_completion_is_not_diagnosed_as_physical_bus_disconnect(self):
        snapshot = build_scenario("disconnected_reconnect")[1]["snapshot"]
        snapshot["dataSource"] = "file"
        result = MockAiProvider().analyze(snapshot)
        self.assertIn("文件", result.summary)
        self.assertNotIn("真实 CAN 已断线", result.summary)
        self.assertTrue(any("文件结束" in check for check in result.next_checks))

    def test_info_event_alone_does_not_become_warning(self):
        snapshot = build_scenario("disconnected_reconnect")[2]["snapshot"]
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual("info", result.severity)
        self.assertIn("CAN_RECONNECTED", result.evidence)

    def test_result_keeps_remote_provider_response_contract(self):
        result = analysis_to_dict(MockAiProvider().analyze(Simulator().next_snapshot()))
        self.assertEqual({"provider", "model", "summary", "severity", "evidence", "suggestedChain", "nextChecks"}, set(result))

    def test_real_alarm_keeps_fault_chain_priority_over_unknown_sampling(self):
        snapshot = Simulator().next_snapshot()
        snapshot["dataSource"] = "can"
        for cell in snapshot["cells"]:
            cell.update(valid=False, quality="unknown")
        snapshot["signals"] = {"alarm.IMD_Error": {"value": 1, "quality": "unknown", "valid": False,
                                                  "timestamp": 10, "sequence": 2, "source": "can1:0x142", "rawFrameIds": [0x142]}}
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual("H07/A01", result.suggested_chain)
        self.assertTrue(any("source=can1:0x142" in item for item in result.evidence))

    def test_firmware_error_state_is_not_clean_without_alarm_frames(self):
        snapshot = Simulator().next_snapshot()
        snapshot["signals"] = {"bmsState": {"value": 7, "quality": "valid", "valid": True,
                                            "timestamp": 10, "sequence": 2, "rawFrameIds": [0x101]}}
        original = deepcopy(snapshot)
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual("warning", result.severity)
        self.assertIn("故障状态", result.summary)
        self.assertTrue(any("bmsState=7" in item for item in result.evidence))
        self.assertEqual(original, snapshot)


if __name__ == "__main__":
    unittest.main()
