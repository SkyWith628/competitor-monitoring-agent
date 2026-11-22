"""시간대 처리. 담당: D

이 프로젝트의 시각 규칙
    - 수집 시각(RawItem.collected_at)과 감지 시각(ChangeEvent.detected_at)은 app/core/models.py 가
      datetime.now() 로 찍는다. 시간대 정보가 없는 값이고, "한국 시각"이라고 약속한다.
    - 주차 경계(월요일 00:00), 날짜별 묶음, "36시간째 수집 없음" 같은 판단이 모두 이 약속에 기대고 있다.
    - 그래서 CLI는 실행할 때마다 use_korean_time() 으로 프로세스의 시간대를 한국으로 맞춘다.
      UTC 로 도는 GitHub Actions 러너에서도 datetime.now() 가 한국 시각을 돌려주게 하기 위해서다.
"""
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
