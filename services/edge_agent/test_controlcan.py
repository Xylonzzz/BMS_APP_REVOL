from __future__ import annotations

import ctypes
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.edge_agent.controlcan import (
    VCI_CAN_OBJ,
    VCI_INIT_CONFIG,
    ControlCanError,
    ControlCanSource,
    pe_bitness,
)


class FakeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.callback(*args)


class FakeLibrary:
    def __init__(self, *, init_result=1, start_result=1, open_result=1, receive_result=1):
        self.received = receive_result
        self.VCI_OpenDevice = FakeFunction(lambda *_: open_result)
        self.VCI_InitCAN = FakeFunction(lambda *_: init_result)
        self.VCI_StartCAN = FakeFunction(lambda *_: start_result)
        self.VCI_CloseDevice = FakeFunction(lambda *_: 1)
        self.VCI_Receive = FakeFunction(self.receive)

    def receive(self, _device_type, _device_index, _channel, records, count, _wait):
        if self.received == 1:
            records[0].ID = 0x100
            records[0].TimeStamp = 1234
            records[0].TimeFlag = 1
            records[0].RemoteFlag = 0
            records[0].ExternFlag = 0
            records[0].DataLen = 3
            records[0].Data[0] = 1
            records[0].Data[1] = 2
            records[0].Data[2] = 3
        return self.received


class ControlCanSourceTests(unittest.TestCase):
    def test_abi_struct_sizes_match_controlcan(self) -> None:
        self.assertEqual(24, ctypes.sizeof(VCI_CAN_OBJ))
        self.assertEqual(16, ctypes.sizeof(VCI_INIT_CONFIG))

    def test_open_init_start_receive_maps_canonical_frame(self) -> None:
        library = FakeLibrary()
        source = ControlCanSource("ControlCAN.dll", library=library)
        source.open()
        result = source.receive(limit=1, timeout_ms=100)
        self.assertEqual(1, len(result))
        self.assertEqual({"id": 0x100, "bus": "can1", "dlc": 3,
                          "data": [1, 2, 3], "direction": "rx", "ide": "standard",
                          "rtr": False, "acquisitionSource": "can"},
                         {key: result[0][key] for key in ("id", "bus", "dlc", "data", "direction", "ide", "rtr", "acquisitionSource")})
        self.assertEqual(1234, result[0]["hardwareTimestamp"])
        self.assertEqual(1, result[0]["TimeFlag"])
        self.assertIsInstance(result[0]["timestamp"], float)
        self.assertEqual(1, result[0]["sequence"])
        source.close()

    def test_init_failure_closes_device_and_reports_error(self) -> None:
        library = FakeLibrary(init_result=5)
        source = ControlCanSource("ControlCAN.dll", library=library)
        with self.assertRaisesRegex(ControlCanError, r"VCI_InitCAN.*0x00000005"):
            source.open()
        self.assertEqual(1, len(library.VCI_CloseDevice.calls))

    def test_start_failure_closes_device(self) -> None:
        library = FakeLibrary(start_result=2)
        with self.assertRaises(ControlCanError):
            ControlCanSource("ControlCAN.dll", library=library).open()
        self.assertEqual(1, len(library.VCI_CloseDevice.calls))

    def test_receive_error_code_and_result_boundary_are_explicit(self) -> None:
        library = FakeLibrary(receive_result=0xFFFFFFFF)
        source = ControlCanSource("ControlCAN.dll", library=library)
        source.open()
        with self.assertRaisesRegex(ControlCanError, r"0xFFFFFFFF"):
            source.receive()
        library.received = 2
        with self.assertRaisesRegex(ControlCanError, r"exceeds limit"):
            source.receive(limit=1)

    def test_listen_only_config_uses_500k_vendor_timing(self) -> None:
        library = FakeLibrary(receive_result=0)
        source = ControlCanSource("ControlCAN.dll", bitrate=500000, listen_only=True, library=library)
        source.open()
        config = library.VCI_InitCAN.calls[0][3]._obj
        self.assertEqual((0x00, 0x1C, 1), (config.Timing0, config.Timing1, config.Mode))
        self.assertFalse(hasattr(source, "transmit"))

    def test_open_failure_does_not_close_unowned_device(self) -> None:
        library = FakeLibrary(open_result=0)
        source = ControlCanSource("ControlCAN.dll", library=library)
        with self.assertRaisesRegex(ControlCanError, r"VCI_OpenDevice.*0x00000000"):
            source.open()
        self.assertEqual([], library.VCI_CloseDevice.calls)
        self.assertEqual([], library.VCI_InitCAN.calls)

    def test_close_is_idempotent_and_failure_is_reported(self) -> None:
        library = FakeLibrary()
        source = ControlCanSource("ControlCAN.dll", library=library)
        source.close()
        source.open()
        library.VCI_CloseDevice.callback = lambda *_: 0
        with self.assertRaisesRegex(ControlCanError, r"VCI_CloseDevice"):
            source.close()
        with self.assertRaisesRegex(ControlCanError, "not open"):
            source.receive()
        library.VCI_CloseDevice.callback = lambda *_: 1
        source.close()
        source.close()
        self.assertEqual(2, len(library.VCI_CloseDevice.calls))

    def test_empty_receive_preserves_sequence_and_sdk_wait_time(self) -> None:
        library = FakeLibrary(receive_result=0)
        source = ControlCanSource("ControlCAN.dll", library=library)
        source.open()
        self.assertEqual([], source.receive(limit=3, timeout_ms=44))
        self.assertFalse(source.exhausted)
        library.received = 1
        with patch("services.edge_agent.controlcan.time.time", return_value=123.456):
            result = source.receive(limit=3, timeout_ms=44)
        self.assertEqual((1, 123.456), (result[0]["sequence"], result[0]["timestamp"]))
        self.assertEqual((3, 44), library.VCI_Receive.calls[-1][-2:])
        source.close()

    def test_abi_offsets_and_function_arguments(self) -> None:
        self.assertEqual(8, VCI_CAN_OBJ.TimeFlag.offset)
        self.assertEqual(12, VCI_CAN_OBJ.DataLen.offset)
        self.assertEqual(13, VCI_CAN_OBJ.Data.offset)
        self.assertEqual(21, VCI_CAN_OBJ.Reserved.offset)
        library = FakeLibrary()
        source = ControlCanSource("ControlCAN.dll", bitrate=250000, library=library)
        source.open()
        config = library.VCI_InitCAN.calls[0][3]._obj
        self.assertEqual((0x01, 0x1C), (config.Timing0, config.Timing1))
        self.assertEqual(ctypes.c_int32, library.VCI_Receive.argtypes[-1])
        self.assertEqual(ctypes.c_uint32, library.VCI_Receive.restype)
        source.close()

    def test_constructor_does_not_load_dll(self) -> None:
        with patch("services.edge_agent.controlcan.ctypes.WinDLL", create=True) as loader:
            ControlCanSource("does-not-exist.dll")
        loader.assert_not_called()

    def test_pe_bitness_mismatch_is_reported_before_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ControlCAN.dll"
            content = bytearray(128)
            content[:2] = b"MZ"
            struct.pack_into("<I", content, 0x3C, 64)
            content[64:68] = b"PE\0\0"
            mismatch_machine = 0x14C if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x8664
            struct.pack_into("<H", content, 68, mismatch_machine)
            path.write_bytes(content)
            self.assertEqual(32 if mismatch_machine == 0x14C else 64, pe_bitness(path))
            with patch("services.edge_agent.controlcan.ctypes.WinDLL", create=True) as loader:
                with self.assertRaisesRegex(ControlCanError, "use matching SDK and Python architectures"):
                    ControlCanSource(path).open()
            loader.assert_not_called()

    def test_invalid_sdk_frame_aborts_with_explicit_error(self) -> None:
        library = FakeLibrary()
        def bad_receive(*args):
            library.receive(*args)
            args[3][0].DataLen = 9
            return 1
        library.VCI_Receive.callback = bad_receive
        source = ControlCanSource("ControlCAN.dll", library=library)
        source.open()
        with self.assertRaisesRegex(ControlCanError, "invalid DLC"):
            source.receive()
        source.close()

    def test_invalid_receive_bounds_never_call_sdk(self) -> None:
        library = FakeLibrary()
        source = ControlCanSource("ControlCAN.dll", library=library)
        source.open()
        for values in ({"limit": 0}, {"limit": 10001}, {"limit": True}, {"timeout_ms": -1}, {"timeout_ms": 60001}):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    source.receive(**values)
        self.assertEqual([], library.VCI_Receive.calls)
        source.close()

    def test_failed_close_prevents_double_open_of_owned_device(self) -> None:
        library = FakeLibrary()
        source = ControlCanSource("ControlCAN.dll", library=library)
        source.open()
        library.VCI_CloseDevice.callback = lambda *_: 0
        with self.assertRaises(ControlCanError):
            source.close()
        with self.assertRaisesRegex(ControlCanError, "still open"):
            source.open()
        self.assertEqual(1, len(library.VCI_OpenDevice.calls))
        library.VCI_CloseDevice.callback = lambda *_: 1
        source.close()

    def test_invalid_record_does_not_discard_previous_frames_in_same_sdk_batch(self) -> None:
        library = FakeLibrary()
        def partly_invalid_receive(*args):
            library.receive(*args)
            args[3][1].DataLen = 9
            return 2
        library.VCI_Receive.callback = partly_invalid_receive
        source = ControlCanSource("ControlCAN.dll", library=library)
        source.open()
        first = source.receive(limit=2)
        self.assertEqual([0x100], [item["id"] for item in first])
        self.assertEqual([1], [item["sequence"] for item in first])
        with self.assertRaisesRegex(ControlCanError, "invalid DLC"):
            source.receive(limit=2)
        self.assertEqual(1, len(library.VCI_Receive.calls))
        source.close()


if __name__ == "__main__":
    unittest.main()
