#!/usr/bin/env python3
"""
Progress reporting module for OpenClaw integration.

Implements the spec's reporting policy:
- Report at: start, phase transitions, completion, failure
- Periodic summary: max every 15 minutes
- Default: short, practical summaries
- Uses `openclaw system event` for notifications

Also provides a structured ProgressTracker that can be used by
translate.py, retry_engine.py, and the web GUI.

Phase 4 additions:
- Numbered pipeline stages with completion percentage
- Sub-step progress within each stage
- Quality gate status in completion report
"""

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── OpenClaw notification ────────────────────────────────────

def notify_openclaw(text: str, mode: str = "now") -> bool:
    """Send a notification via OpenClaw system event.

    Args:
        text: Message text
        mode: Delivery mode ("now" for immediate)

    Returns:
        True if notification was sent successfully.
    """
    try:
        result = subprocess.run(
            ["openclaw", "system", "event", "--text", text, "--mode", mode],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # openclaw not available or timed out — not a fatal error
        return False


# ── Progress tracker ─────────────────────────────────────────

class ProgressTracker:
    """Tracks translation pipeline progress and emits reports.

    Usage:
        tracker = ProgressTracker(input_path="input.txt", notify=True)
        tracker.start(total_chunks=50)
        tracker.update_chunk(1, 50)
        tracker.phase("review")
        tracker.complete(summary={...})
    """

    # Ordered pipeline stages with human-readable labels and weights
    PIPELINE_STAGES = [
        ("translation",        "Translating",                    40),
        ("parallel_translation","Parallel translating",           40),
        ("structural_review",  "Structural review",              10),
        ("auto_retry",         "Auto-retrying problem chunks",   15),
        ("split_retry",        "Split-retrying large chunks",     5),
        ("ai_review",          "AI semantic review",             10),
        ("quality_gate",       "Quality gate evaluation",         5),
        ("subagent_dispatch",  "Subagent task dispatch",          3),
        ("report_generation",  "Report generation",               5),
        ("finalize",           "Finalize & packaging",            7),
        ("complete",           "Pipeline complete",                0),
    ]

    def __init__(
        self,
        input_path: str = "",
        output_path: str = "",
        notify: bool = False,
        report_interval: int = 900,  # 15 minutes in seconds
        log_path: Optional[str] = None,
    ):
        self.input_path = input_path
        self.output_path = output_path
        self.notify = notify
        self.report_interval = report_interval
        self.log_path = log_path

        self._start_time: Optional[float] = None
        self._last_report_time: float = 0
        self._current_phase: str = ""
        self._current_stage_idx: int = 0
        self._total_chunks: int = 0
        self._completed_chunks: int = 0
        self._problem_chunks: int = 0
        self._retried_chunks: int = 0
        self._events: list = []
        self._active_stages: list = []  # stages actually used in this run
        self._completed_stages: list = []
        self._quality_status: str = ""  # draft / reviewed / deliverable

    def start(self, total_chunks: int, preset_name: str = None):
        """Report pipeline start."""
        self._start_time = time.time()
        self._total_chunks = total_chunks
        self._current_phase = "translation"
        self._current_stage_idx = 0

        input_name = Path(self.input_path).name if self.input_path else "?"

        msg = f"Translation started: {input_name} ({total_chunks} chunks)"
        if preset_name:
            msg += f" [preset: {preset_name}]"

        self._emit("start", msg)

    def phase(self, phase_name: str, detail: str = ""):
        """Report phase transition with stage numbering."""
        # Mark previous phase as completed
        if self._current_phase and self._current_phase != phase_name:
            self._completed_stages.append(self._current_phase)

        self._current_phase = phase_name
        if phase_name not in self._active_stages:
            self._active_stages.append(phase_name)
        self._current_stage_idx = len(self._active_stages)

        stage_labels = {s[0]: s[1] for s in self.PIPELINE_STAGES}
        label = stage_labels.get(phase_name, phase_name)

        # Compute rough overall progress %
        pct = self._overall_progress_pct()

        msg = f"[{self._current_stage_idx}/{self._current_stage_idx + 2}] {label} ({pct:.0f}%)"
        if detail:
            msg += f" — {detail}"
        self._emit("phase", msg)

    def update_chunk(self, current: int, total: int):
        """Update chunk progress. Emits periodic report if interval elapsed."""
        self._completed_chunks = current

        now = time.time()
        if now - self._last_report_time >= self.report_interval:
            self._periodic_report()
            self._last_report_time = now

    def report_problems(self, problem_chunks: list, retried: list = None):
        """Report problem chunk discovery."""
        self._problem_chunks = len(problem_chunks)
        if retried:
            self._retried_chunks = len(retried)

        msg = f"Found {len(problem_chunks)} problem chunks"
        if retried:
            msg += f", retried {len(retried)}"
        self._emit("problems", msg)

    def set_quality_status(self, status: str):
        """Set the quality gate status (draft/reviewed/deliverable)."""
        self._quality_status = status

    def complete(self, summary: dict = None):
        """Report pipeline completion."""
        elapsed = time.time() - self._start_time if self._start_time else 0
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)

        input_name = Path(self.input_path).name if self.input_path else "?"
        msg = f"Done: {input_name} — {self._total_chunks} chunks in {minutes}m{seconds}s"

        if self._problem_chunks:
            msg += f" ({self._problem_chunks} problems"
            if self._retried_chunks:
                msg += f", {self._retried_chunks} retried"
            msg += ")"

        if summary and summary.get('still_problematic'):
            msg += f" | {len(summary['still_problematic'])} need manual review"

        # Show quality status
        quality_status = self._quality_status
        if summary and summary.get('quality_verdict'):
            quality_status = summary['quality_verdict'].get('status', quality_status)
        if quality_status:
            status_labels = {
                "draft": "DRAFT",
                "reviewed": "REVIEWED",
                "deliverable": "DELIVERABLE",
            }
            msg += f" | status: {status_labels.get(quality_status, quality_status)}"

        self._current_phase = "complete"
        self._emit("complete", msg)

    def fail(self, error: str):
        """Report pipeline failure."""
        input_name = Path(self.input_path).name if self.input_path else "?"
        msg = f"FAILED: {input_name} — {error}"
        self._emit("failure", msg)

    def _periodic_report(self):
        """Emit a periodic progress summary."""
        if not self._start_time:
            return

        elapsed = time.time() - self._start_time
        minutes = int(elapsed // 60)

        chunk_pct = (self._completed_chunks / self._total_chunks * 100) if self._total_chunks else 0
        overall_pct = self._overall_progress_pct()
        stage_labels = {s[0]: s[1] for s in self.PIPELINE_STAGES}
        phase_label = stage_labels.get(self._current_phase, self._current_phase)
        msg = (
            f"Progress: {self._completed_chunks}/{self._total_chunks} chunks "
            f"({chunk_pct:.0f}%) — overall {overall_pct:.0f}% — "
            f"{minutes}m elapsed — stage: {phase_label}"
        )
        self._emit("progress", msg, periodic=True)

    def _overall_progress_pct(self) -> float:
        """Estimate overall pipeline progress percentage."""
        stage_weights = {s[0]: s[2] for s in self.PIPELINE_STAGES}
        completed_weight = sum(
            stage_weights.get(s, 5)
            for s in self._completed_stages
        )
        current_weight = stage_weights.get(self._current_phase, 5)

        # Within-stage progress: use chunk ratio for translation phases
        within_pct = 0.0
        if self._current_phase in ("translation", "parallel_translation"):
            within_pct = (self._completed_chunks / self._total_chunks) if self._total_chunks else 0
        else:
            within_pct = 0.5  # assume halfway through non-chunk stages

        total_possible = completed_weight + current_weight + 10  # +10 for remaining stages
        done = completed_weight + (current_weight * within_pct)
        return min(99.0, (done / max(total_possible, 1)) * 100)

    def _emit(self, event_type: str, message: str, periodic: bool = False):
        """Emit an event: print, log, and optionally notify."""
        timestamp = datetime.now().isoformat(timespec='seconds')
        event = {
            'type': event_type,
            'message': message,
            'timestamp': timestamp,
            'phase': self._current_phase,
        }
        self._events.append(event)

        # Always print
        prefix = "[PROGRESS]" if periodic else f"[{event_type.upper()}]"
        print(f"{prefix} {message}")

        # Log to file if configured
        if self.log_path:
            try:
                with open(self.log_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(event, ensure_ascii=False) + '\n')
            except Exception:
                pass

        # Notify OpenClaw for non-periodic events (or periodic summaries)
        if self.notify:
            notify_openclaw(message)

    def get_events(self) -> list:
        """Return all recorded events."""
        return list(self._events)

    def get_status(self) -> dict:
        """Return current status as a dict."""
        elapsed = time.time() - self._start_time if self._start_time else 0
        return {
            'phase': self._current_phase,
            'stage_index': self._current_stage_idx,
            'active_stages': list(self._active_stages),
            'completed_stages': list(self._completed_stages),
            'overall_progress_pct': round(self._overall_progress_pct(), 1),
            'total_chunks': self._total_chunks,
            'completed_chunks': self._completed_chunks,
            'problem_chunks': self._problem_chunks,
            'retried_chunks': self._retried_chunks,
            'elapsed_seconds': int(elapsed),
            'events': len(self._events),
            'quality_status': self._quality_status,
        }
