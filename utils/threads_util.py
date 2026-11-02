"""Threads 게시 공통 모듈 (Meta Threads API).

다른 프로젝트에서도 쓸 수 있게 Threads 관련 일을 이 파일 하나에 모았다.
계정 연결(토큰 발급/연장), 이미지 게시, 게시 초안 파일, 명령줄 실행까지 여기서 한다.
의존: httpx, python-dotenv, utils/logger_util.py. 프로젝트 루트 아래 utils/ 에 두고
루트의 .env 를 읽는다 (다른 프로젝트로 옮길 때도 같은 위치에 둔다).

프로젝트 하나에 Threads 계정 하나를 연결한다. .env 의 토큰이 곧 게시할 계정이다.
다른 계정에 올리는 프로젝트는 그 프로젝트의 .env 에서 따로 login 한다. Meta 앱은
여러 프로젝트가 같이 써도 된다 (계정마다 Threads 테스터로 등록해 둔다).

명령줄은 세 가지다 (프로젝트 루트에서 실행).
  python -m utils.threads_util login                    토큰 발급 (처음 한 번, 만료됐을 때)
  python -m utils.threads_util refresh                  토큰 60일 연장
  python -m utils.threads_util post 초안.json [--publish]  미리보기 / 게시

==============================================================================
1. 처음 한 번: 계정과 앱 준비 (2026-11 공식 문서 기준, 메뉴 이름은 바뀔 수 있다)
==============================================================================

  1) Threads 계정을 준비한다. 2025-09 부터 인스타그램 연결 없이도 API 를 쓸 수 있다.

  2) developers.facebook.com 에서 앱을 만들고 사용 사례로 "Access the Threads API"
     (Threads API 액세스)를 고른다. 앱을 만들면 앱 ID/시크릿이 두 쌍 생기는데,
     Threads 에는 "Threads App ID" / "Threads App secret" 을 쓴다.
     위치: 앱 대시보드 > 앱 설정 > 기본 설정 (또는 사용 사례 > 설정).

  3) 사용 사례 > Access the Threads API > 권한에서 threads_content_publish 옆의
     "추가"를 누른다. threads_basic 은 필수라 이미 들어 있다.

  4) 사용 사례 > 설정 > "클라이언트 OAuth 설정"의 유효한 OAuth 리디렉션 URI 에
     주소 하나를 등록한다 (예: https://mqway.com/). 인증 코드를 받을 주소일 뿐이라
     그 페이지가 실제로 무언가를 할 필요는 없다. 주소창에 붙은 code 만 복사해 쓴다.

  5) 앱 대시보드 > 앱 역할 > 역할 > "사람 추가" > "Threads 테스터"로 내 Threads 계정을
     초대하고, Threads 앱이나 웹(threads.com)의 설정 > 계정 > 웹사이트 권한에서 초대를
     수락한다. 앱 심사 전에는 테스터 계정에만 게시할 수 있다. 내 계정에만 올린다면
     앱 심사(App Review)는 필요 없다.

  6) .env 에 적는다. 이 세 값은 login 할 때만 쓴다.
       THREADS_APP_ID=        Threads App ID
       THREADS_APP_SECRET=    Threads App secret
       THREADS_REDIRECT_URI=  4) 에서 등록한 주소와 글자 하나까지 같아야 한다
     평소 게시와 연장에는 login 이 적어 주는 THREADS_ACCESS_TOKEN 하나만 있으면 된다.

  7) python -m utils.threads_util login
       출력된 인증 주소를 브라우저로 열어 로그인/허용하면 리디렉션 주소로 돌아온다.
       주소창의 ?code=... 값을 복사해 붙여넣으면(끝의 #_ 는 있어도 된다)
       인증 코드(1시간, 1회용) -> 단기 토큰(1시간) -> 장기 토큰(60일) 으로 바꿔
       .env 에 THREADS_ACCESS_TOKEN 을 적고 토큰 주인 계정을 보여준다.
       사용자 id 는 적지 않는다. 게시할 때 토큰으로 조회한다 (GET /me).

==============================================================================
2. 토큰 유지
==============================================================================

  장기 토큰은 60일짜리다. 만료 전에 refresh 로 연장하면 .env 의 값이 새 토큰으로
  바뀌고 다시 60일이 된다. 발급 후 24시간이 지나야 연장할 수 있고, 만료되면 연장할 수 없어
  login 을 다시 해야 한다. 매일 도는 스케줄러가 있으면 한 달에 한 번쯤 연장을 걸어 둔다.

       python -m utils.threads_util refresh

==============================================================================
3. 게시
==============================================================================

  Threads API 는 이미지 파일을 직접 받지 않는다. Meta 서버가 게시 시점에 내려받을 수 있는
  공개 https 주소(image_url)만 받는다. localhost 나 로그인이 필요한 주소는 안 된다.

  이미지 게시는 세 단계다. post_image() 가 묶어서 한다.
    1. POST /{user-id}/threads           media_type=IMAGE, image_url, text 로 컨테이너 생성
    2. GET  /{container-id}?fields=status FINISHED 가 될 때까지 기다린다 (권장 30초)
    3. POST /{user-id}/threads_publish   creation_id 로 게시

  보통은 초안 파일(JSON)을 만들어 두고 publish_draft() 나 명령줄로 올린다.
  초안 키: text(필수), image_url(필수), topic_tag, alt_text. 게시하면
  threads_media_id / threads_permalink / threads_published_at 이 적히고, 같은 초안은
  force 없이 다시 올리지 않는다.

  코드에서 쓸 때:
       from utils.threads_util import ThreadsUtil, update_draft, publish_draft
       update_draft("x.threads.json", text="...", image_url="https://...", topic_tag="ETF")
       publish_draft("x.threads.json", publish=True)

  Threads 제한: 본문 500자, 주제 태그는 게시물당 하나(1~50자, 마침표와 & 불가),
  이미지 PNG/JPEG 8MB 이하. 폭은 320~1440px 범위를 벗어날 때만 Threads 가 늘리거나 줄인다.
  링크 5개 이하, 24시간 게시 250건.

텔레그램과 달리 게시 실패는 그 작업의 실패이므로 API 메서드는 bool 이 아니라
ThreadsError 를 낸다. 토큰이 요청에 들어가므로 요청 URL 과 httpx 예외 메시지는 로그에
남기지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
from dotenv import load_dotenv, set_key

from utils.logger_util import LoggerUtil

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

# graph.threads.net 도 받지만 공식 문서 예제가 .com 을 쓴다.
API = "https://graph.threads.com"
VERSION = "v1.0"
AUTHORIZE_URL = "https://threads.com/oauth/authorize"
SCOPES = ("threads_basic", "threads_content_publish")
TIMEOUT_SEC = 30.0

# Threads 제한 (2026-11 공식 문서 기준)
TEXT_MAX = 500
ALT_TEXT_MAX = 1000
TOPIC_TAG_MAX = 50
IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg"}

# 문서 권장: 컨테이너를 만든 뒤 평균 30초 기다렸다가 게시하고, 상태는 1분에 한 번씩
# 5분까지만 조회한다.
FIRST_WAIT_SEC = 30
POLL_INTERVAL_SEC = 60
POLL_TIMEOUT_SEC = 300

logger = LoggerUtil().get_logger()


def env(name: str) -> str:
    return os.getenv(name, "").strip()


def save_env(name: str, value: str) -> None:
    """.env 의 한 줄만 바꾼다. 없으면 끝에 붙인다. 다른 줄은 건드리지 않는다."""
    set_key(str(ENV_PATH), name, value, quote_mode="never")
    os.environ[name] = value


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class ThreadsError(Exception):
    """Threads 요청 실패. 메시지에 토큰이 들어가지 않게 만든다."""


class ThreadsUtil:
    def __init__(self):
        self.logger = logger
        self.token = env("THREADS_ACCESS_TOKEN")
        self._user_id = None

    def _require_token(self) -> None:
        if not self.token:
            raise ThreadsError(
                "환경변수 THREADS_ACCESS_TOKEN 이 없다. python -m utils.threads_util login "
                "으로 발급한다 (이 파일 맨 위 설명 참고)"
            )

    @property
    def user_id(self) -> str:
        """토큰 주인의 Threads 사용자 id. 환경변수로 받지 않고 처음 쓸 때 /me 로 조회한다."""
        if self._user_id is None:
            self._user_id = str(self.me()["id"])
        return self._user_id

    def _request(self, method: str, path: str, params: dict,
                 versioned: bool = True) -> dict:
        """GET 은 쿼리로, POST 는 폼 본문으로 보낸다. 실패하면 ThreadsError."""
        url = f"{API}/{VERSION}/{path}" if versioned else f"{API}/{path}"
        try:
            if method == "GET":
                resp = httpx.get(url, params=params, timeout=TIMEOUT_SEC)
            else:
                resp = httpx.post(url, data=params, timeout=TIMEOUT_SEC)
        # 예외 메시지에 토큰이 든 URL 이 섞일 수 있어 예외 종류만 남긴다.
        except httpx.RequestError as exc:
            raise ThreadsError(f"{path} 요청 실패: {type(exc).__name__}") from None

        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code != 200 or "error" in body:
            err = body.get("error") or {}
            if isinstance(err, str):
                msg = body.get("error_description") or err
            else:
                msg = err.get("error_user_msg") or err.get("message") or resp.text[:300]
            raise ThreadsError(
                f"{path} 실패 (HTTP {resp.status_code}): {msg}"
            )
        return body

    # --- 계정 연결 -----------------------------------------------------------

    @staticmethod
    def authorize_url(state: str = "") -> str:
        """브라우저로 열 인증 창 주소. 허용하면 THREADS_REDIRECT_URI 로 ?code= 가 붙어 돌아온다."""
        app_id, redirect = env("THREADS_APP_ID"), env("THREADS_REDIRECT_URI")
        if not (app_id and redirect):
            raise ThreadsError(
                "환경변수 THREADS_APP_ID / THREADS_REDIRECT_URI 가 없다. "
                "앱 대시보드의 Threads App ID 와 등록한 리디렉션 URI 를 .env 에 넣는다"
            )
        params = {"client_id": app_id, "redirect_uri": redirect,
                  "scope": ",".join(SCOPES), "response_type": "code"}
        if state:
            params["state"] = state
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> tuple[str, str]:
        """인증 코드를 단기 토큰(1시간)으로 바꾼다. (토큰, 사용자 id)

        코드는 1시간 동안 한 번만 쓸 수 있다. redirect_uri 는 인증 창에 넘긴 값과 같아야 한다.
        """
        app_id, secret, redirect = (env("THREADS_APP_ID"), env("THREADS_APP_SECRET"),
                                    env("THREADS_REDIRECT_URI"))
        if not (app_id and secret and redirect):
            raise ThreadsError(
                "환경변수 THREADS_APP_ID / THREADS_APP_SECRET / THREADS_REDIRECT_URI 가 모두 "
                "있어야 한다"
            )
        # 리디렉션 주소 끝에 붙는 #_ 를 같이 복사해 와도 되게 한다.
        code = code.strip().removesuffix("#_")
        body = self._request("POST", "oauth/access_token",
                             {"client_id": app_id, "client_secret": secret, "code": code,
                              "grant_type": "authorization_code", "redirect_uri": redirect},
                             versioned=False)
        return body["access_token"], str(body.get("user_id", ""))

    def exchange_token(self, short_token: str) -> tuple[str, int]:
        """단기 토큰(1시간)을 장기 토큰(60일)으로 바꾼다. (토큰, 남은 초)"""
        secret = env("THREADS_APP_SECRET")
        if not secret:
            raise ThreadsError(
                "환경변수 THREADS_APP_SECRET 이 없다. 앱 대시보드의 Threads App secret 을 "
                ".env 에 넣는다"
            )
        body = self._request("GET", "access_token",
                             {"grant_type": "th_exchange_token", "client_secret": secret,
                              "access_token": short_token},
                             versioned=False)
        return body["access_token"], int(body.get("expires_in", 0))

    def refresh_token(self) -> tuple[str, int]:
        """장기 토큰을 60일 연장한다. 발급 후 24시간이 지나야 하고, 만료되면 연장할 수 없다."""
        self._require_token()
        body = self._request("GET", "refresh_access_token",
                             {"grant_type": "th_refresh_token", "access_token": self.token},
                             versioned=False)
        return body["access_token"], int(body.get("expires_in", 0))

    def me(self) -> dict:
        """토큰 주인의 id 와 사용자 이름."""
        self._require_token()
        return self._request("GET", "me", {"fields": "id,username",
                                           "access_token": self.token})

    def publishing_limit(self) -> dict:
        """24시간 게시 한도 사용량. {"quota_usage": 4, "config": {"quota_total": 250, ...}}"""
        self._require_token()
        body = self._request("GET", f"{self.user_id}/threads_publishing_limit",
                             {"fields": "quota_usage,config", "access_token": self.token})
        return (body.get("data") or [{}])[0]

    # --- 게시 ---------------------------------------------------------------

    def create_image_container(self, image_url: str, text: str, topic_tag: str = "",
                               alt_text: str = "") -> str:
        """이미지 컨테이너를 만들고 id 를 돌려준다. 이 단계에서는 아직 게시되지 않는다."""
        self._require_token()
        params = {
            "media_type": "IMAGE",
            "image_url": image_url,
            "text": text,
            "access_token": self.token,
        }
        if topic_tag:
            params["topic_tag"] = topic_tag
        if alt_text:
            params["alt_text"] = alt_text[:ALT_TEXT_MAX]
        body = self._request("POST", f"{self.user_id}/threads", params)
        return body["id"]

    def wait_until_finished(self, container_id: str) -> None:
        """컨테이너가 FINISHED 가 될 때까지 기다린다. ERROR/EXPIRED 나 시간 초과면 ThreadsError."""
        self.logger.info(f"Threads 컨테이너 처리 대기 ({FIRST_WAIT_SEC}초)...")
        time.sleep(FIRST_WAIT_SEC)
        deadline = time.monotonic() + POLL_TIMEOUT_SEC
        while True:
            body = self._request("GET", container_id,
                                 {"fields": "status,error_message", "access_token": self.token})
            status = body.get("status")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise ThreadsError(
                    f"컨테이너 {container_id} 상태 {status}: {body.get('error_message') or '-'}"
                )
            if time.monotonic() >= deadline:
                raise ThreadsError(
                    f"컨테이너 {container_id} 가 {POLL_TIMEOUT_SEC}초가 지나도 {status} 상태다"
                )
            self.logger.info(f"Threads 컨테이너 상태 {status}, {POLL_INTERVAL_SEC}초 뒤 다시 확인")
            time.sleep(POLL_INTERVAL_SEC)

    def publish(self, container_id: str) -> str:
        """컨테이너를 게시하고 게시물 id 를 돌려준다."""
        self._require_token()
        body = self._request("POST", f"{self.user_id}/threads_publish",
                             {"creation_id": container_id, "access_token": self.token})
        return body["id"]

    def permalink(self, media_id: str) -> str | None:
        """게시물 주소. 조회에 실패해도 게시는 끝났으므로 None 을 돌려준다."""
        try:
            body = self._request("GET", media_id,
                                 {"fields": "permalink", "access_token": self.token})
        except ThreadsError as exc:
            self.logger.warning(f"게시물 주소 조회 실패: {exc}")
            return None
        return body.get("permalink")

    def post_image(self, image_url: str, text: str, topic_tag: str = "",
                   alt_text: str = "") -> tuple[str, str | None]:
        """이미지 게시 세 단계를 묶는다. (게시물 id, 게시물 주소)"""
        container_id = self.create_image_container(image_url, text, topic_tag, alt_text)
        self.logger.info(f"Threads 컨테이너 생성: {container_id}")
        self.wait_until_finished(container_id)
        media_id = self.publish(container_id)
        return media_id, self.permalink(media_id)


# ---------------------------------------------------------------------------
# 게시 전 점검
# ---------------------------------------------------------------------------

def check_image_url(url: str) -> str | None:
    """Meta 서버가 받아갈 수 있는 주소인지 미리 본다. 문제가 있으면 사유를, 없으면 None."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname in ("localhost", "127.0.0.1"):
        return f"공개 https 주소가 아니라 Meta 서버가 이미지를 받지 못합니다: {url}"
    try:
        resp = httpx.head(url, follow_redirects=True, timeout=15.0)
        if resp.status_code == 405:
            resp = httpx.get(url, follow_redirects=True, timeout=15.0)
    except httpx.RequestError as exc:
        return f"이미지 주소에 접속하지 못했습니다: {type(exc).__name__}"
    if resp.status_code != 200:
        return f"이미지 주소가 HTTP {resp.status_code} 를 돌려줍니다 (로그인 없이 열려야 합니다)."
    ctype = resp.headers.get("content-type", "").split(";")[0].strip()
    if ctype not in IMAGE_TYPES:
        return f"이미지 형식이 {ctype or '알 수 없음'} 입니다. Threads 는 PNG/JPEG 만 받습니다."
    size = int(resp.headers.get("content-length") or 0)
    if size > IMAGE_MAX_BYTES:
        return f"이미지가 {size / 1024 / 1024:.1f}MB 로 8MB 상한을 넘습니다."
    return None


def check_post(text: str, image_url: str | None, topic_tag: str = "") -> list[str]:
    """게시 전에 걸리는 문제 목록. 비어 있으면 올릴 수 있다."""
    problems = []
    if not text:
        problems.append("본문이 비어 있습니다.")
    elif len(text) > TEXT_MAX:
        problems.append(f"본문이 {len(text)}자로 {TEXT_MAX}자 상한을 넘습니다.")
    if topic_tag and (len(topic_tag) > TOPIC_TAG_MAX or any(ch in topic_tag for ch in ".&")):
        problems.append(f"주제 태그 '{topic_tag}' 는 {TOPIC_TAG_MAX}자 이하, 마침표와 & 없이 써야 합니다.")
    if not image_url:
        problems.append("이미지 주소가 없습니다.")
    else:
        reason = check_image_url(image_url)
        if reason:
            problems.append(reason)
    return problems


# ---------------------------------------------------------------------------
# 초안 파일
# ---------------------------------------------------------------------------

def draft_path(board_path: str | Path) -> Path:
    """이미지/문서 파일 옆의 초안 경로. 예: board.png -> board.threads.json"""
    return Path(board_path).with_suffix(".threads.json")


def load_draft(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def update_draft(path: str | Path, **fields) -> dict:
    """초안에 값을 덮어 쓴다. None 인 값은 건드리지 않는다.

    원본을 다시 만들어도 이미 적힌 게시물 id 가 지워지지 않게 통째로 덮지 않는다.
    """
    data = load_draft(path)
    data.update({k: v for k, v in fields.items() if v is not None})
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def publish_draft(path: str | Path, publish: bool = False, force: bool = False,
                  image_url: str | None = None, text_file: str | Path | None = None) -> bool:
    """초안을 미리 보여주고, publish 면 게시한다. 성공(미리보기 통과 포함)이면 True.

    image_url / text_file 을 주면 초안 값 대신 쓴다. 공개 SNS 라 지우기 전까지 그대로
    보이므로 기본은 미리보기이고, 이미 게시한 초안은 force 없이 다시 올리지 않는다.
    """
    draft = load_draft(path)
    if not draft and not (image_url and text_file):
        logger.error(f"초안이 없습니다: {path}")
        return False

    text = (Path(text_file).read_text(encoding="utf-8").strip()
            if text_file else draft.get("text", ""))
    image_url = image_url or draft.get("image_url")
    topic_tag = draft.get("topic_tag", "")
    alt_text = draft.get("alt_text", "")

    logger.info(f"Threads 초안: {path}")
    print("-" * 60)
    print(text)
    print("-" * 60)
    print(f"주제 태그: {topic_tag or '(없음)'}")
    print(f"이미지: {image_url or '(없음)'}")
    print(f"본문 {len(text)}자 / 상한 {TEXT_MAX}자")

    problems = check_post(text, image_url, topic_tag)
    if draft.get("threads_media_id") and not force:
        problems.append(f"이미 게시한 초안입니다 "
                        f"({draft.get('threads_permalink') or draft['threads_media_id']}). "
                        "다시 올리려면 force 로 호출하거나 초안의 threads_media_id 를 지우세요.")
    for p in problems:
        logger.error(p)
    if problems:
        return False

    if not publish:
        logger.info("미리보기만 했습니다. 게시하려면 --publish 를 붙이세요.")
        return True

    try:
        media_id, permalink = ThreadsUtil().post_image(image_url, text, topic_tag, alt_text)
    except ThreadsError as e:
        logger.error(f"Threads 게시 실패: {e}")
        return False
    update_draft(path, threads_media_id=media_id, threads_permalink=permalink,
                 threads_published_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info(f"Threads 게시 완료: {permalink or media_id}")
    return True


# ---------------------------------------------------------------------------
# 명령줄: python -m utils.threads_util login | refresh | post
# ---------------------------------------------------------------------------

def _save_long_token(token: str, expires_in: int) -> None:
    save_env("THREADS_ACCESS_TOKEN", token)
    until = datetime.now() + timedelta(seconds=expires_in)
    logger.info(f".env 의 THREADS_ACCESS_TOKEN 을 갱신했습니다 (만료 예정 {until:%Y-%m-%d}).")


def _cmd_login() -> bool:
    """인증 주소를 보여주고, 붙여넣은 코드로 장기 토큰을 받아 .env 에 적는다."""
    print("아래 주소를 브라우저로 열어 로그인/허용하세요.")
    print(ThreadsUtil.authorize_url())
    code = input("돌아온 주소창의 code 값을 붙여넣으세요: ").strip()
    if not code:
        logger.error("code 가 비어 있습니다.")
        return False

    th = ThreadsUtil()
    short_token, _ = th.exchange_code(code)
    token, expires_in = th.exchange_token(short_token)
    _save_long_token(token, expires_in)

    # 엉뚱한 계정으로 로그인했는지 바로 알 수 있게 토큰 주인을 보여준다.
    me = ThreadsUtil().me()
    logger.info(f"토큰 주인: @{me.get('username')} (id {me.get('id')})")
    return True


def main(argv: list[str] | None = None) -> int:
    # 한국어 Windows 콘솔(cp949)에서 인코딩 못 하는 문자가 나와도 죽지 않게 한다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(prog="python -m utils.threads_util",
                                 description="Threads 계정 연결과 게시. 설명은 이 파일 맨 위 참고")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="토큰 발급 (THREADS_APP_ID/SECRET/REDIRECT_URI 필요)")
    sub.add_parser("refresh", help="장기 토큰 60일 연장")
    p = sub.add_parser("post", help="초안 미리보기 (--publish 면 게시)")
    p.add_argument("draft", help="초안 JSON 경로")
    p.add_argument("--publish", action="store_true", help="실제로 게시 (기본: 미리보기)")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "login":
            ok = _cmd_login()
        elif args.cmd == "refresh":
            _save_long_token(*ThreadsUtil().refresh_token())
            ok = True
        else:
            ok = publish_draft(args.draft, publish=args.publish)
    except ThreadsError as e:
        logger.error(f"Threads 실패: {e}")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
