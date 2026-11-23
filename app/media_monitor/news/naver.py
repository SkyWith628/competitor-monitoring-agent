"""네이버 검색 API(뉴스) 수집기 — NAVER API HUB(네이버 클라우드) 기준.

2026년 7월 말부터 검색 API 신규 신청은 개발자센터가 아닌 NAVER API HUB에서만 가능.
주소·인증 헤더 이름만 다르고 요청 파라미터·응답 모양(items, title, originallink, pubDate)은 같다.
https://guide.ncloud-docs.com/docs/apihub-migration
"""
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

from app.core.base_collector import BaseCollector
from app.core.config import env
from app.core.models import RawItem, SourceType
from app.media_monitor.news.rules import has_excluded, mentions_brand

API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
DISPLAY = 100  # 한 번에 받을 기사 수 (API 최대값)
RECENT_DAYS = 7  # 구글 뉴스(when:7d)와 기준을 맞춰 최근 7일 기사만


class NaverNewsCollector(BaseCollector):
    source = "naver_news"
    request_interval_sec = 1.0

    def collect(self) -> list[RawItem]:
        exclude = self.config.get("exclude") or []
        items: list[RawItem] = []
        seen: set[str] = set()
        # 네이버는 '최신 100건'을 줘서 기사가 적은 브랜드는 몇 달 전 기사까지 온다 → 7일로 자름
        since = datetime.now() - timedelta(days=RECENT_DAYS)

        # ① 키워드마다 한 번씩 검색 ("스파오" 검색, "SPAO" 검색)
        for keyword in self.config["keywords"]:
            for a in self._fetch(keyword):
                url = a.get("originallink") or a["link"]  # 언론사 원문 주소 우선
                # ② 두 키워드에 똑같이 걸린 기사는 한 번만
                if url in seen:
                    continue
                seen.add(url)

                published = _to_datetime(a.get("pubDate"))
                if published and published < since:  # 7일보다 오래된 기사는 버리기
                    continue

                title = _clean(a["title"])
                body = _clean(a.get("description", ""))
                # ③ 공통 기준(rules.py): 제목·요약 어디에도 브랜드명이 없거나 제외어가 있으면 버리기
                if not mentions_brand(self.config["keywords"], title, body) or has_excluded(exclude, title, body):
                    continue

                # ④ 팀 약속 양식(RawItem)에 옮겨 적기
                items.append(RawItem(
                    brand=self.brand,
                    source_type=SourceType.NEWS,
                    source=self.source,
                    external_id=hashlib.sha1(url.encode()).hexdigest(),
                    url=url,
                    title=title,
                    body=body or None,
                    published_at=published,
                ))
        return items

    def _fetch(self, keyword: str) -> list[dict]:
        """인터넷 접속은 여기서만 한다 (테스트 때 이 부분만 가짜로 바꿈)."""
        client_id, secret = env("NAVER_CLIENT_ID"), env("NAVER_CLIENT_SECRET")
        if not client_id or not secret:
            raise RuntimeError(".env 에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (API HUB 인증 정보) 를 채워야 합니다")
        self.throttle()
        r = httpx.get(API_URL,
                      params={"query": keyword, "display": DISPLAY, "sort": "date"},
                      headers={"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": secret},
                      timeout=10)
        r.raise_for_status()
        return r.json()["items"]


def _clean(text: str) -> str:
    """'<b>스파오</b>, 신상 &quot;출시&quot;' → '스파오, 신상 "출시"'"""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _to_datetime(s: str | None) -> datetime | None:
    """'Wed, 18 Nov 2026 11:00:00 +0900' → 로컬 시간 datetime (collected_at과 형식 맞춤)"""
    if not s:
        return None
    return parsedate_to_datetime(s).astimezone().replace(tzinfo=None)
