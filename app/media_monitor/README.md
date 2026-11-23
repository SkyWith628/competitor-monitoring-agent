# 🅱 media_monitor — 뉴스·SNS 수집 (담당: B)

브랜드 관련 **뉴스 기사**와 공식 **SNS 게시물·반응수**를 수집하고, 중복 제거·반응 급증 감지 후 `ChangeEvent`를 만든다.

| 파일 | 하는 일 |
|---|---|
| `news/naver.py` | 네이버 검색 API(뉴스) 수집기 |
| `news/google_rss.py` | Google News RSS 수집기 |
| `sns/instagram.py` | Instagram Graph API `business_discovery`로 공식계정 게시물·좋아요·댓글 |
| `sns/youtube.py` | YouTube Data API v3 공식 채널 신규 영상·조회수 |
| `dedup.py` | URL 정규화 + 제목 유사도로 중복 기사 병합 |
| `spike.py` | 조회수·좋아요·댓글을 따로, 계정 최근 게시물 최고 기록 경신 시 → `SNS_SPIKE` (평소→지금·추정 이유 포함) |
| `pipeline.py` | `run(brand)` : 수집 → 중복제거 → 저장 → 이벤트화 |

- 입력: `config/brands.yaml`의 `news`, `sns` 항목 (키워드·제외어·계정 — **B가 관리**)
- 출력: `list[RawItem]` → D의 저장소, `list[ChangeEvent]` → C
- API 키: `.env` 의 `NAVER_CLIENT_ID/SECRET`, `IG_*`, `YOUTUBE_API_KEY` (1주차에 발급 신청)
- 규칙: 비공식 SNS 스크래핑 금지, 공식 API만 사용
