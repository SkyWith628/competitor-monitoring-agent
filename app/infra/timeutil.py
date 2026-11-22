"""시간대 처리. 담당: D"""
from __future__ import annotations

import os
import time


def use_korean_time() -> None:
    """프로세스의 로컬 시간대를 한국 시간으로 맞춘다.

    수집 시각(collected_at)·감지 시각(detected_at)은 시간대 없는 datetime.now() 로 찍히고, 주차 경계와
    날짜 묶음은 그 값을 한국 시간으로 읽는다. GitHub Actions 러너는 UTC라서 그대로 두면 9시간 어긋나
    월요일 06:00 KST 수집분이 전주 리포트에 들어간다. (Windows 는 tzset 이 없어 PC 설정을 따른다)
    """
    if hasattr(time, "tzset"):
        os.environ["TZ"] = "Asia/Seoul"
        time.tzset()
