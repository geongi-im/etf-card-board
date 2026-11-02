"""MQWAY 게시판 API 전송.

형제 프로젝트 liquidity-feed 의 utils/api_util.py 를 가져왔다. 같은 규약을 따른다.
BASE_URL 은 스킴과 호스트까지만 받고 /api 는 여기서 붙인다.

서버 응답에서 주의할 점 두 가지.
  - HTTP 200 이라도 실패일 수 있다. 본문의 success 플래그를 봐야 한다.
    (BoardApiController 가 검증 실패를 200 + success:false 로 돌려준다)
  - 생성된 게시글 번호는 data.id 로 온다. 조회 주소는
    {BASE_URL}/board-research/{id} 이고 열람에는 로그인이 필요하다.

보내는 본문은 완결 문서가 아니라 조각이어야 한다. 게시판 페이지 본문에
그대로 삽입되므로 <!DOCTYPE> 나 <head> 가 들어가면 안 되고, CSS 도
.eci 안쪽으로 범위를 좁혀야 게시판 페이지가 덮이지 않는다.
render.render_fragment() 가 그 조각이고, output/ 에 저장하는 완결 문서는 보내면 안 된다.

썸네일이 있으면 multipart 로, 없으면 JSON 으로 보낸다.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from utils.logger_util import LoggerUtil

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# 게시판은 바뀌지 않으므로 고정한다. board-* 엔드포인트에는 인증
# 미들웨어가 없다. 키 없이 URL 로 바로 POST 한다.
MQWAY_BOARD = "board-research"

# 서버가 응답하지 않을 때 무한 대기하지 않도록 반드시 건다.
TIMEOUT_SEC = 30.0

# mq_content 가 MySQL text 라 64KB 가 상한이다. 넘기면 조용히 잘리므로
# 보내기 전에 막는다.
CONTENT_MAX_BYTES = 65535

# 서버 검증 규칙 (BoardApiController)
TITLE_MAX = 255
CATEGORY_MAX = 50
WRITER_MAX = 50

THUMBNAIL_MAX_BYTES = 1 * 1024 * 1024
THUMBNAIL_MAX_WIDTH = 800


class MissingSettingError(RuntimeError):
    """필수 환경변수가 없을 때. 어떤 값을 어떻게 채우는지 메시지에 담는다."""


def base_url() -> str:
    """사이트 주소. 스킴과 호스트까지만. 게시물 조회 URL 조립에도 쓴다.

    BASE_URL 은 스킴과 호스트까지만 적고 /api 는 코드가 붙인다.
    형제 프로젝트가 같은 규칙을 쓰므로 .env 를 공유할 수 있다.
      BASE_URL=http://localhost   ->  http://localhost/api/board-research

    기본값을 두지 않는다. 기본값이 있으면 .env 를 빠뜨린 채 실행했을 때
    운영 서버로 글이 올라간다. 형제 프로젝트도 같은 이유로 막아둔다.
    """
    value = os.getenv("BASE_URL", "").strip()
    if not value:
        raise MissingSettingError(
            "환경변수 BASE_URL 이 없다. .env 에 설정한다 (.env.example 참고). "
            "예) BASE_URL=http://localhost"
        )
    return value.rstrip("/")


def mqway_endpoint() -> str:
    """게시물을 POST 할 주소."""
    return f"{base_url()}/api/{MQWAY_BOARD}"


class ApiError(Exception):
    """게시 실패. status_code 는 HTTP 상태 또는 자체 판단 값이다."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"MQWAY 전송 실패 (status={status_code}): {message}")


class ApiUtil:
    def __init__(self):
        self.logger = LoggerUtil().get_logger()

    @staticmethod
    def check_payload(title: str, content: str, category: str, writer: str) -> None:
        """보내기 전에 서버 검증 규칙과 컬럼 상한을 먼저 확인한다."""
        if not title.strip():
            raise ApiError(400, "제목이 비어 있다")
        if not content.strip():
            raise ApiError(400, "본문이 비어 있다")

        size = len(content.encode("utf-8"))
        if size > CONTENT_MAX_BYTES:
            raise ApiError(
                400,
                f"본문이 {size:,} B 로 상한 {CONTENT_MAX_BYTES:,} B 를 넘는다. "
                "카드 수를 줄이거나 MQWAY 쪽 컬럼을 mediumtext 로 올려야 한다",
            )

        lowered = content.lstrip().lower()
        if lowered.startswith("<!doctype") or lowered.startswith("<html"):
            raise ApiError(
                400,
                "완결 문서를 보내려 한다. 게시판 본문에는 조각만 넣는다. "
                "render() 가 아니라 render_fragment() 결과를 보낸다",
            )

        for name, value, limit in (
            ("제목", title, TITLE_MAX),
            ("카테고리", category, CATEGORY_MAX),
            ("작성자", writer, WRITER_MAX),
        ):
            if len(value) > limit:
                raise ApiError(400, f"{name}가 {len(value)}자로 {limit}자 상한을 넘는다")

    def _compress_image(self, path: Path) -> tuple[bytes, str] | None:
        """썸네일을 줄여서 바이트로 돌려준다. 실패하면 None.

        썸네일 문제로 게시 자체가 막히면 안 되므로 예외를 밖으로 내지 않는다.
        """
        try:
            from PIL import Image
        except ImportError:
            self.logger.warning("Pillow 미설치. 썸네일 없이 보낸다")
            return None

        try:
            with Image.open(path) as img:
                fmt = (img.format or "PNG").upper()
                if img.width > THUMBNAIL_MAX_WIDTH:
                    ratio = THUMBNAIL_MAX_WIDTH / img.width
                    img = img.resize(
                        (THUMBNAIL_MAX_WIDTH, int(img.height * ratio)),
                        Image.Resampling.LANCZOS,
                    )

                buf = io.BytesIO()
                if fmt == "PNG":
                    img.save(buf, format="PNG", optimize=True)
                else:
                    img.save(buf, format=fmt, quality=85, optimize=True)
                data = buf.getvalue()

                # 그래도 크면 JPEG 로 품질을 낮춰가며 줄인다
                quality = 85
                while len(data) > THUMBNAIL_MAX_BYTES and quality > 30:
                    buf = io.BytesIO()
                    img.convert("RGB").save(
                        buf, format="JPEG", quality=quality, optimize=True
                    )
                    data = buf.getvalue()
                    fmt = "JPEG"
                    quality -= 10

                self.logger.debug(f"썸네일 준비 완료: {len(data) / 1024:.1f} KB ({fmt})")
                return data, fmt.lower()
        # 썸네일 실패로 게시를 막지 않는다
        except Exception as exc:
            self.logger.warning(
                f"썸네일 처리 실패: {type(exc).__name__}: {exc}. 없이 보낸다"
            )
            return None

    def post_url(self, response: dict | None) -> str | None:
        """게시 응답에서 조회 주소를 만든다. 만들 수 없으면 None.

        서버는 생성된 게시글 번호를 data.id 로 돌려준다.
        """
        data = (response or {}).get("data") or {}
        post_id = data.get("id")
        if post_id is None:
            self.logger.warning("응답에 게시글 id 가 없어 게시물 주소를 만들지 못했다")
            return None
        return f"{base_url()}/{MQWAY_BOARD}/{post_id}"

    def create_post(
        self,
        title: str,
        content: str,
        category: str,
        writer: str,
        thumbnail_path: Path | None = None,
    ) -> dict:
        """게시글을 만든다. 성공하면 응답 dict 를, 실패하면 ApiError 를 낸다."""
        self.check_payload(title, content, category, writer)

        url = mqway_endpoint()
        fields = {
            "title": title,
            "content": content,
            "category": category,
            "writer": writer,
        }

        thumb = None
        if thumbnail_path and Path(thumbnail_path).exists():
            thumb = self._compress_image(Path(thumbnail_path))
        elif thumbnail_path:
            self.logger.warning(f"썸네일 파일이 없다: {thumbnail_path}. 없이 보낸다")

        try:
            if thumb:
                data, fmt = thumb
                resp = httpx.post(
                    url,
                    data=fields,
                    files={
                        "thumbnail_image": (
                            Path(thumbnail_path).name,
                            data,
                            f"image/{fmt}",
                        )
                    },
                    headers={"Accept": "application/json"},
                    timeout=TIMEOUT_SEC,
                )
            else:
                resp = httpx.post(
                    url,
                    json=fields,
                    headers={"Accept": "application/json"},
                    timeout=TIMEOUT_SEC,
                )
        except httpx.RequestError as exc:
            raise ApiError(500, f"{url} 요청 실패: {type(exc).__name__}: {exc}") from exc

        # 한국어가 깨지지 않게 인코딩을 못박는다
        resp.encoding = "utf-8"

        try:
            body = resp.json()
        except ValueError as exc:
            raise ApiError(
                resp.status_code, f"JSON 이 아닌 응답: {resp.text[:300]}"
            ) from exc

        # HTTP 200 이어도 success 가 false 면 실패다
        if not body.get("success", False):
            raise ApiError(resp.status_code, f"서버가 실패로 응답: {resp.text[:300]}")

        return body
