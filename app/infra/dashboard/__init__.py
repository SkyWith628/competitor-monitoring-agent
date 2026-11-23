"""DB → 정적 HTML 대시보드(단일 파일, 외부 리소스 없음). 담당: D

무엇을 하나
    DB에 쌓인 수집 항목·변화 이벤트·주간 리포트·LLM 사용량을 한 장의 HTML로 만든다.
    CSS·JS·차트(SVG)가 전부 파일 안에 들어 있어 메일 첨부나 로컬 파일로 열어도 그대로 보인다.

누가 부르나
    `python -m app dashboard` (app/__main__.py) → build() → data/dashboard.html

데이터 흐름
    repo(storage/repo.py)  ──▶  build()        ──▶  render()            ──▶  HTML 파일
    recent_events 등 조회        조회 결과를 모음      표시용 값으로 가공 후        out 경로에 저장
                                                    templates/dashboard.html.j2 에 넣음

    - build()만 DB를 안다. render()는 인자로 받은 값만 쓰는 순수 함수라서 테스트는 render()에
      샘플 데이터를 직접 넣는다(tests/infra/test_dashboard.py).
    - 계산(집계·좌표·시각 형식·상태 판정)은 전부 여기 파이썬에서 끝내고, 템플릿은 받은 값을
      그리기만 한다. 템플릿에 로직이 흩어지면 테스트하기 어렵기 때문이다.

화면 구성(위에서부터)
    운영 상태 → 브랜드별 현황 → 일별 이벤트 추이 → 주요 이벤트 → 주간 리포트 → LLM 사용량

시각 규칙
    화면에 나오는 시각은 전부 한국 시간으로 바꿔 "YYYY-MM-DD HH:MM KST" 한 가지 형식으로 쓴다.
    "3시간 전" 같은 상대 표현은 브라우저 JS가 아니라 생성 시점(generated_at) 기준으로 여기서 계산한다.
    파일은 만들어진 순간의 스냅샷이므로, 나중에 열었을 때 상대 시간이 달라지면 오히려 틀린 정보가 된다.

보안 규칙
    제목·메모 등은 외부 사이트에서 긁어 온 글이다. Jinja autoescape로 전부 이스케이프하고,
    링크는 http/https 주소만 건다(_safe_url).
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.models import Brand, ChangeEvent, ChangeType, SourceType, WeeklyReport
from app.infra.notify.render import BRAND_LABEL

KST = ZoneInfo("Asia/Seoul")
WINDOW_DAYS = 30  # 카드·추이 차트·주요 이벤트가 보는 기간(오늘 포함 30일)
HIGH_IMPORTANCE = 4  # 이 값 이상을 "주요 이벤트"로 본다
MAX_HIGH_EVENTS = 20
# 마지막 수집이 이보다 오래되면 "지연". 수집은 매일 06:00 KST에 한 번 돈다. 24시간으로 잡으면 실행이
# 조금만 늦어도 매일 경고가 뜨고, 48시간이면 하루를 통째로 거른 것을 다음 날 아침에도 놓친다.
# 36시간이면 "수집을 한 번 걸렀다"는 사실을 그날 저녁부터 알 수 있다.
# 주간 리포트가 나가는 채널 전부 (app/__main__.py 의 CHANNELS 와 같다)
REPORT_CHANNELS = ("slack", "email")
STALE_HOURS = 36

log = logging.getLogger(__name__)

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
    ChangeType.TREND_SPIKE: "쇼핑 클릭 급증",
}
CHANNEL_LABEL = {"email": "메일", "slack": "슬랙"}
# 상태 키는 템플릿의 state() 매크로가 모양(●▲◐○)과 색을 고를 때도 쓴다. 키를 바꾸면 템플릿도 함께.
SOURCE_STATE_LABEL = {"ok": "정상", "stale": "지연", "missing": "기록 없음"}
REPORT_STATE_LABEL = {"sent": "발송", "partial": "일부 발송", "unsent": "미발송"}

# 차트 치수(px). 가로는 %로 그려서 화면 폭에 맞춰 늘어나고, 세로와 글자 크기는 고정이다.
# (viewBox로 통째로 늘이면 좁은 화면에서 글자와 선 굵기까지 같이 줄어 읽기 어렵다)
PLOT_H = 180
BAR_RATIO = 0.56  # 하루 칸 대비 막대 폭
SEG_GAP = 2  # 누적 막대 조각 사이 여백. 색이 비슷하게 보이는 사람도 조각 경계를 알 수 있게 한다

# autoescape: 템플릿에 들어가는 모든 문자열을 HTML 이스케이프한다(긁어 온 글에 태그가 섞여 있어도 안전).
_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html", "j2"]),
)


def _num(value: object) -> str:
    """천 단위 구분 숫자 문자열(템플릿 필터 `| num`).

    Args:
        value: 정수로 바꿀 수 있는 값. None도 허용.
    Returns:
        "1,234" 형식. None·빈 값·숫자가 아닌 값은 "0" (DB 집계가 NULL이어도 화면이 깨지지 않게).
    """
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


_env.filters["num"] = _num


def _int(value: object) -> int:
    """합계 계산용 정수. `_num`과 같은 규칙으로 None·이상값을 0으로 본다."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_url(url: str | None) -> str | None:
    """링크로 걸어도 되는 주소만 돌려준다.

    수집한 주소를 그대로 href에 넣으면 `javascript:`·`data:` 주소가 클릭 한 번에 스크립트를 실행할 수
    있다. 이스케이프로는 막지 못하므로(문법상 멀쩡한 속성값이다) 스킴을 직접 확인한다.

    Args:
        url: 이벤트의 url. None·빈 문자열 가능.
    Returns:
        앞뒤 공백을 뗀 http/https 주소. 그 외(다른 스킴, 호스트 없음, 해석 불가)는 None이고,
        이때 템플릿은 제목을 링크 없이 일반 텍스트로 출력한다.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    return url.strip() if parts.scheme.lower() in ("http", "https") and parts.netloc else None


def _local(dt: datetime) -> datetime:
    """어떤 datetime이든 KST 시각으로 맞춘다.

    수집 시각·감지 시각은 tz 없는 datetime.now()로 찍히고, 발송 시각은 tz가 붙어 있다. 둘을 섞어
    비교·정렬하려면 한쪽으로 통일해야 한다.

    Args:
        dt: tz가 있거나 없는 datetime.
    Returns:
        tzinfo=KST인 datetime. tz 없는 값은 프로세스의 로컬 시각으로 읽는다 — CLI가 시간대를 한국
        시간으로 고정하므로(app/infra/timeutil.py) 운영에서는 그대로 KST이고, 시간대를 고정할 수 없는
        Windows에서도 PC 시각이 KST로 환산된다.
    """
    return dt.astimezone(KST)


def _parse_time(value: object) -> datetime | None:
    """datetime 또는 ISO 문자열을 KST datetime으로.

    Args:
        value: datetime, "2026-11-22T06:01:12"(tz 없음 = 로컬 시각) 또는
            "2026-11-23T08:00:03+09:00" 같은 ISO 문자열, 또는 None.
    Returns:
        KST datetime. 값이 비었거나 형식을 읽을 수 없으면 None(화면에는 "-" 또는 "기록 없음").
    """
    if isinstance(value, str) and value.strip():
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    return _local(value) if isinstance(value, datetime) else None


def _fmt_time(dt: datetime) -> str:
    """화면에 쓰는 유일한 시각 형식: "2026-11-22 09:00 KST"."""
    return _local(dt).strftime("%Y-%m-%d %H:%M") + " KST"


def _ago_text(then: datetime, now: datetime) -> str:
    """생성 시점 기준 상대 시간.

    Args:
        then: 일어난 시각(KST). now: 대시보드 생성 시각(KST).
    Returns:
        1분 미만 "방금 전" / 1시간 미만 "N분 전" / 48시간 미만 "N시간 전" / 그 이상 "N일 전".
        지연 기준이 36시간이라 그 근처는 일 단위로 뭉개지 않고 시간으로 보여 준다.
        then이 now보다 미래(서버 간 시계 오차)여도 음수 대신 "방금 전".
    """
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 1:
        return "방금 전"
    if minutes < 60:
        return f"{minutes}분 전"
    if minutes < 48 * 60:
        return f"{minutes // 60}시간 전"
    return f"{minutes // (24 * 60):,}일 전"


def _time_view(dt: datetime | None, now: datetime) -> dict | None:
    """템플릿의 when() 매크로(<time> 태그 하나)에 필요한 값.

    Returns:
        {"text": "2026-11-22 06:01 KST", "iso": "2026-11-22T06:01:12+09:00", "ago": "2시간 전"}.
        dt가 None이면 None.
    """
    if dt is None:
        return None
    return {"text": _fmt_time(dt), "iso": dt.isoformat(timespec="seconds"), "ago": _ago_text(dt, now)}


def _ticks(peak: int) -> list[int]:
    """세로축 눈금: 0부터 peak 이상까지 3~5개의 깔끔한 정수.

    Args:
        peak: 하루 최대 이벤트 수(1 이상).
    Returns:
        예) 3 → [0, 1, 2, 3], 7 → [0, 2, 4, 6, 8], 37 → [0, 10, 20, 30, 40].
        마지막 값이 차트의 꼭대기가 되므로 항상 peak 이상이다(막대가 위로 잘리지 않는다).
    """
    step = 1
    # 눈금 간격 후보 중 "4칸 안에 peak가 들어가는" 가장 작은 값을 고른다
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000):
        if step * 4 >= peak:
            break
    else:
        step = -(-peak // 4)  # 후보를 넘는 큰 값: 올림 나눗셈으로 4등분
    top = max(step, -(-peak // step) * step)
    return list(range(0, top + 1, step))


def _trend(events: list[ChangeEvent], days: list[date]) -> dict:
    """일자×브랜드 누적 막대 차트의 좌표를 미리 계산한다(템플릿은 그리기만).

    Args:
        events: 30일 창 안의 이벤트(창 밖 이벤트가 섞여 있어도 무시한다).
        days: 차트에 그릴 날짜들, 오래된 날 → 오늘 순서. 길이는 WINDOW_DAYS.
    Returns:
        {
          "columns": [하루에 하나, days와 같은 순서 {
              "label": "11/22", "iso": "2026-11-22",
              "counts": [브랜드별 건수, Brand 선언 순서], "total": 합계,
              "summary": 키보드 포커스·스크린리더용 한 줄 요약,
              "slot_x"/"slot_w": 하루 칸(포인터·포커스 영역)의 x, 폭(%),
              "bar_x"/"bar_w": 막대의 x, 폭(%), "center": 칸 가운데(%), "tick": 날짜 눈금을 찍을지,
              "segments": [{"series": 1~4, "y": px, "h": px, "top": 맨 위 조각인지}],
              "entry": Tab으로 들어왔을 때 처음 포커스를 받을 칸인지
          }],
          "ticks": [{"value": 눈금 값, "y": px}], "plot_h": 그리는 영역 높이(px),
          "total": 기간 합계, "start"/"end": 기간의 첫날·마지막 날(ISO)
        }
        이벤트가 하나도 없으면 total=0이고 템플릿이 차트 대신 빈 상태 문구를 보여 준다.
    """
    brands = list(Brand)
    by_day: dict[date, Counter] = {d: Counter() for d in days}
    for e in events:
        # 날짜 묶음은 반드시 KST 날짜로. UTC로 자르면 00:00~09:00 KST 이벤트가 전날로 넘어간다
        d = _local(e.detected_at).date()
        if d in by_day:
            by_day[d][e.brand] += 1

    peak = max((sum(c.values()) for c in by_day.values()), default=0)
    ticks = _ticks(peak) if peak else [0]
    top = ticks[-1] or 1  # 세로 눈금의 최댓값 = 차트 높이 전체(PLOT_H)에 해당하는 건수
    slot = 100 / len(days)  # 하루가 차지하는 가로 폭(%)
    columns = []
    for i, d in enumerate(days):
        counts = [by_day[d][b] for b in brands]
        stacked = [(n, v) for n, v in enumerate(counts) if v]
        # SVG의 y는 위가 0이다. 기준선(y=PLOT_H)에서 시작해 조각을 위로 쌓아 올린다
        segments, base = [], float(PLOT_H)
        for pos, (n, v) in enumerate(stacked):
            h = v / top * PLOT_H
            # 조각 사이 여백은 조각 아래쪽에서 떼어 낸다. 맨 아래 조각은 기준선에 붙이고,
            # 너무 얇은 조각은 여백을 빼면 사라지므로 그대로 둔다
            gap = SEG_GAP if pos and h > SEG_GAP + 1 else 0
            segments.append({
                "series": n + 1,
                "y": round(base - h, 2),
                "h": round(h - gap, 2),
                "top": pos == len(stacked) - 1,  # 맨 위 조각만 끝을 둥글게
            })
            base -= h
        total = sum(counts)
        named = ", ".join(f"{BRAND_LABEL[b.value]} {v:,}건" for b, v in zip(brands, counts, strict=True) if v)
        columns.append({
            "label": f"{d.month}/{d.day}",
            "iso": d.isoformat(),
            "counts": counts,
            "total": total,
            # 키보드 포커스·스크린리더용 한 줄 요약(툴팁과 같은 내용을 JS 없이도 읽을 수 있게)
            "summary": f"{d.isoformat()} 합계 {total:,}건" + (f": {named}" if named else ""),
            "slot_x": round(i * slot, 4),
            "slot_w": round(slot, 4),
            "bar_x": round((i + (1 - BAR_RATIO) / 2) * slot, 4),  # 칸 가운데에 막대를 놓는다
            "bar_w": round(slot * BAR_RATIO, 4),
            "center": round((i + 0.5) * slot, 4),
            # 날짜 눈금은 오늘에서 거꾸로 5일 간격. 왼쪽 끝이 아니라 오늘을 기준으로 삼아야
            # 가장 중요한 "오늘" 눈금이 항상 찍힌다
            "tick": (len(days) - 1 - i) % 5 == 0,
            "segments": segments,
        })
    # Tab으로 차트에 들어오면 처음 닿는 칸: 이벤트가 있는 가장 최근 날짜(전혀 없으면 오늘).
    # 30칸을 모두 Tab 정지점으로 만들면 차트를 지나가는 데만 Tab을 30번 눌러야 하므로 하나만 둔다
    entry = max((i for i, c in enumerate(columns) if c["total"]), default=len(columns) - 1)
    for i, c in enumerate(columns):
        c["entry"] = i == entry
    return {
        "columns": columns,
        "ticks": [{"value": t, "y": round(PLOT_H - t / top * PLOT_H, 2)} for t in ticks],
        "plot_h": PLOT_H,
        "total": sum(c["total"] for c in columns),
        "start": days[0].isoformat(),
        "end": days[-1].isoformat(),
    }


def _brand_cards(events: list[ChangeEvent], counts: dict[str, dict[str, int]]) -> list[dict]:
    """브랜드 카드 4장의 값.

    Args:
        events: 30일 창 안의 이벤트.
        counts: repo.item_counts() 결과. {"spao": {"site_product": 1234, "news": 5}, ...}
            (브랜드 값 → 소스 타입 값 → 누적 항목 수). 빠진 브랜드·타입은 0으로 본다.
    Returns:
        Brand 선언 순서대로 [{"series": 1~4(색·모양 번호), "label": "스파오", "items_total": 수집 항목 합계,
        "sources": [("상품", 1234), ...], "events_total": 30일 이벤트 수,
        "changes": [("가격 인하", 2), ...] — 0건인 유형은 빼고 ChangeType 선언 순서}].
        series 번호는 브랜드에 고정이다. 데이터가 없는 브랜드가 있어도 다른 브랜드의 색이 바뀌지 않는다.
    """
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


def _source_rows(sources: list[dict], now: datetime) -> list[dict]:
    """운영 상태의 "소스별 수집 상태" 표 한 줄씩.

    Args:
        sources: repo.source_freshness() 결과. 한 줄의 모양:
            {"brand": "spao", "source": "spao.com", "source_type": "site_product", "items": 123,
             "last_collected_at": "2026-11-22T06:01:12"}  (tz 없는 ISO 문자열 = 한국 로컬 시각)
        now: 대시보드 생성 시각(KST).
    Returns:
        [{"brand_key": "spao", "brand": "스파오", "source": "spao.com", "type": "상품", "count": 123,
          "last": _time_view() 값 또는 None, "state": "ok"|"stale"|"missing", "state_label": "정상"…}]
        Brand 선언 순서 → 소스 이름 순으로 정렬한다(브랜드 카드와 같은 순서).

        - state 판정: 마지막 수집 시각이 없거나 읽을 수 없으면 "missing", now 기준 STALE_HOURS를
          넘었으면 "stale", 아니면 "ok".
        - sources에 한 줄도 없는 브랜드는 "기록 없음" 줄을 만들어 넣는다. 수집기가 통째로 죽은
          브랜드는 조회 결과에 아예 나오지 않는데, 그것이 가장 먼저 알아야 할 상황이기 때문이다.
        - 항목 수 키가 "count"인 이유: Jinja에서 `row.items`는 dict.items 메서드로 읽힌다.
    """
    order = {b.value: n for n, b in enumerate(Brand)}
    rows = []
    for s in sources:
        brand = str(s.get("brand") or "")
        last = _parse_time(s.get("last_collected_at"))
        if last is None:
            state = "missing"
        elif now - last > timedelta(hours=STALE_HOURS):
            state = "stale"
        else:
            state = "ok"
        source_type = str(s.get("source_type") or "")
        rows.append({
            "brand_key": brand,
            "brand": BRAND_LABEL.get(brand, brand or "-"),
            "source": str(s.get("source") or "-"),
            "type": SOURCE_LABEL.get(source_type, source_type),
            "count": _int(s.get("items")),
            "last": _time_view(last, now),
            "state": state,
        })
    seen = {r["brand_key"] for r in rows}
    rows += [
        {"brand_key": b.value, "brand": BRAND_LABEL[b.value], "source": "-", "type": "", "count": 0,
         "last": None, "state": "missing"}
        for b in Brand if b.value not in seen
    ]
    rows.sort(key=lambda r: (order.get(r["brand_key"], len(order)), r["brand_key"], r["source"]))
    for r in rows:
        r["state_label"] = SOURCE_STATE_LABEL[r["state"]]
    return rows


def _report_rows(reports: list[dict], now: datetime) -> list[dict]:
    """운영 상태의 "주간 리포트 발송" 표 한 줄씩.

    Args:
        reports: repo.report_status() 결과(최신 주차부터). 한 줄의 모양:
            {"week": "2026-W47", "sent_at": "2026-11-23T08:00:03+09:00" 또는 None,
             "channels": ["email", "slack"]}
        now: 대시보드 생성 시각(KST).
    Returns:
        [{"week": "2026-W47", "state": "sent"|"partial"|"unsent", "state_label": "발송"…,
          "sent": _time_view() 값 또는 None, "channels": ["메일", "슬랙"]}] — 받은 순서 그대로.

        state 판정 (`python -m app status` 와 같은 기준):
          - "sent"    모든 채널(REPORT_CHANNELS)로 끝까지 나감
          - "partial" 일부만 나감. sent_at 은 첫 메시지가 나간 순간 찍히므로, sent_at 이 있어도
                      channels 에 빠진 채널이 있으면 여기에 해당한다. `report` 명령을 다시 실행하면
                      못 나간 부분만 재발송되므로 운영자가 할 일이 "미발송"과 다르다.
          - "unsent"  저장만 되고 나가지 않음 (dry-run 이나 전 채널 발송 실패)
    """
    rows = []
    for r in reports:
        sent = _parse_time(r.get("sent_at"))
        done = {str(c) for c in (r.get("channels") or [])}
        channels = [CHANNEL_LABEL.get(c, c) for c in sorted(done)]
        if sent and done >= set(REPORT_CHANNELS):
            state = "sent"
        else:
            state = "partial" if sent or done else "unsent"
        rows.append({
            "week": str(r.get("week") or "-"),
            "state": state,
            "state_label": REPORT_STATE_LABEL[state],
            "sent": _time_view(sent, now),
            "channels": channels,
        })
    return rows


def _health_view(health: dict | None, now: datetime) -> dict | None:
    """운영 상태 섹션의 표시 값.

    Args:
        health: render()의 health 인자(모양은 render() 설명 참고). None·빈 dict·dict가 아닌 값은 정보 없음.
        now: 대시보드 생성 시각(KST).
    Returns:
        health가 없으면 None → 템플릿이 "운영 상태 정보 없음"을 보여 준다. 있으면:
        {"stale_hours": 36,
         "sources": _source_rows() 결과 | [] (아직 수집 기록이 없음) | None (키가 없음),
         "problems"/"stale"/"missing": 정상이 아닌 줄 수 / 지연 줄 수 / 기록 없음 줄 수,
         "reports": _report_rows() 결과 | None, "latest_report": 가장 최근 주차의 줄 | None,
         "unclassified": int | None, "pending_urgent": int | None}
        값이 None인 칸은 "정보 없음"으로 표시된다. build()가 repo 함수 일부를 못 불렀을 때
        0으로 보여 주면 "문제 없음"으로 읽히므로 0과 None을 구분한다.
    """
    if not isinstance(health, dict) or not health:
        return None
    view: dict = {"stale_hours": STALE_HOURS, "sources": None, "reports": None, "problems": 0, "stale": 0,
                  "missing": 0, "unclassified": None, "pending_urgent": None, "latest_report": None}
    if isinstance(health.get("sources"), list):
        # 빈 목록(아직 한 번도 수집하지 않은 새 DB)은 브랜드마다 "기록 없음" 줄을 채우지 않고
        # 빈 상태 문구 하나로 보여 준다
        view["sources"] = _source_rows(health["sources"], now) if health["sources"] else []
        view["problems"] = sum(r["state"] != "ok" for r in view["sources"])
        view["stale"] = sum(r["state"] == "stale" for r in view["sources"])
        view["missing"] = sum(r["state"] == "missing" for r in view["sources"])
    if isinstance(health.get("reports"), list):
        view["reports"] = _report_rows(health["reports"], now)
        view["latest_report"] = view["reports"][0] if view["reports"] else None
    for key in ("unclassified", "pending_urgent"):
        if health.get(key) is not None:
            view[key] = _int(health[key])
    return view


def render(
    events: list[ChangeEvent],
    reports: list[WeeklyReport],
    usage: list[dict],
    counts: dict[str, dict[str, int]],
    generated_at: datetime | None = None,
    *,
    health: dict | None = None,
) -> str:
    """대시보드 HTML 문자열을 만든다. DB를 건드리지 않는 순수 함수.

    Args:
        events: 변화 이벤트(repo.recent_events 결과). 순서는 상관없다. 오늘 포함 최근 30일(KST 날짜
            기준) 밖의 이벤트는 카드·차트·주요 이벤트 어디에도 넣지 않는다.
        reports: 주간 리포트, 최신 주차부터(repo.list_reports 결과). 받은 순서대로 보여 준다.
        usage: 모델별 LLM 사용량(repo.llm_usage 결과).
            [{"model": "claude-haiku", "calls": 1200, "input_tokens": 2500000, "output_tokens": 90000}]
            값이 None이면 0으로, model이 없으면 "-"로 표시한다.
        counts: 브랜드·소스 타입별 누적 수집 항목 수(repo.item_counts 결과).
            {"spao": {"site_product": 1234, "news": 5}, "uniqlo": {"sns_post": 7}}
        generated_at: 생성 시각. 생략하면 지금. "최근 30일"과 "N시간 전"·"지연" 판정의 기준이 되므로
            테스트는 이 값을 고정해서 넣는다. tz가 없으면 로컬 시각으로 읽는다.
        health: 운영 상태 섹션의 값(키워드 전용, 생략 가능). 모든 키는 선택이고 빠진 키는
            "정보 없음"으로 표시된다.
            {
              "sources": [{"brand": "spao", "source": "spao.com", "source_type": "site_product",
                           "items": 123, "last_collected_at": "2026-11-22T06:01:12"}],  # tz 없음 = KST
              "unclassified": 14,          # 아직 분류되지 않은 이벤트 수
              "reports": [{"week": "2026-W47", "sent_at": "2026-11-23T08:00:03+09:00" | None,
                           "channels": ["email", "slack"]}],  # 최신 주차부터
              "pending_urgent": 2,         # 중요도 5인데 긴급 알림이 아직 나가지 않은 이벤트 수
            }
    Returns:
        완성된 HTML 문서(외부 리소스 없음). 데이터가 전혀 없어도 예외 없이 섹션마다 빈 상태 문구가
        들어간 화면을 돌려준다. health=None이면 운영 상태 섹션은 "운영 상태 정보 없음".
    """
    now = _local(generated_at) if generated_at else datetime.now(KST)
    # 오늘 포함 30일: [오늘-29일, …, 오늘]. 달력 날짜로 세므로 월말·연말을 넘어가도 칸 수는 항상 30
    days = [now.date() - timedelta(days=n) for n in range(WINDOW_DAYS - 1, -1, -1)]
    in_window = [e for e in events if days[0] <= _local(e.detected_at).date() <= days[-1]]

    # 주요 이벤트도 카드·차트와 같은 30일 창 안에서만 고른다. 창 밖 이벤트가 섞이면 "최근 30일"이라는
    # 제목과 어긋나고, 위의 집계에는 없는 이벤트가 목록에만 나타난다
    high = sorted(
        (e for e in in_window if (e.importance or 0) >= HIGH_IMPORTANCE),
        key=lambda e: _local(e.detected_at),
        reverse=True,
    )[:MAX_HIGH_EVENTS]
    usage_rows = [
        {
            "model": str(u.get("model") or "-"),
            "calls": _int(u.get("calls")),
            "input_tokens": _int(u.get("input_tokens")),
            "output_tokens": _int(u.get("output_tokens")),
        }
        for u in usage
    ]
    cards = _brand_cards(in_window, counts or {})
    health_view = _health_view(health, now)
    return _env.get_template("dashboard.html.j2").render(
        generated_at=_time_view(now, now),
        generated_date=now.date().isoformat(),
        health=health_view,
        # 주간 리포트 목록에서도 주차 옆에 발송 상태를 붙일 수 있게 주차 → 상태로 찾아 둔다
        report_state={r["week"]: r for r in (health_view or {}).get("reports") or []},
        window_days=WINDOW_DAYS,
        stale_hours=STALE_HOURS,
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
                "time": _time_view(_local(e.detected_at), now),
            }
            for e in high
        ],
        reports=reports,
        usage=usage_rows,
        usage_total={k: sum(u[k] for u in usage_rows) for k in ("calls", "input_tokens", "output_tokens")},
    )


def _gather_health(repo: object) -> dict | None:
    """운영 상태 값을 repo에서 모아 render()의 health 모양으로 만든다.

    Args:
        repo: app.infra.storage.repo 모듈(테스트에서는 함수 일부를 바꿔 끼운 것).
    Returns:
        {"sources": repo.source_freshness(), "unclassified": repo.unclassified_count(),
         "reports": repo.report_status(), "pending_urgent": len(repo.pending_urgent())} 중 얻은 키만.
        하나도 못 얻으면 None → "운영 상태 정보 없음".

        함수가 없거나(배포된 repo 버전이 낮음) 조회가 예외를 던지면 그 키만 건너뛴다. 운영 상태는
        부가 정보라서, 이것 때문에 대시보드 전체가 실패하면 안 된다. 실패는 로그에 예외 종류만 남긴다
        (예외 메시지에는 DB 주소가 들어 있을 수 있다).
    """
    wanted = {
        "sources": ("source_freshness", list),
        "unclassified": ("unclassified_count", int),
        "reports": ("report_status", list),
        "pending_urgent": ("pending_urgent", len),  # 이벤트 목록을 돌려주므로 건수만 센다
    }
    health = {}
    for key, (name, convert) in wanted.items():
        fn = getattr(repo, name, None)
        if not callable(fn):
            continue
        try:
            health[key] = convert(fn())
        except Exception as exc:  # noqa: BLE001 — 운영 상태 한 칸 때문에 대시보드 전체가 실패하면 안 된다
            log.warning("운영 상태 조회 실패 (%s): %s", name, type(exc).__name__)
    return health or None


def build(out: Path | None = None) -> Path:
    """DB에서 읽어 대시보드 파일을 쓴다. `python -m app dashboard`가 부르는 진입점.

    Args:
        out: 저장 경로. 생략하면 <프로젝트 루트>/data/dashboard.html. 상위 폴더가 없으면 만든다.
    Returns:
        실제로 쓴 파일 경로.

    이벤트·리포트·사용량·항목 수 조회가 실패하면 예외를 그대로 올린다(대시보드의 본문이라 빈 화면을
    만드는 것보다 실패를 알리는 편이 낫다). 운영 상태 값만 _gather_health()가 따로 너그럽게 모은다.
    """
    # 여기서 import하는 이유: render()만 쓰는 쪽(테스트 등)이 DB 설정 없이도 이 모듈을 불러올 수 있게
    from app.core import config
    from app.infra.storage import repo

    out = Path(out) if out else config.ROOT / "data" / "dashboard.html"
    html = render(
        repo.recent_events(WINDOW_DAYS),
        repo.list_reports(12),
        repo.llm_usage(WINDOW_DAYS),
        repo.item_counts(),
        health=_gather_health(repo),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
