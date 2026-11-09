"""
Local storage for job applications and master-profile history (all git-ignored, under data/).

    data/applications/<id>/application.json   job, profile snapshot, versions, reports, status, notes
    data/applications/<id>/resume.pdf|.docx   the exact files of the saved version
    data/profile_history/<timestamp>.txt      earlier versions of the master profile (last 20)

A saved application never changes the master profile, and saving the profile never
changes an application: each application keeps its own copy of what was used.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from pathlib import Path

STATUSES = ("Draft", "Applied", "Interviewing", "Offer", "Rejected", "Withdrawn")
_ID = re.compile(r"^[A-Za-z0-9-]{6,100}$")
HISTORY_KEEP = 20


class StoreError(Exception):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:30] or "job"


def _dir(base: Path, app_id: str) -> Path:
    if not _ID.match(app_id or ""):  # never let an id walk outside the folder
        raise StoreError(f"Invalid application id: {app_id!r}")
    return Path(base) / "applications" / app_id


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def save_application(base: Path, app: dict, pdf: bytes | None = None, docx: bytes | None = None) -> str:
    """Create or update an application. Returns its id."""
    app = dict(app)
    if app.get("id") and not app.get("created"):  # an update: keep the original creation time
        try:
            app["created"] = load_application(base, app["id"]).get("created", _now())
        except StoreError:
            app["created"] = _now()
    if not app.get("id"):
        stamp = datetime.now().strftime("%Y%m%d")
        app["id"] = f"{stamp}-{_slug(app.get('company', ''))}-{_slug(app.get('job_title', ''))}-{secrets.token_hex(2)}"
        app["created"] = _now()
    app.setdefault("status", "Draft")
    app.setdefault("notes", "")
    app["updated"] = _now()
    folder = _dir(base, app["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "application.json").write_text(json.dumps(app, indent=2, ensure_ascii=False), encoding="utf-8")
    if pdf is not None:
        (folder / "resume.pdf").write_bytes(pdf)
    if docx is not None:
        (folder / "resume.docx").write_bytes(docx)
    return app["id"]


def load_application(base: Path, app_id: str) -> dict:
    try:
        return json.loads((_dir(base, app_id) / "application.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StoreError(f"Could not read application {app_id}: {e}") from None


def update_application(base: Path, app_id: str, **fields) -> dict:
    app = load_application(base, app_id)
    app.update(fields)
    save_application(base, app)
    return app


def delete_application(base: Path, app_id: str) -> None:
    folder = _dir(base, app_id)
    for f in folder.glob("*"):
        f.unlink()
    folder.rmdir()


def list_applications(base: Path) -> list[dict]:
    """Every saved application (newest first), without the heavy fields."""
    out = []
    for f in (Path(base) / "applications").glob("*/application.json"):
        try:
            app = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: app.get(k, "") for k in ("id", "company", "job_title", "status", "applied_date", "created",
                                                 "updated", "ats_score", "match_score", "notes")})
    return sorted(out, key=lambda a: a.get("created", ""), reverse=True)


def file_bytes(base: Path, app_id: str, name: str) -> bytes | None:
    if name not in ("resume.pdf", "resume.docx"):
        raise StoreError(f"Unknown file {name}")
    path = _dir(base, app_id) / name
    return path.read_bytes() if path.is_file() else None


# --------------------------------------------------------------------------- #
# Master profile history
# --------------------------------------------------------------------------- #

def backup_profile(base: Path, text: str) -> None:
    """Keep a copy of the profile before it is replaced (the last HISTORY_KEEP versions)."""
    if not text.strip():
        return
    folder = Path(base) / "profile_history"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.txt").write_text(text, encoding="utf-8")
    for old in sorted(folder.glob("*.txt"))[:-HISTORY_KEEP]:
        old.unlink()


def profile_history(base: Path) -> list[Path]:
    return sorted((Path(base) / "profile_history").glob("*.txt"), reverse=True)


def add_to_profile(text: str, section: str, entry: str) -> str:
    """Append an entry under a section heading of a plain-text profile, creating the section if needed."""
    entry = entry.strip()
    if not entry:
        return text
    lines = text.rstrip("\n").splitlines()
    heading = re.compile(rf"^\s*#*\s*{re.escape(section)}\s*:?\s*$", re.IGNORECASE)
    idx = next((i for i, ln in enumerate(lines) if heading.match(ln)), None)
    if idx is None:
        return "\n".join(lines + ["", section.upper(), entry]) + "\n"
    end = idx + 1  # insert at the end of that section: before the next heading-like line
    while end < len(lines) and not (lines[end].strip() and lines[end].strip().isupper()
                                    and len(lines[end].split()) <= 4):
        end += 1
    while end > idx + 1 and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[:end] + [entry] + lines[end:]) + "\n"
