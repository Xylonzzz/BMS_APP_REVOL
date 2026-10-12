from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SessionStore:
    """使用 JSONL 保存不可变快照，并提供最新记录查询。"""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "snapshots.jsonl"

    def append(self, snapshot: dict[str, Any]) -> str:
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")
        return str(snapshot["id"])

    def list_snapshots(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]

    def latest(self) -> dict[str, Any] | None:
        items = self.list_snapshots()
        return items[-1] if items else None
