#!/usr/bin/env python3
"""Read-only IMAP MCP server over stdio. Uses only the Python standard library.

Mailboxes are opened with EXAMINE and messages are fetched with BODY.PEEK, so
nothing on the server changes: no deleting, moving, sending, or marking as read.

Settings come from a private JSON file (default ~/.config/opencode/email.json,
override with IMAP_CONFIG), kept out of Git:
  {"host": "...", "port": 993, "user": "...", "password_file": "~/..."}
"""
import base64
import email
import email.policy
import html
import imaplib
import json
import os
import re
import sys
from collections import Counter
from email.utils import getaddresses, parsedate_to_datetime

SERVER_NAME = "imap-email"
SERVER_VERSION = "1.0.0"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SUMMARY_HEADERS = "FROM TO CC DATE SUBJECT MESSAGE-ID IN-REPLY-TO"


def log(*args):
    print(*args, file=sys.stderr, flush=True)


# ---------- IMAP helpers ----------

def connect():
    path = os.path.expanduser(os.environ.get("IMAP_CONFIG", "~/.config/opencode/email.json"))
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    host = config["host"]
    port = int(config.get("port", 993))
    user = config["user"]
    with open(os.path.expanduser(config["password_file"]), encoding="utf-8") as f:
        password = f.read().strip()
    conn = imaplib.IMAP4_SSL(host, port, timeout=60)
    conn.login(user, password)
    return conn


def utf7_decode(name):
    def repl(m):
        chunk = m.group(1)
        if not chunk:
            return "&"
        b = chunk.replace(",", "/")
        b += "=" * (-len(b) % 4)
        return base64.b64decode(b).decode("utf-16-be")
    return re.sub(r"&([^-]*)-", repl, name)


def utf7_encode(name):
    out, buf = [], []

    def flush():
        if buf:
            b = base64.b64encode("".join(buf).encode("utf-16-be")).decode("ascii")
            out.append("&" + b.rstrip("=").replace("/", ",") + "-")
            buf.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def open_folder(conn, folder):
    typ, data = conn.select(quote(utf7_encode(folder)), readonly=True)
    if typ != "OK":
        raise ValueError("Cannot open folder %r: %s" % (folder, data))


def imap_date(value):
    y, m, d = (int(x) for x in value.split("-"))
    return "%02d-%s-%04d" % (d, MONTHS[m - 1], y)


def search_uids(conn, args):
    """Return matching UIDs, newest first."""
    criteria, literal = [], None
    text_fields = [("from", "FROM"), ("to", "TO"), ("cc", "CC"), ("subject", "SUBJECT"),
                   ("body", "BODY"), ("text", "TEXT")]
    for key, imap_key in text_fields:
        value = args.get(key)
        if not value:
            continue
        if value.isascii():
            criteria += [imap_key, quote(value)]
        elif literal is None:
            literal = (imap_key, value.encode("utf-8"))
        else:
            raise ValueError("Only one search field may contain non-ASCII characters (umlauts etc.).")
    if args.get("since"):
        criteria += ["SINCE", imap_date(args["since"])]
    if args.get("before"):
        criteria += ["BEFORE", imap_date(args["before"])]
    if args.get("unread_only"):
        criteria.append("UNSEEN")
    if args.get("unanswered_only"):
        criteria.append("UNANSWERED")

    if literal:
        criteria.append(literal[0])
        conn.literal = literal[1]
        typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", *criteria)
    else:
        typ, data = conn.uid("SEARCH", *(criteria or ["ALL"]))
    if typ != "OK":
        raise ValueError("Search failed: %s" % data)
    uids = [int(x) for x in (data[0] or b"").split()]
    uids.sort(reverse=True)
    return uids


def header_str(msg, name):
    try:
        value = msg[name]
        return str(value).strip() if value is not None else ""
    except Exception:
        return ""


def parse_date(value):
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return None


def fetch_summaries(conn, uids, headers=SUMMARY_HEADERS):
    if not uids:
        return []
    query = "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (%s)])" % headers
    typ, data = conn.uid("FETCH", ",".join(str(u) for u in uids), query)
    if typ != "OK":
        raise ValueError("Fetch failed: %s" % data)
    results = {}
    for item in data:
        if not isinstance(item, tuple):
            continue
        meta, raw = item
        uid_m = re.search(rb"UID (\d+)", meta)
        if not uid_m:
            continue
        size_m = re.search(rb"RFC822\.SIZE (\d+)", meta)
        flags_m = re.search(rb"FLAGS \(([^)]*)\)", meta)
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        flags = flags_m.group(1).decode(errors="replace").split() if flags_m else []
        results[int(uid_m.group(1))] = {
            "uid": int(uid_m.group(1)),
            "date": header_str(msg, "Date"),
            "from": header_str(msg, "From"),
            "to": header_str(msg, "To"),
            "cc": header_str(msg, "Cc"),
            "subject": header_str(msg, "Subject"),
            "message_id": header_str(msg, "Message-ID"),
            "in_reply_to": header_str(msg, "In-Reply-To"),
            "seen": "\\Seen" in flags,
            "answered": "\\Answered" in flags,
            "size_kb": round(int(size_m.group(1)) / 1024, 1) if size_m else None,
        }
    return [results[u] for u in uids if u in results]


def html_to_text(value):
    value = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", "", value)
    value = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6])>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    return re.sub(r"\n\s*\n+", "\n\n", value).strip()


def part_text(part):
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ---------- Tools ----------

def tool_list_folders(conn, args):
    typ, data = conn.list()
    if typ != "OK":
        raise ValueError("LIST failed: %s" % data)
    folders = []
    for item in data:
        if isinstance(item, tuple):
            line, name = item[0].decode(errors="replace"), item[1].decode(errors="replace")
        else:
            line = (item or b"").decode(errors="replace")
            m = re.match(r'\((?P<flags>[^)]*)\) (?P<delim>"[^"]*"|NIL) (?P<name>.+)$', line)
            if not m:
                continue
            name = m.group("name")
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if "\\Noselect" in line:
            continue
        folders.append(utf7_decode(name))
    result = []
    for folder in folders:
        try:
            typ, st = conn.status(quote(utf7_encode(folder)), "(MESSAGES UNSEEN)")
            s = st[0].decode(errors="replace")
            total = re.search(r"MESSAGES (\d+)", s)
            unseen = re.search(r"UNSEEN (\d+)", s)
            result.append({"folder": folder,
                           "messages": int(total.group(1)) if total else None,
                           "unread": int(unseen.group(1)) if unseen else None})
        except Exception:
            result.append({"folder": folder})
    return result


def tool_search_emails(conn, args):
    folder = args.get("folder") or "INBOX"
    limit = max(1, min(int(args.get("limit") or 25), 200))
    open_folder(conn, folder)
    uids = search_uids(conn, args)
    return {"folder": folder, "total_matches": len(uids), "returned": min(limit, len(uids)),
            "emails": fetch_summaries(conn, uids[:limit])}


def tool_read_email(conn, args):
    folder = args.get("folder") or "INBOX"
    max_chars = max(500, min(int(args.get("max_chars") or 20000), 200000))
    open_folder(conn, folder)
    typ, data = conn.uid("FETCH", str(int(args["uid"])), "(FLAGS BODY.PEEK[])")
    raw = next((item[1] for item in data if isinstance(item, tuple)), None)
    if typ != "OK" or raw is None:
        raise ValueError("No email with UID %s in %s" % (args["uid"], folder))
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        body = part_text(body_part)
        if body_part.get_content_type() == "text/html":
            body = html_to_text(body)
    truncated = len(body) > max_chars

    attachments = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True) or b""
        attachments.append({"filename": part.get_filename() or "(unnamed)",
                            "type": part.get_content_type(),
                            "size_kb": round(len(payload) / 1024, 1)})

    return {
        "uid": int(args["uid"]),
        "folder": folder,
        "date": header_str(msg, "Date"),
        "from": header_str(msg, "From"),
        "to": header_str(msg, "To"),
        "cc": header_str(msg, "Cc"),
        "reply_to": header_str(msg, "Reply-To"),
        "subject": header_str(msg, "Subject"),
        "message_id": header_str(msg, "Message-ID"),
        "in_reply_to": header_str(msg, "In-Reply-To"),
        "attachments": attachments,
        "body": body[:max_chars],
        "body_truncated": truncated,
    }


def tool_mailbox_stats(conn, args):
    folder = args.get("folder") or "INBOX"
    top = max(1, min(int(args.get("top") or 20), 100))
    max_messages = 20000
    open_folder(conn, folder)
    uids = search_uids(conn, args)
    scanned = uids[:max_messages]

    senders, domains, months, weekdays, hours = Counter(), Counter(), Counter(), Counter(), Counter()
    names = {}
    unread = unanswered = 0
    for batch in chunks(scanned, 500):
        for e in fetch_summaries(conn, batch, headers="FROM DATE"):
            for name, addr in getaddresses([e["from"]]):
                addr = addr.lower()
                if addr:
                    senders[addr] += 1
                    domains[addr.split("@")[-1]] += 1
                    if name:
                        names[addr] = name
            dt = parse_date(e["date"])
            if dt:
                months[dt.strftime("%Y-%m")] += 1
                weekdays[dt.strftime("%A")] += 1
                hours[dt.strftime("%H")] += 1
            unread += not e["seen"]
            unanswered += not e["answered"]

    return {
        "folder": folder,
        "total_matches": len(uids),
        "scanned": len(scanned),
        "unread": unread,
        "not_answered": unanswered,
        "top_senders": [{"address": a, "name": names.get(a, ""), "count": c}
                        for a, c in senders.most_common(top)],
        "top_domains": [{"domain": d, "count": c} for d, c in domains.most_common(top)],
        "per_month": dict(sorted(months.items())),
        "per_weekday": dict(weekdays.most_common()),
        "per_hour": dict(sorted(hours.items())),
    }


SEARCH_PROPS = {
    "folder": {"type": "string", "description": "Folder name from list_folders. Default: INBOX."},
    "from": {"type": "string", "description": "Text in the From header (name or address)."},
    "to": {"type": "string", "description": "Text in the To header."},
    "cc": {"type": "string", "description": "Text in the Cc header."},
    "subject": {"type": "string", "description": "Text in the subject."},
    "body": {"type": "string", "description": "Text in the message body."},
    "text": {"type": "string", "description": "Text anywhere in headers or body."},
    "since": {"type": "string", "description": "On or after this date, YYYY-MM-DD."},
    "before": {"type": "string", "description": "Before this date, YYYY-MM-DD."},
    "unread_only": {"type": "boolean", "description": "Only unread emails."},
    "unanswered_only": {"type": "boolean", "description": "Only emails not marked as answered."},
}

TOOLS = {
    "list_folders": {
        "handler": tool_list_folders,
        "description": "List all mail folders with message and unread counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "search_emails": {
        "handler": tool_search_emails,
        "description": "Search one folder and return email summaries (uid, date, from, to, subject, "
                       "seen/answered flags), newest first. Use read_email with the uid for the full text.",
        "inputSchema": {"type": "object", "properties": dict(SEARCH_PROPS, limit={
            "type": "integer", "description": "Max results to return, 1-200. Default 25."})},
    },
    "read_email": {
        "handler": tool_read_email,
        "description": "Read one email: headers, plain-text body, and attachment list. Does not mark it as read.",
        "inputSchema": {"type": "object", "required": ["uid"], "properties": {
            "uid": {"type": "integer", "description": "UID from search_emails."},
            "folder": {"type": "string", "description": "Folder the UID belongs to. Default: INBOX."},
            "max_chars": {"type": "integer", "description": "Max body characters. Default 20000."},
        }},
    },
    "mailbox_stats": {
        "handler": tool_mailbox_stats,
        "description": "Pattern overview for a folder (optionally filtered like search_emails): top senders, "
                       "top domains, emails per month, weekday and hour, unread and unanswered counts. "
                       "Reads headers only, up to 20000 newest matches.",
        "inputSchema": {"type": "object", "properties": dict(SEARCH_PROPS, top={
            "type": "integer", "description": "How many top senders/domains to list. Default 20."})},
    },
}


# ---------- MCP protocol (JSON-RPC over stdio) ----------

def call_tool(name, args):
    tool = TOOLS.get(name)
    if not tool:
        return {"content": [{"type": "text", "text": "Unknown tool: %s" % name}], "isError": True}
    conn = None
    try:
        conn = connect()
        result = tool["handler"](conn, args or {})
        text = json.dumps(result, ensure_ascii=False, indent=1, default=str)
        return {"content": [{"type": "text", "text": text}]}
    except Exception as e:
        log("tool %s failed: %r" % (name, e))
        return {"content": [{"type": "text", "text": "Error: %s" % e}], "isError": True}
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass


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


def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


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
        try:
            send({"jsonrpc": "2.0", "id": request["id"], "result": handle(request)})
        except LookupError:
            send({"jsonrpc": "2.0", "id": request["id"],
                  "error": {"code": -32601, "message": "Method not found: %s" % request.get("method")}})
        except Exception as e:
            send({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
