"""저장 경로 해석.

실스토어는 메인 체크아웃의 `data/`(비추적)이고, worktree와 테스트는 환경변수로
다른 곳을 가리킨다. spec 불변식 8(`data/` 비추적)과 9(테스트는 실스토어에 쓰지
않는다)가 이 한 곳의 경로 해석에 걸려 있으므로, 다른 모듈은 경로를 직접
조립하지 말고 여기를 거친다.
"""

from __future__ import annotations

import os
from pathlib import Path

#: 실스토어 대신 쓸 경로를 지정하는 환경변수. worktree 실행과 테스트가 쓴다.
DATA_DIR_ENV = "REJECTBENCH_DATA_DIR"

#: 이 패키지가 놓인 체크아웃의 루트. worktree에서는 worktree 루트가 된다.
REPO_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """비추적 원본 저장소 경로. 환경변수가 있으면 그쪽이 이긴다.

    디렉터리를 만들지는 않는다 — 생성 시점을 정하는 것은 적재하는 쪽 몫이다.
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / "data"
