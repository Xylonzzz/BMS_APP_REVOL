from __future__ import annotations

import unittest

from packages.bms_protocol.decoder import decode_can1_frame


def frame(frame_id: int, data: list[int], **changes) -> dict:
    result = {"id": frame_id, "bus": "can1", "direction": "rx", "ide": "standard", "rtr": False,
              "dlc": len(data), "data": data, "timestamp": 123.0, "sequence": 1}
    result.update(changes)
    return result


def signals(frame_id: int, data: list[int], **changes) -> dict:
    return {value["name"]: value for value in decode_can1_frame(frame(frame_id, data, **changes))}


class TelemetryTests(unittest.TestCase):
    def test_state_tail_is_rtc_bms_state_and_init_flag_not_alarm(self):
        actual = signals(0x101, [0, 0, 0, 0, 0x34, 0x12, 5, 1])
        self.assertEqual(0x1234, actual["rtcTime"]["value"])
        self.assertEqual("raw", actual["rtcTime"]["unit"])
        self.assertEqual(5, actual["bmsState"]["value"])
        self.assertEqual(1, actual["initFlag"]["value"])
        self.assertFalse(any("alarm" in name.lower() for name in actual))

    def test_extrema_are_u16_and_indices_ignore_tail(self):
        actual = signals(0x134, [0xA0, 0x0F, 0x48, 0x0D, 40, 0, 15, 0])
        self.assertEqual([4000, 3400, 40, 15], [item["value"] for item in actual.values()])
        self.assertEqual("mV", actual["maxCellVoltage"]["unit"])
        actual = signals(0x138, [143, 0, 47, 2, 255, 255, 255, 255])
        self.assertEqual([143, 0, 47, 2], [item["value"] for item in actual.values()])
        self.assertEqual(4, len(actual))

    def test_alarm_mask_and_eighteen_flags_ignore_two_neighboring_bytes(self):
        payload = list((0xA1234567).to_bytes(4, "little")) + list(range(1, 19)) + [255, 254]
        actual = {}
        for index in range(3):
            actual.update(signals(0x141 + index, payload[index * 8:index * 8 + 8]))
        self.assertEqual(19, len(actual))
        self.assertEqual(0xA1234567, actual["alarmMask"]["value"])
        self.assertEqual(1, actual["alarm.OV"]["value"])
        self.assertEqual(12, actual["alarm.IMD_Error"]["value"])
        self.assertEqual(18, actual["alarm.Vol_Error"]["value"])
        self.assertFalse(any(value["value"] in (254, 255) for value in actual.values()))

    def test_bitmaps_consume_only_firmware_valid_ranges(self):
        for first_id, last_id, prefix, count in ((0x135, 0x137, "dcc", 18),
                                                  (0x144, 0x146, "openwireVoltage", 19),
                                                  (0x147, 0x147, "openwireTemperature", 6)):
            actual = {}
            for frame_id in range(first_id, last_id + 1):
                offset = (frame_id - first_id) * 8
                actual.update(signals(frame_id, list(range(offset, offset + 8))))
            with self.subTest(prefix=prefix):
                self.assertEqual(count, len(actual))
                self.assertEqual(list(range(count)), [value["value"] for value in actual.values()])
                self.assertIn(f"{prefix}[{count - 1}]", actual)

    def test_threshold_structure_has_108_bytes_and_no_tail_fields(self):
        payload = list(range(108)) + [255] * 4
        actual = {}
        for index in range(14):
            actual.update(signals(0x150 + index, payload[index * 8:index * 8 + 8]))
        self.assertEqual(72, len(actual))
        self.assertEqual(0x0100, actual["threshold.OTV_LV1"]["value"])
        self.assertEqual(0x1110, actual["threshold.StaticDischarge_OC_LV1"]["value"])
        self.assertEqual(72, actual["threshold.Cell_OT_LV1"]["value"])
        self.assertEqual(104, actual["threshold.Balance_Start_DV"]["value"])
        self.assertEqual(105, actual["threshold.Balance_End_DV"]["value"])
        self.assertEqual(106, actual["threshold.Precharge_End_Vol_Percent"]["value"])
        self.assertEqual(107, actual["threshold.Precharge_Time"]["value"])
        self.assertEqual(["threshold.Balance_Start_DV", "threshold.Balance_End_DV",
                          "threshold.Precharge_End_Vol_Percent", "threshold.Precharge_Time"],
                         list(signals(0x15D, [50, 30, 95, 6, 255, 255, 255, 255])))

    def test_target_charge_values_remain_raw_and_ignore_tail(self):
        actual = signals(0x15F, [0xF8, 0x16, 30, 0, 255, 255, 255, 255])
        self.assertEqual(2, len(actual))
        self.assertEqual(5880, actual["chargeTargetVoltage"]["value"])
        self.assertEqual("0.1V", actual["chargeTargetVoltage"]["unit"])
        self.assertEqual(30, actual["chargeTargetCurrent"]["value"])
        self.assertEqual("0.1A", actual["chargeTargetCurrent"]["unit"])

    def test_all_short_frames_remain_partial_without_reading_padding(self):
        for frame_id in (0x101, 0x134, 0x135, 0x138, 0x141, 0x146, 0x147, 0x150, 0x159, 0x15D, 0x15F):
            for dlc in range(8):
                with self.subTest(frame_id=frame_id, dlc=dlc):
                    actual = signals(frame_id, [1] * 8, dlc=dlc)
                    self.assertTrue(all(item["quality"] == "partial" for item in actual.values()))
                    for item in actual.values():
                        last_byte = int(item["byte_range"].split("..")[-1])
                        if last_byte >= dlc:
                            self.assertIsNone(item["value"])

    def test_unknown_padding_ids_remain_undefined(self):
        for frame_id in (0x148, 0x15E, 0x160):
            self.assertEqual(["unknownFrame"], list(signals(frame_id, [0] * 8)))

    def test_acquisition_quality_and_direction_guards_are_preserved(self):
        for frame_id in (0x101, 0x134, 0x141, 0x150, 0x15F):
            for source in ("can", "file"):
                actual = signals(frame_id, [0] * 8, acquisitionSource=source,
                                 qualityMetadataSource="simulation", signalQualities={"bmsState": "valid"})
                self.assertTrue(all(item["quality"] == "unknown" for item in actual.values()))
            for invalid in ({"bus": "can2"}, {"direction": "tx"}, {"rtr": True}, {"ide": "extended"}):
                self.assertEqual(["unknownFrame"], list(signals(frame_id, [0] * 8, **invalid)))

    def test_catalog_has_original_paths_and_confirmed_unit_conversion(self):
        from packages.bms_protocol.telemetry import SIGNAL_CATALOG
        expected = {"bmsState": ("A03.State.BMS_State", None, 1),
                    "initFlag": ("A03.State.Init_flag", None, 1),
                    "totalCurrent": ("A03.State.TotalCurrent", "A", 1000),
                    "chargerVoltage": ("A03.charge.ChargeVol_rx", "V", 10),
                    "threshold.StaticDischarge_OC_LV1": ("A03.Threshold.StaticDischarge_OC_LV1", "A", 10),
                    "threshold.SOC_High_LV1": ("A03.Threshold.SOC_High_LV1", "%", 10),
                    "threshold.OTV_LV1": ("A03.Threshold.OTV_LV1", "raw", 1),
                    "threshold.Precharge_Time": ("A03.Threshold.Precharge_Time", "s", 1),
                    "openwireVoltage[18]": ("A03.Alarm.Openwire_Vol[18]", "raw", 1)}
        for name, values in expected.items():
            self.assertEqual(values, tuple(SIGNAL_CATALOG[name][key] for key in ("path", "unit", "divisor")))
            self.assertTrue(SIGNAL_CATALOG[name]["label"])
        for frame_id in [0x100, 0x101, *range(0x104, 0x13A), *range(0x140, 0x148), *range(0x150, 0x15E), 0x15F]:
            for name in signals(frame_id, [0] * 8):
                self.assertIn(name, SIGNAL_CATALOG)

    def test_new_telemetry_integrates_with_aggregator_without_scaling_raw_signals(self):
        from packages.bms_protocol.aggregator import SnapshotAggregator
        from packages.bms_protocol.telemetry import SIGNAL_CATALOG
        aggregator = SnapshotAggregator(data_source="file")
        aggregator.ingest(frame(0x15F, [0xF8, 0x16, 30, 0, 0, 0, 0, 0]))
        aggregator.ingest(frame(0x141, [1, 0, 0, 0, 1, 2, 3, 4], sequence=2))
        snapshot = aggregator.snapshot(timestamp=123.0)
        target = snapshot["signals"]["chargeTargetVoltage"]
        self.assertEqual((5880, "0.1V", "unknown"), (target["value"], target["unit"], target["quality"]))
        self.assertEqual([0x15F], target["rawFrameIds"])
        self.assertEqual(1, snapshot["signals"]["alarm.OV"]["value"])
        self.assertEqual("A03.charge.ChargeVol_tx", SIGNAL_CATALOG["chargeTargetVoltage"]["path"])


if __name__ == "__main__":
    unittest.main()
