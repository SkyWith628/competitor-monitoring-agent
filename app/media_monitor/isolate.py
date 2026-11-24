"""수집기 실패 격리: 한 수집기가 실패해도 같은 파이프라인의 다른 수집기는 계속 돈다. 담당: B"""
from __future__ import annotations

from app.core.models import Brand


class Isolated:
    """수집기들을 하나씩 감싸서, 한 수집기의 실패가 나머지를 멈추지 않게 한다.

    실패한 수집기는 빈 목록을 돌려주고 실패 내용만 적어 둔다. 모두 돈 뒤 raise_if_failed()로 알린다.
    아직 구현 전인 수집기(NotImplementedError)는 실패로 치지 않고 건너뛴다.
    """

    def __init__(self, *classes: type) -> None:
        self.classes = classes
        self.failures: list[str] = []
        self.not_implemented = 0

    def __iter__(self):
        for cls in self.classes:
            yield lambda brand, cfg, cls=cls: _SafeCollector(self, cls, brand, cfg)

    def raise_if_failed(self, message: str) -> None:
        if self.failures:
            raise RuntimeError(f"{message}: " + "; ".join(self.failures))
        if self.not_implemented == len(self.classes):
            raise NotImplementedError  # 전부 구현 전이면 CLI가 '아직 구현되지 않음'으로 표시


class _SafeCollector:
    def __init__(self, group: Isolated, cls: type, brand: Brand, cfg: dict) -> None:
        self.group, self.cls, self.brand, self.cfg = group, cls, brand, cfg
        self.source: str = getattr(cls, "source", None) or getattr(cls, "__name__", repr(cls))  # 실패해도 어느 수집기인지 알 수 있게

    def collect(self) -> list:
        try:
            return self.cls(self.brand, self.cfg).collect()
        except NotImplementedError:
            self.group.not_implemented += 1
        except Exception as e:  # noqa: BLE001 — 한 수집기 실패가 다른 수집기를 멈추지 않게
            self.group.failures.append(f"{self.source}: {e!r}")
        return []
