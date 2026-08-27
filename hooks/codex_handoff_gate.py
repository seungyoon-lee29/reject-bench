#!/usr/bin/env python3
import hashlib
import json
import os
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import continuity


SCHEMA = "v1"
HANDOFF_TRIGGER_REMAINING = 0.40
MAX_TRANSCRIPT_TAIL_BYTES = 2 * 1024 * 1024


def read_payload():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def project_dir(payload):
    raw = payload.get("cwd") or os.getcwd()
    return Path(raw).expanduser().resolve()


def state_path(payload, root):
    root = Path(root).resolve()
    session_id = str(payload.get("session_id") or "unknown")
    identity = f"{root}\0{session_id}".encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()[:16]
    return continuity.continuity_dir() / f"{root.name}-{suffix}.gate.json"


def handoff_digest(root):
    try:
        data = (root / "HANDOFF.md").read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def file_signature(path):
    try:
        value = path.stat()
    except OSError:
        return None
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def read_state(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_state(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def latest_usage(transcript_path):
    if not transcript_path:
        return None
    path = Path(transcript_path)
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            end = stream.tell()
            start = max(0, end - MAX_TRANSCRIPT_TAIL_BYTES)
            stream.seek(start)
            tail = stream.read()
    except OSError:
        return None

    for raw_line in reversed(tail.splitlines()):
        try:
            event = json.loads(raw_line)
        except (UnicodeDecodeError, ValueError):
            continue
        payload = event.get("payload") if isinstance(event, dict) else None
        if event.get("type") != "event_msg" or not isinstance(payload, dict):
            continue
        if payload.get("type") != "token_count":
            continue
        info = payload.get("info")
        if not isinstance(info, dict):
            continue
        last = info.get("last_token_usage")
        window = info.get("model_context_window")
        used = last.get("total_tokens") if isinstance(last, dict) else None
        if not isinstance(used, int) or not isinstance(window, int) or window <= 0:
            continue
        remaining = max(0.0, min(1.0, (window - used) / window))
        return {"used": used, "window": window, "remaining": remaining}
    return None


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit(value):
    print(json.dumps(value, ensure_ascii=False))


def pending_state(payload, root, digest, usage):
    return {
        "schema": SCHEMA,
        "session_id": str(payload.get("session_id") or "unknown"),
        "status": "pending",
        "required_hash": digest,
        "requested_at": now(),
        "used_tokens": usage.get("used") if usage else None,
        "context_window": usage.get("window") if usage else None,
    }


def update_ready(path, state, digest):
    if state.get("status") == "pending" and digest and digest != state.get("required_hash"):
        state = {
            **state,
            "status": "ready",
            "ready_hash": digest,
            "ready_at": now(),
        }
        write_state(path, state)
    return state


def handoff_instruction(usage):
    detail = ""
    if usage:
        remaining_pct = round(usage["remaining"] * 100, 1)
        detail = f" 현재 컨텍스트 잔여량은 약 {remaining_pct}%입니다."
    return (
        "컨텍스트 compact 전에 HANDOFF.md 갱신이 필요합니다."
        f"{detail} 다른 작업을 진행하지 말고 HANDOFF.md를 먼저 갱신하세요. "
        "반드시 지금 하던 것, 다음 할 일의 구체적 첫 행동 1개, 미결 결정, 함정을 현재 상태로 기록한 뒤 턴을 끝내세요. "
        "직접 /compact를 실행하거나 새 Codex 프로세스를 시작하지 마세요."
    )


def start(payload):
    root = project_dir(payload)
    path = state_path(payload, root)
    write_state(
        path,
        {
            "schema": SCHEMA,
            "session_id": str(payload.get("session_id") or "unknown"),
            "status": "idle",
            "baseline_hash": handoff_digest(root),
            "started_at": now(),
            "source": payload.get("source"),
        },
    )
    return 0


def prompt(payload):
    usage = latest_usage(payload.get("transcript_path"))
    if not usage or usage["remaining"] > HANDOFF_TRIGGER_REMAINING:
        return 0

    root = project_dir(payload)
    path = state_path(payload, root)
    digest = handoff_digest(root)
    state = update_ready(path, read_state(path), digest)
    if state.get("status") in {"ready", "captured"}:
        return 0
    if state.get("status") != "pending":
        state = pending_state(payload, root, digest, usage)
        write_state(path, state)

    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": handoff_instruction(usage),
            }
        }
    )
    return 0


def stop(payload):
    usage = latest_usage(payload.get("transcript_path"))
    if not usage or usage["remaining"] > HANDOFF_TRIGGER_REMAINING:
        emit({})
        return 0

    root = project_dir(payload)
    path = state_path(payload, root)
    digest = handoff_digest(root)
    state = update_ready(path, read_state(path), digest)
    if state.get("status") in {"ready", "captured"}:
        emit({})
        return 0

    if state.get("status") != "pending":
        state = pending_state(payload, root, digest, usage)
        write_state(path, state)

    instruction = handoff_instruction(usage)
    if payload.get("stop_hook_active"):
        emit({"systemMessage": f"{instruction} HANDOFF 갱신 전 compact는 차단됩니다."})
        return 0

    emit({"decision": "block", "reason": instruction})
    return 0


def precompact(payload):
    root = project_dir(payload)
    path = state_path(payload, root)
    digest = handoff_digest(root)
    state = update_ready(path, read_state(path), digest)
    ready = (
        state.get("status") in {"ready", "captured"}
        and digest is not None
        and digest != state.get("required_hash")
    )
    if not ready:
        usage = latest_usage(payload.get("transcript_path"))
        write_state(path, pending_state(payload, root, digest, usage))
        emit(
            {
                "continue": False,
                "stopReason": "HANDOFF.md must be refreshed before compaction",
                "systemMessage": "compact를 차단했습니다. HANDOFF.md를 먼저 갱신한 뒤 compact를 다시 시도하세요.",
            }
        )
        return 0

    target = continuity.snapshot_path(payload, root)
    previous_signature = file_signature(target)
    continuity.capture(payload)
    current_signature = file_signature(target)
    handoff = continuity.read_handoff(root)
    try:
        snapshot = target.read_text(encoding="utf-8")
    except OSError:
        snapshot = None
    captured_current_handoff = (
        handoff is not None
        and snapshot is not None
        and snapshot.endswith(f"{handoff.rstrip()}\n")
    )
    if (
        current_signature is None
        or current_signature == previous_signature
        or not captured_current_handoff
    ):
        emit(
            {
                "continue": False,
                "stopReason": "HANDOFF snapshot failed",
                "systemMessage": "HANDOFF 스냅샷 저장에 실패해 compact를 차단했습니다.",
            }
        )
        return 0

    write_state(path, {**state, "status": "captured", "captured_at": now()})
    emit({})
    return 0


def reset(payload):
    return start({**payload, "source": "compact"})


def cleanup(payload):
    root = project_dir(payload)
    try:
        state_path(payload, root).unlink(missing_ok=True)
    except OSError:
        pass
    return 0


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = read_payload()
    actions = {
        "start": start,
        "prompt": prompt,
        "stop": stop,
        "precompact": precompact,
        "reset": reset,
        "cleanup": cleanup,
    }
    handler = actions.get(action)
    if handler is None:
        print("usage: codex_handoff_gate.py start|prompt|stop|precompact|reset|cleanup", file=sys.stderr)
        return 2
    return handler(payload)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"codex-handoff-gate: {error}", file=sys.stderr)
        raise SystemExit(1)
