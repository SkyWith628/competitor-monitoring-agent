# 🅲 analysis — LLM 분석 & 리포트 콘텐츠 (담당: C)

A·B가 만든 `ChangeEvent`를 **분류 → 브랜드별 요약 → 크로스 브랜드 인사이트** 3단계로 분석해 `WeeklyReport`를 만든다.

| 파일 | 하는 일 | 모델 |
|---|---|---|
| `llm_client.py` | Claude API 래퍼 (JSON 응답 검증·재시도, refusal 처리, 토큰 로깅) | — |
| `classify.py` | ① 이벤트별 카테고리·중요도(1~5)·무관 필터 (매일) | `claude-haiku-5-5` |
| `summarize.py` | ② 브랜드별 주간 핵심 변화 3~5개 | `claude-opus-5-5` |
| `insight.py` | ③ 4개 브랜드 비교 인사이트 + 액션 제안 | `claude-opus-5-5` |
| `report_builder.py` | ①~③ + 코드로 계산한 통계 → `WeeklyReport` | — |
| `prompts/*.md` | 프롬프트 템플릿 (코드와 분리해 버전 관리) | — |
| `eval/` | 분류 골든셋(`golden_classify.jsonl`) + 정확도 측정 스크립트 | — |

- 입력: `infra.storage.repo`에서 조회한 `ChangeEvent`
- 출력: `WeeklyReport` → D의 `infra.notify`가 렌더링·발송
- 규칙: **숫자(가격·개수·할인율)는 코드로 계산해 프롬프트에 넣는다.** LLM이 계산하게 하지 않는다.
- 규칙: 모든 요약 문장은 근거 `[#event_id]`를 인용해야 하며, 인용 없는 문장은 빌더가 제거한다.
- 모델은 `.env`의 `CLASSIFY_MODEL`, `REPORT_MODEL`로 바꿀 수 있다.
