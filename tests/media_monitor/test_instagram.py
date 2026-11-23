"""[B] 인스타그램 수집기 테스트 — 인터넷·토큰 없이 가짜 응답으로."""
from app.core.models import Brand, SourceType
from app.media_monitor.sns.instagram import InstagramCollector

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
    assert p2.metrics["likes"] == 0
    assert p2.image_url == "https://cdn.example.com/p2.jpg"


def test_empty_username_is_skipped():
    assert _collector(username="").collect() == []


def test_title_skips_blank_braille_lines():
    c = InstagramCollector(Brand.UNIQLO, {"instagram": "uniqlokr"})
    post = dict(FAKE["business_discovery"]["media"]["data"][0], caption="⠀\n\u200b\n유니클로 감사제 오픈")
    c._get = lambda fields: {"business_discovery": {"media": {"data": [post]}}}
    assert c.collect()[0].title == "유니클로 감사제 오픈"
