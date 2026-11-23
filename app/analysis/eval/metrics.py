"""골든셋 대비 분류 결과 채점 (LLM 호출 없음 — 테스트 가능한 순수 함수). 담당: C"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.core.models import Category, ChangeEvent

MISSING = "(없음)"  # 모델이 해당 id에 라벨을 안 준 경우


@dataclass
class Miss:
    id: int
    title: str
    want_category: str
    got_category: str
    want_importance: int
    got_importance: int | None


@dataclass
class CategoryScore:
    precision: float | None  # 그 카테고리로 예측한 것 중 맞은 비율 (예측 0건이면 None)
    recall: float  # 정답이 그 카테고리인 것 중 맞힌 비율
    support: int  # 정답 건수


@dataclass
class EvalResult:
    n: int
    category_correct: int
    importance_exact: int
    importance_near: int  # 오차 ±1 이내
    missing: int  # 라벨 누락 건수
    per_category: dict[str, CategoryScore] = field(default_factory=dict)
    misses: list[Miss] = field(default_factory=list)

    @property
    def category_accuracy(self) -> float:
        return self.category_correct / self.n if self.n else 0.0

    @property
    def irrelevant(self) -> CategoryScore:
        """무관 필터 성능. recall이 낮으면 잡음 기사가 리포트로 새고, precision이 낮으면 진짜 소식이 버려진다."""
        return self.per_category[Category.IRRELEVANT.value]


def evaluate(rows: list[dict], predicted: dict[int, ChangeEvent]) -> EvalResult:
    pairs: list[tuple[str, str]] = []
    imp_exact = imp_near = missing = 0
    misses: list[Miss] = []

    for r in rows:
        eid = r["event"]["id"]
        p = predicted.get(eid)
        got_cat = p.category.value if p and p.category else MISSING
        got_imp = p.importance if p else None
        if got_cat == MISSING:
            missing += 1
        pairs.append((r["category"], got_cat))
        if got_imp is not None:
            imp_exact += got_imp == r["importance"]
            imp_near += abs(got_imp - r["importance"]) <= 1
        if got_cat != r["category"] or got_imp != r["importance"]:
            misses.append(Miss(eid, r["event"]["title"], r["category"], got_cat, r["importance"], got_imp))

    want_n = Counter(w for w, _ in pairs)
    got_n = Counter(g for _, g in pairs)
    hit_n = Counter(w for w, g in pairs if w == g)
    per_category = {
        c.value: CategoryScore(
            precision=hit_n[c.value] / got_n[c.value] if got_n[c.value] else None,
            recall=hit_n[c.value] / want_n[c.value] if want_n[c.value] else 0.0,
            support=want_n[c.value],
        )
        for c in Category
    }
    return EvalResult(
        n=len(rows),
        category_correct=sum(hit_n.values()),
        importance_exact=imp_exact,
        importance_near=imp_near,
        missing=missing,
        per_category=per_category,
        misses=misses,
    )


def _pct(x: float | None) -> str:
    return "  -  " if x is None else f"{x:5.0%}"


def format_report(res: EvalResult) -> str:
    n = res.n or 1
    lines = [
        f"category accuracy : {res.category_correct}/{res.n} ({res.category_correct / n:.0%})",
        f"importance exact  : {res.importance_exact}/{res.n} ({res.importance_exact / n:.0%})",
        f"importance ±1     : {res.importance_near}/{res.n} ({res.importance_near / n:.0%})",
        f"label missing     : {res.missing}",
        "",
        "카테고리        정밀도  재현율  정답수",
    ]
    for name, s in res.per_category.items():
        lines.append(f"  {name:<10}  {_pct(s.precision)}  {_pct(s.recall)}  {s.support:>4}")
    if res.misses:
        lines += ["", "틀린 사례 (정답 → 예측):"]
        for m in res.misses:
            lines.append(f"  #{m.id} {m.title}  |  {m.want_category}/{m.want_importance}"
                         f" → {m.got_category}/{m.got_importance if m.got_importance is not None else '-'}")
    return "\n".join(lines)
