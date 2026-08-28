"""테스트 공용 가드.

하드 게이트: 테스트는 임시 디렉터리만 사용하고 실제 운영 저장소(`data/`)에
절대 쓰지 않는다. autouse 픽스처가 각 테스트 전후의 `data/` 상태를 대조해
위반을 즉시 실패로 만든다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"


def _snapshot(root: Path):
    if not root.exists():
        return None
    entries = []
    for p in sorted(root.rglob("*")):
        stat = p.stat()
        entries.append((str(p.relative_to(root)), p.is_dir(), stat.st_size))
    return tuple(entries)


@pytest.fixture(autouse=True)
def no_production_writes():
    before = _snapshot(DATA_DIR)
    yield
    after = _snapshot(DATA_DIR)
    assert after == before, (
        "테스트가 운영 저장 경로 data/ 를 변경했다 — 테스트는 임시 디렉터리만 써야 한다"
    )
