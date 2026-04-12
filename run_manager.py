#!/usr/bin/env python3
"""
Run/log directory structure management.

Each translation run gets a timestamped directory under runs/:
    runs/
    └── 20260321-143022-sample/
        ├── run.json              # Run metadata
        ├── input_state.json      # Input pipeline result metadata
        ├── translation.txt       # Final translated text
        ├── translation.chunks.json  # Chunk metadata (sidecar)
        ├── review_structural.json   # Structural review results
        ├── review_ai.json           # AI review results (if run)
        ├── problem_chunks.json      # Problem chunk summary
        └── run.log               # Runtime log

The run manager also maintains backward compatibility with the existing
output/ and logs/ directory structure.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional


class RunManager:
    """Manages per-run directory structure and metadata."""

    def __init__(self, base_dir: str = None, run_id: str = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).parent
        self.runs_dir = self.base_dir / "runs"
        self.runs_dir.mkdir(exist_ok=True)

        if run_id:
            self.run_id = run_id
            self.run_dir = self.runs_dir / run_id
        else:
            self.run_id = None
            self.run_dir = None

        self._logger = None

    def create_run(self, input_path: str, label: str = None) -> str:
        """Create a new run directory and return the run ID.

        Args:
            input_path: Path to input file
            label: Optional label (defaults to input filename stem)

        Returns:
            Run ID string (YYYYMMDD-HHMMSS-label)
        """
        now = datetime.now()
        if label is None:
            label = Path(input_path).stem
        # Sanitize label for filesystem
        safe_label = "".join(c if c.isalnum() or c in '-_' else '_' for c in label)
        self.run_id = now.strftime('%Y%m%d-%H%M%S') + '-' + safe_label
        self.run_dir = self.runs_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # Write initial run metadata
        self._write_json('run.json', {
            'run_id': self.run_id,
            'input_path': str(Path(input_path).resolve()),
            'created_at': now.isoformat(timespec='seconds'),
            'status': 'started',
        })

        return self.run_id

    def resume_run(self, run_id: str) -> bool:
        """Resume an existing run.

        Returns True if the run directory exists and is valid.
        """
        self.run_id = run_id
        self.run_dir = self.runs_dir / run_id
        if not self.run_dir.exists():
            return False

        meta_path = self.run_dir / 'run.json'
        return meta_path.exists()

    def update_status(self, status: str, extra: dict = None) -> None:
        """Update run status in run.json."""
        if not self.run_dir:
            return
        meta_path = self.run_dir / 'run.json'
        meta = {}
        if meta_path.exists():
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
        meta['status'] = status
        meta['updated_at'] = datetime.now().isoformat(timespec='seconds')
        if extra:
            meta.update(extra)
        self._write_json('run.json', meta)

    def save_input_state(self, input_state: dict) -> None:
        """Save input pipeline state metadata."""
        self._write_json('input_state.json', input_state)

    def save_structural_review(self, review_data: dict) -> None:
        """Save structural review results."""
        self._write_json('review_structural.json', review_data)

    def save_ai_review(self, review_data: dict) -> None:
        """Save AI review results."""
        self._write_json('review_ai.json', review_data)

    def save_problem_chunks(self, problem_chunks: list) -> None:
        """Save problem chunk summary."""
        self._write_json('problem_chunks.json', {
            'problem_chunks': problem_chunks,
            'count': len(problem_chunks),
            'saved_at': datetime.now().isoformat(timespec='seconds'),
        })

    def get_run_path(self, filename: str) -> Path:
        """Get full path for a file within the run directory."""
        if not self.run_dir:
            raise RuntimeError("No active run. Call create_run() or resume_run() first.")
        return self.run_dir / filename

    def get_logger(self) -> logging.Logger:
        """Get a logger that writes to run.log within the run directory."""
        if self._logger:
            return self._logger
        if not self.run_dir:
            # Return a basic logger if no run is active
            return logging.getLogger('translator')

        self._logger = logging.getLogger(f'translator.{self.run_id}')
        self._logger.setLevel(logging.DEBUG)

        # File handler
        log_path = self.run_dir / 'run.log'
        fh = logging.FileHandler(str(log_path), encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                                datefmt='%H:%M:%S')
        fh.setFormatter(fmt)
        self._logger.addHandler(fh)

        return self._logger

    def list_runs(self, limit: int = 20) -> list:
        """List recent runs.

        Returns list of run metadata dicts, newest first.
        """
        runs = []
        if not self.runs_dir.exists():
            return runs

        for run_dir in sorted(self.runs_dir.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            meta_path = run_dir / 'run.json'
            if meta_path.exists():
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                runs.append(meta)
            if len(runs) >= limit:
                break

        return runs

    # ── Internal helpers ─────────────────────────────────────────

    def _write_json(self, filename: str, data: dict) -> None:
        if not self.run_dir:
            return
        path = self.run_dir / filename
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
