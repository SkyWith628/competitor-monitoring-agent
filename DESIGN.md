# 경쟁사 모니터링 Agent — 설계 문서

> SPA 브랜드(스파오 / 에잇세컨즈 / 무신사 스탠다드 / 유니클로)의 **공식 사이트·뉴스·SNS**를 매일 수집하고,
> **변화를 감지**해 LLM으로 **주간 인사이트 리포트**를 만들어 **슬랙/메일**로 보내는 에이전트.
>
> 난이도 ★★★ · 팀 4명 · 기간 4주(권장)

---

## 1. 목표와 범위

### 1.1 핵심 질문 (리포트가 매주 답해야 하는 것)
1. **무엇이 바뀌었나?** — 신상품, 가격 인하/인상, 품절, 프로모션·기획전, 메인 배너
2. **무엇이 화제인가?** — 뉴스(신규 매장, 실적, 협업, 모델), SNS 반응(좋아요·댓글 급증 게시물)
3. **그래서 우리는?** — 브랜드별 전략 시그널 해석 + 액션 제안 (예: "유니클로 히트텍 가격 동결 → 스파오 웜테크 프로모션 시점 검토")

### 1.2 MVP 범위
| 구분 | MVP (1~3주차) | 확장 (4주차~) |
|---|---|---|
| 상품 | 신상품·가격 변동 (네이버 쇼핑 검색 API) | 품절·기획전(공식몰 수집 허용 시), 리뷰 수·평점 추이, 랭킹 |
| 뉴스 | 네이버 뉴스 검색 API, Google News RSS | 언론사 직접 RSS, 공시(DART) |
| SNS | 인스타그램 공식계정 게시물·반응수 | 유튜브, 틱톡, 커뮤니티 언급량 |
| 분석 | 변화 분류 + 브랜드별 요약 + 주간 인사이트 | 이상치 탐지, 전주 대비 트렌드 차트 |
| 알림 | 슬랙 주간 리포트 + 메일 | 긴급 알림(대폭 할인 등 즉시 푸시), 슬랙 Q&A 봇 |

### 1.3 비범위 / 주의
- 로그인이 필요한 영역, 개인정보, 유료 데이터는 수집하지 않는다.
- 각 사이트 **robots.txt·이용약관을 1주차에 확인**하고, 요청 간격(≥ 2~3초)·User-Agent를 지킨다. 금지된 경로는 수집 대상에서 제외.
  - 확인 결과(2026-11-24): 4개 브랜드 공식몰 모두 일반 봇의 수집을 금지하거나 차단한다 → 상품·가격은 **네이버 쇼핑 검색 API**로 받는다 (6.1).
- SNS는 **공식 API 우선**(Instagram Graph API의 Business Discovery 등). 비공식 스크래핑은 계정 차단·약관 위반 위험이 있어 지양.

---

## 2. 시스템 아키텍처

```
 ┌──────────────────────── Scheduler (GitHub Actions cron / APScheduler) ───────────────────────┐
 │  매일 06:00 수집 → 변화감지   |   매주 월 08:00 주간 리포트   |   (옵션) 긴급 알림 즉시        │
 └──────────────────────────────────────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
 │ Site        │   │ News        │   │ SNS         │      ← collectors/ (공통 인터페이스 BaseCollector)
 │ Collector   │   │ Collector   │   │ Collector   │
 └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
        └────────── RawItem 리스트 ─────────┘
                          ▼
                 ┌─────────────────┐
                 │ Storage (DB)    │  raw_items / snapshots / change_events / reports
                 └────────┬────────┘
                          ▼
                 ┌─────────────────┐
                 │ Change Detector │  이전 스냅샷과 비교 → ChangeEvent 생성 (규칙 기반, LLM 미사용)
                 └────────┬────────┘
                          ▼
                 ┌─────────────────┐
                 │ LLM Analyzer    │  ① 분류/중요도 태깅 (저비용 모델)
                 │                 │  ② 브랜드별 요약 → ③ 주간 인사이트 (고성능 모델)
                 └────────┬────────┘
                          ▼
                 ┌─────────────────┐
                 │ Report Builder  │  Markdown/HTML 리포트 + 슬랙 Block Kit
                 └────────┬────────┘
                          ▼
                 ┌─────────────────┐
                 │ Notifier        │  Slack Webhook / SMTP 메일
                 └─────────────────┘
```

**설계 원칙**
- **변화 감지는 코드로, 해석은 LLM으로.** 가격·품절·신상품 판단은 규칙 기반 diff로 정확하게 하고, LLM은 "의미 해석·요약"에만 쓴다 → 비용↓, 환각↓.
- **모듈 간 계약은 데이터 모델 하나(`RawItem`, `ChangeEvent`)로.** 4명이 동시에 개발해도 서로 막히지 않게 1주차 첫날 확정한다.
- **모든 단계는 단독 실행 가능** (`python -m app collect --source site --brand spao`) → 디버깅·분업 용이.

---

## 3. 기술 스택

| 영역 | 선택 | 이유 |
|---|---|---|
| 언어 | Python 3.12 | 크롤링·LLM 생태계 |
| 상품·가격 | 네이버 쇼핑 검색 API (`httpx`) | 공식 API. 공식몰 직접 수집은 robots.txt·봇 차단으로 하지 않음 |
| 뉴스 | 네이버 뉴스 검색(NAVER API HUB), Google News RSS(`feedparser`) | 무료(API HUB는 현재 무료 기간)·공식 |
| SNS | Instagram Graph API (Business Discovery), YouTube Data API v3 | 공식 API |
| DB | SQLite(개발) → PostgreSQL(운영) + `SQLAlchemy` | 로컬 시작이 쉬움 |
| LLM | Claude API — 분류: `claude-haiku-5-5`, 요약·인사이트: `claude-opus-5-5` (.env로 교체 가능) | 비용/품질 분리 |
| 스키마 검증 | `pydantic` v2 | LLM 출력 JSON 검증 |
| 알림 | Slack Incoming Webhook(Block Kit), SMTP(`smtplib`) + Jinja2 HTML | 단순·무료 |
| 스케줄 | GitHub Actions cron (or 서버면 APScheduler) | 서버 없이 운영 가능 |
| 대시보드(옵션) | Streamlit | 데모용 |
| 품질 | `pytest`, `ruff`, pre-commit | 4인 협업 코드 일관성 |

---

## 4. 디렉터리 구조 (담당자 표기)

> 폴더 하나 = 역할 하나. 각자 자기 폴더만 수정하고, 폴더 간 통신은 `app/core/models.py`의 모델로만 한다.
> 폴더별 상세 설명은 각 폴더의 `README.md`, 전체 지도는 루트 [README.md](README.md) 참고.

```
MINI/
├── app/
│   ├── __main__.py                  # CLI 진입점 (typer)                         [D]
│   ├── core/                        # 🤝 공통 계약 — 변경 시 전원 승인            [D 관리]
│   │   ├── models.py                #   RawItem, ChangeEvent, WeeklyReport ...
│   │   ├── config.py                #   .env + brands.yaml 로드
│   │   └── base_collector.py        #   모든 수집기의 인터페이스 (throttle 포함)
│   ├── site_monitor/                # 🅰 사이트 수집 & 변화 감지                  [A]
│   │   ├── collectors/              #   spao / eightseconds / musinsa_standard / uniqlo
│   │   ├── shopping.py              #   네이버 쇼핑 검색 API 수집기
│   │   ├── differ.py                #   전일 대비 → 신상품/가격/품절/재입고/기획전
│   │   └── pipeline.py
│   ├── media_monitor/               # 🅱 뉴스·SNS 수집                            [B]
│   │   ├── news/                    #   naver.py, google_rss.py
│   │   ├── sns/                     #   instagram.py, youtube.py
│   │   ├── dedup.py                 #   중복 기사 제거
│   │   ├── spike.py                 #   SNS 반응 급증 감지
│   │   └── pipeline.py
│   ├── analysis/                    # 🅲 LLM 분석 & 리포트 콘텐츠                 [C]
│   │   ├── llm_client.py            #   Claude API 래퍼 (JSON 검증, refusal, 토큰 로깅)
│   │   ├── classify.py / summarize.py / insight.py
│   │   ├── report_builder.py        #   → WeeklyReport
│   │   ├── prompts/                 #   classify.md, summarize.md, insight.md
│   │   └── eval/                    #   골든셋 + 정확도 측정
│   └── infra/                       # 🅳 저장소·알림·통합                         [D]
│       ├── storage/                 #   db.py (스키마), repo.py (모든 저장/조회 함수)
│       └── notify/                  #   render.py, slack.py, email.py, templates/
├── config/brands.yaml               # 브랜드별 URL·키워드·SNS 계정               [B 주도, A]
├── tests/                           # site_monitor/ media_monitor/ analysis/ infra/ fixtures/
├── .github/                         # CODEOWNERS, workflows(ci/daily/weekly)      [D]
├── .env.example
└── pyproject.toml
```

---

## 5. 데이터 모델 (★ 1주차 1일차에 확정할 계약)

```python
# app/models.py
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, HttpUrl

class Brand(str, Enum):
    SPAO = "spao"
    EIGHT_SECONDS = "8seconds"
    MUSINSA_STANDARD = "musinsa_standard"
    UNIQLO = "uniqlo"

class SourceType(str, Enum):
    SITE_PRODUCT = "site_product"     # 상품 1개
    SITE_PAGE = "site_page"           # 메인/기획전 페이지 스냅샷
    NEWS = "news"
    SNS_POST = "sns_post"

class RawItem(BaseModel):
    brand: Brand
    source_type: SourceType
    source: str                 # "spao.com", "naver_news", "instagram" ...
    external_id: str            # 상품코드 / 기사URL 해시 / 게시물ID  → (brand, source, external_id) 유니크
    url: HttpUrl
    title: str
    body: str | None = None     # 기사 본문 요약, 게시물 캡션, 페이지 텍스트
    price: int | None = None    # 원 단위
    sale_price: int | None = None
    in_stock: bool | None = None
    metrics: dict = {}          # {"likes": 1200, "comments": 35} 등
    image_url: HttpUrl | None = None
    published_at: datetime | None = None
    collected_at: datetime

class ChangeType(str, Enum):
    NEW_PRODUCT = "new_product"
    PRICE_DOWN = "price_down"
    PRICE_UP = "price_up"
    SOLD_OUT = "sold_out"
    RESTOCK = "restock"
    PROMO_CHANGED = "promo_changed"   # 메인 배너/기획전 변경
    NEWS = "news"
    SNS_SPIKE = "sns_spike"           # 반응수 급증 게시물
    SNS_NEW_POST = "sns_new_post"

class ChangeEvent(BaseModel):
    id: int | None = None
    brand: Brand
    change_type: ChangeType
    title: str
    detail: dict                # {"before": 39900, "after": 29900} 등
    url: HttpUrl
    detected_at: datetime
    # ↓ LLM Analyzer가 채움
    category: str | None = None         # 가격전략/신제품/마케팅/매장·유통/실적·경영/협업
    importance: int | None = None       # 1~5
    llm_note: str | None = None
```

**DB 테이블**: `raw_items`(최신 상태 + 해시), `snapshots`(일자별 원본, 보관 8주), `change_events`, `reports`(주차별 리포트 원문·발송 이력), `llm_calls`(토큰·비용 로깅).

---

## 6. 모듈별 상세 설계

### 6.1 상품·가격 수집 + 변화 감지 (A)
- **공식몰을 직접 수집하지 않는다.** 2026-11-24 확인 결과 스파오·SSF샵(에잇세컨즈)은 robots.txt가 허용 목록 밖의 봇에 `Disallow: /`이고, 무신사·유니클로는 봇 접속을 차단한다. User-Agent를 속이거나 차단을 우회하지 않는다.
- 대신 **네이버 쇼핑 검색 API**(공식)로 브랜드 상품명·가격을 받는다 (`site_monitor/shopping.py`).
  - `brands.yaml`의 `site.shopping`: 검색어(`queries`), 브랜드 칸 필터(`brand_names`), 공식 판매처 필터(`malls`, 선택)
  - 검색어당 최신 등록순 300개까지. 중고·렌탈·해외직구와 단종·판매예정 상품은 제외한다.
  - 가격은 네이버 쇼핑 최저가(`lprice`). 재판매처 가격이 섞이지 않게 하려면 `malls`에 공식 판매처를 넣는다.
- `differ.py`: `(brand, external_id)` 기준 지난번 대비 → 신규 / 가격 변동(±5% 이상)
  - **첫 수집은 기준선만 저장**하고 이벤트를 만들지 않는다 (처음 본 상품 수백 개가 전부 신상품이 되는 것을 막음).
- **이 방식으로 알 수 없는 것**: 품절·재입고(API에 재고 정보 없음), 메인 배너·기획전 변경(페이지를 보지 않음). 프로모션은 뉴스·SNS(6.2)에서 잡는다. `SOLD_OUT`·`RESTOCK`·`PROMO_CHANGED` 이벤트는 공식몰 수집이 허용되기 전까지 발생하지 않는다.
- 테스트: 문서 형식대로 만든 응답 fixture로 단위 테스트 (실제 API를 매번 부르지 않음)

### 6.2 뉴스·SNS 수집 (B)
- **수집 기간**: 뉴스·SNS 모두 **최근 7일**. 첫 실행 때 몇 달 치 옛 기사·게시물이 한꺼번에 이번 주 이벤트가 되는 것을 막는다.
- **뉴스**: `brands.yaml`의 브랜드별 키워드(`"스파오" OR "SPAO"`)와 제외어(동명이인·무관 기사)
  - 네이버: **NAVER API HUB** 뉴스 검색(`naverapihub.apigw.ntruss.com/search/v1/news`). 키워드당 최신 100건을 받아 `pubDate`가 7일 안인 기사만 남긴다.
    - 2026-11 기존 개발자센터 검색 API가 API HUB로 이전됐다. 키는 NCP 콘솔에서 API HUB 전용으로 발급한다(NCP 계정 마스터 키는 쓰지 않는다).
  - Google News RSS: 검색어에 `when:7d`를 붙인다. 블로그·쇼핑 등 뉴스가 아닌 사이트(`NON_NEWS_SITES`)는 뺀다. RSS 대신 HTML(동의·차단 페이지)이 오면 0건으로 넘기지 않고 예외로 알린다.
  - **공통 관련성 기준**(`news/rules.py`): 두 수집기가 같은 함수로 판단한다.
    - 브랜드명이 제목이나 요약 어디에도 없으면 버린다. 네이버는 요약에 있으면 통과하고, 구글은 요약이 없어 제목만 본다.
    - 제외어가 제목이나 요약에 있으면 버린다.
  - `dedup.py`: 같은 소식을 언론사마다 다른 제목으로 쓴 기사를 하나로 합친다.
    - URL이 같으면(쿼리스트링 무시) 중복이다.
    - 제목에서 브랜드명·기호를 뺀 핵심 내용을 2글자 묶음(bigram)으로 비교한다. 짧은 제목 기준으로 60% 이상 겹치면 같은 소식이다.
    - 숫자나 지역이 서로 다르면 다른 소식이다(예: "3분기" ≠ "4분기", "제주 첫 매장" ≠ "부산 첫 매장").
    - 제목이 많이 달라도 **요약**까지 보면 같은 보도자료인 기사도 합친다. 조사와 흔한 말을 뺀 단어가 4개 이상, 짧은 쪽 기준 30% 이상 겹치면 같은 소식이다.
      - 예: "농심, 무신사와 이색 마케팅 전개" ≒ "[식음료 소식] KT&G·풀무원·농심" (요약이 둘 다 새우깡 55주년 티셔츠)
      - 실데이터(2026-11-23, 7일): 무신사 스탠다드 147→37건, 유니클로 176→61건
    - 같은 소식이면 이미 저장된 기사, 그다음 제목에 브랜드명이 있는 기사가 대표로 남는다. 묶음 기사 제목이 이벤트 제목이 되지 않게 하기 위해서다.
  - 제외어는 실데이터에서 **확실히 무관한** 말만 넣는다(예: 유니클로의 "코웨이", "유니클로 필드"). 애매한 기사는 C의 LLM 분류(무관 필터)에 맡긴다. 제외어를 늘리면 진짜 기사까지 잃기 때문이다.
- **SNS**: 공식 API만 쓴다. 계정당 최근 게시물 20개를 본다.
  - Instagram: 우리 팀 비즈니스 계정 + Graph API `business_discovery`로 경쟁 브랜드 공식 계정의 게시물·좋아요·댓글 수 조회
  - YouTube: Data API v3로 공식 채널의 최근 영상·조회수·좋아요·댓글 수 조회
  - 좋아요·댓글 수를 숨긴 게시물·영상은 그 지표를 0으로 채우지 않고 **비워 둔다**(0과 '모름'은 다르다).
  - 토큰·키는 주소(`?key=`)가 아니라 **헤더**로 보낸다. 오류 메시지와 Actions 로그에 키가 남지 않게 하기 위해서다.
  - `SNS_NEW_POST`: 최근 7일 안에 올라온 새 게시물만 만든다. 오래된 게시물도 저장은 한다(급증 비교의 기준 자료).
  - **실패 격리**: 수집기 하나가 실패해도(예: 인스타 토큰 만료) 나머지 수집기 결과는 저장한다. 실패는 저장을 마친 뒤 예외로 알려 운영 경고로 보낸다.
- **`SNS_SPIKE`**(`spike.py`): 조회수·좋아요·댓글을 **지표별로 따로** 보고, 같은 계정 다른 게시물들의 **최고 기록을 넘으면** 급증이다.
  - 비교할 다른 게시물이 5개 미만이면 판단하지 않는다.
  - 최소 수치(조회수 1,000 / 좋아요 50 / 댓글 10)를 넘어야 한다.
  - 지난번 수집보다 최소 그만큼 늘어야 한다. 천천히 쌓여 기록을 살짝 넘은 영상은 제외한다.
  - 지난번에 이미 최고 기록으로 알렸으면 다시 알리지 않는다.
  - `detail`에 지표별 `평소(중앙값) → 지금 (몇 배, 이전 최고)`와 추정 이유를 남긴다.
    - 댓글이 늘고 캡션에 이벤트·추첨 단어가 있으면 → 댓글 참여 이벤트
    - 조회수만 늘고 좋아요는 평소 수준이면 → 광고(유료 노출) 가능성
  - 처음 안 '평균의 2배' 기준은 실데이터에서 게시물의 약 40%를 잡아 버렸다. 그래서 최고 기록 기준(약 8%)으로 바꿨다.
- API 키: 네이버(NCP API HUB), Meta 앱(인스타 장기 토큰, **약 60일마다 갱신**), Google Cloud(YouTube). 로컬은 `.env`, 자동 수집은 GitHub Secrets에 둔다. 키를 바꾸면 두 곳 모두 고친다.

### 6.3 LLM 분석 (C)
3단계 파이프라인, 모든 출력은 **JSON + pydantic 검증**, 실패 시 1회 재시도.

| 단계 | 입력 | 모델 | 출력 |
|---|---|---|---|
| ① 분류 | ChangeEvent (일 단위 배치) | haiku | `category`, `importance(1~5)`, 한 줄 메모, 무관 기사 필터 |
| ② 브랜드 요약 | 브랜드별 1주치 이벤트(중요도순 상위 N) | opus | 핵심 변화 3~5개, 근거 URL |
| ③ 인사이트 | 4개 브랜드 요약 + 전주 리포트 | opus | 크로스 브랜드 트렌드, 시사점, 액션 제안 3개 |

- **환각 방지**: 프롬프트에 "제공된 이벤트에 없는 사실은 쓰지 말 것, 모든 주장에 event id를 인용" → 빌더가 id를 URL 링크로 치환
- **비용 관리**: `llm_calls`에 토큰 기록, 주간 예상 비용 리포트 하단 표기. 프롬프트 캐싱(공통 시스템 프롬프트) 활용.
- **평가**: 2주차에 과거 1주치 데이터로 골든셋(분류 정답 50건) 만들어 정확도 측정, 프롬프트 버전 관리.

### 6.4 플랫폼·리포트·알림 (D)
- **주간 리포트 구성**
  1. 이번 주 한 줄 요약 (TL;DR)
  2. 🔥 Top 5 변화 (중요도순, 브랜드 태그)
  3. 브랜드별 섹션: 가격·신상품·프로모션·뉴스·SNS
  4. 💡 인사이트 & 액션 제안
  5. 부록: 수치 표(신상품 수, 평균 할인율, SNS 반응 합계 — 전주 대비)
- **슬랙**: Block Kit으로 TL;DR + Top 5 + "전체 리포트 보기"(메일/대시보드 링크). 너무 길면 스레드로 브랜드별 분할.
- **메일**: Jinja2 HTML 템플릿, 수신자 목록은 설정 파일.
- **스케줄**: `daily.yml`(수집·감지·분류), `weekly.yml`(요약·인사이트·발송), `ci.yml`(ruff+pytest). 비밀값은 GitHub Secrets.
- **운영 알림**: 수집 실패, 파싱 0건, LLM 오류 → `#monitor-ops` 채널.

---

## 7. 역할 분배 (4명)

| 역할 | 담당 | 주요 산출물 | 협업 접점 |
|---|---|---|---|
| **A. 사이트 수집 & 변화 감지** | 팀원 1 | 4개 브랜드 사이트 수집기, `differ.py`, HTML fixture 테스트 | `RawItem`→D의 저장소, `ChangeEvent`→C |
| **B. 뉴스·SNS 수집** | 팀원 2 | 뉴스/인스타/유튜브 수집기, 중복 제거, `brands.yaml` 키워드 관리, API 키 발급 | A와 `BaseCollector` 공유, C에 노이즈 기사 피드백 |
| **C. LLM 분석 & 리포트 콘텐츠** | 팀원 3 | 분류·요약·인사이트 프롬프트와 파이프라인, 골든셋 평가, `report/builder.py` | A·B 이벤트 소비, D 템플릿에 데이터 전달 |
| **D. 플랫폼·알림·통합 (PM 겸)** | 팀원 4 | 모델/DB/설정/CLI, 슬랙·메일, 리포트 템플릿, GitHub Actions, (옵션) 대시보드 | 전원 코드 통합, PR 리뷰 배정, 주간 데모 진행 |

> 💡 C와 D가 1주차에 상대적으로 한가하므로: **C는 목업 이벤트로 프롬프트 선개발**, **D는 1~2일차에 뼈대(모델·DB·CLI)를 먼저 깔아 A·B가 바로 붙게** 한다.
> 브랜드가 4개라 "브랜드별 1명" 분배도 생각할 수 있지만, 그러면 4명이 같은 파이프라인을 각자 만들게 되어 중복·충돌이 많다 → **레이어별 분배 + 브랜드 데이터는 모두가 공유**를 추천.

### 7.1 협업 규칙
- 브랜치: `main`(보호) ← `feat/<영역>-<내용>` (예: `feat/site-uniqlo`), PR 최소 1명 리뷰
- 리뷰 짝: **A↔B**(수집), **C↔D**(분석·출력), `models.py` 변경은 **전원 승인**
- 커밋 전 `ruff` + `pytest` (pre-commit), 비밀값은 `.env`만 — 절대 커밋 금지
- 매일 10분 스탠드업(어제/오늘/막힘), 매주 금 데모

---

## 8. 일정 (4주)

| 주차 | A (사이트) | B (뉴스·SNS) | C (LLM) | D (플랫폼) | 마일스톤 |
|---|---|---|---|---|---|
| **1주** | robots/약관 확인, 2개 브랜드 수집기 | API 키 발급, 뉴스 수집기 | 목업 데이터로 분류 프롬프트 | **D1~2: models/DB/CLI 뼈대**, CI | 4개 소스에서 DB 적재 |
| **2주** | 나머지 2개 브랜드, `differ.py` | 인스타·유튜브, 중복 제거 | 분류 파이프라인 + 골든셋 평가 | 슬랙/메일 발송, daily 워크플로 | 매일 자동 수집·변화 감지 |
| **3주** | 파싱 안정화, 실패 알림 | 키워드 튜닝, SNS 급증 감지 | 요약·인사이트, 근거 링크 | 리포트 템플릿, weekly 워크플로 | **첫 주간 리포트 자동 발송** |
| **4주** | (확장) 리뷰·랭킹 | (확장) 커뮤니티 언급량 | 품질 개선, 비용 최적화 | 대시보드, 긴급 알림, 문서화 | 최종 데모 |

---

## 9. 리스크 & 대응

| 리스크 | 대응 |
|---|---|
| 공식몰이 봇 수집을 금지·차단 | 공식몰 직접 수집 대신 네이버 쇼핑 검색 API 사용, 0건 경고, fixture 테스트 |
| 봇 차단·약관 이슈 | robots 준수, 낮은 빈도(일 1회), 공식 API 우선, 금지 영역 제외 |
| Instagram API 심사 지연 | 1주차 즉시 신청, 그동안 뉴스·사이트 우선 개발 |
| LLM 환각 | 이벤트 id 인용 강제, 규칙 기반 수치는 코드로 계산해 주입 |
| 무관 뉴스(동명이인 등) | 제외 키워드 + LLM 분류 단계에서 relevance 필터 |
| 비용 증가 | 분류는 haiku, 상위 N건만 요약, 토큰 로깅 |

---

## 10. 1일차 체크리스트
- [ ] Git 저장소 생성, 브랜치 보호, 팀원 초대 (D)
- [ ] `models.py` 계약 리뷰 미팅 30분 (전원)
- [ ] `brands.yaml` 초안 — 브랜드별 공식 URL·SNS 계정·뉴스 키워드 (B 주도)
- [ ] 각 사이트 robots.txt 확인 및 수집 가능 경로 정리 (A)
- [ ] 네이버 / Meta / Google API 키 신청 (B)
- [ ] Anthropic API 키, Slack Webhook, SMTP 계정 준비 → `.env.example` (D)
- [ ] 목업 ChangeEvent 20건 작성 (C)
