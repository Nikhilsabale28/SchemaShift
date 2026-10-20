"""JSON file store for pipeline artifacts (inspection, proposal, schema map, reports)."""
from __future__ import annotations
import json
from pathlib import Path

class Store:
    def __init__(self, base_dir: Path):
        self.base = Path(base_dir)
        self.artifacts = self.base / "artifacts"
        self.reports = self.base / "reports"
        self.generated = self.base / "migrations" / "generated"
        for d in (self.artifacts, self.reports, self.generated):
            d.mkdir(parents=True, exist_ok=True)

    inspection = property(lambda s: s.artifacts / "inspection_report.json")
    proposal = property(lambda s: s.artifacts / "schema_proposal.json")
    schema_map = property(lambda s: s.artifacts / "schema_map.json")
    similarity_json = property(lambda s: s.reports / "similarity_report.json")
    similarity_md = property(lambda s: s.reports / "similarity_report.md")
    migration_report = property(lambda s: s.reports / "migration_report.json")
    verification_report = property(lambda s: s.reports / "verification_report.json")
    final_report = property(lambda s: s.reports / "final_report.md")

    @staticmethod
    def write_json(path: Path, data) -> None:
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def read_json(path: Path):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"{path.name} not found - run the earlier pipeline step first")
        return json.loads(path.read_text(encoding="utf-8"))

    def exists(self, path: Path) -> bool:
        return Path(path).is_file()
