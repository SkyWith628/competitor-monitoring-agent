"""[B] 유튜브 수집기 테스트 — 인터넷·API 키 없이 가짜 응답으로."""
from app.core.models import Brand, SourceType
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


def test_hidden_like_count_becomes_zero():
    v2 = _collector().collect()[1]
    assert v2.metrics["likes"] == 0
    assert v2.image_url is None


def test_empty_channel_id_is_skipped():
    assert _collector(channel_id="").collect() == []
