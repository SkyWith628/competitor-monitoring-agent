"""robots.txt 판정기 (RFC 9309 / Google 규격). 담당: A

표준 urllib.robotparser 는 경로의 * · $ 와일드카드를 지원하지 않아
'Disallow: /*/*/cart' 같은 규칙을 놓친다 → 직접 구현.
- User-agent 매칭: 우리 봇 이름에 해당하는 그룹, 없으면 '*' 그룹
- 경로 매칭: 가장 긴 규칙이 이김, 길이가 같으면 Allow 우선
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit


def _bot_token(user_agent: str) -> str:
    return user_agent.split("/")[0].strip().lower()


def _to_regex(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = re.escape(pattern[:-1] if anchored else pattern).replace(r"\*", ".*")
    return re.compile(body + ("$" if anchored else ""))


class Robots:
    def __init__(self, text: str, user_agent: str):
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (s.strip() for s in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if rules:  # 규칙이 나온 뒤의 user-agent 는 새 그룹 시작
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
            elif key in ("allow", "disallow") and agents and value:
                # 빈 Disallow 는 '전부 허용' 이므로 규칙 없음과 같다
                rules.append((key == "allow", value))
        if agents:
            groups.append((agents, rules))

        token = _bot_token(user_agent)
        named = [r for a, r in groups if token in a]
        chosen = named or [r for a, r in groups if "*" in a]
        self.rules = [(allow, len(p), _to_regex(p)) for r in chosen for allow, p in r]

    def can_fetch(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None  # (규칙 길이, allow)
        for allow, length, rx in self.rules:
            if rx.match(path) and (best is None or (length, allow) > best):
                best = (length, allow)
        return True if best is None else best[1]
