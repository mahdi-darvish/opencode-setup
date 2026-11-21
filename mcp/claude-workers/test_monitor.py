import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jobs = Path(self.tmp.name) / "jobs"
        self.patch = patch.object(monitor, "JOBS", self.jobs)
        self.patch.start()
        self.job = {"id": "12345678", "worker": "developer", "task": "Check sample.py", "directory": "/sample", "status": "running", "started": 1}
        self.m = monitor.Monitor(self.job)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_deduplicates_partial_and_final_usage(self):
        self.m.consume({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1", "usage": {"input_tokens": 2, "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 100}}}})
        self.m.consume({"type": "stream_event", "event": {"type": "message_delta", "usage": {"output_tokens": 20}}})
        self.m.consume({"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 2, "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 100, "output_tokens": 20}, "content": []}})
        self.assertEqual(self.job["tokens"], {"input": 2, "cache_read": 9000, "cache_write": 100, "output": 20})
        self.assertEqual(self.job["context_tokens"], 9102)
        self.m.consume({"type": "result", "usage": {"input_tokens": 100000}, "result": "OK"})
        self.assertEqual(self.job["tokens"]["input"], 2)

    def test_stream_text_and_tools_no_reasoning(self):
        self.m.consume({"type": "assistant", "message": {"id": "m1", "content": [{"type": "thinking", "thinking": "SECRET THOUGHT"}, {"type": "text", "text": "Checking"}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "python3 test.py"}}]}})
        self.m.consume({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "passed"}]}})
        self.assertNotIn("SECRET THOUGHT", json.dumps(self.job))
        self.assertEqual(self.job["events"][-1]["output"], "passed")
        self.assertEqual(self.job["events"][-1]["status"], "done")

    def test_limits_and_private_files(self):
        self.m.consume({"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {"five_hour": {"utilization": .35}, "seven_day": {"utilization": .2}}}})
        self.m.flush(True)
        self.assertEqual(self.job["limits"]["five_hour"]["utilization"], .35)
        self.assertEqual((self.jobs / "12345678.json").stat().st_mode & 0o777, 0o600)

    def test_bound_history_and_redact(self):
        for i in range(200):
            self.m.add("assistant", "Claude", "Bearer secret123 password=abc")
        self.assertEqual(len(self.job["events"]), 160)
        self.assertNotIn("secret123", json.dumps(self.job))
        self.assertNotIn("password=abc", json.dumps(self.job))

    def test_compaction_does_not_expose_summary(self):
        self.m.consume({"type": "system", "subtype": "compact_boundary", "summary": "Do not show"})
        self.assertEqual(self.job["compactions"], 1)
        self.assertNotIn("Do not show", json.dumps(self.job))

    def test_dead_owner_is_not_shown_as_working(self):
        self.job["owner_pid"] = 99999999
        self.m.flush(True)
        self.assertEqual(monitor.snapshots("/sample")[0]["status"], "interrupted")

    def test_text_stream_reconciles_without_duplicates(self):
        self.m.consume({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}})
        for text in ["Hello", " world"]:
            self.m.consume({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}})
        self.m.consume({"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "Hello world"}]}})
        texts = [e for e in self.job["events"] if e["kind"] == "assistant"]
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0]["text"], "Hello world")


if __name__ == "__main__":
    unittest.main()
