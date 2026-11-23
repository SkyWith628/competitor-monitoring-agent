"""[D] 플랫폼: 저장소·알림·통합."""
from datetime import timedelta, timezone

KST = timezone(timedelta(hours=9))  # 스케줄 실행 서버는 UTC — "오늘·지난주"는 항상 KST로 계산
