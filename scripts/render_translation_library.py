#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
from pathlib import Path


BASE_DIR = Path("/home/hoonsw/dev/translator")
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
RUNS_DIR = BASE_DIR / "runs"
WORKSPACE_LIBRARY = Path("/home/hoonsw/.openclaw/workspace/TRANSLATION-LIBRARY.md")


def _rel_name(path: Path) -> str:
    return path.name


def _list_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        [item for item in directory.iterdir() if item.is_file()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def _format_source_section(files: list[Path]) -> list[str]:
    lines = ["## Source Files", ""]
    if not files:
        lines.append("- 없음")
        lines.append("")
        return lines
    for item in files[:20]:
        stem = item.stem
        ts = datetime.fromtimestamp(item.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"- `{item.name}`")
        lines.append(f"  hint: `{stem} 번역해줘`")
        lines.append(f"  updated: `{ts}`")
    lines.append("")
    return lines


def _format_output_section(files: list[Path]) -> list[str]:
    lines = ["## Recent Translated Files", ""]
    if not files:
        lines.append("- 없음")
        lines.append("")
        return lines
    visible = [
        item for item in files
        if item.suffix.lower() in {".txt", ".docx", ".md"}
        and ".v2." not in item.name
        and not item.name.endswith(".chunks.json")
        and not item.name.endswith(".report.json")
        and not item.name.endswith(".report.txt")
        and not item.name.endswith(".report.html")
    ]
    for item in visible[:20]:
        ts = datetime.fromtimestamp(item.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"- `{item.name}`")
        lines.append(f"  updated: `{ts}`")
    lines.append("")
    return lines


def _format_runs_section(entries: list[Path]) -> list[str]:
    lines = ["## Recent Jobs", ""]
    if not entries:
        lines.append("- 없음")
        lines.append("")
        return lines
    for item in entries[:15]:
        ts = datetime.fromtimestamp(item.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"- `{item.name}`")
        lines.append(f"  updated: `{ts}`")
    lines.append("")
    return lines


def main() -> int:
    source_files = _list_files(INPUT_DIR)
    output_files = _list_files(OUTPUT_DIR)
    run_dirs = sorted(
        [item for item in RUNS_DIR.iterdir() if item.is_dir()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ) if RUNS_DIR.exists() else []

    lines = [
        "# Translation Library",
        "",
        "번역할 파일, 최근 결과, 최근 실행을 한눈에 보는 참고 파일입니다.",
        "최신 상태가 중요하면 실제 `source/`, `translated/`, `jobs/`를 직접 확인하세요.",
        "",
        "빠른 사용 예시:",
        "",
        "- `번역할 파일 뭐 있어?`",
        "- `reco 번역해줘`",
        "- `source/reco.txt 번역해줘`",
        "- `translated 폴더 보여줘`",
        "- `jobs 최근 실행 보여줘`",
        "",
    ]
    lines.extend(_format_source_section(source_files))
    lines.extend(_format_output_section(output_files))
    lines.extend(_format_runs_section(run_dirs))
    WORKSPACE_LIBRARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
