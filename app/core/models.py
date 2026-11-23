"""모듈 간 데이터 계약 — 이 파일을 바꾸면 A·B·C·D 모두 영향을 받는다.

흐름: 수집기(A,B) → RawItem → 변화감지(A,B) → ChangeEvent → 분석(C) → Report → 발송(D)
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Brand(str, Enum):
    SPAO = "spao"
    EIGHT_SECONDS = "8seconds"
    MUSINSA_STANDARD = "musinsa_standard"
    UNIQLO = "uniqlo"
    WHOAU = "whoau"  # 학습용 직접 수집 대상 (robots.txt 허용, 카페24)


class SourceType(str, Enum):
    SITE_PRODUCT = "site_product"  # 상품 1개
    SITE_PAGE = "site_page"  # 메인/기획전 페이지 스냅샷
    NEWS = "news"
    SNS_POST = "sns_post"


class RawItem(BaseModel):
    """수집기가 내보내는 단일 항목. (brand, source, external_id)가 유니크 키."""

    brand: Brand
    source_type: SourceType
    source: str  # "spao.com", "naver_news", "instagram" ...
    external_id: str  # 상품코드 / 기사 URL 해시 / 게시물 ID
    url: str
    title: str
    body: str | None = None
    price: int | None = None  # 원 단위
    sale_price: int | None = None
    in_stock: bool | None = None
    metrics: dict[str, int] = Field(default_factory=dict)  # {"likes": 1200, "comments": 35}
    image_url: str | None = None
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=datetime.now)


class ChangeType(str, Enum):
    NEW_PRODUCT = "new_product"
    PRICE_DOWN = "price_down"
    PRICE_UP = "price_up"
    SOLD_OUT = "sold_out"
    RESTOCK = "restock"
    PROMO_CHANGED = "promo_changed"
    NEWS = "news"
    SNS_SPIKE = "sns_spike"
    SNS_NEW_POST = "sns_new_post"


class Category(str, Enum):
    PRICING = "가격전략"
    PRODUCT = "신제품"
    MARKETING = "마케팅"
    RETAIL = "매장·유통"
    BUSINESS = "실적·경영"
    COLLAB = "협업"
    IRRELEVANT = "무관"


class ChangeEvent(BaseModel):
    """변화 감지 결과. 앞부분은 A·B가, 뒷부분(category~)은 C가 채운다."""

    id: int | None = None
    brand: Brand
    change_type: ChangeType
    title: str
    detail: dict = Field(default_factory=dict)  # {"before": 39900, "after": 29900}
    url: str
    detected_at: datetime = Field(default_factory=datetime.now)
    # --- C(분석)가 채움 ---
    category: Category | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    llm_note: str | None = None


class BrandSummary(BaseModel):
    brand: Brand
    highlights: list[str]  # 각 문장 끝에 [#event_id] 인용
    event_ids: list[int]


class WeeklyReport(BaseModel):
    """C가 만들고 D가 렌더링·발송한다."""

    week: str  # "2026-W47"
    tldr: str
    top_events: list[ChangeEvent]
    brand_summaries: list[BrandSummary]
    insights: list[str]
    actions: list[str]
    stats: dict = Field(default_factory=dict)  # 신상품 수, 평균 할인율 등 (코드로 계산)
