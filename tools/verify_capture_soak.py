"""无硬件连续采集验收：假输入 → 原帧/聚合 → HTTP → 并发导出 → 退出。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apps.desktop_launcher import LauncherSettings, SessionController
from services.edge_agent.simulator import Simulator


class SyntheticSource:
    def __init__(self):
        self.templates = Simulator().next_snapshot()["frames"]
        self.exhausted = False
        self.sequence = 0
        self.closed = False

    def open(self):
        pass

    def receive(self, limit=100, timeout_ms=100):
        time.sleep(0.05)
        frames = []
        for template in self.templates[:limit]:
            frame = deepcopy(template)
            for key in ("decoded", "qualityMetadataSource", "signalQualities"):
                frame.pop(key, None)
            self.sequence += 1
            frame.update(timestamp=time.time(), sequence=self.sequence, acquisitionSource="can")
            frames.append(frame)
        return frames

    def close(self):
        self.closed = True


def run(seconds):
    report = {"hardware": False, "source": "synthetic injected adapter", "requestedSeconds": seconds}
    source = SyntheticSource()
    with tempfile.TemporaryDirectory(prefix="revol-soak-") as directory:
        controller = SessionController(directory, source_factory=lambda _: source)
        url = controller.start(LauncherSettings(mode="can", dll_path="synthetic-never-loaded"))
        port = controller._server.server_address[1]
        start = time.monotonic()
        polls, exports, latencies = 0, 0, []
        try:
            while time.monotonic() - start < seconds:
                tick = time.monotonic()
                with urlopen(url + "api/v1/snapshots/latest", timeout=10) as response:
                    latest = json.load(response)
                latencies.append(time.monotonic() - tick)
                assert latest["dataSource"] == "can"
                assert all(not value["valid"] for value in latest["cells"])
                polls += 1
                if latest["frames"] and polls % 20 == 0:
                    with urlopen(url + "api/v1/sessions/current/export?kind=full", timeout=15) as response:
                        with ZipFile(io.BytesIO(response.read())) as archive:
                            manifest = json.loads(archive.read("manifest.json"))
                            for item in manifest["files"]:
                                contents = archive.read(item["name"])
                                assert hashlib.sha256(contents).hexdigest() == item["sha256"]
                                assert len(contents) == item["size"]
                    exports += 1
                time.sleep(0.1)
        finally:
            controller.stop()
        session = Path(controller.status()["sessionDirectory"])
        sequences = []
        with (session / "frames.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                sequences.append(json.loads(line)["sequence"])
        assert sequences == list(range(1, source.sequence + 1)), "raw frame loss or sequence reordering"
        with (session / "snapshots.jsonl").open(encoding="utf-8") as stream:
            snapshots = [json.loads(line) for line in stream]
        assert snapshots[-1]["connectionState"] == "disconnected"
        assert snapshots[-1]["packStatus"]["totalVoltageAdc"]["quality"] == "disconnected"
        assert source.closed
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(("127.0.0.1", port)) != 0, "server left listening after exit"
        report.update(success=True, elapsedSeconds=round(time.monotonic() - start, 2),
                      rawFrames=len(sequences), snapshots=len(snapshots), httpPolls=polls,
                      concurrentZipExports=exports, maxLatestLatencyMs=round(max(latencies) * 1000, 1),
                      missingFrames=0, portClosed=True, sourceClosed=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.seconds <= 3600:
        parser.error("--seconds must be in [2, 3600]")
    result = run(args.seconds)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
