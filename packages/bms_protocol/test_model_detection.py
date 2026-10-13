import unittest

from packages.bms_protocol.model_detection import detect_model


BOUNDARIES = [17, 35, 53, 71, 89, 107, 125]


def make_snapshot(boundary_value=0, temperature_value=0, *, missing_temperature=False):
    cells = [
        {"value": 3800, "valid": True, "quality": "valid", "timestamp": 100.0, "sequence": 1}
        for _ in range(136)
    ]
    for index in BOUNDARIES:
        cells[index]["value"] = boundary_value
    temperatures = [] if missing_temperature else [
        {"value": temperature_value, "valid": True, "quality": "valid", "timestamp": 100.0, "sequence": 1}
        for _ in range(70)
    ]
    return {"timestamp": 100.0, "cells": cells, "temperatures": temperatures}


class ModelDetectionTests(unittest.TestCase):
    def test_complete_af_evidence_selects_af(self):
        result = detect_model(make_snapshot(boundary_value=0, temperature_value=0))
        self.assertEqual(result["model"], "af")
        self.assertEqual(result["mode"], "automatic")
        self.assertEqual(result["confidence"], "high")

    def test_complete_ef_evidence_selects_ef(self):
        result = detect_model(make_snapshot(boundary_value=3800, temperature_value=25))
        self.assertEqual(result["model"], "ef")
        self.assertEqual(result["confidence"], "high")

    def test_mixed_evidence_is_unknown_instead_of_defaulting_to_ef(self):
        snapshot = make_snapshot(boundary_value=0, temperature_value=0)
        snapshot["cells"][BOUNDARIES[0]]["value"] = 3800
        result = detect_model(snapshot)
        self.assertEqual(result["model"], "unknown")
        self.assertIn("mixed", result["reason"])

    def test_incomplete_evidence_preserves_previous_model(self):
        result = detect_model(
            make_snapshot(missing_temperature=True),
            previous={"model": "af", "mode": "automatic"},
        )
        self.assertEqual(result["model"], "af")
        self.assertIn("incomplete", result["reason"])

    def test_invalid_or_stale_evidence_does_not_select_model(self):
        snapshot = make_snapshot(boundary_value=3800, temperature_value=25)
        snapshot["cells"][BOUNDARIES[0]]["quality"] = "stale"
        result = detect_model(snapshot)
        self.assertEqual(result["model"], "unknown")
        self.assertIn("quality", result["reason"])

    def test_manual_override_takes_precedence_and_can_return_to_automatic(self):
        snapshot = make_snapshot(boundary_value=0, temperature_value=0)
        manual = detect_model(snapshot, override="ef")
        self.assertEqual(manual["model"], "ef")
        self.assertEqual(manual["mode"], "manual")
        automatic = detect_model(snapshot, override=None)
        self.assertEqual(automatic["model"], "af")

    def test_old_or_mixed_batch_evidence_cannot_select_model(self):
        for field, value in (("timestamp", 1.0), ("sequence", 2)):
            snapshot = make_snapshot()
            snapshot["temperatures"][69][field] = value
            with self.subTest(field=field):
                self.assertEqual(detect_model(snapshot)["model"], "unknown")

    def test_missing_value_or_sequence_cannot_select_model(self):
        for field in ("value", "sequence", "timestamp"):
            snapshot = make_snapshot()
            snapshot["temperatures"][69].pop(field)
            with self.subTest(field=field):
                self.assertEqual(detect_model(snapshot)["model"], "unknown")


if __name__ == "__main__":
    unittest.main()
