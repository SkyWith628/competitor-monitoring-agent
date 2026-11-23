"""Instagram Graph API(business_discovery) 공식 계정 수집기.

우리 팀 비즈니스 계정(IG_BUSINESS_ACCOUNT_ID)의 권한으로 경쟁 브랜드 공식 비즈니스 계정의
공개 게시물·좋아요·댓글 수를 조회한다. 비공식 스크래핑 없이 공식 API만 사용.
"""
from __future__ import annotations

from datetime import datetime

import httpx

from app.core.base_collector import BaseCollector
from app.core.config import env
from app.core.models import RawItem, SourceType

API = "https://graph.facebook.com/v27.0"
MAX_POSTS = 20  # 계정당 최근 게시물 몇 개까지 볼지
MEDIA_FIELDS = "id,caption,like_count,comments_count,timestamp,permalink,media_type,media_url,thumbnail_url"
# 공백 + 캡션 줄 간격용 투명 문자들 (점자 공백, 폭 없는 공백·결합자, BOM, 한글 채움 문자)
INVISIBLE = " \t\r\u2800\u200b\u200c\u200d\ufeff\u3164"


class InstagramCollector(BaseCollector):
    source = "instagram"
    request_interval_sec = 1.0

    def collect(self) -> list[RawItem]:
        username = self.config.get("instagram")
        if not username:  # brands.yaml 에 계정명이 아직 없으면 건너뛰기
            return []

        # ① 우리 계정 이름으로 브랜드 계정의 최근 게시물을 한 번에 조회
        fields = f"business_discovery.username({username}){{media.limit({MAX_POSTS}){{{MEDIA_FIELDS}}}}}"
        data = self._get(fields)
        if "business_discovery" not in data:  # 200 인데 계정 정보가 빠진 응답
            raise RuntimeError(f"Instagram API 오류: '{username}' 계정 정보가 응답에 없습니다")
        posts = data["business_discovery"].get("media", {}).get("data", [])

        # ② 팀 약속 양식(RawItem)에 옮겨 적기
        items: list[RawItem] = []
        for p in posts:
            caption = (p.get("caption") or "").strip()
            items.append(RawItem(
                brand=self.brand,
                source_type=SourceType.SNS_POST,
                source=self.source,
                external_id=p["id"],
                url=p["permalink"],
                title=_title_from(caption, p.get("media_type", "")),
                body=caption or None,
                # 좋아요 수를 숨긴 게시물은 like_count 가 아예 안 온다.
                # 0(아무도 안 누름)과 '모름'은 다르므로 0으로 채우지 않고 칸을 비워 둔다
                # → 급증 감지가 값이 있는 게시물끼리만 비교한다
                metrics={name: p[key] for name, key in (("likes", "like_count"), ("comments", "comments_count"))
                         if key in p},
                # 영상(VIDEO)은 media_url 이 영상 파일이라 썸네일을 쓴다
                image_url=p.get("thumbnail_url") or p.get("media_url"),
                published_at=_to_datetime(p.get("timestamp")),
            ))
        return items

    def _get(self, fields: str) -> dict:
        """인터넷 접속은 여기서만 한다 (테스트 때 이 부분만 가짜로 바꿈)."""
        token, me = env("IG_ACCESS_TOKEN"), env("IG_BUSINESS_ACCOUNT_ID")
        if not token or not me:
            raise RuntimeError(".env 에 IG_ACCESS_TOKEN / IG_BUSINESS_ACCOUNT_ID 를 채워야 합니다")
        self.throttle()
        # 토큰을 주소(?access_token=)가 아니라 헤더로 보낸다 → 요청 주소가 로그에 찍혀도 토큰은 없다
        r = httpx.get(f"{API}/{me}", params={"fields": fields},
                      headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if r.status_code != 200:
            # httpx 기본 에러(요청 URL 포함) 대신 Meta 에러 메시지만 보여준다
            raise RuntimeError(f"Instagram API 오류 {r.status_code}: {_error_message(r)}")
        try:
            return r.json()
        except ValueError:  # 200 인데 JSON 이 아닌 응답 (점검 페이지 등)
            raise RuntimeError(f"Instagram API 오류: JSON 이 아닌 응답 {r.text[:200]!r}") from None


def _error_message(r: httpx.Response) -> str:
    """Meta 오류 응답(JSON)의 message 만 꺼낸다. JSON 이 아니면(예: 502 HTML) 앞부분만."""
    try:
        return r.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return r.text[:200]


def _title_from(caption: str, media_type: str) -> str:
    """캡션의 첫 번째 '글자가 있는' 줄을 제목으로 (최대 80자). 캡션이 없으면 '(캡션 없음 · VIDEO)'

    인스타 캡션은 줄 간격용 투명 문자(⠀ U+2800, 폭 없는 공백 U+200B 등)로 시작하는 경우가 많아 함께 지운다.
    """
    for line in caption.split("\n"):
        line = line.strip(INVISIBLE)
        if line:
            return line[:80]
    return f"(캡션 없음 · {media_type})" if media_type else "(캡션 없음)"


def _to_datetime(s: str | None) -> datetime | None:
    """'2026-11-21T15:12:21+0000' → 로컬 시간 datetime (collected_at과 형식 맞춤)"""
    if not s:
        return None
    return datetime.fromisoformat(s).astimezone().replace(tzinfo=None)
