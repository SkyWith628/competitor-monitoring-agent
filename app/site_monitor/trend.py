"""네이버 쇼핑인사이트 키워드 클릭 추이 수집 (5개 브랜드 공통). 담당: A

공식몰 직접 수집이 막힌 4개 브랜드도 '관심도 추이'만큼은 비교할 수 있게 하는 보조 지표다.
이벤트(ChangeEvent)는 만들지 않고 결과를 data/trends/ 아래 JSON 으로만 저장한다(1단계).
급증 판정도 계산해서 파일·로그에만 남긴다. 알림·리포트 연동은 팀 협의 후(ChangeType 추가 필요).

공식 API (NCP API Gateway, 쇼핑인사이트 > 분야 내 키워드별 트렌드):
  POST https://naverapihub.apigw.ntruss.com/shopping/v1/category/keywords
  헤더 X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY
    → .env 의 NCP_INSIGHT_KEY_ID / NCP_INSIGHT_KEY (B 의 키와 섞이지 않게 이름을 따로 둔다)
  본문 startDate·endDate(yyyy-mm-dd), timeUnit(date|week|month), category, keyword[≤5]{name, param[]}
  응답 results[]{title, keyword, data[]{period, ratio}}  ratio 는 '이 요청 안에서' 최댓값 100 기준 상대값.
  (2026-11-23 실호출 확인: title = 요청한 name, 100 은 그룹 5개 전체의 최댓값 기준)
  클릭이 없는 날은 data 에서 빠진다 → 0 으로 채운다.
  한도: API 당 월 50,000회, 키당 50 RPS. 하루 1회 실행이면 1회 호출이다.

비교 규칙: ratio 는 요청마다 다시 정규화되므로 '한 응답 안에서만' 브랜드끼리 비교한다.
  그래서 5개 브랜드를 한 번의 요청(그룹 5개)으로 묶어 보낸다.

키워드는 config/brands.yaml 의 site.shopping_insight.keyword. 분야는 패션의류(50000000).

실행:  python -m app.site_monitor.trend [--days 30]
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.core.config import ROOT, brands_config, env
from app.core.models import Brand

log = logging.getLogger(__name__)

API_URL = "https://naverapihub.apigw.ntruss.com/shopping/v1/category/keywords"
CATEGORY_FASHION = "50000000"  # 패션의류
MAX_GROUPS = 5
RETRY_STATUSES = {429, 500, 502, 503, 504}
OUT_DIR = ROOT / "data" / "trends"
KST = ZoneInfo("Asia/Seoul")

# 급증 판정: 최근 하루가 직전 WINDOW 일 평균의 FACTOR 배 이상이고, 값 자체도 MIN_RATIO 이상
SPIKE_WINDOW = 7
SPIKE_FACTOR = 1.5
SPIKE_MIN_RATIO = 5.0


def brand_keywords(config: dict | None = None) -> dict[Brand, str]:
    """brands.yaml 에서 브랜드별 트렌드 키워드를 읽는다. 키워드가 없는 브랜드는 빠진다."""
    config = config if config is not None else brands_config()
    out: dict[Brand, str] = {}
    for b in Brand:
        kw = ((config.get(b.value) or {}).get("site") or {}).get("shopping_insight", {}).get("keyword")
        if kw:
            out[b] = str(kw)
    return out


def fill_days(points: list[dict], start: date, end: date) -> list[tuple[str, float]]:
    """응답에서 빠진 날(클릭 없음)을 0 으로 채워 start~end 의 날짜순 목록으로."""
    got = {p["period"]: float(p["ratio"]) for p in points}
    days = (end - start).days + 1
    return [((d := (start + timedelta(i)).isoformat()), got.get(d, 0.0)) for i in range(days)]


def spike(series: list[tuple[str, float]], *, window: int = SPIKE_WINDOW, factor: float = SPIKE_FACTOR,
          min_ratio: float = SPIKE_MIN_RATIO) -> dict | None:
    """마지막 날이 직전 window 일 평균보다 factor 배 이상 높으면 그 내용을, 아니면 None."""
    if len(series) < window + 1:
        return None
    period, latest = series[-1]
    base = sum(r for _, r in series[-window - 1:-1]) / window
    if latest < min_ratio or base <= 0 or latest < base * factor:
        return None
    return {"period": period, "ratio": latest, "baseline": round(base, 2), "x": round(latest / base, 2)}


class ShoppingInsightTrend:
    source = "naver_shopping_insight"
    request_interval_sec = 0.5
    max_retries = 3

    def __init__(self, keywords: dict[Brand, str], category: str = CATEGORY_FASHION):
        if not keywords:
            raise ValueError("brands.yaml 에 site.shopping_insight.keyword 가 있는 브랜드가 없음")
        if len(keywords) > MAX_GROUPS:
            raise ValueError(f"쇼핑인사이트는 한 요청에 키워드 그룹 {MAX_GROUPS}개까지")
        self.keywords = keywords
        self.category = category
        self.api_calls = 0

    # --- 네트워크 (테스트에서 override) ---
    def _request(self, body: dict) -> dict:
        key_id, key = env("NCP_INSIGHT_KEY_ID"), env("NCP_INSIGHT_KEY")
        if not key_id or not key:
            raise RuntimeError(".env 에 NCP_INSIGHT_KEY_ID / NCP_INSIGHT_KEY 가 없음 "
                               "(NCP 콘솔에서 쇼핑인사이트 API 를 신청한 키)")
        headers = {"X-NCP-APIGW-API-KEY-ID": key_id, "X-NCP-APIGW-API-KEY": key,
                   "Content-Type": "application/json"}
        for attempt in range(1, self.max_retries + 1):
            self.api_calls += 1
            try:
                resp = httpx.post(API_URL, json=body, headers=headers, timeout=15)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
            else:
                if resp.status_code not in RETRY_STATUSES:
                    if resp.status_code in (401, 403):
                        raise RuntimeError(f"NCP API 인증/권한 오류 {resp.status_code}: "
                                           "키 값과 쇼핑인사이트 API 이용 신청을 확인")
                    resp.raise_for_status()
                    return resp.json()
                if attempt == self.max_retries:
                    resp.raise_for_status()
            time.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    # --- 수집 ---
    def collect(self, *, days: int = 30, end: date | None = None) -> dict:
        """최근 days 일(끝 = 어제 KST)의 일별 추이와 급증 판정을 돌려준다."""
        end = end or (datetime.now(KST).date() - timedelta(days=1))  # 당일 데이터는 아직 집계 전
        start = end - timedelta(days=days - 1)
        body = {
            "startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": "date",
            "category": self.category,
            "keyword": [{"name": b.value, "param": [kw]} for b, kw in self.keywords.items()],
        }
        data = self._request(body)
        by_name = {r.get("title"): r.get("data") or [] for r in data.get("results") or []}
        brands = {}
        for b, kw in self.keywords.items():
            series = fill_days(by_name.get(b.value, []), start, end)
            brands[b.value] = {"keyword": kw, "series": [{"period": p, "ratio": r} for p, r in series],
                               "spike": spike(series)}
            if brands[b.value]["spike"]:
                log.info("%s 쇼핑 클릭 급증 후보: %s", b.value, brands[b.value]["spike"])
        log.info("쇼핑인사이트 API 호출 %d회", self.api_calls)
        return {"source": self.source, "category": self.category, "start": start.isoformat(),
                "end": end.isoformat(), "collected_at": datetime.now(KST).isoformat(timespec="seconds"),
                "brands": brands}


def save(result: dict, out_dir: Path = OUT_DIR) -> Path:
    """data/trends/shopping_insight-<end>.json 로 저장. 같은 날 다시 돌리면 덮어쓴다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"shopping_insight-{result['end']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="네이버 쇼핑인사이트 브랜드 키워드 추이 수집")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = ShoppingInsightTrend(brand_keywords()).collect(days=args.days)
    path = save(result)
    for name, b in result["brands"].items():
        last = b["series"][-1]["ratio"] if b["series"] else 0
        mark = f"  ▲ 급증 x{b['spike']['x']}" if b["spike"] else ""
        print(f"{name:18} {b['keyword']:10} 최근 {last:6.1f}{mark}")
    print(f"저장: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
