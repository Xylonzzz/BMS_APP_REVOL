import unittest
from copy import deepcopy

from packages.bms_protocol.aggregator import SnapshotAggregator


def frame(frame_id=0x104, timestamp=100.0, sequence=1, data=None, **extra):
    return dict(id=frame_id, timestamp=timestamp, sequence=sequence, bus="can1", direction="rx",
                ide="standard", rtr=False, dlc=8, data=data or [0x70, 0x0E] * 4, **extra)


class AggregatorTests(unittest.TestCase):
    def test_empty_does_not_invent_measurements(self):
        result = SnapshotAggregator().snapshot(timestamp=100)
        self.assertIsNone(result["cells"][0]["value"])
        self.assertIsNone(result["totalVoltage"]["value"])
        self.assertEqual(result["packStatus"], {})
        self.assertEqual(len(result["cells"]), 144)
        self.assertEqual(sum(c["physicalValid"] for c in result["cells"]), 136)

    def test_raw_receive_is_not_sampling_validity(self):
        source = frame(qualityMetadataSource="simulation", signalQualities={"cellVoltage[0]": "valid"})
        original = deepcopy(source)
        aggregate = SnapshotAggregator()
        aggregate.ingest(source)
        result = aggregate.snapshot(timestamp=100)
        self.assertEqual(result["cells"][0]["value"], 3696)
        self.assertEqual(result["cells"][0]["quality"], "unknown")
        self.assertFalse(result["cells"][0]["valid"])
        self.assertEqual(result["sequenceScope"], "host_rx")
        self.assertEqual(source, original)
        self.assertEqual(result["frames"][0]["data"], source["data"])

    def test_partial_updates_and_timeouts_keep_original_evidence(self):
        aggregate = SnapshotAggregator(stale_after=2)
        aggregate.ingest(frame())
        first = aggregate.snapshot(timestamp=100)
        aggregate.ingest(frame(0x105, 101, 2))
        second = aggregate.snapshot(timestamp=101)
        self.assertEqual(len(second["frames"]), 1)
        self.assertEqual(second["cells"][0]["timestamp"], 100)
        self.assertEqual(second["cells"][4]["sequence"], 2)
        stale = aggregate.snapshot(timestamp=103)
        self.assertEqual(stale["cells"][0]["quality"], "timeout")
        self.assertEqual(stale["cells"][0]["timestamp"], 100)
        self.assertEqual(stale["frames"], [])
        self.assertEqual(len(stale["evidenceFrames"]), 2)
        self.assertEqual(first["cells"][0]["quality"], "unknown")

    def test_status_units_and_disconnect_recovery(self):
        aggregate = SnapshotAggregator(data_source="file")
        aggregate.ingest(frame(0x100, data=list((-1000).to_bytes(4, "little", signed=True)) + [0xD8, 0x16, 0xE0, 0x16]))
        result = aggregate.snapshot(timestamp=100)
        self.assertEqual(result["current"]["value"], -1)
        self.assertEqual(result["totalVoltage"]["value"], 584800)
        self.assertEqual(result["packStatus"]["totalVoltageAdc"]["value"], 585.6)
        disconnected = aggregate.snapshot(timestamp=101, connected=False)
        self.assertEqual(disconnected["packStatus"]["current"]["quality"], "disconnected")
        self.assertEqual(disconnected["packStatus"]["current"]["timestamp"], 100)
        aggregate.ingest(frame(0x101, 102, 2, data=[0x94, 0x13, 0x38, 3, 0, 0, 0, 0]))
        restored = aggregate.snapshot(timestamp=102)
        self.assertEqual(restored["packStatus"]["prechargeVoltage"]["value"], 501.2)
        self.assertEqual(restored["packStatus"]["prechargeVoltage"]["quality"], "unknown")

    def test_wrong_direction_cannot_overwrite_and_short_frame_does_not_clear_old_values(self):
        aggregate = SnapshotAggregator()
        aggregate.ingest(frame())
        wrong = frame(sequence=2); wrong["direction"] = "tx"
        aggregate.ingest(wrong)
        short = frame(sequence=3, timestamp=101); short.update(dlc=2, data=[0, 15])
        aggregate.ingest(short)
        result = aggregate.snapshot(timestamp=101)
        self.assertEqual(result["cells"][0]["value"], 3840)
        self.assertEqual(result["cells"][0]["quality"], "partial")
        self.assertEqual(result["cells"][1]["value"], 3696)
        self.assertEqual(result["cells"][1]["sequence"], 1)

    def test_bad_timestamps_and_backwards_sequence_are_rejected(self):
        aggregate = SnapshotAggregator()
        for source in (frame(timestamp=float("nan")), frame(sequence=-1)):
            with self.assertRaises(ValueError):
                aggregate.ingest(source)
        aggregate.ingest(frame(sequence=3))
        with self.assertRaises(ValueError):
            aggregate.ingest(frame(sequence=2))


if __name__ == "__main__":
    unittest.main()
