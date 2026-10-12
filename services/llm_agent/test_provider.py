import unittest

from services.llm_agent.provider import MockAiProvider
from services.edge_agent.simulator import Simulator


class AiProviderTests(unittest.TestCase):
    def test_empty_snapshot_does_not_claim_valid_sampling(self):
        result = MockAiProvider().analyze({"cells": [], "diagnostics": []})
        self.assertEqual(result.severity, "warning")
        self.assertIn("缺少", result.summary)
    def test_provider_returns_evidence_bounded_analysis(self):
        simulator = Simulator()
        simulator.inject_pec_error(17)
        result = MockAiProvider().analyze(simulator.next_snapshot())
        self.assertEqual(result.suggested_chain, "H01/A01")
        self.assertIn("CELL_PEC_ERROR", result.evidence)
        self.assertTrue(result.next_checks)

    def test_provider_does_not_claim_clean_data_when_invalid_cell_exists(self):
        snapshot = Simulator().next_snapshot()
        result = MockAiProvider().analyze(snapshot)
        self.assertEqual(result.severity, "info")
        self.assertIsNone(result.suggested_chain)


if __name__ == "__main__":
    unittest.main()
