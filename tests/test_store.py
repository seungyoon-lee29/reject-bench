"""append 전용 JSONL 저장소: 원자 append, 락, 부분 쓰기 가시화, 운영 경계."""

from __future__ import annotations

import json
import threading

from rejectbench import AppendStore, production_root
from rejectbench.store import RECORDS_FILENAME
from tests.factories import make_event, make_spec


def test_append_and_load_roundtrip(tmp_path):
    store = AppendStore(tmp_path / "store")
    spec = make_spec()
    event = make_event(spec)
    store.append(spec)
    store.append(event)
    result = store.load()
    assert result.records == [spec, event]
    assert result.corrupt == []


def test_load_of_missing_file_is_empty(tmp_path):
    result = AppendStore(tmp_path / "nowhere").load()
    assert result.records == []
    assert result.corrupt == []


def test_constructing_store_does_not_touch_disk(tmp_path):
    root = tmp_path / "untouched"
    AppendStore(root)
    assert not root.exists()


def test_append_order_is_preserved(tmp_path):
    store = AppendStore(tmp_path / "store")
    spec = make_spec()
    store.append(spec)
    events = [make_event(spec, event_id=f"ev-{i}", session_id=f"claude:s-{i}") for i in range(20)]
    for event in events:
        store.append(event)
    loaded = store.load().records
    assert [r.record_id for r in loaded] == [spec.record_id] + [e.event_id for e in events]


def test_partial_trailing_line_is_surfaced_not_silently_dropped(tmp_path):
    store = AppendStore(tmp_path / "store")
    spec = make_spec()
    event = make_event(spec)
    store.append(spec)
    store.append(event)
    with open(store.path, "ab") as f:
        f.write(b'{"record_type": "guard_event", "half')  # 개행 없는 부분 쓰기
    result = store.load()
    assert result.records == [spec, event]
    assert len(result.corrupt) == 1
    assert result.corrupt[0].line_no == 3


def test_corrupt_line_metadata_carries_no_content(tmp_path):
    store = AppendStore(tmp_path / "store")
    store.append(make_spec())
    secret = b'password=hunter2-not-json'
    with open(store.path, "ab") as f:
        f.write(secret + b"\n")
    result = store.load()
    corrupt = result.corrupt[0]
    assert corrupt.byte_length == len(secret)
    # 손상 줄 메타데이터에 원문이 실리지 않는다.
    assert "hunter2" not in repr(corrupt)


def test_corrupt_middle_line_does_not_lose_neighbors(tmp_path):
    store = AppendStore(tmp_path / "store")
    spec = make_spec()
    store.append(spec)
    with open(store.path, "ab") as f:
        f.write(b"not json at all\n")
    event = make_event(spec)
    store.append(event)
    result = store.load()
    assert result.records == [spec, event]
    assert [c.line_no for c in result.corrupt] == [2]


def test_concurrent_appends_do_not_interleave_lines(tmp_path):
    store = AppendStore(tmp_path / "store")
    spec = make_spec()
    n_threads, per_thread = 8, 25

    def worker(t: int) -> None:
        for i in range(per_thread):
            store.append(make_event(spec, event_id=f"ev-{t}-{i}", session_id=f"claude:s-{t}"))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    with open(store.path, "rb") as f:
        lines = f.read().splitlines()
    assert len(lines) == n_threads * per_thread
    for line in lines:
        json.loads(line)  # 모든 줄이 온전한 JSON — 줄 섞임 없음
    result = store.load()
    assert result.corrupt == []
    assert len(result.records) == n_threads * per_thread


def test_production_root_is_central_data_path():
    root = production_root()
    assert root.parts[-2:] == ("data", "v7")
    # 이 저장소(작업 트리) 안의 중앙 경로를 가리킨다.
    repo_root = root.parent.parent
    assert (repo_root / "pyproject.toml").exists()


def test_production_path_not_created_by_import_or_lookup():
    existed_before = production_root().exists()
    AppendStore(production_root())  # 생성만으로는 디스크를 건드리지 않는다
    assert production_root().exists() == existed_before


def test_store_file_name_is_stable(tmp_path):
    store = AppendStore(tmp_path / "store")
    assert store.path.name == RECORDS_FILENAME
