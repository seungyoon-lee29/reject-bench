"""judge 서브커맨드 — 비용 승인 게이트 (T4 계약 7).

과금 호출 전에 판정 대상 건수·모델을 보여주고, 명시 승인(`--approve-billing`
또는 `REJECTBENCH_BILLING_APPROVED=1`) 없이는 한 건도 호출하지 않는다.
게이트 미통과는 대상 목록만 출력하고 exit 0 — dry-run이 기본이다.
"""

from __future__ import annotations

import json

import pytest

from rejectbench import AppendStore, Dataset, Origin, OriginEvidence, Verdict
from rejectbench.cli import main
from rejectbench.judge import BILLING_ENV, DEFAULT_MODEL_ID
from tests.factories import make_event, make_spec


@pytest.fixture()
def store_dir(tmp_path):
    return tmp_path / "store"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(BILLING_ENV, raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def seed(store_dir, *, events: int = 1):
    store = AppendStore(store_dir)
    spec = make_spec()
    store.append(spec)
    for i in range(events):
        store.append(
            make_event(spec, event_id=f"ev-{i + 1}", session_id=f"claude:s-{i + 1}")
        )
    return store, spec


class ForbiddenTransport:
    """게이트 미통과 경로에서 전송 계층이 만들어지면 즉시 실패."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("승인 없이 전송 계층이 생성됐다 — 과금 호출 경로 진입")


class FakeTransport:
    instances: list = []

    def __init__(self, *args, **kwargs):
        self.calls = []
        FakeTransport.instances.append(self)

    def complete(self, *, model_id, messages, settings):
        self.calls.append(model_id)
        return json.dumps({"verdict": "correct_block", "reason": "근거"}, ensure_ascii=False)


class TestBillingGate:
    def test_default_is_dry_run_listing_targets_exit_0(self, store_dir, capsys, monkeypatch):
        seed(store_dir, events=2)
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", ForbiddenTransport)
        assert main(["judge", "--store", str(store_dir)]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["approved"] is False
        assert out["model_id"] == DEFAULT_MODEL_ID
        assert out["pending_event_ids"] == ["ev-1", "ev-2"]
        assert out["planned_llm_calls"] >= 2

    def test_env_zero_is_not_approval(self, store_dir, capsys, monkeypatch):
        seed(store_dir)
        monkeypatch.setenv(BILLING_ENV, "0")
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", ForbiddenTransport)
        assert main(["judge", "--store", str(store_dir)]) == 0
        assert json.loads(capsys.readouterr().out)["approved"] is False

    def test_dry_run_reports_excluded_counts(self, store_dir, capsys, monkeypatch):
        store, spec = seed(store_dir)
        store.append(
            make_event(
                spec, event_id="ev-t", origin=Origin.TEST,
                origin_evidence=OriginEvidence.EXPLICIT_FLAG,
            )
        )
        store.append(
            make_event(
                spec, event_id="ev-u", origin=Origin.UNKNOWN,
                origin_evidence=OriginEvidence.NO_CONTEXT,
            )
        )
        store.append(
            make_event(event_id="ev-r", unregistered=True, guard_hint="/tmp/g.sh")
        )
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", ForbiddenTransport)
        assert main(["judge", "--store", str(store_dir)]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["excluded"] == {"unregistered": 1, "test": 1, "unknown": 1}

    def test_flag_approval_runs_judgement(self, store_dir, capsys, monkeypatch):
        seed(store_dir, events=1)
        FakeTransport.instances = []
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", FakeTransport)
        code = main(
            ["judge", "--store", str(store_dir), "--approve-billing", "--skip-calibration"]
        )
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["approved"] is True
        assert out["judged"] == [
            {"event_id": "ev-1", "verdict": "correct_block"}
        ] or out["judged"][0]["event_id"] == "ev-1"
        dataset = Dataset(AppendStore(store_dir).load().records)
        verdict = dataset.latest_verdict("ev-1")
        assert verdict is not None and verdict.verdict is Verdict.CORRECT_BLOCK

    def test_env_approval_runs_judgement(self, store_dir, capsys, monkeypatch):
        seed(store_dir, events=1)
        FakeTransport.instances = []
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", FakeTransport)
        monkeypatch.setenv(BILLING_ENV, "1")
        assert main(["judge", "--store", str(store_dir), "--skip-calibration"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["approved"] is True
        assert Dataset(AppendStore(store_dir).load().records).latest_verdict("ev-1")

    def test_approved_but_nothing_pending_makes_no_calls(self, store_dir, capsys, monkeypatch):
        store = AppendStore(store_dir)
        store.append(make_spec())  # 사건 없음
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", ForbiddenTransport)
        assert main(["judge", "--store", str(store_dir), "--approve-billing"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["approved"] is True
        assert out["pending_event_ids"] == []
        assert out["planned_llm_calls"] == 0

    def test_missing_api_key_with_approval_is_error(self, store_dir, capsys):
        seed(store_dir, events=1)
        code = main(["judge", "--store", str(store_dir), "--approve-billing"])
        assert code == 1
        err = capsys.readouterr().err
        assert "OPENAI_API_KEY" in err

    def test_rejudge_flags_pass_through(self, store_dir, capsys, monkeypatch):
        seed(store_dir, events=1)
        FakeTransport.instances = []
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", FakeTransport)
        assert main(
            ["judge", "--store", str(store_dir), "--approve-billing", "--skip-calibration"]
        ) == 0
        capsys.readouterr()
        assert main(
            [
                "judge", "--store", str(store_dir), "--approve-billing",
                "--skip-calibration", "--rejudge", "ev-1",
                "--rejudge-reason", "모델 교체",
            ]
        ) == 0
        dataset = Dataset(AppendStore(store_dir).load().records)
        verdicts = dataset.verdicts_for("ev-1")
        assert len(verdicts) == 2
        assert "[재판정: 모델 교체]" in verdicts[1].reason


class SettingsRecordingTransport:
    """`complete`에 실제로 도착한 settings를 그대로 붙잡는다."""

    seen: list = []

    def __init__(self, *args, **kwargs):
        pass

    def complete(self, *, model_id, messages, settings):
        SettingsRecordingTransport.seen.append(dict(settings))
        return json.dumps({"verdict": "correct_block", "reason": "근거"}, ensure_ascii=False)


class TestModelSettingsInjection:
    """`--model-settings` — 모델이 기본 설정을 거부할 때의 조정 경로.

    `rejectbench/AGENTS.md`가 "판정 기본 설정 `{"temperature": 0}`은 모델이
    거부할 수 있다 — settings 주입으로 조정하고 `model_settings_hash`가 바뀐
    재판정 규율을 따른다"고 처방한 그 주입구다. 실제로 gpt-5 계열이
    `temperature: 0`을 거부해(HTTP 400) 첫 과금 호출이 전량 실패했고,
    라이브러리 계층은 이미 `model_settings`를 받는데 CLI에만 구멍이 있었다.

    의미는 **전체 교체**다 — 병합이 아니다. `model_settings_hash`가 실제로
    쓰인 설정을 가리켜야 하므로, 명령줄에 적은 것이 곧 해시 대상이 된다.
    """

    def test_default_settings_unchanged_without_flag(self, store_dir, capsys, monkeypatch):
        seed(store_dir, events=1)
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", ForbiddenTransport)
        assert main(["judge", "--store", str(store_dir)]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["model_settings"] == {"temperature": 0}

    def test_empty_object_drops_temperature_entirely(self, store_dir, capsys, monkeypatch):
        """gpt-5 계열이 요구하는 형태 — temperature 키 자체가 없어야 한다."""
        seed(store_dir, events=1)
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", ForbiddenTransport)
        assert main(["judge", "--store", str(store_dir), "--model-settings", "{}"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["model_settings"] == {}

    def test_injected_settings_reach_the_transport(self, store_dir, capsys, monkeypatch):
        seed(store_dir, events=1)
        SettingsRecordingTransport.seen = []
        monkeypatch.setattr(
            "rejectbench.judge.OpenAITransport", SettingsRecordingTransport
        )
        assert main(
            [
                "judge", "--store", str(store_dir), "--approve-billing",
                "--skip-calibration", "--model-settings", "{}",
            ]
        ) == 0
        assert SettingsRecordingTransport.seen == [{}]

    def test_replacement_not_merge(self, store_dir, capsys, monkeypatch):
        """전체 교체 — 기본값의 temperature가 남아 따라오면 안 된다."""
        seed(store_dir, events=1)
        SettingsRecordingTransport.seen = []
        monkeypatch.setattr(
            "rejectbench.judge.OpenAITransport", SettingsRecordingTransport
        )
        assert main(
            [
                "judge", "--store", str(store_dir), "--approve-billing",
                "--skip-calibration", "--model-settings", '{"top_p": 1}',
            ]
        ) == 0
        assert SettingsRecordingTransport.seen == [{"top_p": 1}]

    def test_settings_change_moves_the_hash(self, store_dir, capsys, monkeypatch):
        """재판정 규율의 근거 — 설정이 바뀌면 `model_settings_hash`가 바뀐다."""
        from rejectbench.judge import load_calibrations  # noqa: PLC0415

        seed(store_dir, events=1)
        SettingsRecordingTransport.seen = []
        monkeypatch.setattr(
            "rejectbench.judge.OpenAITransport", SettingsRecordingTransport
        )
        assert main(
            ["judge", "--store", str(store_dir), "--approve-billing"]
        ) == 0
        capsys.readouterr()
        assert main(
            [
                "judge", "--store", str(store_dir), "--approve-billing",
                "--model-settings", "{}", "--rejudge", "ev-1",
                "--rejudge-reason", "설정 조정",
            ]
        ) == 0
        calibrations, corrupt = load_calibrations(store_dir)
        assert corrupt == 0
        hashes = {c.model_settings_hash for c in calibrations}
        assert len(hashes) == 2, "설정이 달라졌는데 교정 해시가 같다"

    def test_invalid_json_is_error_before_any_billing(self, store_dir, capsys, monkeypatch):
        seed(store_dir, events=1)
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", ForbiddenTransport)
        assert main(
            [
                "judge", "--store", str(store_dir), "--approve-billing",
                "--model-settings", "{not json",
            ]
        ) == 1
        assert "--model-settings" in capsys.readouterr().err

    def test_non_object_json_is_error(self, store_dir, capsys, monkeypatch):
        seed(store_dir, events=1)
        monkeypatch.setattr("rejectbench.judge.OpenAITransport", ForbiddenTransport)
        assert main(
            [
                "judge", "--store", str(store_dir), "--approve-billing",
                "--model-settings", "[1, 2]",
            ]
        ) == 1
        assert "--model-settings" in capsys.readouterr().err
