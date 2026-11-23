"""알림 출구: 리포트·경고를 사람에게 보내는 마지막 단계. 담당: D

- `render`: WeeklyReport·ChangeEvent → 메일 HTML / Markdown / 슬랙 블록 (보이는 모양만, 부작용 없음)
- `slack`: Slack Incoming Webhook 발송 (리포트 채널 / 운영 경고 채널), 재시도, 오류 문구의 주소 가리기
- `email`: SMTP 메일 발송
- `templates/`: 메일·Markdown 본문 Jinja 템플릿

흐름: `app/__main__.py`(CLI) → render 로 본문을 만들고 → slack / email 로 내보낸다.
"""
