"""[D] 슬랙·메일 발송 테스트 — 네트워크 없이 httpx.post / smtplib.SMTP를 가짜로 바꿔 검증."""
import httpx
import pytest

from app.core.models import Brand, BrandSummary, ChangeEvent, ChangeType, WeeklyReport
from app.infra.notify import email, slack

REPORT_URL = "https://hooks.slack.test/report"
OPS_URL = "https://hooks.slack.test/ops"


class _Resp:
    def __init__(self, status: int = 200):
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


@pytest.fixture
def posts(monkeypatch):
    """httpx.post 호출 기록. posts.fail_on 에 넣은 호출 순번(0부터)은 500 응답."""
    class Calls(list):
        pass

    calls = Calls()
    calls.fail_on = set()

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _Resp(500 if len(calls) - 1 in calls.fail_on else 200)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("SLACK_WEBHOOK_REPORT", REPORT_URL)
    monkeypatch.setenv("SLACK_WEBHOOK_OPS", OPS_URL)
    return calls


def _event(i: int = 1) -> ChangeEvent:
    return ChangeEvent(id=i, brand=Brand.SPAO, change_type=ChangeType.PRICE_DOWN, title=f"이벤트 {i}",
                       url=f"https://example.com/{i}", importance=5, llm_note="메모")


def _report() -> WeeklyReport:
    return WeeklyReport(
        week="2026-W47", tldr="요약", top_events=[_event()],
        brand_summaries=[
            BrandSummary(brand=Brand.SPAO, highlights=["스파오 하이라이트 [#1]"], event_ids=[1]),
            BrandSummary(brand=Brand.EIGHT_SECONDS, highlights=[], event_ids=[]),
            BrandSummary(brand=Brand.UNIQLO, highlights=["유니클로 하이라이트 [#2]"], event_ids=[2]),
        ],
        insights=["인사이트"], actions=["액션"])


def _section_text(payload: dict) -> str:
    return "\n".join(b["text"]["text"] for b in payload["blocks"] if b["type"] == "section")


# ---------------------------------------------------------------- 슬랙

def test_send_blocks_posts_payload(posts):
    slack.send_blocks([{"type": "divider"}], fallback_text="대체 텍스트")
    assert posts == [{"url": REPORT_URL, "json": {"text": "대체 텍스트", "blocks": [{"type": "divider"}]},
                      "timeout": 10}]


def test_send_blocks_requires_webhook(posts, monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_REPORT", "")
    with pytest.raises(RuntimeError, match="SLACK_WEBHOOK_REPORT"):
        slack.send_blocks([], fallback_text="x")
    assert posts == []


def test_send_report_sends_main_then_brand_followups(posts):
    slack.send_report(_report(), full_report_url="https://example.com/report")
    assert [p["url"] for p in posts] == [REPORT_URL] * 3  # 본 메시지 + 하이라이트 있는 브랜드 2개
    main, spao, uniqlo = (p["json"] for p in posts)
    assert main["blocks"][0]["type"] == "header" and main["blocks"][-1]["type"] == "actions"
    assert main["blocks"][-1]["elements"][0]["url"] == "https://example.com/report"
    assert "2026-W47" in main["text"] and "요약" in main["text"]
    assert "[스파오]" in _section_text(spao) and "스파오 하이라이트 [#1]" in _section_text(spao)
    assert "[유니클로]" in _section_text(uniqlo)
    assert all(p["json"]["text"] for p in posts)


def test_send_report_without_url_or_highlights(posts):
    r = _report()
    r.brand_summaries = []
    slack.send_report(r)
    assert len(posts) == 1
    assert all(b["type"] != "actions" for b in posts[0]["json"]["blocks"])


def test_send_report_propagates_failure(posts):
    posts.fail_on = {0}
    with pytest.raises(httpx.HTTPError):
        slack.send_report(_report())
    assert len(posts) == 1  # 본 메시지가 실패하면 후속 메시지는 보내지 않는다


def test_send_urgent_sends_each_event(posts):
    assert [e.id for e in slack.send_urgent([_event(1), _event(2)])] == [1, 2]
    assert [p["url"] for p in posts] == [REPORT_URL, REPORT_URL]
    assert "긴급 알림" in posts[0]["json"]["blocks"][0]["text"]["text"]
    assert "<https://example.com/2|이벤트 2>" in _section_text(posts[1]["json"])
    assert "이벤트 1" in posts[0]["json"]["text"]


def test_send_urgent_without_webhook_sends_nothing(posts, monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_REPORT")
    assert slack.send_urgent([_event()]) == []
    assert posts == []


def test_send_urgent_empty_list(posts):
    assert slack.send_urgent([]) == [] and posts == []


def test_send_urgent_continues_after_failure(posts, capsys):
    posts.fail_on = {1}
    assert [e.id for e in slack.send_urgent([_event(1), _event(2), _event(3)])] == [1, 3]  # 실패한 2번은 빠진다
    assert len(posts) == 3
    assert "id=2" in capsys.readouterr().err


def test_send_urgent_survives_transport_error(monkeypatch, posts):
    def boom(*a, **kw):
        raise httpx.ConnectError("연결 실패")

    monkeypatch.setattr(httpx, "post", boom)
    assert slack.send_urgent([_event()]) == []


def test_send_ops_alert_never_raises(monkeypatch, posts):
    slack.send_ops_alert("수집 0건")
    assert posts[0]["url"] == OPS_URL and "수집 0건" in posts[0]["json"]["text"]

    def boom(*a, **kw):
        raise httpx.ConnectError("연결 실패")

    monkeypatch.setattr(httpx, "post", boom)
    slack.send_ops_alert("수집 0건")
    monkeypatch.delenv("SLACK_WEBHOOK_OPS")
    slack.send_ops_alert("수집 0건")


# ---------------------------------------------------------------- 메일

@pytest.fixture
def smtp(monkeypatch):
    """smtplib.SMTP 대역. 보낸 메시지와 호출 순서를 기록한다."""
    log: dict = {"calls": [], "messages": []}

    class FakeSMTP:
        def __init__(self, host, port):
            log["addr"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            log["calls"].append("starttls")

        def login(self, user, password):
            log["calls"].append(("login", user, password))

        def send_message(self, msg):
            log["calls"].append("send_message")
            log["messages"].append(msg)

    monkeypatch.setattr(email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("MAIL_TO", "a@example.com, b@example.com,")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    return log


def test_send_html_only(smtp):
    email.send_html("[경쟁사 주간 리포트] 2026-W47", "<p>안녕하세요</p>")
    msg = smtp["messages"][0]
    assert smtp["addr"] == ("smtp.example.com", 2525)
    assert smtp["calls"] == ["starttls", ("login", "bot@example.com", "pw"), "send_message"]
    assert msg["Subject"] == "[경쟁사 주간 리포트] 2026-W47"
    assert msg["From"] == "bot@example.com" and msg["To"] == "a@example.com, b@example.com"
    assert not msg.is_multipart() and msg.get_content_type() == "text/html"
    assert msg.get_content().strip() == "<p>안녕하세요</p>"
    assert "utf-8" in msg["Content-Type"].lower()


def test_send_html_with_text_is_multipart_alternative(smtp):
    email.send_html("제목", "<p>본문</p>", text="# 본문")
    msg = smtp["messages"][0]
    assert msg.get_content_type() == "multipart/alternative"
    plain, html = msg.get_payload()  # 순서: plain 먼저, html 나중(클라이언트는 마지막 파트를 우선)
    assert plain.get_content_type() == "text/plain" and plain.get_content().strip() == "# 본문"
    assert html.get_content_type() == "text/html" and html.get_content().strip() == "<p>본문</p>"
    assert msg.as_bytes()  # 실제 전송 가능한 형태로 직렬화된다


@pytest.mark.parametrize("missing", ["SMTP_USER", "SMTP_PASSWORD", "MAIL_TO"])
def test_send_html_reports_missing_settings(smtp, monkeypatch, missing):
    monkeypatch.setenv(missing, "")
    with pytest.raises(RuntimeError, match=missing):
        email.send_html("제목", "<p>본문</p>")
    assert smtp["messages"] == [] and "addr" not in smtp


def test_send_html_lists_all_missing_settings(smtp, monkeypatch):
    for key in ("SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("MAIL_TO", " , ")
    with pytest.raises(RuntimeError, match="SMTP_USER, SMTP_PASSWORD, MAIL_TO"):
        email.send_html("제목", "<p>본문</p>")


# ---------- 비밀값이 경고·로그로 새지 않는다 ----------
def _status_error(url: str, status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", url)
    try:
        httpx.Response(status, request=request).raise_for_status()
    except httpx.HTTPStatusError as e:
        return e
    raise AssertionError("오류가 나야 한다")


def test_describe_error_hides_webhook_url():
    e = _status_error("https://hooks.slack.com/services/T000/B000/SECRETTOKEN", 404)
    assert "SECRETTOKEN" in repr(e)  # httpx 예외는 주소를 통째로 담는다
    assert slack.describe_error(e) == "HTTPStatusError (HTTP 404)"


def test_describe_error_hides_urls_in_any_message():
    e = RuntimeError("could not connect to postgresql://user:pw123@db.example.com:5432/app "
                     "nor 'https://www.googleapis.com/youtube/v3/channels?id=x&key=APIKEY'")
    text = slack.describe_error(e)
    assert "pw123" not in text and "APIKEY" not in text and "db.example.com" not in text
    assert text.startswith("RuntimeError: could not connect to [주소 가림]")


def test_describe_error_keeps_plain_messages():
    assert slack.describe_error(RuntimeError("MAIL_TO 가 .env 에 없습니다")) == "RuntimeError: MAIL_TO 가 .env 에 없습니다"
    assert slack.describe_error(KeyError()) == "KeyError"


def test_urgent_failure_log_has_no_webhook_url(monkeypatch, capsys):
    monkeypatch.setenv("SLACK_WEBHOOK_REPORT", "https://hooks.slack.com/services/T000/B000/SECRETTOKEN")

    def post(url, json=None, timeout=None):
        return httpx.Response(404, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert slack.send_urgent([_event()]) == []
    err = capsys.readouterr().err
    assert "HTTP 404" in err and "SECRETTOKEN" not in err and "hooks.slack.com" not in err


def test_send_ops_alert_survives_malformed_webhook_url(monkeypatch, capsys):
    monkeypatch.setenv("SLACK_WEBHOOK_OPS", "https://hooks.slack.test/ops\n")  # Secret 끝의 줄바꿈
    seen = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: seen.append(url) or _Resp())
    slack.send_ops_alert("수집 0건")
    assert seen == ["https://hooks.slack.test/ops"]

    def invalid(url, **kw):
        raise httpx.InvalidURL("Invalid non-printable ASCII character in URL")  # HTTPError 의 하위가 아니다

    monkeypatch.setattr(httpx, "post", invalid)
    slack.send_ops_alert("수집 0건")  # 예외가 밖으로 나오지 않는다
    assert "운영 경고 발송 실패" in capsys.readouterr().err


def test_send_ops_alert_logs_rejected_alert(posts, capsys):
    posts.fail_on = {0}  # 슬랙이 500 으로 거절
    slack.send_ops_alert("수집 0건")
    assert "운영 경고 발송 실패" in capsys.readouterr().err


def test_send_report_reports_progress_and_can_resume(posts):
    sent: list[int] = []
    posts.fail_on = {1}  # 본 메시지는 성공, 첫 후속 메시지에서 실패
    with pytest.raises(httpx.HTTPError):
        slack.send_report(_report(), on_sent=sent.append)
    assert sent == [0]

    posts.fail_on = set()
    before = len(posts)
    slack.send_report(_report(), start=len(sent), on_sent=sent.append)
    resumed = posts[before:]
    assert sent[:2] == [0, 1] and all("브랜드별 요약" in p["json"]["text"] for p in resumed)  # 본 메시지 없음
