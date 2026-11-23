"""샘플 데이터. 담당: D

두 가지 용도
    1. 발송 리허설 (seed / clear): 과거 주차(REHEARSAL_WEEK)에만 미분류 이벤트를 넣는다. 실제 주차와 섞이지 않는다.
    2. 화면 확인용 데모 (seed_demo / clear_demo): 최근 30일에 분류까지 끝난 이벤트와 수집 항목·리포트·LLM 사용량을
       넣어, 수집기가 붙기 전에도 웹 대시보드가 어떻게 보이는지 확인한다. **실제 데이터와 같은 기간에 섞이므로**
       운영을 시작하기 전에 반드시 clear_demo 로 지운다. (지우기 전에는 report 명령이 그 주차 리포트를 거부한다)

샘플의 표식 — 이것으로만 골라 지우므로 실제 데이터는 건드리지 않는다
    이벤트: url 이 SAMPLE_URL 로 시작, 제목에 [샘플] / 수집 항목: source 가 "sample-…" / 리포트: tldr 이 [샘플] 로 시작
    / LLM 호출: model 이 "sample-…"
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from app.core.models import (
    Brand,
    BrandSummary,
    Category,
    ChangeEvent,
    ChangeType,
    RawItem,
    SourceType,
    WeeklyReport,
)
from app.infra.storage import repo

REHEARSAL_WEEK = "2020-W01"
SAMPLE_URL = "https://example.com/sample/"
SAMPLE_TAG = "[샘플]"
DEMO_DAYS = 30

_SAMPLES: list[tuple[Brand, ChangeType, str, dict]] = [
    (Brand.SPAO, ChangeType.PRICE_DOWN, "웜테크 발열내의 U넥", {"before": 15900, "after": 9900}),
    (Brand.SPAO, ChangeType.PRICE_DOWN, "베이직 푸퍼 패딩", {"before": 79900, "after": 59900}),
    (Brand.SPAO, ChangeType.PROMO_CHANGED, "메인 배너: 겨울 아우터 최대 40% 세일", {"before": "가을 신상", "after": "윈터 세일"}),
    (Brand.EIGHT_SECONDS, ChangeType.NEW_PRODUCT, "울 블렌드 발마칸 코트", {"price": 199000}),
    (Brand.EIGHT_SECONDS, ChangeType.NEWS, "에잇세컨즈, 성수동에 팝업스토어 오픈", {"source": "naver_news"}),
    (Brand.EIGHT_SECONDS, ChangeType.SNS_SPIKE, "아이돌 협업 화보 공개", {"engagement": 48200, "baseline": 9100}),
    (Brand.MUSINSA_STANDARD, ChangeType.NEWS, "무신사 스탠다드, 오프라인 매장 30호점 돌파", {"source": "google_news"}),
    (Brand.MUSINSA_STANDARD, ChangeType.NEW_PRODUCT, "시티 레저 경량 다운 재킷", {"price": 89900}),
    (Brand.MUSINSA_STANDARD, ChangeType.SOLD_OUT, "캐시미어 블렌드 머플러", {}),
    (Brand.UNIQLO, ChangeType.PRICE_DOWN, "히트텍 크루넥 T", {"before": 19900, "after": 14900}),
    (Brand.UNIQLO, ChangeType.NEWS, "유니클로, 디자이너 협업 컬렉션 내달 출시", {"source": "naver_news"}),
    (Brand.UNIQLO, ChangeType.SNS_NEW_POST, "감사제 예고 영상", {}),
]


def is_sample(event: ChangeEvent) -> bool:
    return event.url.startswith(SAMPLE_URL)


def seed() -> int:
    """샘플 이벤트를 미분류 상태로 저장한다. 이미 있던 샘플은 지우고 다시 넣는다."""
    clear()
    year, week = REHEARSAL_WEEK.split("-W")
    # 이 프로젝트의 감지 시각은 시간대 없는 한국 시각이다 (app/infra/timeutil.py)
    monday = datetime.combine(date.fromisocalendar(int(year), int(week), 1), time(6))
    events = [
        ChangeEvent(brand=brand, change_type=change_type, title=f"[샘플] {title}", detail=detail,
                    url=f"{SAMPLE_URL}{n}", detected_at=monday + timedelta(days=n % 7))
        for n, (brand, change_type, title, detail) in enumerate(_SAMPLES)
    ]
    repo.save_events(events)
    return len(events)


def clear() -> int:
    """샘플 이벤트와 리허설 주차의 리포트·발송 기록을 지운다. 삭제한 이벤트 수 반환."""
    ids = [e.id for e in repo.events_of_week(REHEARSAL_WEEK) if is_sample(e)]
    repo.delete_events(ids)
    repo.delete_report(REHEARSAL_WEEK)
    return len(ids)


# ---------- 화면 확인용 데모 ----------
_NOTES = {
    ChangeType.PRICE_DOWN: (Category.PRICING, "시즌 초 가격 인하. 동일 품목 경쟁사 대비 가장 낮은 가격."),
    ChangeType.PROMO_CHANGED: (Category.MARKETING, "메인 프로모션이 시즌 세일로 교체됨."),
    ChangeType.NEW_PRODUCT: (Category.PRODUCT, "겨울 시즌 신상품 추가."),
    ChangeType.SOLD_OUT: (Category.PRODUCT, "주력 상품 품절 — 수요가 예상보다 높았던 것으로 보임."),
    ChangeType.NEWS: (Category.RETAIL, "오프라인 확장 관련 보도."),
    ChangeType.SNS_SPIKE: (Category.MARKETING, "평소 대비 반응이 크게 늘어난 게시물."),
    ChangeType.SNS_NEW_POST: (Category.MARKETING, "공식 계정 새 게시물."),
}
_DEMO_SOURCES = (  # (소스 이름, 종류, 브랜드당 항목 수)
    (f"{repo.SAMPLE_SOURCE_PREFIX}site", SourceType.SITE_PRODUCT, 6),
    (f"{repo.SAMPLE_SOURCE_PREFIX}news", SourceType.NEWS, 4),
    (f"{repo.SAMPLE_SOURCE_PREFIX}instagram", SourceType.SNS_POST, 3),
)


def _last_week(today: date) -> str:
    y, w, _ = (today - timedelta(days=7)).isocalendar()
    return f"{y}-W{w:02d}"


def seed_demo() -> dict[str, int]:
    """최근 30일에 화면 확인용 샘플을 넣는다. 다시 불러도 중복되지 않는다(먼저 지우고 넣는다).

    - 이벤트는 분류까지 끝난 상태로 넣는다 → daily 의 classify 가 샘플에 LLM 을 쓰지 않는다.
    - 중요도 5 샘플은 "긴급 알림 발송됨"으로 표시해 둔다 → 실제 슬랙 채널로 알림이 나가지 않는다.
    - 지난주에 실제 리포트가 이미 있으면 샘플 리포트는 넣지 않는다.
    """
    clear_demo()
    rng = random.Random(20261123)  # 실행할 때마다 같은 모양이 나오게 고정
    now = datetime.now()  # 이 프로젝트의 시각은 시간대 없는 한국 시각이다 (app/infra/timeutil.py)
    by_brand = {b: [s for s in _SAMPLES if s[0] == b] for b in Brand}

    events: list[ChangeEvent] = []
    for days_ago in range(DEMO_DAYS - 1, -1, -1):
        for brand in Brand:
            for _ in range(rng.randint(0, 4)):
                _, change_type, title, detail = rng.choice(by_brand[brand])
                category, note = _NOTES[change_type]
                importance = rng.choices([1, 2, 3, 4, 5], weights=[2, 4, 5, 3, 1])[0]
                at = (now - timedelta(days=days_ago)).replace(hour=6, minute=rng.randint(0, 59), second=0,
                                                              microsecond=0)
                events.append(ChangeEvent(
                    brand=brand, change_type=change_type, title=f"{SAMPLE_TAG} {title}", detail=detail,
                    url=f"{SAMPLE_URL}demo/{len(events)}", detected_at=min(at, now), category=category,
                    importance=importance, llm_note=note if importance >= 4 else None))
    repo.save_events(events)
    repo.mark_urgent_sent([e.id for e in events if e.importance == 5 and e.id is not None])

    items = [
        RawItem(brand=brand, source_type=source_type, source=source, external_id=f"demo-{n}",
                url=f"{SAMPLE_URL}demo/item/{brand.value}/{source}/{n}", title=f"{SAMPLE_TAG} 수집 항목 {n + 1}",
                collected_at=now)
        for brand in Brand for source, source_type, count in _DEMO_SOURCES for n in range(count)
    ]
    repo.save_items(items)

    for model, calls, tokens_in, tokens_out in ((f"{repo.SAMPLE_SOURCE_PREFIX}classify-model", 30, 2400, 180),
                                                (f"{repo.SAMPLE_SOURCE_PREFIX}report-model", 5, 9000, 1500)):
        for _ in range(calls):
            repo.save_llm_call(model, tokens_in, tokens_out)

    week = _last_week(now.date())
    wrote_report = repo.get_report(week) is None
    if wrote_report:
        in_week = [e for e in repo.events_of_week(week) if is_sample(e)]
        top = sorted(in_week, key=lambda e: e.importance or 0, reverse=True)[:5]
        summaries = []
        for brand in Brand:
            mine = [e for e in in_week if e.brand == brand][:2]
            summaries.append(BrandSummary(brand=brand, event_ids=[e.id for e in mine if e.id is not None],
                                          highlights=[f"{e.title.removeprefix(SAMPLE_TAG).strip()} [#{e.id}]"
                                                      for e in mine]))
        # 초안(sent=False)으로 저장한다: 실제로 발송된 적이 없으므로 화면에도 "미발송"으로 나온다
        repo.save_report(WeeklyReport(
            week=week, top_events=top, brand_summaries=summaries, stats={},
            tldr=f"{SAMPLE_TAG} 화면 확인용 예시 리포트입니다. 스파오가 발열내의 가격을 먼저 내렸고, 유니클로는 감사제 예고로 "
                 "반응을 끌어올렸습니다.",
            insights=["(예시) 겨울 시즌 가격 인하 시점이 전년보다 빨라졌습니다.",
                      "(예시) 오프라인 팝업이 SNS 반응을 끌어올리는 패턴이 반복됩니다."],
            actions=["(예시) 발열내의 가격 대응안 검토", "(예시) 12월 첫 주 팝업 일정 확인"]), sent=False)
    return {"events": len(events), "items": len(items), "reports": int(wrote_report)}


def clear_demo() -> dict[str, int]:
    """표식이 붙은 샘플(데모 + 리허설 잔여분)을 모두 지운다. 실제 데이터는 건드리지 않는다."""
    return repo.delete_sample_data(SAMPLE_URL, SAMPLE_TAG)


def has_samples_in_week(week: str) -> bool:
    return week != REHEARSAL_WEEK and any(is_sample(e) for e in repo.events_of_week(week))
