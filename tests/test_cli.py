"""GuardSpec 등록 CLI (T2).

`python -m rejectbench.cli` — 작성(validate)·등록(register)·조회(list/show).
테스트는 임시 디렉터리 store만 쓴다 (운영 `data/` 비접촉은 conftest가 강제).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rejectbench.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]

DRAFT = {
    "guard_id": "guard-cli",
    "project": "reject-bench",
    "purpose": "시험 가드",
    "policy": "위험 명령을 차단한다",
    "exceptions": [],
    "allow_examples": ["git status"],
    "block_examples": ["git push --force"],
}


@pytest.fixture()
def store_dir(tmp_path) -> Path:
    return tmp_path / "store"


def write_draft(tmp_path, **overrides) -> Path:
    payload = dict(DRAFT)
    payload.update(overrides)
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TestRegisterShowRoundTrip:
    def test_register_then_show(self, tmp_path, store_dir, capsys):
        draft = write_draft(tmp_path)
        assert main(["register", "--store", str(store_dir), "--file", str(draft)]) == 0
        registered = json.loads(capsys.readouterr().out)
        assert registered["guard_id"] == "guard-cli"
        assert registered["version"] == 1
        assert registered["created"] is True
        assert registered["content_hash"].startswith("sha256:")

        assert main(["show", "--store", str(store_dir), "--guard", "guard-cli"]) == 0
        shown = json.loads(capsys.readouterr().out)
        assert shown["record_type"] == "guard_spec"
        assert shown["content_hash"] == registered["content_hash"]
        assert shown["policy"] == DRAFT["policy"]

    def test_show_specific_version(self, tmp_path, store_dir, capsys):
        main(["register", "--store", str(store_dir), "--file", str(write_draft(tmp_path))])
        main(
            [
                "register",
                "--store",
                str(store_dir),
                "--file",
                str(write_draft(tmp_path, policy="개정 정책")),
            ]
        )
        capsys.readouterr()
        assert (
            main(["show", "--store", str(store_dir), "--guard", "guard-cli", "--version", "1"])
            == 0
        )
        assert json.loads(capsys.readouterr().out)["policy"] == DRAFT["policy"]

    def test_list_shows_guard_and_latest_version(self, tmp_path, store_dir, capsys):
        main(["register", "--store", str(store_dir), "--file", str(write_draft(tmp_path))])
        main(
            [
                "register",
                "--store",
                str(store_dir),
                "--file",
                str(write_draft(tmp_path, policy="개정 정책")),
            ]
        )
        capsys.readouterr()
        assert main(["list", "--store", str(store_dir)]) == 0
        listed = json.loads(capsys.readouterr().out)
        assert listed == [{"guard_id": "guard-cli", "latest_version": 2, "versions": [1, 2]}]


class TestVersioningThroughCli:
    def test_same_content_reregister_is_idempotent(self, tmp_path, store_dir, capsys):
        draft = write_draft(tmp_path)
        main(["register", "--store", str(store_dir), "--file", str(draft)])
        capsys.readouterr()
        assert main(["register", "--store", str(store_dir), "--file", str(draft)]) == 0
        again = json.loads(capsys.readouterr().out)
        assert again["created"] is False
        assert again["version"] == 1

    def test_changed_content_bumps_version(self, tmp_path, store_dir, capsys):
        main(["register", "--store", str(store_dir), "--file", str(write_draft(tmp_path))])
        capsys.readouterr()
        changed = write_draft(tmp_path, policy="개정 정책")
        assert main(["register", "--store", str(store_dir), "--file", str(changed)]) == 0
        assert json.loads(capsys.readouterr().out)["version"] == 2


class TestValidate:
    def test_valid_draft(self, tmp_path, capsys):
        assert main(["validate", "--file", str(write_draft(tmp_path))]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["valid"] is True
        assert out["content_hash"].startswith("sha256:")

    def test_blank_policy_fails(self, tmp_path, capsys):
        assert main(["validate", "--file", str(write_draft(tmp_path, policy=" "))]) == 1
        assert "policy" in capsys.readouterr().err

    def test_missing_examples_fail_via_register(self, tmp_path, store_dir, capsys):
        draft = write_draft(tmp_path, block_examples=[])
        assert main(["register", "--store", str(store_dir), "--file", str(draft)]) == 1
        assert "block_examples" in capsys.readouterr().err

    def test_unknown_key_rejected(self, tmp_path, capsys):
        assert main(["validate", "--file", str(write_draft(tmp_path, version=3))]) == 1
        assert "version" in capsys.readouterr().err

    def test_missing_required_key_rejected(self, tmp_path, capsys):
        payload = {k: v for k, v in DRAFT.items() if k != "purpose"}
        path = tmp_path / "draft.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert main(["validate", "--file", str(path)]) == 1
        assert "purpose" in capsys.readouterr().err


class TestEnforcementScript:
    def test_register_with_script_records_ref(self, tmp_path, store_dir, capsys):
        script = tmp_path / "guard.sh"
        script.write_bytes(b"#!/bin/sh\nexit 2\n")
        draft = write_draft(tmp_path)
        assert (
            main(
                [
                    "register",
                    "--store",
                    str(store_dir),
                    "--file",
                    str(draft),
                    "--enforcement-script",
                    str(script),
                ]
            )
            == 0
        )
        capsys.readouterr()
        main(["show", "--store", str(store_dir), "--guard", "guard-cli"])
        shown = json.loads(capsys.readouterr().out)
        assert shown["enforcement_ref"]["script_path"] == str(script)
        assert shown["enforcement_ref"]["file_hash"].startswith("sha256:")

    def test_missing_script_is_error(self, tmp_path, store_dir, capsys):
        draft = write_draft(tmp_path)
        code = main(
            [
                "register",
                "--store",
                str(store_dir),
                "--file",
                str(draft),
                "--enforcement-script",
                str(tmp_path / "ghost.sh"),
            ]
        )
        assert code == 1
        assert "ghost.sh" in capsys.readouterr().err


class TestErrors:
    def test_show_unknown_guard(self, store_dir, capsys):
        assert main(["show", "--store", str(store_dir), "--guard", "ghost"]) == 1
        assert "ghost" in capsys.readouterr().err

    def test_list_empty_store(self, store_dir, capsys):
        assert main(["list", "--store", str(store_dir)]) == 0
        assert json.loads(capsys.readouterr().out) == []

    def test_draft_file_missing(self, tmp_path, capsys):
        assert main(["validate", "--file", str(tmp_path / "none.json")]) == 1
        assert capsys.readouterr().err != ""


class TestModuleEntry:
    def test_python_dash_m_smoke(self, tmp_path, store_dir):
        """등록→조회 왕복이 `python -m rejectbench.cli`로 실제 동작한다."""
        draft = write_draft(tmp_path)
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
        register = subprocess.run(
            [
                sys.executable,
                "-m",
                "rejectbench.cli",
                "register",
                "--store",
                str(store_dir),
                "--file",
                str(draft),
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
            timeout=60,
        )
        assert register.returncode == 0, register.stderr
        show = subprocess.run(
            [
                sys.executable,
                "-m",
                "rejectbench.cli",
                "show",
                "--store",
                str(store_dir),
                "--guard",
                "guard-cli",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
            timeout=60,
        )
        assert show.returncode == 0, show.stderr
        assert json.loads(show.stdout)["guard_id"] == "guard-cli"
