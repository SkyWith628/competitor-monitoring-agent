"""[D] CLI 통합 테스트 — 이벤트 저장 → classify → report 전 구간. LLM·슬랙·메일은 가짜로 대체."""
import json
from datetime import datetime

import pytest
from typer.testing import CliRunner

from app.__main__ import cli
from app.analysis import classify as classify_mod
from app.analysis import insight as insight_mod
from app.analysis import summarize as summarize_mod
from app.core import config
from app.core.models import Brand, BrandSummary, ChangeEvent, ChangeType
from app.infra.notify import email, slack
from app.infra.storage import repo

WEEK = "2026-W47"
runner = CliRunner()


def _fake_llm(model, system, user, schema, **kwargs):
    if schema is BrandSummary:
        brand = user.split("\n", 1)[0].removeprefix("brand=")
        cited = brand == "spao"
        return BrandSummary(brand=brand, highlights=["히트텍 가격 인하 [#1]"] if cited else ["근거 없는 문장"],
                            event_ids=[1] if cited else [])
    if schema is insight_mod.Insight:
        return schema(tldr="스파오 가격 공세", insights=["인사이트"], actions=["액션"])
    ids = [json.loads(line)["id"] for line in user.splitlines()]
    return schema.model_validate(
        {"labels": [{"id": i, "category": "가격전략", "importance": 5, "note": "25% 인하"} for i in ids]})


@pytest.fixture
def env(database, tmp_path, monkeypatch):
    """임시 DB·출력 폴더, 가짜 LLM, 발송 내역을 기록하는 가짜 슬랙·메일."""
    monkeypatch.setattr(config, "ROOT", tmp_path)
    for mod in (classify_mod, summarize_mod, insight_mod):
        monkeypatch.setattr(mod, "ask_json", _fake_llm)
    sent = {"slack": [], "email": [], "ops": []}
    monkeypatch.setattr(slack, "send_blocks", lambda blocks, fallback_text: sent["slack"].append(fallback_text))
    monkeypatch.setattr(slack, "send_ops_alert", lambda message: sent["ops"].append(message))
    monkeypatch.setattr(email, "send_html", lambda subject, html: sent["email"].append(subject))
    assert runner.invoke(cli, ["init-db"]).exit_code == 0
    return sent


def _seed():
    repo.save_events([ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.PRICE_DOWN, title="히트텍 인하",
                                  url="https://example.com/p/1", detail={"before": 39900, "after": 29900},
                                  detected_at=datetime.fromisoformat("2026-11-17T09:00:00"))])


def _sent_at():
    return repo.report_sent_at(WEEK)


def test_dry_run_writes_html_without_sending(env, tmp_path):
    _seed()
    assert runner.invoke(cli, ["classify"]).exit_code == 0
    assert repo.unclassified_events() == []

    result = runner.invoke(cli, ["report", "--week", WEEK, "--dry-run"])
    assert result.exit_code == 0, result.output
    html = (tmp_path / "data" / f"report-{WEEK}.html").read_text(encoding="utf-8")
    assert "스파오 가격 공세" in html and "히트텍 인하" in html
    md = (tmp_path / "data" / f"report-{WEEK}.md").read_text(encoding="utf-8")
    assert "1. [스파오] [히트텍 인하](https://example.com/p/1) — 25% 인하" in md
    assert "근거 없는 문장" not in html  # 인용 없는 하이라이트는 걸러진다
    assert env == {"slack": [], "email": [], "ops": []}
    assert _sent_at() is None


def test_report_sends_to_both_channels(env):
    _seed()
    runner.invoke(cli, ["classify"])
    assert runner.invoke(cli, ["report", "--week", WEEK]).exit_code == 0
    assert env["slack"] == ["스파오 가격 공세"] and len(env["email"]) == 1
    assert _sent_at() is not None


def test_email_still_sent_when_slack_fails(env, monkeypatch):
    def boom(blocks, fallback_text):
        raise RuntimeError("webhook down")

    monkeypatch.setattr(slack, "send_blocks", boom)
    _seed()
    runner.invoke(cli, ["classify"])
    assert runner.invoke(cli, ["report", "--week", WEEK]).exit_code == 1
    assert len(env["email"]) == 1 and "slack" in env["ops"][0]


def test_report_skipped_when_no_classified_events(env):
    _seed()  # classify 를 돌리지 않음
    assert runner.invoke(cli, ["report", "--week", WEEK]).exit_code == 1
    assert env["slack"] == [] and "0건" in env["ops"][0]
    assert _sent_at() is None


def test_collect_rejects_unknown_source(env):
    assert runner.invoke(cli, ["collect", "--source", "bogus"]).exit_code == 2
    assert env["ops"] == []


def test_collect_exits_nonzero_but_continues_on_brand_failure(env, monkeypatch):
    from app.media_monitor import pipeline

    def run_news(brand):
        if brand is Brand.SPAO:
            raise RuntimeError("timeout")
        return []

    monkeypatch.setattr(pipeline, "run_news", run_news)
    result = runner.invoke(cli, ["collect", "--source", "news"])
    assert result.exit_code == 1
    assert "[news/uniqlo] 이벤트 0건" in result.output 
    assert [m for m in env["ops"] if "수집 실패" in m] == ["수집 실패 news/spao: RuntimeError('timeout')"]


def test_default_week_is_last_week_in_kst(env, monkeypatch):
    """주간 스케줄은 일요일 23:00 UTC(= 월요일 08:00 KST)에 돈다 — 방금 끝난 KST 주를 골라야 한다."""
    import app.__main__ as main
    from app.infra import KST

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 23, 8, tzinfo=KST).astimezone(tz)

    monkeypatch.setattr(main, "datetime", FakeDatetime)
    _seed()
    runner.invoke(cli, ["classify"])
    result = runner.invoke(cli, ["report", "--dry-run"])
    assert result.exit_code == 0 and f"report-{WEEK}.html" in result.output


def test_rehearsal_seed_classify_report_clear(env, tmp_path):
    from app.infra.sample_data import REHEARSAL_WEEK

    _seed()  # 실제 이벤트 1건 — 리허설이 건드리면 안 된다
    assert "12건 저장" in runner.invoke(cli, ["seed"]).output
    assert "12건 저장" in runner.invoke(cli, ["seed"]).output  # 다시 넣어도 중복되지 않는다
    assert len(repo.events_of_week(REHEARSAL_WEEK)) == 12
    runner.invoke(cli, ["classify"])
    assert runner.invoke(cli, ["report", "--week", REHEARSAL_WEEK]).exit_code == 0
    assert len(env["slack"]) == 1 and repo.report_sent_at(REHEARSAL_WEEK)

    assert "12건 삭제" in runner.invoke(cli, ["seed", "--clear"]).output
    assert repo.events_of_week(REHEARSAL_WEEK) == [] and repo.report_sent_at(REHEARSAL_WEEK) is None
    assert repo.last_report_tldr(WEEK) is None  # 리허설 리포트가 '지난주 요약'으로 새지 않는다
    assert len(repo.events_of_week(WEEK)) == 1
