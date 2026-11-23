"""YouTube Data API v3 공식 채널 수집기."""
from __future__ import annotations

from datetime import datetime

import httpx

from app.core.base_collector import BaseCollector
from app.core.config import env
from app.core.models import RawItem, SourceType

API = "https://www.googleapis.com/youtube/v3"
MAX_VIDEOS = 20  # 채널당 최근 영상 몇 개까지 볼지


class YoutubeCollector(BaseCollector):
    source = "youtube"
    request_interval_sec = 1.0

    def collect(self) -> list[RawItem]:
        channel_id = self.config.get("youtube_channel_id")
        if not channel_id:  # brands.yaml 에 채널 ID가 아직 없으면 건너뛰기
            return []

        # ① 채널의 "업로드 영상 목록"(uploads 재생목록) ID 찾기
        channel = self._get("channels", {"part": "contentDetails", "id": channel_id})
        if not channel.get("items"):  # 없는 채널이면 items 가 비거나 아예 빠져서 온다
            raise RuntimeError(f"유튜브 채널을 찾을 수 없습니다: {channel_id}")
        uploads = channel["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # ② 업로드 목록에서 최근 영상 ID 가져오기
        playlist = self._get("playlistItems", {"part": "contentDetails", "playlistId": uploads,
                                               "maxResults": MAX_VIDEOS})
        video_ids = [v["contentDetails"]["videoId"] for v in playlist["items"]]
        if not video_ids:
            return []

        # ③ 영상들의 제목·날짜(snippet)와 조회수·좋아요·댓글(statistics)을 한 번에 받기
        videos = self._get("videos", {"part": "snippet,statistics", "id": ",".join(video_ids)})

        # ④ 팀 약속 양식(RawItem)에 옮겨 적기
        items: list[RawItem] = []
        for v in videos["items"]:
            s, stats = v["snippet"], v.get("statistics", {})
            items.append(RawItem(
                brand=self.brand,
                source_type=SourceType.SNS_POST,
                source=self.source,
                external_id=v["id"],
                url=f"https://www.youtube.com/watch?v={v['id']}",
                title=s["title"],
                # 좋아요(댓글)를 숨긴 영상은 likeCount(commentCount)가 아예 안 온다.
                # 0(아무도 안 누름)과 '모름'은 다르므로 0으로 채우지 않고 칸을 비워 둔다
                metrics={name: int(stats[key]) for name, key in
                         (("views", "viewCount"), ("likes", "likeCount"), ("comments", "commentCount"))
                         if key in stats},
                image_url=s.get("thumbnails", {}).get("high", {}).get("url"),
                published_at=_to_datetime(s.get("publishedAt")),
            ))
        return items

    def _get(self, endpoint: str, params: dict) -> dict:
        """인터넷 접속은 여기서만 한다 (테스트 때 이 부분만 가짜로 바꿈)."""
        key = env("YOUTUBE_API_KEY")
        if not key:
            raise RuntimeError(".env 에 YOUTUBE_API_KEY 를 채워야 합니다")
        self.throttle()
        # 키를 주소(?key=)가 아니라 헤더로 보낸다. 주소에 넣으면 요청이 실패했을 때
        # httpx 오류 메시지(요청 주소 포함)에 키가 그대로 찍혀 슬랙·Actions 로그로 새어 나간다
        r = httpx.get(f"{API}/{endpoint}", params=params, headers={"X-Goog-Api-Key": key}, timeout=10)
        if r.status_code != 200:
            raise RuntimeError(f"YouTube API 오류 {r.status_code}: {_error_message(r)}")
        return r.json()


def _error_message(r: httpx.Response) -> str:
    """Google 오류 응답(JSON)의 message 만 꺼낸다. JSON 이 아니면(예: 502 HTML) 앞부분만."""
    try:
        return r.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return r.text[:200]


def _to_datetime(s: str | None) -> datetime | None:
    """'2026-11-20T09:00:00Z' → 로컬 시간 datetime (collected_at과 형식 맞춤)"""
    if not s:
        return None
    return datetime.fromisoformat(s).astimezone().replace(tzinfo=None)  # Python 3.11+ 는 'Z' 인식
