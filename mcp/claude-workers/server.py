#!/usr/bin/env python3
"""MCP server that lets the OpenCode coordinator hand work to two Claude Code workers.

Each worker is the official `claude` CLI logged in to its own account through its
own CLAUDE_CONFIG_DIR, so Claude Code handles its own tools, caching and compaction
and talks to Anthropic directly. The server keeps one continuing conversation per
worker and project, and resumes it on every call instead of starting a new one.

Workers (role → config dir):
  developer → ~/.claude-dev     (writes code)
  verifier  → ~/.claude-verify  (reviews and tests; edits only when explicitly asked)

Session IDs are stored in ~/.local/share/claude-workers/sessions.json.
Uses only the Python standard library.
"""
import json
import os
import pwd
import shutil
import subprocess
import sys
import threading
import time
import uuid
import fcntl
from monitor import Monitor, JOBS, snapshots, private_write, redact

SERVER_NAME = "claude-workers"
SERVER_VERSION = "1.0.0"
HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".local/share/claude-workers")
SESSIONS_FILE = os.path.join(STATE_DIR, "sessions.json")
CLAUDE = shutil.which("claude") or "/opt/homebrew/bin/claude"
CONTEXT_WINDOW = os.environ.get("CLAUDE_WORKERS_CONTEXT", "256k")

WORKERS = {
    "developer": {
        "config": os.path.join(HOME, ".claude-dev"),
        "model": "opus",
        "prompt": (
            "You are the developer worker. A coordinator agent sends you tasks from the user. "
            "Do exactly the task given, then stop. Do not start follow-up tasks. "
            "End with a short report: what you changed (files), what you ran, and anything left open."
        ),
    },
    "verifier": {
        "config": os.path.join(HOME, ".claude-verify"),
        "model": "sonnet",
        "prompt": (
            "You are the verifier worker. A coordinator agent sends you work to check. "
            "Review the changes, run relevant tests or checks, and report findings in severity order "
            "with file and line references. Do not edit files unless the task explicitly says to. "
            "End with a clear verdict: OK, or what must be fixed."
        ),
    },
}

lock = threading.Lock()
jobs = {}  # job id -> dict


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def load_sessions():
    try:
        with open(SESSIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_sessions(data):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = SESSIONS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, SESSIONS_FILE)


def remember_session(key, session):
    # There can be one MCP process per OpenCode project. Do not lose another
    # project's saved session if two workers finish at the same time.
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    with open(os.path.join(STATE_DIR, "sessions.lock"), "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        sessions = load_sessions()
        sessions[key] = session
        private_write(SESSIONS_FILE, sessions)


def session_key(worker, directory):
    return "%s:%s" % (worker, os.path.realpath(directory))


def run_claude(job):
    worker = WORKERS[job["worker"]]
    key = session_key(job["worker"], job["directory"])
    with lock:
        session = None if job["new_session"] else load_sessions().get(key)
    monitor = Monitor(job)
    job["resumed"] = bool(session)
    cmd = [CLAUDE, "-p", job["task"], "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--model", job["model"] or worker["model"],
           "--permission-mode", "auto", "--permission-prompts", "none",
           "--append-system-prompt", worker["prompt"]]
    if job["effort"]:
        cmd += ["--effort", job["effort"]]
    if session:
        cmd += ["--resume", session]
    env = dict(os.environ, CLAUDE_CONFIG_DIR=worker["config"],
               CLAUDE_CODE_AUTO_COMPACT_WINDOW=CONTEXT_WINDOW)
    # Claude Code looks up its keychain login by USER; without it the worker reports "Not logged in".
    env.setdefault("USER", pwd.getpwuid(os.getuid()).pw_name)
    # These profiles must use their official Claude login, never inherited proxy credentials.
    for name in list(env):
        if name.startswith("ANTHROPIC_") or name in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
            env.pop(name, None)
    try:
        proc = subprocess.Popen(cmd, cwd=job["directory"], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        job["pid"] = proc.pid
        errors = []
        def read_errors():
            for line in proc.stderr:
                errors.append(line)
                del errors[:-30]
        def controls():
            control = JOBS / (job["id"] + ".cancel")
            while proc.poll() is None:
                if control.exists() or job.get("cancelled"):
                    job["cancelled"] = True
                    control.unlink(missing_ok=True)
                    try:
                        proc.send_signal(2)
                    except ProcessLookupError:
                        pass
                    return
                # Heartbeat distinguishes a quiet model request from a disconnected bridge.
                time.sleep(1)
        threading.Thread(target=read_errors, daemon=True).start()
        threading.Thread(target=controls, daemon=True).start()
        for line in proc.stdout:
            try:
                monitor.consume(json.loads(line))
            except ValueError:
                continue
        proc.wait()
        if job.get("session_id"):
            with lock:
                remember_session(key, job["session_id"])
        job.update(
            status="cancelled" if job.get("cancelled") else ("done" if proc.returncode == 0 and not job.get("is_error") else "failed"),
            result=job.get("result") or "".join(errors)[-2000:] or "Claude Code exited without a report",
        )
        job["activity"] = "Permission denied — see report" if job.get("denied") else {
            "done": "Finished", "failed": "Failed — see report", "cancelled": "Stopped"}[job["status"]]
    except Exception as e:  # noqa: BLE001
        job.update(status="failed", result="Error: %s" % e)
        job["activity"] = "Failed — see report"
    job["result"] = redact(job.get("result", ""))
    job["finished"] = time.time()
    job["updated"] = time.time()
    monitor.flush(True)


accounts = {}


def account(worker):
    """Email of the Claude account the worker is logged in to, cached per run."""
    if worker not in accounts:
        try:
            env = dict(os.environ, CLAUDE_CONFIG_DIR=WORKERS[worker]["config"])
            env.setdefault("USER", pwd.getpwuid(os.getuid()).pw_name)
            out = subprocess.run([CLAUDE, "auth", "status"], env=env, capture_output=True, text=True, timeout=30).stdout
            accounts[worker] = json.loads(out).get("email") or "not logged in"
        except Exception:  # noqa: BLE001
            return "unknown"
    return accounts[worker]


def public(job):
    keys = ["id", "worker", "account", "directory", "status", "activity", "result", "session_id", "resumed", "denied", "tokens", "model", "context_tokens", "context_budget", "limits"]
    out = {k: job.get(k) for k in keys if job.get(k) not in (None, [], "")}
    end = job.get("finished") or time.time()
    out["seconds"] = round(end - job["started"])
    return out


# ---------- tools ----------

def tool_assign(args):
    worker = args.get("worker")
    if worker not in WORKERS:
        raise ValueError("worker must be 'developer' or 'verifier'")
    directory = os.path.expanduser(args.get("directory") or "")
    if not os.path.isdir(directory):
        raise ValueError("directory does not exist: %s" % directory)
    if not (args.get("task") or "").strip():
        raise ValueError("task is empty")
    busy = [j for j in snapshots(directory) if j["status"] == "running" and j["worker"] == worker]
    if busy:
        raise ValueError("%s is still working on job %s in this project; wait for it or cancel it" % (worker, busy[0]["id"]))
    job = {"id": uuid.uuid4().hex[:8], "worker": worker, "account": account(worker),
           "directory": directory, "task": args["task"],
           "model": args.get("model"), "effort": args.get("effort"), "new_session": bool(args.get("new_session")),
            "status": "running", "started": time.time(), "owner_pid": os.getpid()}
    jobs[job["id"]] = job
    thread = threading.Thread(target=run_claude, args=(job,), daemon=True)
    thread.start()
    thread.join(timeout=min(max(int(args.get("wait_seconds", 600)), 0), 3000))
    return public(job)


def tool_status(args):
    job = jobs.get(args.get("job_id") or "") or next((j for j in snapshots() if j["id"] == args.get("job_id")), None)
    if not job:
        raise ValueError("unknown job id")
    wait = min(max(int(args.get("wait_seconds", 0)), 0), 3000)
    deadline = time.time() + wait
    while job["status"] == "running" and time.time() < deadline:
        time.sleep(2)
        if job["id"] not in jobs:
            job = next((j for j in snapshots() if j["id"] == job["id"]), job)
    return public(job)


def tool_list(_args):
    sessions = load_sessions()
    return {"jobs": [public(j) | {"result": (j.get("result") or "")[:200]} for j in snapshots()[:40]],
            "sessions": sessions}


def tool_cancel(args):
    job = jobs.get(args.get("job_id") or "") or next((j for j in snapshots() if j["id"] == args.get("job_id")), None)
    if not job or job["status"] != "running":
        raise ValueError("no running job with that id")
    job["cancelled"] = True
    private_write(JOBS / (job["id"] + ".cancel"), {"requested": time.time()})
    if job.get("pid"):
        os.kill(job["pid"], 2)  # SIGINT lets Claude Code end the turn cleanly
    return {"id": job["id"], "status": "cancelling"}


def tool_reset(args):
    worker = args.get("worker")
    directory = os.path.expanduser(args.get("directory") or "")
    with lock:
        sessions = load_sessions()
        removed = sessions.pop(session_key(worker, directory), None)
        save_sessions(sessions)
    return {"removed": removed is not None}


DIRECTORY = {"type": "string", "description": "Absolute path of the project folder the worker should work in."}
TOOLS = {
    "assign": {
        "handler": tool_assign,
        "description": (
            "Send a task to a Claude Code worker and wait for its report. The worker continues its own saved "
            "conversation for this project, so send only the new task and what changed, not the whole history. "
            "'developer' writes code (Opus by default); 'verifier' reviews and tests (Sonnet by default) and edits "
            "only when the task explicitly asks. If the job is still running after wait_seconds, it keeps running "
            "in the background: check it with 'status'."
        ),
        "inputSchema": {"type": "object", "properties": {
            "worker": {"type": "string", "enum": ["developer", "verifier"]},
            "directory": DIRECTORY,
            "task": {"type": "string", "description": "What to do, with acceptance criteria."},
            "model": {"type": "string", "description": "Optional override: 'opus', 'sonnet' or 'haiku'."},
            "effort": {"type": "string", "enum": ["low", "medium", "high"], "description": "Optional thinking effort."},
            "new_session": {"type": "boolean", "description": "Start a fresh conversation instead of continuing. Default false."},
            "wait_seconds": {"type": "integer", "description": "How long to wait for the result. Default 600."},
        }, "required": ["worker", "directory", "task"]},
    },
    "status": {
        "handler": tool_status,
        "description": "Get the status or final report of a job, optionally waiting up to wait_seconds for it to finish.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "string"}, "wait_seconds": {"type": "integer"}}, "required": ["job_id"]},
    },
    "list": {
        "handler": tool_list,
        "description": "List jobs from this OpenCode run and the saved worker conversations per project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "cancel": {
        "handler": tool_cancel,
        "description": "Stop a running job.",
        "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    },
    "reset": {
        "handler": tool_reset,
        "description": "Forget a worker's saved conversation for a project, so the next task starts fresh. Only when the user asks.",
        "inputSchema": {"type": "object", "properties": {
            "worker": {"type": "string", "enum": ["developer", "verifier"]}, "directory": DIRECTORY},
            "required": ["worker", "directory"]},
    },
}


# ---------- MCP protocol (JSON-RPC over stdio) ----------

out_lock = threading.Lock()


def send(message):
    with out_lock:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def call_tool(name, args):
    tool = TOOLS.get(name)
    if not tool:
        return {"content": [{"type": "text", "text": "Unknown tool: %s" % name}], "isError": True}
    try:
        result = tool["handler"](args or {})
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}]}
    except Exception as e:  # noqa: BLE001
        log("tool %s failed: %r" % (name, e))
        return {"content": [{"type": "text", "text": "Error: %s" % e}], "isError": True}


def handle(request):
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
                          for n, t in TOOLS.items()]}
    if method == "tools/call":
        return call_tool(params.get("name"), params.get("arguments"))
    raise LookupError(method)


def respond(request):
    try:
        send({"jsonrpc": "2.0", "id": request["id"], "result": handle(request)})
    except LookupError:
        send({"jsonrpc": "2.0", "id": request["id"],
              "error": {"code": -32601, "message": "Method not found: %s" % request.get("method")}})
    except Exception as e:  # noqa: BLE001
        send({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32603, "message": str(e)}})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if "id" not in request:
            continue  # notification
        # Tool calls can take minutes; answer them on their own thread so status/cancel stay responsive.
        threading.Thread(target=respond, args=(request,), daemon=True).start()


if __name__ == "__main__":
    main()
