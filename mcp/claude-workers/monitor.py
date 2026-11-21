"""Private, bounded worker telemetry shared by MCP processes and the GUI RPC.

Only public assistant text and tool activity are retained, never thinking blocks,
credentials, or raw provider traffic. Files stay outside the setup repository.
"""
import json
import os
import re
import time
import uuid
from pathlib import Path

ROOT = Path.home() / ".local/share/claude-workers"
JOBS = ROOT / "jobs"


def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False)
        os.replace(str(tmp), str(path))
    finally:
        tmp.unlink(missing_ok=True)


def redact(text):
    text = str(text)
    text = re.sub(r"(?i)(Bearer\s+)[\w.\-]+", r"\1[redacted]", text)
    text = re.sub(r"sk-ant-[\w-]+", "[redacted]", text)
    text = re.sub(r'(?i)(["\']?(?:access_token|refresh_token|api_key|password)["\']?\s*[:=]\s*)["\']?[^\s,}\"\']+', r"\1[redacted]", text)
    return text


def text_content(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if b.get("type") == "text")


class Monitor:
    def __init__(self, job):
        self.job = job
        self.last_write = 0
        self.messages = {}
        self.job.setdefault("events", [])
        self.job.setdefault("context_budget", 256000)
        self.job.setdefault("activity", "Starting Claude Code")
        self.job.setdefault("updated", time.time())
        self.add("assignment", "Task", job["task"])
        self.flush(True)

    def add(self, kind, title, text="", event_id=None, **extra):
        event_id = event_id or uuid.uuid4().hex
        event = next((e for e in self.job["events"] if e["id"] == event_id), None)
        if event is None:
            event = {"id": event_id, "kind": kind, "title": title, "text": "", "time": time.time()}
            self.job["events"].append(event)
        event.update(text=redact(text)[:16000], **extra)
        self.job["events"] = self.job["events"][-160:]
        self.job["updated"] = time.time()
        return event

    def usage(self, usage, message_id):
        if not usage:
            return
        old = self.messages.get(message_id, {})
        old.update(usage)
        self.messages[message_id] = old
        self.job["tokens"] = {
            key: sum(u.get(native, 0) or 0 for u in self.messages.values())
            for key, native in [("input", "input_tokens"), ("cache_read", "cache_read_input_tokens"),
                                ("cache_write", "cache_creation_input_tokens"), ("output", "output_tokens")]
        }
        if "input_tokens" in usage:
            self.job["context_tokens"] = sum(old.get(k, 0) or 0 for k in
                                             ["input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"])
            self.job["context_updated"] = time.time()

    def consume(self, data):
        kind = data.get("type")
        if data.get("session_id"):
            self.job["session_id"] = data["session_id"]
        if kind == "system":
            sub = data.get("subtype")
            if sub == "init":
                self.job.update(model=data.get("model"), cli_version=data.get("claude_code_version"), activity="Preparing request")
            elif sub == "status":
                self.job["activity"] = "Compacting context" if data.get("status") == "compacting" else "Working"
            elif sub == "compact_boundary":
                self.job["compactions"] = self.job.get("compactions", 0) + 1
                self.job["activity"] = "Context compacted"
                # No summary text in the timeline: the user requested quiet compaction.
            elif sub == "api_retry":
                self.job["activity"] = "Retrying request (attempt %s)" % data.get("attempt", "?")
                self.add("notice", "Retry", data.get("error", "Provider temporarily unavailable"))
        elif kind == "stream_event" and not data.get("parent_tool_use_id"):
            e = data.get("event", {})
            mid = data.get("api_message_id") or "current"
            if e.get("type") == "message_start":
                mid = e.get("message", {}).get("id", mid)
                self.job["stream_message"] = mid
                self.usage(e.get("message", {}).get("usage"), mid)
                self.job["activity"] = "Responding"
            elif e.get("type") == "content_block_start":
                block = e.get("content_block", {})
                if block.get("type") == "thinking":
                    self.job["activity"] = "Thinking"
                elif block.get("type") == "tool_use":
                    self.job["activity"] = "Using " + block.get("name", "tool")
            elif e.get("type") == "content_block_delta":
                delta = e.get("delta", {})
                if delta.get("type") == "text_delta":
                    eid = "text:" + (self.job.get("stream_message") or mid) + ":" + str(e.get("index", 0))
                    prev = next((x["text"] for x in self.job["events"] if x["id"] == eid), "")
                    self.add("assistant", "Claude", prev + delta.get("text", ""), eid)
                    self.job["activity"] = "Writing response"
            elif e.get("type") == "message_delta":
                self.usage(e.get("usage"), self.job.get("stream_message") or mid)
        elif kind == "assistant":
            msg = data.get("message", {})
            if not data.get("parent_tool_use_id"):
                self.usage(msg.get("usage"), msg.get("id", "assistant"))
                self.job["model"] = msg.get("model") or self.job.get("model")
            for index, b in enumerate(msg.get("content", [])):
                if b.get("type") == "text":
                    self.add("assistant", "Claude", b.get("text", ""), "text:" + msg.get("id", "assistant") + ":" + str(index))
                elif b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    detail = inp.get("command") or inp.get("file_path") or inp.get("pattern") or json.dumps(inp, ensure_ascii=False)
                    self.add("tool", b.get("name", "Tool"), detail, b.get("id"), status="running")
                    self.job["activity"] = b.get("name", "Tool") + ": " + redact(detail).replace("\n", " ")[:110]
        elif kind == "user":
            for b in (data.get("message") or {}).get("content", []):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    event = next((e for e in self.job["events"] if e["id"] == b.get("tool_use_id")), None)
                    if event:
                        event.update(status="failed" if b.get("is_error") else "done",
                                     output=redact(text_content(b.get("content")))[:12000])
                        self.job["updated"] = time.time()
        elif kind == "rate_limit_event":
            info = data.get("rate_limit_info", {})
            windows = info.get("unifiedWindows") or {}
            if not windows and info.get("rateLimitType"):
                windows = {info["rateLimitType"]: info}
            if windows:
                self.job.setdefault("limits", {}).update(windows)
                self.job["limits_updated"] = time.time()
            if info.get("status") == "rejected":
                self.job["activity"] = "Account limit reached"
        elif kind == "result":
            self.job["result"] = redact(data.get("result") or "\n".join(data.get("errors") or []))[:50000]
            self.job["denied"] = [x.get("tool_name") for x in data.get("permission_denials") or []]
            # Result usage is cumulative across a resumed conversation; live turn totals
            # above are deliberately separate and never overwritten by that total.
            self.job["session_usage"] = data.get("usage", {})
            self.job["is_error"] = data.get("is_error", False)
        self.flush()

    def flush(self, force=False):
        now = time.time()
        if not force and now - self.last_write < 0.35:
            return
        self.last_write = now
        # PID, secrets, and private prompt internals are not exposed in snapshots.
        value = {k: v for k, v in self.job.items() if k not in ("pid", "stream_message")}
        value["task"] = redact(value.get("task", ""))
        private_write(JOBS / (self.job["id"] + ".json"), value)


def snapshots(directory=None):
    result = []
    for path in JOBS.glob("*.json"):
        try:
            job = json.loads(path.read_text())
            if directory and os.path.realpath(job["directory"]) != os.path.realpath(directory):
                continue
            if job.get("status") == "running" and job.get("owner_pid"):
                try:
                    os.kill(job["owner_pid"], 0)
                except ProcessLookupError:
                    job.update(status="interrupted", activity="Bridge stopped; session can be resumed")
            result.append(job)
        except (OSError, ValueError, KeyError):
            continue
    return sorted(result, key=lambda j: j["started"], reverse=True)
