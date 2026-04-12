#!/usr/bin/env python3
"""
Glossary approval workflow scaffold.

Implements the spec's glossary approval pipeline:
1. Auto-extract term candidates from source text (extract_glossary.py)
2. Present candidates for user approval/modification
3. Apply approved terms to main glossary
4. Track approval status per term

Phase 2 scope: CLI-based approval workflow + structured data model.
Full GUI integration is phase 3.

Glossary entry schema (extended from phase 1):
{
    "term": "原文の用語",
    "translation": "번역어",
    "category": "person|place|work|organization|concept",
    "mode": "translate|transliterate|keep",
    "annotate": true,
    "aliases": ["variant1", "variant2"],
    "first_mention_note": "optional note for first occurrence",
    "note": "translator note",
    "status": "pending|approved|rejected|modified",
    "approved_at": "ISO timestamp or null"
}
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


# ── Status constants ─────────────────────────────────────────

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_MODIFIED = "modified"

VALID_MODES = ("translate", "transliterate", "keep")
VALID_CATEGORIES = ("person", "place", "work", "organization", "concept")
VALID_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_MODIFIED)


# ── Glossary entry normalization ─────────────────────────────

def normalize_entry(term: str, value) -> dict:
    """Normalize a glossary entry to the extended schema.

    Handles both legacy simple string values and full dict entries.
    """
    if isinstance(value, str):
        return {
            "translation": value,
            "category": "",
            "mode": "translate",
            "annotate": False,
            "aliases": [],
            "first_mention_note": "",
            "note": "",
            "status": STATUS_APPROVED,  # Legacy entries are implicitly approved
            "approved_at": None,
        }

    if isinstance(value, dict):
        return {
            "translation": value.get("translation", ""),
            "category": value.get("category", ""),
            "mode": value.get("mode", "translate"),
            "annotate": value.get("annotate", False),
            "aliases": value.get("aliases", []),
            "first_mention_note": value.get("first_mention_note", ""),
            "note": value.get("note", ""),
            "status": value.get("status", STATUS_APPROVED),
            "approved_at": value.get("approved_at"),
        }

    return {
        "translation": str(value),
        "category": "",
        "mode": "translate",
        "annotate": False,
        "aliases": [],
        "first_mention_note": "",
        "note": "",
        "status": STATUS_PENDING,
        "approved_at": None,
    }


def minimize_entry(entry: dict) -> dict:
    """Minimize a glossary entry for storage — strip empty optional fields.

    Keeps the entry compatible with phase 1 glossary.json consumers.
    """
    result = {"translation": entry.get("translation", "")}

    if entry.get("category"):
        result["category"] = entry["category"]
    if entry.get("mode") and entry["mode"] != "translate":
        result["mode"] = entry["mode"]
    if entry.get("annotate"):
        result["annotate"] = True
    if entry.get("aliases"):
        result["aliases"] = entry["aliases"]
    if entry.get("first_mention_note"):
        result["first_mention_note"] = entry["first_mention_note"]
    if entry.get("note"):
        result["note"] = entry["note"]
    if entry.get("status") and entry["status"] != STATUS_APPROVED:
        result["status"] = entry["status"]
    if entry.get("approved_at"):
        result["approved_at"] = entry["approved_at"]

    return result


# ── Workflow operations ──────────────────────────────────────

class GlossaryWorkflow:
    """Manages the glossary approval workflow.

    Operates on a workflow file (glossary_workflow.json) that tracks
    approval state, and syncs approved entries to the main glossary.json.
    """

    def __init__(
        self,
        workflow_path: str = "glossary_workflow.json",
        glossary_path: str = "glossary.json",
    ):
        self.workflow_path = Path(workflow_path)
        self.glossary_path = Path(glossary_path)
        self.entries: Dict[str, dict] = {}
        self._load()

    def _load(self):
        """Load workflow state from file."""
        if self.workflow_path.exists():
            with open(self.workflow_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            self.entries = {
                term: normalize_entry(term, value)
                for term, value in raw.items()
            }
        else:
            self.entries = {}

    def _save(self):
        """Save workflow state to file."""
        output = {
            term: minimize_entry(entry)
            for term, entry in self.entries.items()
        }
        with open(self.workflow_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    def import_draft(self, draft_path: str = "glossary_draft.json") -> int:
        """Import candidates from extract_glossary draft into workflow.

        Returns number of new terms added.
        """
        draft_file = Path(draft_path)
        if not draft_file.exists():
            raise FileNotFoundError(f"Draft file not found: {draft_path}")

        with open(draft_file, 'r', encoding='utf-8') as f:
            draft = json.load(f)

        added = 0
        for term, value in draft.items():
            if term not in self.entries:
                entry = normalize_entry(term, value)
                entry['status'] = STATUS_PENDING
                entry['approved_at'] = None
                self.entries[term] = entry
                added += 1

        self._save()
        return added

    def approve(self, term: str, translation: str = None) -> bool:
        """Approve a pending term, optionally updating its translation."""
        if term not in self.entries:
            return False

        entry = self.entries[term]
        if translation:
            entry['translation'] = translation
            entry['status'] = STATUS_MODIFIED
        else:
            entry['status'] = STATUS_APPROVED
        entry['approved_at'] = datetime.now().isoformat(timespec='seconds')
        self._save()
        return True

    def reject(self, term: str) -> bool:
        """Reject a pending term."""
        if term not in self.entries:
            return False
        self.entries[term]['status'] = STATUS_REJECTED
        self._save()
        return True

    def approve_all_pending(self) -> int:
        """Approve all pending terms. Returns count approved."""
        count = 0
        now = datetime.now().isoformat(timespec='seconds')
        for entry in self.entries.values():
            if entry['status'] == STATUS_PENDING:
                entry['status'] = STATUS_APPROVED
                entry['approved_at'] = now
                count += 1
        self._save()
        return count

    def sync_to_glossary(self) -> int:
        """Sync approved/modified entries to the main glossary.json.

        Returns number of entries synced.
        """
        glossary = {}
        if self.glossary_path.exists():
            with open(self.glossary_path, 'r', encoding='utf-8') as f:
                glossary = json.load(f)

        synced = 0
        for term, entry in self.entries.items():
            if entry['status'] not in (STATUS_APPROVED, STATUS_MODIFIED):
                continue

            # Write to glossary in phase-1-compatible format
            glossary_entry = minimize_entry(entry)
            # Remove workflow-only fields from glossary
            glossary_entry.pop('status', None)
            glossary_entry.pop('approved_at', None)

            glossary[term] = glossary_entry
            synced += 1

        with open(self.glossary_path, 'w', encoding='utf-8') as f:
            json.dump(glossary, f, ensure_ascii=False, indent=2)

        return synced

    def get_pending(self) -> Dict[str, dict]:
        """Get all pending terms."""
        return {
            term: entry
            for term, entry in self.entries.items()
            if entry['status'] == STATUS_PENDING
        }

    def get_approved(self) -> Dict[str, dict]:
        """Get all approved/modified terms."""
        return {
            term: entry
            for term, entry in self.entries.items()
            if entry['status'] in (STATUS_APPROVED, STATUS_MODIFIED)
        }

    def summary(self) -> dict:
        """Return summary statistics."""
        counts = {s: 0 for s in VALID_STATUSES}
        for entry in self.entries.values():
            status = entry.get('status', STATUS_PENDING)
            if status in counts:
                counts[status] += 1
        return {
            'total': len(self.entries),
            **counts,
        }


# ── CLI interface ────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Glossary approval workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Import draft from extraction
  python3 glossary_workflow.py import glossary_draft.json

  # List pending terms
  python3 glossary_workflow.py pending

  # Approve a term (with optional translation override)
  python3 glossary_workflow.py approve "東浩紀" --translation "아즈마 히로키"

  # Approve all pending
  python3 glossary_workflow.py approve-all

  # Sync approved to glossary.json
  python3 glossary_workflow.py sync

  # Show summary
  python3 glossary_workflow.py status
""",
    )

    parser.add_argument("action", choices=[
        "import", "pending", "approve", "reject",
        "approve-all", "sync", "status",
    ])
    parser.add_argument("term_or_file", nargs="?", default=None,
                        help="Term name (for approve/reject) or file path (for import)")
    parser.add_argument("--translation", type=str, default=None,
                        help="Override translation when approving")
    parser.add_argument("--workflow", default="glossary_workflow.json",
                        help="Workflow file path")
    parser.add_argument("--glossary", default="glossary.json",
                        help="Main glossary file path")

    args = parser.parse_args()
    wf = GlossaryWorkflow(args.workflow, args.glossary)

    if args.action == "import":
        if not args.term_or_file:
            parser.error("import requires a draft file path")
        added = wf.import_draft(args.term_or_file)
        print(f"Imported {added} new terms into workflow")

    elif args.action == "pending":
        pending = wf.get_pending()
        if not pending:
            print("No pending terms")
        else:
            print(f"Pending terms ({len(pending)}):")
            for term, entry in pending.items():
                print(f"  {term} -> {entry['translation']} [{entry['category']}]")

    elif args.action == "approve":
        if not args.term_or_file:
            parser.error("approve requires a term name")
        ok = wf.approve(args.term_or_file, args.translation)
        if ok:
            print(f"Approved: {args.term_or_file}")
        else:
            print(f"Term not found: {args.term_or_file}")

    elif args.action == "reject":
        if not args.term_or_file:
            parser.error("reject requires a term name")
        ok = wf.reject(args.term_or_file)
        if ok:
            print(f"Rejected: {args.term_or_file}")
        else:
            print(f"Term not found: {args.term_or_file}")

    elif args.action == "approve-all":
        count = wf.approve_all_pending()
        print(f"Approved {count} pending terms")

    elif args.action == "sync":
        count = wf.sync_to_glossary()
        print(f"Synced {count} approved terms to {args.glossary}")

    elif args.action == "status":
        s = wf.summary()
        print(f"Glossary workflow status:")
        print(f"  Total: {s['total']}")
        print(f"  Pending: {s['pending']}")
        print(f"  Approved: {s['approved']}")
        print(f"  Modified: {s['modified']}")
        print(f"  Rejected: {s['rejected']}")


if __name__ == "__main__":
    main()
