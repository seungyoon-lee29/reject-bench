"""경로 해석과 테스트 격리 골격 자체를 검사한다.

각 태스크의 동작 검증은 그 태스크가 자기 테스트로 가져간다. 여기 있는 것은
그 테스트들이 딛고 설 바닥이 맞게 깔렸는지만 본다.
"""

from __future__ import annotations

from pathlib import Path

from rejectbench.paths import DATA_DIR_ENV, REPO_ROOT, data_dir


def test_환경변수가_있으면_그_경로를_쓴다(isolated_store):
    assert data_dir() == isolated_store.resolve()


def test_기본값은_체크아웃의_data(monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert data_dir() == REPO_ROOT / "data"


def test_상대경로도_절대경로로_해석한다(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(DATA_DIR_ENV, "상대/경로")
    resolved = data_dir()
    assert resolved.is_absolute()
    assert resolved == (tmp_path / "상대/경로").resolve()


def test_기본_격리가_실스토어를_피한다(isolated_store):
    """불변식 9 — autouse 픽스처가 실제로 실스토어 밖을 가리키는지."""
    assert data_dir() != (REPO_ROOT / "data").resolve()
    assert Path(data_dir()).exists()
