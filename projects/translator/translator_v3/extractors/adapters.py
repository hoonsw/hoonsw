from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass
from typing import List, Optional

from translator_v3.core.models import ExtractorCapability


@dataclass
class AdapterSpec:
    name: str
    kind: str
    module_name: Optional[str] = None
    cli_name: Optional[str] = None


_ADAPTER_SPECS = [
    AdapterSpec(name="ocrmypdf", kind="preprocessor", module_name="ocrmypdf", cli_name="ocrmypdf"),
    AdapterSpec(name="marker", kind="extractor", module_name="marker", cli_name="marker"),
    AdapterSpec(name="docling", kind="extractor", module_name="docling", cli_name="docling"),
]


def _find_module_path(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    spec = importlib.util.find_spec(name)
    if spec is None:
        return None
    return spec.origin


def probe_adapter(spec: AdapterSpec) -> ExtractorCapability:
    module_path = _find_module_path(spec.module_name)
    cli_path = shutil.which(spec.cli_name) if spec.cli_name else None
    available = bool(module_path or cli_path)
    detail = "module_or_cli_available" if available else "not_installed"
    return ExtractorCapability(
        name=spec.name,
        kind=spec.kind,
        available=available,
        module_path=module_path,
        cli_path=cli_path,
        detail=detail,
    )


def available_capabilities() -> List[ExtractorCapability]:
    return [probe_adapter(spec) for spec in _ADAPTER_SPECS]
