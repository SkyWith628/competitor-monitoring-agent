"""Google News RSS 수집기."""
from __future__ import annotations

import hashlib
import logging
from calendar import timegm
from datetime import datetime
from urllib.parse import quote, urlsplit

import feedparser
import httpx

from app.core.base_collector import USER_AGENT, BaseCollector
from app.core.models import RawItem, SourceType
from app.media_monitor.news.rules import has_excluded, mentions_brand

RSS_URL = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
# 구글 뉴스 RSS 는 검색 한 번에 최대 100건만 준다. 꽉 차면 7일치 중 오래된 기사가 잘렸을 수 있다
RSS_LIMIT = 100

log = logging.getLogger(__name__)

# 구글 뉴스에 섞여 들어오는 뉴스가 아닌 사이트 (블로그·카페·쇼핑). 하위 도메인도 함께 차단된다.
NON_NEWS_SITES = (
    "blog.naver.com", "post.naver.com", "cafe.naver.com", "tistory.com", "brunch.co.kr",
    "velog.io", "pandarank.net",
)


class GoogleNewsCollector(BaseCollector):
    source = "google_news"
    request_interval_sec = 1.0

    def collect(self) -> list[RawItem]:
        # ① 검색어 만들기: "스파오" OR "SPAO" when:7d  (최근 7일)
        query = " OR ".join(f'"{k}"' for k in self.config["keywords"]) + " when:7d"

        # ② 구글 뉴스에서 기사 목록 받아오기
        text = self._fetch(query)
        feed = feedparser.parse(text)
        # 구글이 RSS 대신 HTML(동의·차단 안내)을 200으로 주면 feedparser 는 오류 없이 0건이 된다.
        # 뉴스는 '새 기사 0건'이 정상인 날도 있어서 그대로 두면 며칠째 깨져도 모르므로, 예외로 알린다.
        # bozo 만으로는 판단하지 않는다: 정의 안 된 엔티티 하나 같은 사소한 문제에도 켜지는데 기사는 정상 파싱된다
        if not feed.get("version") or (feed.bozo and not feed.entries):
            raise RuntimeError(f"구글 뉴스 응답이 RSS가 아닙니다 (차단·동의 페이지 가능성): "
                               f"{feed.get('bozo_exception') or text[:80]!r}")
        if len(feed.entries) >= RSS_LIMIT:
            # 유니클로는 평소 7일에 65~84건이라 감사제 같은 주간에는 한도에 닿는다 → 잘렸을 수 있음을 남긴다
            log.warning("구글 뉴스 %s: 기사 %d건으로 RSS 한도(%d건)에 닿음 — 최근 7일 중 오래된 기사가 빠졌을 수 있음",
                        self.brand.value, len(feed.entries), RSS_LIMIT)
        exclude = self.config.get("exclude") or []

        items: list[RawItem] = []
        for e in feed.entries:  # ③ 기사 하나씩 꺼내기
            source = e.get("source", {})
            # 블로그·쇼핑 등 뉴스가 아닌 사이트는 버리기
            if _is_non_news(source.get("href", "")):
                continue
            title = _strip_press(e.title, source.get("title"))

            # ④ 공통 기준(rules.py, 네이버와 같음): 브랜드명이 없거나 제외어가 있으면 버리기
            #    구글 RSS는 요약을 주지 않아 제목만 본다. 이 기준으로 빠지는 기사 대부분은
            #    네이버가 요약 근거와 함께 같은 소식을 가져온다 (실데이터 18건 중 16건)
            if not mentions_brand(self.config["keywords"], title) or has_excluded(exclude, title):
                continue

            # ⑤ 팀 약속 양식(RawItem)에 옮겨 적기
            items.append(RawItem(
                brand=self.brand,
                source_type=SourceType.NEWS,
                source=self.source,
                external_id=hashlib.sha1(e.link.encode()).hexdigest(),
                url=e.link,
                title=title,
                published_at=_to_datetime(e.get("published_parsed")),
            ))
        return items

    def _fetch(self, query: str) -> str:
        """인터넷 접속은 여기서만 한다 (테스트 때 이 부분만 가짜로 바꿈)."""
        self.throttle()
        r = httpx.get(RSS_URL.format(q=quote(query)), headers={"User-Agent": USER_AGENT},
                      timeout=10, follow_redirects=True)
        r.raise_for_status()
        return r.text


def _is_non_news(site_url: str) -> bool:
    """'https://blog.naver.com' → True, 'https://m.blog.naver.com' → True, 'https://www.mt.co.kr' → False"""
    host = urlsplit(site_url).netloc.lower()
    return any(host == s or host.endswith("." + s) for s in NON_NEWS_SITES)


def _strip_press(title: str, press: str | None) -> str:
    """'기사 제목 - 머니투데이' → '기사 제목'

    언론사 이름이 제목 끝과 정확히 맞을 때만 뗀다. 모르는 채로 마지막 ' - ' 뒤를 지우면
    '스파오 - 산리오 협업 컬렉션 공개' 같은 제목이 '스파오'로 잘린다.
    """
    if press and title.endswith(f" - {press}"):
        return title.removesuffix(f" - {press}")
    return title


def _to_datetime(t) -> datetime | None:
    """feedparser 시간 → 파이썬 datetime"""
    return datetime.fromtimestamp(timegm(t)) if t else None
