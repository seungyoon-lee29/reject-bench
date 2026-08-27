#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("codex_handoff_gate.py")
SPEC = importlib.util.spec_from_file_location("codex_handoff_gate", SCRIPT)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class CodexHandoffGateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.snapshots = Path(self.temporary.name) / "snapshots"
        self.transcript = Path(self.temporary.name) / "rollout.jsonl"
        self.root.mkdir()
        (self.root / "HANDOFF.md").write_text("# 핸드오프\n\n이전 상태\n", encoding="utf-8")
        self.old_env = os.environ.copy()
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        os.environ["REJECT_BENCH_CONTINUITY_DIR"] = str(self.snapshots)
        self.payload = {
            "session_id": "codex-session-1",
            "cwd": str(self.root),
            "transcript_path": str(self.transcript),
        }
        gate.start({**self.payload, "source": "startup"})

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        self.temporary.cleanup()

    def write_usage(self, used, window=100_000, cumulative=9_999_999):
        event = {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"total_tokens": used},
                    "total_token_usage": {"total_tokens": cumulative},
                    "model_context_window": window,
                },
            },
        }
        self.transcript.write_text(json.dumps(event) + "\n", encoding="utf-8")

    def call_with_output(self, function, payload):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = function(payload)
        text = output.getvalue().strip()
        return result, json.loads(text) if text else None

    def test_stop_uses_last_request_usage_and_does_nothing_above_buffer(self):
        self.write_usage(59_000)
        result, output = self.call_with_output(gate.stop, self.payload)
        self.assertEqual(result, 0)
        self.assertEqual(output, {})

    def test_stop_forces_handoff_then_marks_changed_file_ready(self):
        self.write_usage(60_000)
        result, output = self.call_with_output(
            gate.stop, {**self.payload, "stop_hook_active": False}
        )
        self.assertEqual(result, 0)
        self.assertEqual(output["decision"], "block")
        self.assertIn("HANDOFF.md", output["reason"])

        (self.root / "HANDOFF.md").write_text("# 핸드오프\n\n현재 상태\n", encoding="utf-8")
        _, output = self.call_with_output(
            gate.stop, {**self.payload, "stop_hook_active": True}
        )
        self.assertEqual(output, {})
        state = gate.read_state(gate.state_path(self.payload, self.root))
        self.assertEqual(state["status"], "ready")

    def test_precompact_blocks_until_handoff_changed(self):
        self.write_usage(72_000)
        _, output = self.call_with_output(
            gate.precompact, {**self.payload, "trigger": "auto"}
        )
        self.assertFalse(output["continue"])
        snapshot = gate.continuity.snapshot_path(self.payload, self.root)
        self.assertFalse(snapshot.exists())

        (self.root / "HANDOFF.md").write_text("# 핸드오프\n\ncompact 직전 상태\n", encoding="utf-8")
        _, output = self.call_with_output(
            gate.precompact, {**self.payload, "trigger": "auto"}
        )
        self.assertEqual(output, {})
        self.assertTrue(snapshot.exists())
        self.assertIn("compact 직전 상태", snapshot.read_text(encoding="utf-8"))

    def test_prompt_injects_handoff_instruction_before_work(self):
        self.write_usage(65_000)
        _, output = self.call_with_output(gate.prompt, self.payload)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("다른 작업을 진행하지 말고", context)
        self.assertIn("HANDOFF.md", context)

    def test_precompact_rejects_a_stale_snapshot_when_capture_fails(self):
        self.write_usage(72_000)
        self.call_with_output(gate.stop, self.payload)
        (self.root / "HANDOFF.md").write_text("# 핸드오프\n\n새 상태\n", encoding="utf-8")
        self.call_with_output(gate.stop, {**self.payload, "stop_hook_active": True})

        snapshot = gate.continuity.snapshot_path(self.payload, self.root)
        snapshot.write_text("이전 주기의 낡은 스냅샷\n", encoding="utf-8")
        with mock.patch.object(gate.continuity, "capture", return_value=0):
            _, output = self.call_with_output(
                gate.precompact, {**self.payload, "trigger": "auto"}
            )

        self.assertFalse(output["continue"])
        self.assertEqual(output["stopReason"], "HANDOFF snapshot failed")

    def test_reset_requires_a_fresh_handoff_for_the_next_compaction(self):
        self.write_usage(70_000)
        self.call_with_output(gate.stop, self.payload)
        (self.root / "HANDOFF.md").write_text("# 핸드오프\n\n첫 갱신\n", encoding="utf-8")
        self.call_with_output(gate.stop, {**self.payload, "stop_hook_active": True})
        gate.reset(self.payload)

        _, output = self.call_with_output(
            gate.precompact, {**self.payload, "trigger": "auto"}
        )
        self.assertFalse(output["continue"])


if __name__ == "__main__":
    unittest.main()
