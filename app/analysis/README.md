# 🅲 analysis — LLM 분석 & 리포트 콘텐츠 (담당: C)

A·B가 만든 `ChangeEvent`를 **분류 → 브랜드별 요약 → 크로스 브랜드 인사이트** 3단계로 분석해 `WeeklyReport`를 만든다.

| 파일 | 하는 일 | 모델 |
|---|---|---|
| `llm_client.py` | Claude API 래퍼 (JSON 응답 검증·재시도, refusal 처리, 토큰 로깅) | — |
| `classify.py` | ① 이벤트별 카테고리·중요도(1~5)·무관 필터 (매일) | `claude-haiku-5-5` |
| `summarize.py` | ② 브랜드별 주간 핵심 변화 3~5개 | `claude-opus-5-5` |
| `insight.py` | ③ 4개 브랜드 비교 인사이트 + 액션 제안 | `claude-opus-5-5` |
| `report_builder.py` | ①~③ + 코드로 계산한 통계(건수·평균/최대 할인율·LLM 비용) → `WeeklyReport` | — |
| `features.py` | 가격 변화율·할인율·SNS 급증 배수 계산, 프롬프트용 이벤트 한 줄 | — |
| `citations.py` | `[#id]` 근거 검사 — 인용 없거나 입력에 없는 id를 인용한 문장 제거 | — |
| `cost.py` | 모델 단가표, 최근 7일 LLM 비용 추정 (리포트 '그 밖의 지표'에 표시) | — |
| `prompts/*.md` | 프롬프트 템플릿 (코드와 분리해 버전 관리) | — |
| `eval/` | 분류 골든셋(`golden_classify.jsonl`) + 정확도 측정 스크립트 | — |

- 입력: `infra.storage.repo`에서 조회한 `ChangeEvent`
- 출력: `WeeklyReport` → D의 `infra.notify`가 렌더링·발송
- 규칙: **숫자(가격·개수·할인율)는 코드로 계산해 프롬프트에 넣는다.** LLM이 계산하게 하지 않는다.
- 규칙: 모든 요약 문장은 근거 `[#event_id]`를 인용해야 하며, 인용 없는 문장은 빌더가 제거한다.
- 모델은 `.env`의 `CLASSIFY_MODEL`, `REPORT_MODEL`로 바꿀 수 있다. 바꾸면 `cost.PRICES`에 단가를 추가할 것.
- 분류: 모델이 빠뜨린 id는 한 번 더 묻고, 그래도 남으면 미분류로 두어 다음 실행에서 재시도. 무관은 중요도 1로 고정.
- 이벤트가 없는 브랜드·요약이 없는 주는 LLM을 부르지 않는다 (비용 절감 + 빈 입력에서 지어내기 방지).

## 평가
```powershell
python -m app.analysis.eval.run_eval --json data/eval/<날짜>.json   # ANTHROPIC_API_KEY 필요
```
카테고리 정확도, 중요도 정확/±1, 카테고리별 정밀도·재현율(특히 '무관' 필터), 틀린 사례 목록을 출력한다.
첫 줄의 `prompt` 해시로 어느 프롬프트 버전의 점수인지 구분한다.
