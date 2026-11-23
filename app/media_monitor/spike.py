"""SNS 반응 급증 감지. 담당: B

조회수·좋아요·댓글을 **따로** 본다.
- 급증 = 같은 계정 최근 게시물들의 그 지표 **최고 기록을 넘었을 때**
  + 지난번 수집값이 있으면 그 사이 **충분히 늘었을 때만** (조회수는 계속 쌓이기만 해서,
    오래된 영상이 하루 +20 으로 기록을 살짝 넘는 것까지 급증으로 잡지 않으려고)
  (브랜드 게시물은 '반응 거의 없는 글'과 '광고·이벤트로 크게 터지는 글'로 나뉘어서,
   '평소의 2배' 기준으로는 실데이터에서 게시물의 40%가 잡혔다 → 최고 기록 기준은 약 8%)
- 설명용 평소 값 = 다른 게시물들의 중앙값 (튀는 게시물에 끌려가지 않게 평균 대신 중앙값)
- detail 에 어느 지표가 평소 몇 → 지금 몇(몇 배), 이전 최고 몇인지와 추정 이유를 남긴다
"""
from __future__ import annotations

import re
from statistics import median

from app.core.models import ChangeEvent, ChangeType, RawItem

MIN_HISTORY = 5  # 비교할 다른 게시물이 이보다 적으면 '평소'를 모르니 판단하지 않음
# 평소가 아주 작을 때 2 → 5 같은 잡음을 거르는 최소 수치
MIN_COUNT = {"views": 1000, "likes": 50, "comments": 10}
# 지난번 수집(보통 하루 전) 이후 최소 이만큼은 늘어야 급증 (천천히 쌓여 기록을 넘은 것 거르기)
MIN_JUMP = {"views": 1000, "likes": 50, "comments": 10}
METRIC_KO = {"views": "조회수", "likes": "좋아요", "comments": "댓글"}
# 한글은 부분 일치('이벤트' ⊂ '감사이벤트'), 영어는 단어 단위('prevent'의 event 는 제외, '#EVENT'는 포함)
# (앞뒤에 영어 글자만 없으면 됨 → 'EVENT참여', '#giveaway' 도 잡힘)
GIVEAWAY_WORDS = re.compile(r"이벤트|추첨|경품|(?<![a-z])(?:events?|giveaways?)(?![a-z])", re.IGNORECASE)


def detect_spikes(recent: list[RawItem], history: list[RawItem]) -> list[ChangeEvent]:
    """recent: 이번에 수집한 게시물 / history: 같은 계정의 지난번 저장 상태 (최근 30개)."""
    events = []
    for item in recent:
        others = [h for h in history if h.external_id != item.external_id]
        before = next((h for h in history if h.external_id == item.external_id), None)
        spikes = {}
        for metric, value in item.metrics.items():
            values = [h.metrics[metric] for h in others if metric in h.metrics]
            if len(values) < MIN_HISTORY:
                continue
            record = max(values)
            if value <= record or value < MIN_COUNT.get(metric, 0):
                continue
            previous = before.metrics.get(metric) if before else None
            if previous is not None and previous > record:
                continue  # 지난번에 이미 최고 기록으로 알렸으면 매일 반복해서 알리지 않음
            if previous is not None and value - previous < MIN_JUMP.get(metric, 0):
                continue  # 지난번보다 조금만 늘었음 → 천천히 쌓여 기록을 넘은 것
            usual = median(values)
            spikes[metric] = {"value": value, "usual": round(usual), "record": record,
                              "ratio": round(value / usual, 1) if usual else None}
        if spikes:
            events.append(ChangeEvent(
                brand=item.brand, change_type=ChangeType.SNS_SPIKE, title=item.title, url=item.url,
                detail={"source": item.source, "spikes": spikes, "reason": _reason(item, spikes, others)},
            ))
    return events


def _reason(item: RawItem, spikes: dict, others: list[RawItem]) -> str:
    """'좋아요 평소 120 → 480 (4.0배, 이전 최고 300)' + 추정 이유 한 줄."""
    summary = ", ".join(
        f"{METRIC_KO.get(m, m)} 평소 {s['usual']:,} → {s['value']:,} "
        f"({str(s['ratio']) + '배, ' if s['ratio'] else ''}이전 최고 {s['record']:,})"
        for m, s in spikes.items())
    text = f"{item.title} {item.body or ''}"
    if "comments" in spikes and GIVEAWAY_WORDS.search(text):
        return f"{summary} · 추정: 댓글 참여 이벤트 게시물이라 댓글이 몰림"
    if "views" in spikes:
        likes = [h.metrics["likes"] for h in others if "likes" in h.metrics]
        if likes and item.metrics.get("likes", 0) < median(likes) * 1.5:
            return f"{summary} · 추정: 조회수만 늘고 좋아요는 평소 수준 → 광고(유료 노출) 가능성"
    return summary
