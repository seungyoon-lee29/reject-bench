"""append 전용 JSONL 저장소 (spec §4).

- 파일 락(flock) + 단일 write의 원자 append로 동시 기록의 줄 섞임을 막는다.
- 원본 수정·삭제 API가 없다 — append와 load뿐이다.
- 손상·부분 쓰기 줄은 원문 없이(줄 번호·바이트 길이만) 가시화한다.
- 운영 저장 위치는 이 저장소의 `data/v7` 중앙 경로다. 테스트는 임시
  디렉터리에 만든 별도 store만 쓴다. 생성자는 디스크를 건드리지 않으므로
  경로 조회만으로 운영 경로가 오염되지 않는다.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path

from rejectbench.records import Record, SchemaError, record_from_json, to_json

RECORDS_FILENAME = "records.jsonl"


@dataclass(frozen=True)
class CorruptLine:
    """손상 줄의 원문 없는 메타데이터."""

    line_no: int
    byte_length: int


@dataclass(frozen=True)
class LoadResult:
    records: list[Record]
    corrupt: list[CorruptLine]


class AppendStore:
    """단일 JSONL 로그. append 순서가 곧 선행성 근거다 (spec §3.1)."""

    def __init__(self, root: Path | str):
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def path(self) -> Path:
        return self._root / RECORDS_FILENAME

    def append(self, record: Record) -> None:
        line = json.dumps(
            to_json(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        data = line.encode("utf-8") + b"\n"
        self._root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                os.write(fd, data)  # O_APPEND + 단일 write — 줄 섞임 방지
                os.fsync(fd)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def load(self) -> LoadResult:
        if not self.path.exists():
            return LoadResult(records=[], corrupt=[])
        data = self.path.read_bytes()
        if not data:
            return LoadResult(records=[], corrupt=[])
        ends_with_newline = data.endswith(b"\n")
        lines = data.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        records: list[Record] = []
        corrupt: list[CorruptLine] = []
        for line_no, raw in enumerate(lines, start=1):
            partial_tail = line_no == len(lines) and not ends_with_newline
            if partial_tail:
                corrupt.append(CorruptLine(line_no=line_no, byte_length=len(raw)))
                continue
            try:
                payload = json.loads(raw.decode("utf-8"))
                records.append(record_from_json(payload))
            except (UnicodeDecodeError, json.JSONDecodeError, SchemaError):
                corrupt.append(CorruptLine(line_no=line_no, byte_length=len(raw)))
        return LoadResult(records=records, corrupt=corrupt)


def production_root() -> Path:
    """운영 중앙 저장 경로 — 이 저장소의 `data/v7`. 조회만으로 생성하지 않는다."""
    return Path(__file__).resolve().parents[1] / "data" / "v7"


def open_production_store() -> AppendStore:
    return AppendStore(production_root())
