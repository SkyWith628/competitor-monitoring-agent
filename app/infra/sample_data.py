"""발송 리허설용 샘플 이벤트. 실제 수집 데이터와 섞이지 않게 과거 주차(REHEARSAL_WEEK)에만 넣는다. 담당: D"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.core.models import Brand, ChangeEvent, ChangeType
from app.infra.storage import repo

REHEARSAL_WEEK = "2020-W01"
SAMPLE_URL = "https://example.com/sample/"

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
