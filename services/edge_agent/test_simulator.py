import tempfile
import unittest
from pathlib import Path

from services.edge_agent.simulator import Simulator, build_snapshot
from packages.bms_protocol.pack_status import pack_status_from_frames
from services.edge_agent.session_store import SessionStore


class SimulatorTests(unittest.TestCase):
    def test_normal_snapshot_contains_144_slots_and_136_physical_cells(self):
        snapshot = Simulator().next_snapshot()
        self.assertEqual(len(snapshot["cells"]), 144)
        self.assertEqual(sum(c["physicalValid"] for c in snapshot["cells"]), 136)
        self.assertEqual(len(snapshot["frames"]), 38)
        self.assertTrue(all(cell["valid"] for cell in snapshot["cells"]))

    def test_module_metadata_preserves_physical_and_reserved_slot_boundaries(self):
        cells = Simulator().next_snapshot()["cells"]
        modules = {}
        for cell in cells:
            modules.setdefault(cell["moduleIndex"], []).append(cell)

        self.assertEqual(sorted(modules), list(range(8)))
        for module_index, module_cells in modules.items():
            self.assertEqual([cell["channelIndex"] for cell in module_cells], list(range(18)))
            self.assertEqual([cell["cellIndex"] for cell in module_cells],
                             list(range(module_index * 18, (module_index + 1) * 18)))
            self.assertEqual(sum(cell["physicalValid"] for cell in module_cells), 17)
            self.assertTrue(all(cell["physicalValid"] for cell in module_cells[:-1]))
            self.assertFalse(module_cells[-1]["physicalValid"])
            self.assertEqual(module_cells[-1]["value"], 0)

    def test_pec_error_keeps_value_but_is_invalid_and_has_diagnostic(self):
        simulator = Simulator()
        simulator.inject_pec_error(18)
        snapshot = simulator.next_snapshot()
        cell = snapshot["cells"][18]
        self.assertEqual(cell["value"], 3700)
        self.assertFalse(cell["valid"])
        self.assertEqual(cell["quality"], "pec_error")
        self.assertTrue(snapshot["diagnostics"])

    def test_pack_status_is_encoded_from_independent_simulation_inputs(self):
        snapshot = build_snapshot(7, 1500, snapshot_id="independent-inputs", firmware_version="BMS_SIM_0.2.0",
                                  total_voltage_adc=505.7, precharge_voltage=120.3, current=-2.5, soc=67.8)
        status = snapshot["packStatus"]
        self.assertEqual(status, pack_status_from_frames(snapshot["frames"]))
        self.assertEqual(status["totalVoltageAdc"]["value"], 505.7)
        self.assertEqual(status["prechargeVoltage"]["value"], 120.3)
        self.assertEqual(status["current"]["value"], -2.5)
        self.assertEqual(status["soc"]["value"], 67.8)
        self.assertAlmostEqual(status["totalVoltageAccumulated"]["value"], int(snapshot["totalVoltage"]["value"] / 100) / 10)
        self.assertTrue(all(frame["qualityMetadataSource"] == "simulation" for frame in snapshot["frames"]))

    def test_session_store_appends_and_reads_immutable_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory)
            snapshot = Simulator().next_snapshot()
            self.assertEqual(store.append(snapshot), snapshot["id"])
            self.assertEqual(store.latest()["id"], snapshot["id"])
            self.assertEqual(len(store.list_snapshots()), 1)
            self.assertTrue(Path(directory, "snapshots.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
