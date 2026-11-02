"""텔레그램 알림과 보드 이미지 전송.

형제 프로젝트 liquidity-feed 의 utils/telegram_util.py 를 가져와 사진 전송
(send_photo)을 더했다. 성공하면 보드 PNG 를 게시물 링크와 함께 보내고,
실패하면 사유를 보낸다.

형제 프로젝트 krx-daily-brief 와 같은 환경변수 이름을 쓴다.
  TELEGRAM_BOT_TOKEN       봇 토큰
  TELEGRAM_CHAT_ID         운영 알림이 가는 방
  TELEGRAM_CHAT_TEST_ID    실패와 테스트 알림이 가는 방

**알림 실패는 작업 실패가 아니다.** 게시글이 이미 올라갔는데 알림이 안 갔다고
전체를 실패로 만들면 안 된다. 이 클래스의 메서드는 예외를 밖으로 내지 않고
성공 여부를 bool 로 돌려준다.
"""

from __future__ import annotations

import html as html_mod
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from utils.logger_util import LoggerUtil

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

API = "https://api.telegram.org"
TIMEOUT_SEC = 15.0
# 사진 업로드는 수백 KB 라 메시지보다 오래 걸린다.
PHOTO_TIMEOUT_SEC = 60.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SEC = 2
MAX_RETRY_AFTER_SEC = 60
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# 텔레그램 제한. 사진 캡션은 1024자, 사진 파일은 10MB 까지다.
CAPTION_MAX = 1024
PHOTO_MAX_BYTES = 10 * 1024 * 1024


def env(name: str) -> str:
    return os.getenv(name, "").strip()


class TelegramUtil:
    def __init__(self):
        self.logger = LoggerUtil().get_logger()

    def is_configured(self) -> bool:
        """토큰과 방 번호가 있는지. 없으면 알림을 건너뛴다."""
        return bool(env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"))

    def _call(self, method: str, chat_id: str, data: dict,
              files: dict | None = None, timeout: float = TIMEOUT_SEC) -> bool:
        token = env("TELEGRAM_BOT_TOKEN")
        if not token or not chat_id:
            self.logger.debug("텔레그램 설정이 없어 알림을 건너뛴다")
            return False
        # 네트워크 오류, 429, 5xx 는 잠깐 쉬고 다시 보낸다. 400 같은 요청 오류는
        # 다시 보내도 같으니 바로 끝낸다. 응답 대기 시간 초과 뒤 재시도하면 이미
        # 도착한 알림이 한 번 더 갈 수 있다.
        # files 의 내용은 bytes 로 받는다. 파일 객체를 넘기면 재시도 때 읽기
        # 위치가 끝에 가 있어 빈 파일이 올라간다.
        for attempt in range(1, MAX_ATTEMPTS + 1):
            wait_sec = RETRY_BACKOFF_SEC * attempt
            try:
                resp = httpx.post(
                    f"{API}/bot{token}/{method}",
                    data={"chat_id": chat_id, "parse_mode": "HTML", **data},
                    files=files,
                    timeout=timeout,
                )
            # 알림 실패로 작업을 죽이지 않는다. 예외 메시지에는 봇 토큰이 든
            # 요청 URL 이 들어가므로 예외 종류만 남긴다.
            except Exception as exc:
                failure = type(exc).__name__
                retryable = isinstance(exc, httpx.TransportError)
            else:
                try:
                    body = resp.json()
                except ValueError:
                    body = {}
                if resp.status_code == 200 and body.get("ok"):
                    break
                reason = body.get("description") or resp.text[:120]
                failure = f"HTTP {resp.status_code} {reason}"
                retryable = resp.status_code in RETRYABLE_STATUS_CODES
                retry_after = (body.get("parameters") or {}).get("retry_after")
                if retry_after:
                    wait_sec = min(int(retry_after) + 1, MAX_RETRY_AFTER_SEC)

            if not retryable or attempt == MAX_ATTEMPTS:
                self.logger.error(
                    f"텔레그램 {method} 실패 (chat_id={chat_id}, {attempt}/{MAX_ATTEMPTS}): {failure}"
                )
                return False
            self.logger.warning(
                f"텔레그램 {method} 재시도 (chat_id={chat_id}, {attempt}/{MAX_ATTEMPTS}): {failure}"
            )
            time.sleep(wait_sec)

        # 받아준 방 이름을 남긴다. 엉뚱한 방으로 가도 로그로 알아챌 수 있다.
        chat = body.get("result", {}).get("chat", {})
        name = chat.get("title") or chat.get("username") or chat.get("first_name") or "?"
        self.logger.info(f"텔레그램 {method} 완료 (chat_id={chat_id}, 방 {name})")
        return True

    def _send(self, chat_id: str, text: str) -> bool:
        return self._call("sendMessage", chat_id, {"text": text})

    def send_message(self, text: str) -> bool:
        """운영 방으로 보낸다."""
        return self._send(env("TELEGRAM_CHAT_ID"), text)

    def send_error(self, text: str) -> bool:
        """실패 방으로 보낸다. 없으면 운영 방으로 보낸다."""
        return self._send(env("TELEGRAM_CHAT_TEST_ID") or env("TELEGRAM_CHAT_ID"), text)

    def send_photo(self, photo_path: str | Path, caption: str = "") -> bool:
        """운영 방으로 사진을 보낸다. 캡션은 HTML 이며 1024자를 넘으면 자른다.

        텔레그램은 sendPhoto 로 받은 사진을 다시 압축한다. 보드 글자가 뭉개질
        만큼은 아니지만, 원본 해상도가 필요해지면 sendDocument 로 바꾼다.
        """
        path = Path(photo_path)
        if not path.exists():
            self.logger.error(f"텔레그램 사진 전송 건너뜀: 파일이 없다 ({path})")
            return False
        content = path.read_bytes()
        if len(content) > PHOTO_MAX_BYTES:
            self.logger.error(
                f"텔레그램 사진 전송 건너뜀: {len(content) / 1024 / 1024:.1f} MB 로 10MB 상한 초과"
            )
            return False
        if len(caption) > CAPTION_MAX:
            self.logger.warning(f"캡션이 {len(caption)}자라 {CAPTION_MAX}자로 자른다")
            caption = caption[:CAPTION_MAX]
        return self._call(
            "sendPhoto",
            env("TELEGRAM_CHAT_ID"),
            {"caption": caption},
            files={"photo": (path.name, content, "image/png")},
            timeout=PHOTO_TIMEOUT_SEC,
        )

    @staticmethod
    def photo_caption(title: str, url: str | None, facts: list[str]) -> str:
        """보드 사진 캡션. parse_mode 가 HTML 이라 escape 가 필요하다.

        훑어보는 알림이라 짧게 유지한다. 제목, 요약 몇 줄, 링크로 끝낸다.
        facts 는 render.summary_lines() 가 만든다. ETF 링크가 들어 있는 텔레그램
        HTML 이고 escape 도 거기서 끝냈으므로 여기서는 그대로 붙인다. url 이
        없으면(게시를 안 했거나 실패) 링크 줄을 빼고 보낸다.
        """
        lines = [f"📈 <b>{html_mod.escape(title)}</b>", ""]
        lines += facts
        if url:
            # 문구 링크로 건다. 주소를 그대로 노출하면 텔레그램이 누를 수 있는
            # 링크로 만들어 주지 않는 경우가 있다.
            safe = html_mod.escape(url, quote=True)
            lines += ["", f'<a href="{safe}">게시물 열기</a>']
        return "\n".join(lines)

    @staticmethod
    def failure_message(stage: str, detail: str) -> str:
        """실패 알림 본문. stage 는 어느 단계에서 깨졌는지."""
        return (
            f"<b>[etf-card-board] {html_mod.escape(stage)} 실패</b>\n\n"
            f"{html_mod.escape(detail[:600])}"
        )
