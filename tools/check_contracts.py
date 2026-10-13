"""验证当前模拟输出及历史记录的 JSON Schema，检查 OpenAPI 本地引用。"""

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.api_server.server import decorate_snapshot
from services.edge_agent.replay_scenarios import SCENARIO_NAMES, build_scenario
from services.edge_agent.simulator import Simulator
from packages.bms_protocol.aggregator import SnapshotAggregator


def main() -> None:
    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8-sig"))
        for path in (ROOT / "schemas").glob("*.json")
    }
    registry = Registry()
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    snapshot_validator = Draft202012Validator(schemas["signal-tree.json"], registry=registry)
    replay_validator = Draft202012Validator(schemas["replay-response.json"], registry=registry)
    snapshot_validator.validate(decorate_snapshot(Simulator().next_snapshot()))
    aggregate = SnapshotAggregator()
    for sequence, frame in enumerate(Simulator().next_snapshot()["frames"], 1):
        aggregate.ingest(dict(frame, sequence=sequence))
    snapshot_validator.validate(decorate_snapshot(aggregate.snapshot()))
    snapshot_validator.validate(decorate_snapshot(aggregate.snapshot(connected=False)))
    snapshot_validator.validate(decorate_snapshot(SnapshotAggregator(data_source="file").snapshot()))
    records = 0
    for name in SCENARIO_NAMES:
        items = build_scenario(name)
        for item in items:
            item["snapshot"] = decorate_snapshot(item["snapshot"])
            item["frames"] = item["snapshot"]["frames"]
        replay_validator.validate({"name": name, "items": items})
        records += len(items)
    historic = 0
    for line in (ROOT / "runtime/demo/snapshots.jsonl").read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            snapshot_validator.validate(decorate_snapshot(json.loads(line)))
            historic += 1
    api = yaml.safe_load((ROOT / "schemas/openapi.yaml").read_text(encoding="utf-8"))
    assert api["openapi"] == "3.1.0"

    def check_refs(value):
        if isinstance(value, dict):
            ref = value.get("$ref")
            if ref and ref.startswith("./"):
                assert (ROOT / "schemas" / ref[2:].split("#")[0]).is_file(), ref
            elif ref and ref.startswith("#/"):
                target = api
                for key in ref[2:].split("/"):
                    target = target[key]
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)

    check_refs(api)
    print(f"Schema OK: {len(schemas)} schemas, {records} replay records, 1 simulator + 3 acquisition snapshots, {historic} historical snapshots; OpenAPI YAML/references OK")


if __name__ == "__main__":
    main()
