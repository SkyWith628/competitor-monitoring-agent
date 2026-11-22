"""DB → 정적 HTML 대시보드(단일 파일, 외부 리소스 없음). 담당: D

`python -m app dashboard` 가 build()를 호출하고, 결과 파일은 GitHub Actions 아티팩트로 올라간다.
render()는 DB를 모르는 순수 함수라 테스트는 여기에 샘플 데이터를 직접 넣는다.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.models import Brand, ChangeEvent, ChangeType, SourceType, WeeklyReport
from app.infra.notify.render import BRAND_LABEL

KST = ZoneInfo("Asia/Seoul")
WINDOW_DAYS = 30  # 카드·추이 차트가 보는 기간
HIGH_IMPORTANCE = 4  # 이 값 이상을 "주요 이벤트"로 본다
MAX_HIGH_EVENTS = 20

SOURCE_LABEL = {
    SourceType.SITE_PRODUCT.value: "상품",
    SourceType.SITE_PAGE.value: "페이지",
    SourceType.NEWS.value: "뉴스",
    SourceType.SNS_POST.value: "SNS",
}
CHANGE_LABEL = {
    ChangeType.NEW_PRODUCT: "신상품",
    ChangeType.PRICE_DOWN: "가격 인하",
    ChangeType.PRICE_UP: "가격 인상",
    ChangeType.SOLD_OUT: "품절",
    ChangeType.RESTOCK: "재입고",
    ChangeType.PROMO_CHANGED: "프로모션 변경",
    ChangeType.NEWS: "뉴스",
    ChangeType.SNS_SPIKE: "SNS 급증",
    ChangeType.SNS_NEW_POST: "SNS 새 게시물",
}

# 차트 치수(px). 가로는 %로 그려서 화면 폭에 맞춰 늘어나고, 세로와 글자 크기는 고정이다.
PLOT_H = 180
BAR_RATIO = 0.56  # 하루 칸 대비 막대 폭
SEG_GAP = 2  # 누적 막대 조각 사이 여백

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html", "j2"]),
)


def _num(value: object) -> str:
    """천 단위 구분 숫자. None·이상값은 0으로 본다."""
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


_env.filters["num"] = _num


def _safe_url(url: str | None) -> str | None:
    """http/https만 링크로 쓴다. javascript:, data: 등은 None → 템플릿이 일반 텍스트로 출력."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    return url.strip() if parts.scheme.lower() in ("http", "https") and parts.netloc else None


def _local(dt: datetime) -> datetime:
    """tz가 있으면 KST로 맞추고, 없으면(수집기의 datetime.now()) 그대로 쓴다."""
    return dt.astimezone(KST) if dt.tzinfo else dt


def _ticks(peak: int) -> list[int]:
    """0부터 peak 이상까지 3~5개의 깔끔한 정수 눈금."""
    step = 1
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000):
        if step * 4 >= peak:
            break
    else:
        step = -(-peak // 4)
    top = max(step, -(-peak // step) * step)
    return list(range(0, top + 1, step))


def _trend(events: list[ChangeEvent], days: list[date]) -> dict:
    """일자×브랜드 누적 막대 차트의 좌표를 미리 계산한다(템플릿은 그리기만)."""
    brands = list(Brand)
    by_day: dict[date, Counter] = {d: Counter() for d in days}
    for e in events:
        d = _local(e.detected_at).date()
        if d in by_day:
            by_day[d][e.brand] += 1

    peak = max((sum(c.values()) for c in by_day.values()), default=0)
    ticks = _ticks(peak) if peak else [0]
    top = ticks[-1] or 1
    slot = 100 / len(days)
    columns = []
    for i, d in enumerate(days):
        counts = [by_day[d][b] for b in brands]
        stacked = [(n, v) for n, v in enumerate(counts) if v]
        segments, base = [], float(PLOT_H)
        for pos, (n, v) in enumerate(stacked):
            h = v / top * PLOT_H
            gap = SEG_GAP if pos and h > SEG_GAP + 1 else 0  # 맨 아래 조각은 기준선에 붙인다
            segments.append({
                "series": n + 1,
                "y": round(base - h, 2),
                "h": round(h - gap, 2),
                "top": pos == len(stacked) - 1,  # 맨 위 조각만 끝을 둥글게
            })
            base -= h
        columns.append({
            "label": f"{d.month}/{d.day}",
            "iso": d.isoformat(),
            "counts": counts,
            "total": sum(counts),
            "slot_x": round(i * slot, 4),
            "slot_w": round(slot, 4),
            "bar_x": round((i + (1 - BAR_RATIO) / 2) * slot, 4),
            "bar_w": round(slot * BAR_RATIO, 4),
            "center": round((i + 0.5) * slot, 4),
            # 오늘에서 5일 간격으로 날짜 눈금
            "tick": (len(days) - 1 - i) % 5 == 0,
            "segments": segments,
        })
    return {
        "columns": columns,
        "ticks": [{"value": t, "y": round(PLOT_H - t / top * PLOT_H, 2)} for t in ticks],
        "plot_h": PLOT_H,
        "total": sum(c["total"] for c in columns),
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
    }


def _brand_cards(events: list[ChangeEvent], counts: dict[str, dict[str, int]]) -> list[dict]:
    cards = []
    for n, brand in enumerate(Brand, start=1):
        items = dict(counts.get(brand.value) or {})
        known = [(SOURCE_LABEL[k], items.pop(k, 0) or 0) for k in SOURCE_LABEL]
        extra = [(str(k), v or 0) for k, v in sorted(items.items())]  # 모델에 없는 소스 타입도 숨기지 않는다
        changes = Counter(e.change_type for e in events if e.brand == brand)
        cards.append({
            "series": n,
            "label": BRAND_LABEL[brand.value],
            "items_total": sum(v for _, v in known + extra),
            "sources": known + extra,
            "events_total": sum(changes.values()),
            "changes": [(CHANGE_LABEL[t], changes[t]) for t in ChangeType if changes[t]],
        })
    return cards


def render(
    events: list[ChangeEvent],
    reports: list[WeeklyReport],
    usage: list[dict],
    counts: dict[str, dict[str, int]],
    generated_at: datetime | None = None,
) -> str:
    """대시보드 HTML 문자열. 빈 데이터여도 빈 상태 화면을 돌려준다."""
    now = _local(generated_at) if generated_at else datetime.now(KST)
    days = [now.date() - timedelta(days=n) for n in range(WINDOW_DAYS - 1, -1, -1)]
    in_window = [e for e in events if days[0] <= _local(e.detected_at).date() <= days[-1]]

    high = sorted(
        (e for e in events if (e.importance or 0) >= HIGH_IMPORTANCE),
        key=lambda e: _local(e.detected_at).replace(tzinfo=None),
        reverse=True,
    )[:MAX_HIGH_EVENTS]
    usage_rows = [
        {
            "model": str(u.get("model") or "-"),
            "calls": u.get("calls") or 0,
            "input_tokens": u.get("input_tokens") or 0,
            "output_tokens": u.get("output_tokens") or 0,
        }
        for u in usage
    ]
    cards = _brand_cards(in_window, counts or {})
    return _env.get_template("dashboard.html.j2").render(
        generated_at=now.strftime("%Y-%m-%d %H:%M") + (" KST" if now.tzinfo else ""),
        window_days=WINDOW_DAYS,
        brands=[{"series": c["series"], "label": c["label"], "events": c["events_total"]} for c in cards],
        cards=cards,
        items_total=sum(c["items_total"] for c in cards),
        trend=_trend(in_window, days),
        high_importance=HIGH_IMPORTANCE,
        high_events=[
            {
                "importance": e.importance,
                "category": e.category.value if e.category else None,
                "brand": BRAND_LABEL[e.brand.value],
                "change": CHANGE_LABEL[e.change_type],
                "title": e.title,
                "url": _safe_url(e.url),
                "note": e.llm_note,
                "date": _local(e.detected_at).strftime("%m/%d"),
            }
            for e in high
        ],
        reports=reports,
        usage=usage_rows,
        usage_total={k: sum(int(u[k]) for u in usage_rows) for k in ("calls", "input_tokens", "output_tokens")},
    )


def build(out: Path | None = None) -> Path:
    """DB에서 읽어 대시보드 파일을 쓴다. 기본 경로는 data/dashboard.html."""
    from app.core import config
    from app.infra.storage import repo

    out = Path(out) if out else config.ROOT / "data" / "dashboard.html"
    html = render(
        repo.recent_events(WINDOW_DAYS),
        repo.list_reports(12),
        repo.llm_usage(WINDOW_DAYS),
        repo.item_counts(),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
