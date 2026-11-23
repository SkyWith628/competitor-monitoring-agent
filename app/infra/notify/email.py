"""SMTP HTML 메일 발송. 담당: D

누가 부르나
    - `app/__main__.py` 의 report 명령 → `send_html` (주간 리포트: render.to_html + to_markdown)
    - 설정 점검 → `missing_settings`(보내지 않고 설정만 확인), `send_test`(실제로 한 통 보내 확인)

데이터 흐름
    제목·HTML·(선택) 텍스트 ─ `_build()` 가 EmailMessage 로 조립 ─ `send_html()` 이 SMTP 서버에 로그인해 전송

설정 (환경 변수)
    필수: SMTP_USER(로그인 계정이자 보내는 주소), SMTP_PASSWORD(Gmail 은 앱 비밀번호), MAIL_TO(쉼표로 여러 명)
    선택: SMTP_HOST(기본 smtp.gmail.com), SMTP_PORT(기본 587. 465 면 처음부터 TLS),
          MAIL_FROM_NAME(받는 사람에게 보이는 보낸 사람 이름, 기본 "경쟁사 모니터링")

재시도는 하지 않는다. 실패하면 예외를 올리고, CLI 가 운영 경고를 보낸 뒤 같은 명령을 다시 실행하면
메일만 다시 나간다.

주의: 이 파일 이름이 표준 라이브러리 `email` 과 같지만, 패키지 안이라 아래의 `from email...` 은
표준 라이브러리를 가리킨다(절대 임포트).
"""
from __future__ import annotations

import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.core.config import env

REQUIRED_ENV = ("SMTP_USER", "SMTP_PASSWORD", "MAIL_TO")
DEFAULT_FROM_NAME = "경쟁사 모니터링"
_TIMEOUT = 30  # 초. 연결·응답 대기 제한 (없으면 메일 서버가 응답하지 않을 때 워크플로가 끝없이 멈춘다)
_SSL_PORT = 465  # 이 포트는 처음부터 TLS(SMTPS), 나머지는 평문 연결 뒤 STARTTLS


def _setting(key: str, default: str = "") -> str:
    """환경 변수 값. 앞뒤 공백·줄바꿈을 뗀다(Secret 을 붙여 넣을 때 끝에 줄바꿈이 자주 섞인다)."""
    return (env(key) or "").strip() or default


def _recipients() -> list[str]:
    """MAIL_TO(쉼표 구분) → 주소 목록. 주소 앞뒤 공백·줄바꿈은 떼고, 주소 안쪽의 줄바꿈은 `_check_header` 가 걸러낸다."""
    return [r.strip() for r in (env("MAIL_TO") or "").split(",") if r.strip()]


def missing_settings() -> list[str]:
    """메일 발송에 꼭 필요한데 비어 있는 환경 변수 이름을 돌려준다. 서버에 연결하지 않는다.

    Returns:
        ["SMTP_USER", "SMTP_PASSWORD", "MAIL_TO"] 중 비어 있는 것(이 순서). 다 있으면 빈 목록.
        값이 맞는지(비밀번호가 옳은지)는 보지 않는다 — 그건 `send_test` 로 확인한다.

    예:
        if missing := email.missing_settings():
            typer.echo(f"메일 설정 없음: {', '.join(missing)}", err=True)
    """
    missing = [k for k in REQUIRED_ENV if not _setting(k)]
    if "MAIL_TO" not in missing and not _recipients():
        missing.append("MAIL_TO")  # " , " 처럼 쉼표만 있는 경우
    return missing


def _check_header(name: str, value: str) -> str:
    """헤더에 들어갈 값에 줄바꿈이 있으면 거절한다 (줄바꿈 뒤에 Bcc: 같은 헤더를 끼워 넣는 공격 방지).

    메일 헤더는 "이름: 값" 한 줄씩이라, 값에 줄바꿈이 들어가면 그 뒤가 새 헤더로 읽힌다. 오류 문구에는
    어느 값인지(name)만 넣고 값 자체는 넣지 않는다 — 주소나 끼워 넣으려던 내용이 경고로 퍼지지 않게.
    """
    if "\r" in value or "\n" in value:
        raise ValueError(f"{name} 에 줄바꿈 문자가 들어 있어 메일을 보내지 않았습니다")
    return value


def _port() -> int:
    """SMTP_PORT 를 숫자로. 숫자가 아니면 원인을 바로 알 수 있는 문구로 실패한다."""
    raw = _setting("SMTP_PORT", "587")
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError("SMTP_PORT 는 숫자여야 합니다 (예: 587, 465)") from None


def _build(subject: str, html: str, text: str | None) -> EmailMessage:
    """보낼 메시지를 조립한다. 헤더에 들어가는 값은 전부 `_check_header` 를 거친다."""
    user = _check_header("SMTP_USER", _setting("SMTP_USER"))
    recipients = [_check_header("MAIL_TO", r) for r in _recipients()]
    from_name = _check_header("MAIL_FROM_NAME", _setting("MAIL_FROM_NAME", DEFAULT_FROM_NAME))

    msg = EmailMessage()
    msg["Subject"] = _check_header("메일 제목", subject)
    # 표시 이름이 한글이어도 헤더 인코딩(RFC 2047)은 Address 가 처리한다
    msg["From"] = Address(display_name=from_name, addr_spec=user) if "@" in user else user
    msg["To"] = ", ".join(recipients)
    # Date·Message-ID 는 표준상 보내는 쪽이 붙여야 한다. 없으면 서버마다 다르게 채우거나 스팸 점수가 오른다
    msg["Date"] = formatdate(localtime=True)
    # Message-ID 의 @ 뒤는 보내는 주소의 도메인으로 맞춘다 (러너의 임시 호스트 이름이 드러나지 않게)
    msg["Message-ID"] = make_msgid(domain=user.rpartition("@")[2] or None)
    # text 가 있으면 plain → html 순서로 넣는다. 메일 클라이언트는 뒤쪽 파트를 우선해 보여주고,
    # HTML 을 못 그리는 환경(알림 미리보기, 텍스트 전용 클라이언트)만 plain 을 쓴다
    if text is None:
        msg.set_content(html, subtype="html", charset="utf-8")
    else:
        msg.set_content(text, charset="utf-8")
        msg.add_alternative(html, subtype="html", charset="utf-8")
    return msg


def send_html(subject: str, html: str, text: str | None = None) -> None:
    """HTML 메일 발송. text를 주면 multipart/alternative(plain + html)로 보낸다.

    포트가 465면 처음부터 TLS(SMTP_SSL)로, 그 밖에는 평문으로 연결한 뒤 STARTTLS로 암호화한다.
    받는 사람은 MAIL_TO 전원이다.

    Args:
        subject: 메일 제목. 줄바꿈이 있으면 안 된다.
        html: HTML 본문 (`render.to_html` 결과).
        text: 같은 내용의 텍스트 본문 (`render.to_markdown` 결과). 주는 쪽을 권한다 — HTML 만 있는
            메일은 스팸으로 분류되기 쉽다.

    Raises:
        RuntimeError: 필수 설정이 비었거나(메시지에 변수 이름 나열) SMTP_PORT 가 숫자가 아니다.
        ValueError: 제목·주소·보낸 사람 이름에 줄바꿈이 있다. 서버에 연결하기 전에 거절한다.
        smtplib.SMTPException: 로그인 거부, 수신 거부 등 서버가 거절했다.
        OSError: 연결 실패·타임아웃(30초).

    예:
        send_html(f"[경쟁사 주간 리포트] {week}", render.to_html(r, prev), text=render.to_markdown(r, prev))
    """
    missing = missing_settings()
    if missing:
        raise RuntimeError(f"{', '.join(missing)} 가 .env 에 없습니다")
    msg = _build(subject, html, text)  # 연결하기 전에 만든다 — 잘못된 헤더 때문에 로그인만 하고 끊지 않도록
    host, port = _setting("SMTP_HOST", "smtp.gmail.com"), _port()
    user, password = _setting("SMTP_USER"), _setting("SMTP_PASSWORD")

    if port == _SSL_PORT:
        with smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT, context=ssl.create_default_context()) as s:
            s.login(user, password)
            s.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=_TIMEOUT) as s:
        # context 를 넘겨야 서버 인증서를 검증한다. 생략하면 암호화는 되지만 상대가 진짜 메일 서버인지
        # 확인하지 않아, 중간에서 가로채는 공격에 비밀번호가 노출될 수 있다.
        s.starttls(context=ssl.create_default_context())
        s.login(user, password)
        s.send_message(msg)


def send_test() -> None:
    """연결 확인 메일을 MAIL_TO 로 한 통 보낸다 (설정 점검용).

    반환값 없음. 실패하면 `send_html` 과 같은 예외를 그대로 올리므로 호출한 쪽이 사용자에게 알린다.

    예:
        try:
            email.send_test()
        except Exception as e:
            typer.echo(f"메일 연결 실패: {slack.describe_error(e)}", err=True)
    """
    line = "경쟁사 모니터링 메일 연결 확인입니다. 이 메일이 보이면 SMTP 설정이 정상입니다."
    html = ('<!doctype html><html lang="ko"><body style="margin:0;padding:16px;">'
            "<p style=\"font-family:'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',Arial,sans-serif;"
            f'font-size:15px;line-height:24px;color:#1f2937;margin:0;">{line}</p></body></html>')
    send_html("[경쟁사 모니터링] 메일 연결 확인", html, text=line + "\n")
