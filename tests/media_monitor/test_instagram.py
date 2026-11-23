"""[B] 인스타그램 수집기 테스트 — 인터넷·토큰 없이 가짜 응답으로."""
from datetime import datetime, timezone

import httpx
import pytest

from app.core.models import Brand, SourceType
from app.media_monitor.sns import instagram
from app.media_monitor.sns.instagram import InstagramCollector, _to_datetime

# business_discovery 응답 모양 그대로
FAKE = {"business_discovery": {"media": {"data": [
    {"id": "p1", "caption": "#EVENT 팔로워 9만 감사 이벤트\n어느새 9만명!", "like_count": 347,
     "comments_count": 293, "media_type": "VIDEO", "permalink": "https://www.instagram.com/reel/p1/",
     "thumbnail_url": "https://cdn.example.com/p1_thumb.jpg", "timestamp": "2026-11-21T15:12:21+0000"},
    {"id": "p2", "comments_count": 0, "media_type": "IMAGE",  # 캡션 없음 + 좋아요 숨김
     "permalink": "https://www.instagram.com/p/p2/", "media_url": "https://cdn.example.com/p2.jpg",
     "timestamp": "2026-11-20T09:00:00+0000"},
]}}}


def _collector(username="spao_kr"):
    c = InstagramCollector(Brand.SPAO, {"instagram": username})
    c._get = lambda fields: FAKE  # 진짜 인스타 대신 가짜 응답
    return c


def test_posts_become_raw_items_with_metrics():
    items = _collector().collect()
    assert len(items) == 2
    p1 = items[0]
    assert p1.source_type == SourceType.SNS_POST
    assert p1.external_id == "p1"
    assert p1.url == "https://www.instagram.com/reel/p1/"
    assert p1.title == "#EVENT 팔로워 9만 감사 이벤트"  # 캡션 첫 줄
    assert p1.metrics == {"likes": 347, "comments": 293}
    assert p1.image_url == "https://cdn.example.com/p1_thumb.jpg"  # 영상은 썸네일


def test_missing_caption_and_hidden_likes():
    p2 = _collector().collect()[1]
    assert p2.title == "(캡션 없음 · IMAGE)"
    assert p2.body is None
    assert p2.metrics == {"comments": 0}  # 좋아요 숨김 → 0이 아니라 칸 자체가 없음 (댓글 0은 진짜 0)
    assert p2.image_url == "https://cdn.example.com/p2.jpg"


def test_empty_username_is_skipped():
    assert _collector(username="").collect() == []


def test_title_skips_blank_braille_lines():
    c = InstagramCollector(Brand.UNIQLO, {"instagram": "uniqlokr"})
    post = dict(FAKE["business_discovery"]["media"]["data"][0], caption="⠀\n\u200b\n유니클로 감사제 오픈")
    c._get = lambda fields: {"business_discovery": {"media": {"data": [post]}}}
    assert c.collect()[0].title == "유니클로 감사제 오픈"


# ── 진짜 _get 을 쓰되 인터넷 대신 정해 둔 응답 ─────────────────────────────
TOKEN = "SECRET_TOKEN_123"


def _real_get(monkeypatch, response: httpx.Response):
    sent = []

    def fake_get(url, params=None, headers=None, timeout=None):
        request = httpx.Request("GET", url, params=params, headers=headers)
        sent.append(request)
        response.request = request
        return response

    monkeypatch.setenv("IG_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("IG_BUSINESS_ACCOUNT_ID", "1784")
    monkeypatch.setattr(instagram.httpx, "get", fake_get)
    c = InstagramCollector(Brand.SPAO, {"instagram": "spao_kr"})
    c.request_interval_sec = 0
    return c, sent


def test_token_goes_in_header_not_url(monkeypatch):
    c, sent = _real_get(monkeypatch, httpx.Response(200, json=FAKE))
    assert len(c.collect()) == 2
    assert TOKEN not in str(sent[0].url)
    assert sent[0].headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("response", [
    httpx.Response(400, json={"error": {"message": "Invalid OAuth access token"}}),  # Meta JSON 오류
    httpx.Response(502, text="<html>Bad Gateway</html>"),                           # JSON 이 아닌 오류
    httpx.Response(200, text="<html>점검 중</html>"),                                # 200 인데 JSON 아님
    httpx.Response(200, json={"id": "1784"}),                                       # 계정 정보 빠짐
])
def test_odd_responses_give_clear_error_without_token(monkeypatch, response):
    c, _ = _real_get(monkeypatch, response)
    with pytest.raises(RuntimeError, match="Instagram API 오류") as e:
        c.collect()
    assert TOKEN not in str(e.value)


def test_timestamp_becomes_local_datetime():
    # 인스타는 '+0000'(콜론 없음) 형식 → 로컬 시간대의 naive datetime 으로 (collected_at 과 형식 맞춤)
    got = _to_datetime("2026-11-21T15:12:21+0000")
    expected = datetime(2026, 11, 21, 15, 12, 21, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert got == expected
    assert got.tzinfo is None
    assert _to_datetime(None) is None
