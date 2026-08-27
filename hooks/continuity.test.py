#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("continuity.py")
SPEC = importlib.util.spec_from_file_location("continuity", SCRIPT)
continuity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(continuity)


class ContinuityTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.snapshots = Path(self.temporary.name) / "snapshots"
        self.root.mkdir()
        (self.root / "HANDOFF.md").write_text("# 핸드오프\n\n다음은 테스트 실행.\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Continuity Test"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "HANDOFF.md"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)
        self.old_env = os.environ.copy()
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)
        os.environ["REJECT_BENCH_CONTINUITY_DIR"] = str(self.snapshots)
        self.payload = {"session_id": "session-1", "trigger": "auto", "cwd": str(self.root)}

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        self.temporary.cleanup()

    def test_capture_and_compact_load_round_trip_without_transcript_copy(self):
        transcript = self.root / "transcript.jsonl"
        secret = "must-not-be-copied-from-transcript"
        transcript.write_text(json.dumps({"prompt": secret}), encoding="utf-8")
        self.payload["transcript_path"] = str(transcript)

        self.assertEqual(continuity.capture(self.payload), 0)
        target = continuity.snapshot_path(self.payload, self.root)
        snapshot = target.read_text(encoding="utf-8")

        self.assertIn("trigger: auto", snapshot)
        self.assertIn("다음은 테스트 실행.", snapshot)
        self.assertNotIn(secret, snapshot)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            continuity.load({**self.payload, "source": "compact"})
        self.assertIn("compact 직후 자동 핸드오프 재주입", output.getvalue())
        self.assertIn("다음은 테스트 실행.", output.getvalue())

    def test_non_compact_session_loads_current_handoff(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            continuity.load({**self.payload, "source": "startup"})
        self.assertIn("HANDOFF.md 자동 주입", output.getvalue())
        self.assertIn("다음은 테스트 실행.", output.getvalue())

    def test_codex_payload_uses_cwd_without_claude_environment(self):
        os.environ.pop("CLAUDE_PROJECT_DIR")

        self.assertEqual(continuity.capture(self.payload), 0)
        target = continuity.snapshot_path(self.payload, self.root)
        self.assertTrue(target.exists())

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            continuity.load({**self.payload, "source": "compact"})
        self.assertIn("compact 직후 자동 핸드오프 재주입", output.getvalue())
        self.assertIn("다음은 테스트 실행.", output.getvalue())

    def test_cleanup_removes_only_current_session_snapshot(self):
        continuity.capture(self.payload)
        other = {**self.payload, "session_id": "session-2"}
        continuity.capture(other)
        continuity.cleanup(self.payload)
        self.assertFalse(continuity.snapshot_path(self.payload, self.root).exists())
        self.assertTrue(continuity.snapshot_path(other, self.root).exists())


if __name__ == "__main__":
    unittest.main()
