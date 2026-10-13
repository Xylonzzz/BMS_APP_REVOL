from __future__ import annotations

import ctypes
import struct
import time
from pathlib import Path
from typing import Any

from services.edge_agent.adapters import validate_receive_options


class VCI_CAN_OBJ(ctypes.Structure):
    """ControlCAN 厂商 ABI；固定宽度字段，默认 4 字节对齐。"""

    _fields_ = [
        ("ID", ctypes.c_uint32),
        ("TimeStamp", ctypes.c_uint32),
        ("TimeFlag", ctypes.c_uint8),
        ("SendType", ctypes.c_uint8),
        ("RemoteFlag", ctypes.c_uint8),
        ("ExternFlag", ctypes.c_uint8),
        ("DataLen", ctypes.c_uint8),
        ("Data", ctypes.c_uint8 * 8),
        ("Reserved", ctypes.c_uint8 * 3),
    ]


class VCI_INIT_CONFIG(ctypes.Structure):
    _fields_ = [
        ("AccCode", ctypes.c_uint32),
        ("AccMask", ctypes.c_uint32),
        ("Reserved", ctypes.c_uint32),
        ("Filter", ctypes.c_uint8),
        ("Timing0", ctypes.c_uint8),
        ("Timing1", ctypes.c_uint8),
        ("Mode", ctypes.c_uint8),
    ]


class ControlCanError(RuntimeError):
    pass


def pe_bitness(path: str | Path) -> int:
    """读取 PE 头，不加载 DLL 或调用设备。"""
    with Path(path).open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ControlCanError(f"Not a Windows PE DLL: {path}")
        offset = struct.unpack_from("<I", header, 0x3C)[0]
        stream.seek(offset)
        pe_header = stream.read(6)
    if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
        raise ControlCanError(f"Invalid Windows PE header: {path}")
    machine = struct.unpack_from("<H", pe_header, 4)[0]
    if machine == 0x14C:
        return 32
    if machine == 0x8664:
        return 64
    raise ControlCanError(f"Unsupported DLL machine 0x{machine:04X}: {path}; use an x86 or x64 SDK")


class ControlCanSource:
    """只读 ControlCAN 输入；构造时不加载 SDK，默认只听且不发送 ACK。"""

    TIMINGS = {500000: (0x00, 0x1C), 250000: (0x01, 0x1C)}

    def __init__(self, dll_path: str | Path, device_type: int = 4, device_index: int = 0,
                 channel: int = 0, bitrate: int = 500000, listen_only: bool = True,
                 library: Any = None) -> None:
        self.dll_path = Path(dll_path)
        for name, value in (("device_type", device_type), ("device_index", device_index), ("channel", channel)):
            if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"{name} must be a uint32 integer")
        if type(bitrate) is not int or bitrate not in self.TIMINGS:
            raise ValueError("bitrate must be 500000 or 250000")
        if type(listen_only) is not bool:
            raise ValueError("listen_only must be boolean")
        self.device_type = device_type
        self.device_index = device_index
        self.channel = channel
        self.bitrate = bitrate
        self.listen_only = listen_only
        self.exhausted = False
        self._library = library
        self._opened = False
        self._started = False
        self._sequence = 0
        self._receive_error: ControlCanError | None = None

    def _load_library(self) -> Any:
        path = self.dll_path.resolve()
        if not path.is_file():
            raise ControlCanError(f"ControlCAN DLL not found: {path}; configure the vendor SDK DLL path")
        bits = pe_bitness(path)
        process_bits = ctypes.sizeof(ctypes.c_void_p) * 8
        if bits != process_bits:
            raise ControlCanError(f"ControlCAN DLL is {bits}-bit but Python is {process_bits}-bit; "
                                  f"use matching SDK and Python architectures: {path}")
        if not hasattr(ctypes, "WinDLL"):
            raise ControlCanError("ControlCAN requires Windows and the vendor driver")
        try:
            # WinDLL 使用 WINAPI/stdcall；绝对路径限制 DLL 搜索范围。
            return ctypes.WinDLL(str(path))
        except OSError as error:
            raise ControlCanError(f"Cannot load ControlCAN DLL {path}: {error}; "
                                  "check SDK dependencies, vendor driver and DLL/Python bitness") from error

    def _bind(self) -> None:
        u32 = ctypes.c_uint32
        signatures = {
            "VCI_OpenDevice": [u32, u32, u32],
            "VCI_CloseDevice": [u32, u32],
            "VCI_InitCAN": [u32, u32, u32, ctypes.POINTER(VCI_INIT_CONFIG)],
            "VCI_StartCAN": [u32, u32, u32],
            "VCI_Receive": [u32, u32, u32, ctypes.POINTER(VCI_CAN_OBJ), u32, ctypes.c_int32],
        }
        for name, args in signatures.items():
            function = getattr(self._library, name, None)
            if not callable(function):
                raise ControlCanError(f"ControlCAN SDK is missing {name}")
            try:
                function.argtypes = args
                function.restype = u32
            except AttributeError:
                # 注入的 Python 测试 SDK 可能使用绑定方法，不存在 ctypes 属性。
                if isinstance(function, ctypes._CFuncPtr):
                    raise

    @staticmethod
    def _success(operation: str, result: int) -> None:
        if result != 1:
            raise ControlCanError(f"{operation} failed: 0x{result & 0xFFFFFFFF:08X}")

    def open(self) -> None:
        if self._started:
            return
        if self._opened:
            raise ControlCanError("ControlCAN device is still open after a close failure; retry close() first")
        if self._library is None:
            self._library = self._load_library()
        self._bind()
        self._success("VCI_OpenDevice", self._library.VCI_OpenDevice(self.device_type, self.device_index, 0))
        self._opened = True
        try:
            timing0, timing1 = self.TIMINGS[self.bitrate]
            config = VCI_INIT_CONFIG(0, 0xFFFFFFFF, 0, 1, timing0, timing1, int(self.listen_only))
            self._success("VCI_InitCAN", self._library.VCI_InitCAN(
                self.device_type, self.device_index, self.channel, ctypes.byref(config)))
            self._success("VCI_StartCAN", self._library.VCI_StartCAN(
                self.device_type, self.device_index, self.channel))
        except BaseException as error:
            try:
                self.close()
            except BaseException as close_error:
                error.add_note(f"Cleanup also failed: {close_error}")
            raise
        self._started = True
        self._receive_error = None

    def receive(self, limit: int = 1000, timeout_ms: int = 100) -> list[dict[str, Any]]:
        validate_receive_options(limit, timeout_ms)
        if not self._started:
            raise ControlCanError("ControlCanSource is not open")
        if self._receive_error is not None:
            raise self._receive_error
        buffer = (VCI_CAN_OBJ * limit)()
        count = self._library.VCI_Receive(self.device_type, self.device_index, self.channel,
                                         buffer, limit, timeout_ms)
        if count == 0xFFFFFFFF or count < 0:
            raise ControlCanError(f"VCI_Receive failed: 0x{count & 0xFFFFFFFF:08X}")
        if count > limit:
            raise ControlCanError(f"VCI_Receive returned {count} frames, which exceeds limit {limit}")
        timestamp = time.time()
        frames = []
        for record in buffer[:count]:
            if record.DataLen > 8 or record.ExternFlag > 1 or record.RemoteFlag > 1 or record.TimeFlag > 1:
                self._receive_error = ControlCanError("VCI_Receive returned invalid DLC or frame flags")
            elif record.ID > (0x1FFFFFFF if record.ExternFlag else 0x7FF):
                self._receive_error = ControlCanError("VCI_Receive returned an out-of-range CAN ID")
            if self._receive_error is not None:
                # 坏结构不能解码；先交付本批前序原始帧，再由接收循环处理明确错误。
                if frames:
                    break
                raise self._receive_error
            self._sequence += 1
            frames.append({"id": record.ID, "bus": "can1",
                           "data": [] if record.RemoteFlag else list(record.Data[:record.DataLen]),
                           "dlc": record.DataLen, "timestamp": timestamp, "direction": "rx",
                           "sequence": self._sequence, "ide": "extended" if record.ExternFlag else "standard",
                           "rtr": bool(record.RemoteFlag), "acquisitionSource": "can",
                           "hardwareTimestamp": record.TimeStamp, "TimeFlag": record.TimeFlag})
        return frames

    def close(self) -> None:
        if not self._opened:
            return
        self._started = False
        self._success("VCI_CloseDevice", self._library.VCI_CloseDevice(self.device_type, self.device_index))
        self._opened = False
