"""적재 시점 비밀 제거 (spec §4) — 치환과 양성 대조를 함께 고정한다."""

from __future__ import annotations

import pytest

from rejectbench.scrub import PLACEHOLDER, scrub_text


# --- 치환: 일반적 자격증명 형태 ----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "sk-abcdefghijklmnop1234",
        "api key sk-ant-api03-AbCdEf123456789012345678 leaked",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        "github_pat_11ABCDEFG0123456789_abcdefghijk",
        "AKIAIOSFODNN7EXAMPLE",
        "ASIAIOSFODNN7EXAMPLE",
        "xoxb-1234567890-abcdefghijklmno",
    ],
)
def test_credential_tokens_are_redacted(text):
    scrubbed = scrub_text(text)
    assert PLACEHOLDER in scrubbed
    for word in text.split():
        if word.startswith(("sk-", "ghp_", "gho_", "github_pat_", "AKIA", "ASIA", "xoxb-")):
            assert word not in scrubbed


def test_bearer_token_value_is_redacted_but_scheme_kept():
    scrubbed = scrub_text("Authorization: Bearer abc.DEF-123_ghi789xyz")
    assert scrubbed == f"Authorization: Bearer {PLACEHOLDER}"


@pytest.mark.parametrize(
    "text,name",
    [
        ("OPENAI_API_KEY=sk-live-abcdef0123456789", "OPENAI_API_KEY"),
        ("MY_TOKEN=tok_0123456789abcdef", "MY_TOKEN"),
        ("DB_PASSWORD='hunter2 with space'", "DB_PASSWORD"),
        ('AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"', "AWS_SECRET_ACCESS_KEY"),
        ("api_secret=lowercase-name-still-secret", "api_secret"),
    ],
)
def test_env_assignment_value_redacted_name_kept(text, name):
    scrubbed = scrub_text(text)
    assert scrubbed == f"{name}={PLACEHOLDER}"


def test_long_hex_run_is_redacted():
    token = "d" * 64
    assert scrub_text(f"leaked {token} here") == f"leaked {PLACEHOLDER} here"


def test_long_mixed_base64_run_is_redacted():
    token = "Qx7" * 15 + "Zz9w="  # 대소문자·숫자 혼합 40+
    scrubbed = scrub_text(f"blob {token} end")
    assert token not in scrubbed
    assert PLACEHOLDER in scrubbed


def test_jwt_is_redacted():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.TJVA95OrM7E2cBab30RMHrHDcEfxjoYZgeFONFh7HgQ"
    assert jwt not in scrub_text(f"token {jwt}")


# --- 양성 대조: 정상값을 과도 훼손하지 않는다 --------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # 정상 문장 (가드 차단 메시지 형태)
        "BLOCKED: 'git push --force origin main' matches dangerous pattern 'push --force'.",
        "커밋된 라이브 실측 리포트는 사후 편집하지 않는다 — reports/evaluation-live-3.md",
        # 경로
        "/Users/ian/workspace/reject-bench/rejectbench/records.py",
        "reports/evaluation-live.md",
        "~/.claude/hooks/block-dangerous-git.sh",
        # 짧은 해시 표기 (git 축약)
        "commit 9cc3afd fixed it",
        "abc1234def5678",
        # 일반 명령
        "uv run pytest -q",
        "git reset --hard origin/main",
    ],
)
def test_benign_text_survives(text):
    assert scrub_text(text) == text


def test_forty_char_git_sha1_survives():
    sha = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b"  # 40 hex
    assert scrub_text(f"commit {sha} touched store.py") == f"commit {sha} touched store.py"


def test_sha256_notation_survives():
    notation = "sha256:" + "0a" * 32
    assert scrub_text(f"content_hash {notation}") == f"content_hash {notation}"


def test_bare_48_hex_boundary_is_redacted():
    token = "0a" * 24  # 48 hex, sha256: 접두 없음
    assert scrub_text(token) == PLACEHOLDER


def test_long_path_segments_survive():
    path = "/tmp/VeryLongCamelCaseDirectoryName1234567890AbcDef/file.py"
    assert scrub_text(path) == path


def test_scrub_is_idempotent():
    once = scrub_text("OPENAI_API_KEY=sk-live-abcdef0123456789 and Bearer abcdef123456")
    assert scrub_text(once) == once


# ── 명령 전문 제거 (2026-08-29 사용자 결정: reason의 명령 부분은 저장하지 않는다) ──

from rejectbench.scrub import redact_command_echo


def test_blocked_echo_command_is_omitted_pattern_kept():
    reason = (
        "BLOCKED: 'git push --force origin main' matches dangerous pattern "
        "'push --force'. The user has prevented you from doing this."
    )
    out = redact_command_echo(reason)
    assert "<command omitted>" in out
    assert "git push" not in out
    assert "dangerous pattern 'push --force'" in out


def test_blocked_echo_multiline_command_is_omitted():
    reason = "BLOCKED: 'echo line1\nrm -rf x' matches dangerous pattern 'x'. tail"
    out = redact_command_echo(reason)
    assert "<command omitted>" in out
    assert "line1" not in out


def test_non_echo_reason_unchanged():
    reason = "커밋된 라이브 실측 리포트는 사후 편집하지 않는다 — reports/evaluation-live-1.md"
    assert redact_command_echo(reason) == reason


def test_redact_is_idempotent():
    reason = "BLOCKED: 'git clean -fd' matches dangerous pattern 'git clean -fd'. tail"
    once = redact_command_echo(reason)
    assert redact_command_echo(once) == once
