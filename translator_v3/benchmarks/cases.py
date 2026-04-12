from __future__ import annotations

import json
from pathlib import Path
from typing import List

from translator_v3.core.models import BenchmarkCase


def default_manifest_path() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "default_cases.json"


def load_cases(manifest_path: str | Path | None = None) -> List[BenchmarkCase]:
    path = Path(manifest_path) if manifest_path else default_manifest_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    base_dir = path.parent
    return [BenchmarkCase.from_dict(item, base_dir=base_dir) for item in payload["cases"]]
