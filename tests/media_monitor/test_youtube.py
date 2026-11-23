"""[B] 유튜브 수집기 테스트 — 인터넷·API 키 없이 가짜 응답으로."""
import httpx
import pytest

from app.core.models import Brand, SourceType
from app.media_monitor.sns import youtube
from app.media_monitor.sns.youtube import YoutubeCollector

# YouTube Data API 응답 모양 그대로 (endpoint 별)
FAKE = {
    "channels": {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU_spao"}}}]},
    "playlistItems": {"items": [{"contentDetails": {"videoId": "v1"}},
                                {"contentDetails": {"videoId": "v2"}}]},
    "videos": {"items": [
        {"id": "v1",
         "snippet": {"title": "스파오 겨울 파자마 룩북", "publishedAt": "2026-11-20T09:00:00Z",
                     "thumbnails": {"high": {"url": "https://i.ytimg.com/v1.jpg"}}},
         "statistics": {"viewCount": "12000", "likeCount": "340", "commentCount": "21"}},
        {"id": "v2",  # 좋아요 수를 숨긴 영상 → likeCount 없음
         "snippet": {"title": "스파오 x 먼작귀 콜라보", "publishedAt": "2026-11-18T09:00:00Z"},
         "statistics": {"viewCount": "5000", "commentCount": "3"}},
    ]},
}


def _collector(channel_id="UC_spao"):
    c = YoutubeCollector(Brand.SPAO, {"youtube_channel_id": channel_id})
    c._get = lambda endpoint, params: FAKE[endpoint]  # 진짜 유튜브 대신 가짜 응답
    return c


def test_videos_become_raw_items_with_metrics():
    items = _collector().collect()
    assert len(items) == 2
    v1 = items[0]
    assert v1.source_type == SourceType.SNS_POST
    assert v1.external_id == "v1"
    assert v1.url == "https://www.youtube.com/watch?v=v1"
    assert v1.metrics == {"views": 12000, "likes": 340, "comments": 21}
    assert v1.published_at.day == 20


def test_hidden_like_count_is_left_empty():
    # 좋아요를 숨긴 영상은 0이 아니라 '모름' → likes 칸을 비워 둔다
    v2 = _collector().collect()[1]
    assert v2.metrics == {"views": 5000, "comments": 3}
    assert v2.image_url is None


def test_empty_channel_id_is_skipped():
    assert _collector(channel_id="").collect() == []


@pytest.mark.parametrize("channels", [{"items": []}, {}])  # 빈 결과에서 items 키가 빠져도 같은 오류
def test_unknown_channel_raises_clear_error(channels):
    c = YoutubeCollector(Brand.SPAO, {"youtube_channel_id": "UC_none"})
    c._get = lambda endpoint, params: channels
    with pytest.raises(RuntimeError, match="유튜브 채널을 찾을 수 없습니다"):
        c.collect()


SECRET = "SECRET_KEY_123"


def _real_get(monkeypatch, response: httpx.Response):
    """진짜 _get 을 쓰되 인터넷 대신 정해 둔 응답을 돌려준다. 보낸 요청을 기록한다."""
    sent = []

    def fake_get(url, params=None, headers=None, timeout=None):
        request = httpx.Request("GET", url, params=params, headers=headers)
        sent.append(request)
        response.request = request
        return response

    monkeypatch.setenv("YOUTUBE_API_KEY", SECRET)
    monkeypatch.setattr(youtube.httpx, "get", fake_get)
    c = YoutubeCollector(Brand.SPAO, {"youtube_channel_id": "UC_spao"})
    c.request_interval_sec = 0
    return c, sent


def test_api_key_goes_in_header_not_url(monkeypatch):
    c, sent = _real_get(monkeypatch, httpx.Response(200, json={"items": []}))
    c._get("channels", {"part": "contentDetails", "id": "UC_spao"})
    assert SECRET not in str(sent[0].url)
    assert sent[0].headers["X-Goog-Api-Key"] == SECRET


@pytest.mark.parametrize("response", [
    httpx.Response(403, json={"error": {"message": "quota exceeded"}}),  # Google JSON 오류
    httpx.Response(502, text="<html>Bad Gateway</html>"),                # JSON 이 아닌 오류
])
def test_error_message_never_contains_api_key(monkeypatch, response):
    c, _ = _real_get(monkeypatch, response)
    with pytest.raises(RuntimeError, match="YouTube API 오류") as e:
        c._get("channels", {"id": "UC_spao"})
    assert SECRET not in str(e.value)
