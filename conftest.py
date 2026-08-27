"""pytest 공통 설정.

여기서 거는 것은 spec 불변식 9 하나다 — 테스트는 실스토어에 쓰지 않는다.
기본 경로 해석을 임시 디렉터리로 돌려놓고, 실스토어를 가리키게 되면 즉시
실패시킨다. 테스트가 이 픽스처를 무시하고 실경로를 직접 열어버리는 것까지
막지는 못한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rejectbench.paths import DATA_DIR_ENV, REPO_ROOT

REAL_STORE = (REPO_ROOT / "data").resolve()


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """모든 테스트의 데이터 경로를 임시 디렉터리로 돌린다."""
    store = tmp_path / "data"
    store.mkdir()
    monkeypatch.setenv(DATA_DIR_ENV, str(store))

    resolved = Path(os.environ[DATA_DIR_ENV]).resolve()
    assert resolved != REAL_STORE, f"테스트가 실스토어를 가리켰다: {resolved}"
    return store
