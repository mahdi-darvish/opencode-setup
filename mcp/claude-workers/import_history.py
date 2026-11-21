"""Import display-safe messages from the two already-saved worker conversations.

Never copies api-request records, hidden reasoning, credentials, or raw prompts
assembled by the provider. Existing monitor records are not overwritten.
"""
import hashlib
import json
from pathlib import Path
from monitor import ROOT, JOBS, Monitor, snapshots


def import_history():
    try:
        sessions = json.loads((ROOT / "sessions.json").read_text())
    except (OSError, ValueError):
        return 0
    known = {j.get("session_id") for j in snapshots()}
    imported = 0
    for key, sid in sessions.items():
        if sid in known:
            continue
        role, directory = key.split(":", 1)
        profile = Path.home() / (".claude-dev" if role == "developer" else ".claude-verify")
        paths = list((profile / "projects").glob("*/" + sid + ".jsonl"))
        if not paths:
            continue
        job = {"id": hashlib.sha256(sid.encode()).hexdigest()[:8], "worker": role,
               "directory": directory, "task": "Previously saved Claude Code conversation",
               "started": paths[0].stat().st_mtime, "status": "done", "session_id": sid,
               "resumed": True, "activity": "Saved conversation", "historical": True}
        monitor = Monitor(job)
        with paths[0].open() as source:
            for line in source:
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                kind = data.get("type")
                if kind not in ("assistant", "user") or data.get("isSidechain") or data.get("isCompactSummary"):
                    continue
                msg = data.get("message") or {}
                content = msg.get("content")
                if kind == "user" and isinstance(content, str):
                    # Show the actual assignments, not serialized system/environment records.
                    if not content.startswith("<"):
                        monitor.add("assignment", "Task", content)
                elif isinstance(content, list):
                    monitor.consume({"type": kind, "message": msg})
        job.update(finished=paths[0].stat().st_mtime, updated=paths[0].stat().st_mtime, activity="Saved conversation")
        monitor.flush(True)
        imported += 1
    return imported


if __name__ == "__main__":
    print("Imported %s saved worker conversations" % import_history())
