"""[B] SNS 반응 급증 감지 테스트."""
from app.core.models import Brand, RawItem, SourceType
from app.media_monitor.spike import detect_spikes


def _post(pid: str, title: str = "새 시즌 룩북", body: str | None = None, **metrics) -> RawItem:
    return RawItem(brand=Brand.SPAO, source_type=SourceType.SNS_POST, source="instagram",
                   external_id=pid, url=f"https://www.instagram.com/p/{pid}/", title=title, body=body,
                   metrics=metrics)


# 평소(중앙값) 좋아요 100·댓글 20, 최고 기록 좋아요 110·댓글 22 인 계정
HISTORY = [_post(f"old{n}", likes=90 + n * 5, comments=18 + n) for n in range(5)]


def test_likes_spike_reports_usual_and_now():
    events = detect_spikes([_post("new", likes=400, comments=21)], HISTORY)
    assert len(events) == 1
    spikes = events[0].detail["spikes"]
    assert spikes == {"likes": {"value": 400, "usual": 100, "record": 110, "ratio": 4.0}}  # 댓글은 기록 못 넘어 빠짐
    assert "좋아요 평소 100 → 400 (4.0배, 이전 최고 110)" in events[0].detail["reason"]


def test_below_record_is_not_spike():
    assert detect_spikes([_post("new", likes=105, comments=21)], HISTORY) == []


def test_hidden_likes_are_not_judged():
    # 좋아요를 숨긴 게시물은 수집기(인스타 #11, 유튜브 #5)가 likes 칸을 비워 둔다
    # → 비교할 좋아요 값이 없으니 좋아요는 판단하지 않고, 댓글로만 본다 (예전엔 '평소 0 → 300' 오탐)
    history = [_post(f"h{n}", comments=10) for n in range(5)]
    assert detect_spikes([_post("new", likes=300, comments=10)], history) == []


def test_giveaway_comments_get_reason():
    post = _post("new", title="#EVENT 댓글 남기고 추첨 참여", likes=110, comments=900)
    reason = detect_spikes([post], HISTORY)[0].detail["reason"]
    assert "이벤트" in reason


def test_views_only_spike_hints_ads():
    history = [_post(f"v{n}", views=10_000, likes=100) for n in range(5)]
    reason = detect_spikes([_post("ad", views=500_000, likes=120)], history)[0].detail["reason"]
    assert "광고" in reason


def test_already_alerted_post_is_not_repeated():
    yesterday = _post("new", likes=400, comments=21)  # 어제 이미 급증으로 알림
    events = detect_spikes([_post("new", likes=450, comments=21)], [*HISTORY, yesterday])
    assert events == []


def test_small_numbers_are_ignored():
    history = [_post(f"s{n}", likes=3, comments=1) for n in range(5)]
    assert detect_spikes([_post("new", likes=9, comments=4)], history) == []  # 기록은 넘었지만 수치가 너무 작음


def test_not_enough_history():
    assert detect_spikes([_post("new", likes=400)], HISTORY[:2]) == []


def test_slow_growth_past_record_is_not_spike():
    # 어제 105 → 오늘 112: 기록(110)은 넘었지만 하루에 7 늘었을 뿐 → 천천히 쌓인 것
    yesterday = _post("new", likes=105, comments=21)
    assert detect_spikes([_post("new", likes=112, comments=21)], [*HISTORY, yesterday]) == []


def test_slow_growth_views_example_from_review():
    # 리뷰 예: 최고 기록 15,000 채널에서 어제 14,990 → 오늘 15,010 (+20) 이면 급증 아님
    history = [_post(f"v{n}", views=12_500 + n * 500, likes=100) for n in range(5)]  # 최고 14,500
    history.append(_post("top", views=15_000, likes=100))
    yesterday = _post("old_video", views=14_990, likes=100)
    assert detect_spikes([_post("old_video", views=15_010, likes=100)], [*history, yesterday]) == []


def test_fast_growth_since_last_time_is_spike():
    # 어제 105 → 오늘 400: 기록도 넘고 하루에 295 늘었음 → 급증
    yesterday = _post("new", likes=105, comments=21)
    events = detect_spikes([_post("new", likes=400, comments=21)], [*HISTORY, yesterday])
    assert list(events[0].detail["spikes"]) == ["likes"]


def test_word_containing_event_is_not_giveaway():
    # 'prevent' 안의 event 는 이벤트 게시물 단서가 아님
    post = _post("new", title="How to prevent pilling", likes=110, comments=900)
    reason = detect_spikes([post], HISTORY)[0].detail["reason"]
    assert "이벤트" not in reason
