import tempfile
import unittest
from pathlib import Path

from services.edge_agent.simulator import Simulator
from services.edge_agent.session_store import SessionStore


class SimulatorTests(unittest.TestCase):
    def test_normal_snapshot_contains_136_valid_cells_and_frames(self):
        snapshot = Simulator().next_snapshot()
        self.assertEqual(len(snapshot["cells"]), 136)
        self.assertEqual(len(snapshot["frames"]), 34)
        self.assertTrue(all(cell["valid"] for cell in snapshot["cells"]))

    def test_pec_error_keeps_value_but_is_invalid_and_has_diagnostic(self):
        simulator = Simulator()
        simulator.inject_pec_error(17)
        snapshot = simulator.next_snapshot()
        cell = snapshot["cells"][17]
        self.assertEqual(cell["value"], 3700)
        self.assertFalse(cell["valid"])
        self.assertEqual(cell["quality"], "pec_error")
        self.assertTrue(snapshot["diagnostics"])

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
