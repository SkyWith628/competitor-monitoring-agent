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
        if not channel["items"]:
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
                metrics={
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),  # 좋아요 수를 숨긴 영상은 0
                    "comments": int(stats.get("commentCount", 0)),
                },
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
        r = httpx.get(f"{API}/{endpoint}", params={**params, "key": key}, timeout=10)
        r.raise_for_status()
        return r.json()


def _to_datetime(s: str | None) -> datetime | None:
    """'2026-11-20T09:00:00Z' → 로컬 시간 datetime (collected_at과 형식 맞춤)"""
    if not s:
        return None
    return datetime.fromisoformat(s).astimezone().replace(tzinfo=None)  # Python 3.11+ 는 'Z' 인식
