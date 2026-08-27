"""v0 로그 접두 해시 baseline — 불변식 1(기존 줄 불변)의 로컬 검사 장치.

v0 로그(`data/events.jsonl`)는 계속 append되지만 기존 줄은 바뀌지 않아야 한다.
그래서 특정 시점의 **접두**(그 시점까지의 완전한 행 전부)를 해시로 고정해두고,
이후에는 같은 길이의 접두가 같은 해시를 내는지만 본다. 뒤에 붙는 줄은 검사
대상이 아니다.

baseline은 준비 단계에서 뜬다. 소급 기록(T5)이 자기 작업 뒤에 baseline을 뜨면
그 대조는 자기 검증이 되므로, `capture`는 기존 baseline을 덮어쓰지 않는다 —
다시 뜨려면 파일을 지워야 하고, 그 삭제는 git 이력에 남는다.

로그 자체는 비추적이라 이 해시로 제3자가 무엇을 검증할 수는 없다. 사적 대조
장치다.

    python -m rejectbench.v0_baseline capture
    python -m rejectbench.v0_baseline verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rejectbench.paths import REPO_ROOT, data_dir

SCHEMA = 1

#: 추적 경로 — 로그는 비추적이지만 baseline은 이력에 남아야 대조가 성립한다.
BASELINE_PATH = REPO_ROOT / "docs" / "v0-접두해시-baseline.json"

LOG_NAME = "events.jsonl"


def log_path() -> Path:
    """검사 대상 v0 로그. 실스토어 위치는 `paths.data_dir()`가 정한다."""
    return data_dir() / LOG_NAME


def complete_prefix(raw: bytes) -> bytes:
    """마지막 개행까지만 남긴다.

    훅이 append하는 도중에 읽으면 마지막 행이 잘려 있을 수 있다. 잘린 행을
    baseline에 넣으면 그 행이 완성되는 순간 검사가 깨진다.
    """
    end = raw.rfind(b"\n")
    return b"" if end < 0 else raw[: end + 1]


def measure(raw: bytes) -> dict:
    """접두 바이트열에서 baseline 본문을 만든다."""
    prefix = complete_prefix(raw)
    return {
        "prefix_bytes": len(prefix),
        "prefix_lines": prefix.count(b"\n"),
        "sha256": hashlib.sha256(prefix).hexdigest(),
    }


def capture(source: Path, captured_at: str) -> dict:
    """여는 순간의 길이로 접두를 고정한다 — 분석 중에도 로그는 늘어난다."""
    raw = source.read_bytes()
    return {
        "schema": SCHEMA,
        "captured_at": captured_at,
        "source": LOG_NAME,
        **measure(raw),
    }


def verify(baseline: dict, source: Path) -> tuple[bool, str]:
    """접두가 그대로인지 본다. 뒤에 붙은 줄은 보지 않는다."""
    expected_bytes = baseline["prefix_bytes"]
    raw = source.read_bytes()

    if len(raw) < expected_bytes:
        return False, (
            f"로그가 baseline보다 짧다: {len(raw)}바이트 < {expected_bytes}바이트. "
            "기존 줄이 지워졌거나 파일이 갈렸다."
        )

    prefix = raw[:expected_bytes]
    actual = hashlib.sha256(prefix).hexdigest()
    if actual != baseline["sha256"]:
        return False, (
            f"접두 해시 불일치: {actual} ≠ {baseline['sha256']}. "
            f"baseline 시점의 {baseline['prefix_lines']}행 중 어딘가가 바뀌었다."
        )

    grown = len(raw) - expected_bytes
    added = raw[expected_bytes:].count(b"\n")
    return True, (
        f"접두 {baseline['prefix_lines']}행 불변 확인 "
        f"(baseline {baseline['captured_at']}, 이후 +{added}행 / +{grown}바이트)"
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _display(path: Path) -> str:
    """저장소 안이면 상대경로로 — 홈 경로를 출력에 흘리지 않는다."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rejectbench.v0_baseline")
    parser.add_argument("command", choices=["capture", "verify"])
    args = parser.parse_args(argv)

    source = log_path()
    if not source.exists():
        print(f"로그가 없다: {_display(source)}", file=sys.stderr)
        return 2

    if args.command == "capture":
        if BASELINE_PATH.exists():
            print(
                f"baseline이 이미 있다: {_display(BASELINE_PATH)}\n"
                "덮어쓰지 않는다 — 다시 뜨려면 파일을 지워라(삭제는 이력에 남는다).",
                file=sys.stderr,
            )
            return 2
        record = capture(source, _now())
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"{record['prefix_lines']}행 / {record['prefix_bytes']}바이트 고정 "
            f"→ {_display(BASELINE_PATH)}"
        )
        return 0

    if not BASELINE_PATH.exists():
        print(f"baseline이 없다: {_display(BASELINE_PATH)}", file=sys.stderr)
        return 2
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    ok, message = verify(baseline, source)
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
