#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 카드 보드 생성기 - CLI 진입점.

.env 로드, KRX 로그인 진단, 환경 자가진단(--check), 보드 생성과 전송을 맡는다.
수집은 collect.py, HTML/PNG 조립은 render.py 가 담당한다.

전송(--send)은 두 갈래다. HTML 은 MQWAY 게시판에 올리고, PNG 는 게시물 링크를
붙여 텔레그램 운영 방으로 보낸다. 기본값은 보내지 않는 것이다. 실서버에 잘못
올라간 글은 지우기 전까지 되돌릴 수 없으므로 --send 를 명시해야만 전송한다.
--threads 를 더하면 MQWAY 썸네일 주소로 Threads 에도 게시한다.

프로젝트 개요와 사용법은 README.md 를 참고한다.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from collect import Bundle, collect_krx, demo_bundle
from render import (render, render_fragment, render_png, report_title, summary_lines,
                    threads_text)
from utils.logger_util import LoggerUtil
from utils.holiday_util import HolidayUtil, HolidayDataError

# 스케줄러로 돌리면 콘솔 출력은 아무도 보지 않는다. 진행 상황과 실패 사유를
# logs/YYYY-MM-DD_log.log 에 남겨 나중에 원인을 되짚을 수 있게 한다.
logger = LoggerUtil().get_logger()

# 한국어 Windows 콘솔은 cp949라 인코딩하지 못하는 문자가 섞이면 진단 메시지를
# 출력하는 순간 UnicodeEncodeError로 죽는다. 인코딩 실패 문자는 대체 표기한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass


# .env에 적는 KRX 계정 이름. 이 프로젝트는 이 두 이름만 쓴다.
KRX_ID_KEY = "KRX_LOGIN_ID"
KRX_PW_KEY = "KRX_LOGIN_PASSWORD"


def krx_credentials():
    """KRX 계정을 (아이디, 비밀번호)로 돌려준다. 비어 있으면 None."""
    return os.environ.get(KRX_ID_KEY) or None, os.environ.get(KRX_PW_KEY) or None


def export_krx_credentials():
    """pykrx가 읽는 이름(KRX_ID / KRX_PW)으로 계정 값을 옮겨 둔다.

    pykrx 1.2.x는 세션을 만들거나 만료 후 갱신할 때 os.getenv("KRX_ID") /
    os.getenv("KRX_PW")를 직접 부른다(website/comm/auth.py의 get_auth_session).
    이름을 바꿔 끼울 설정이 없어서, 로그인이 안 되면 그 실패가 로그인 오류가
    아니라 빈 응답이나 KeyError 같은 조회 오류로 나타난다. 이름이 어긋나는 지점을
    여기 한 곳으로 모은다.
    """
    login_id, login_pw = krx_credentials()
    if login_id:
        os.environ["KRX_ID"] = login_id
    if login_pw:
        os.environ["KRX_PW"] = login_pw
    return login_id, login_pw


ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load_env(path=None):
    """스크립트 폴더의 .env를 읽고 KRX 계정을 pykrx가 보는 이름으로 맞춘다.

    이미 설정된 환경변수는 덮어쓰지 않는다(load_dotenv의 기본 동작).
    """
    p = path or ENV_PATH
    found = os.path.exists(p)
    if found:
        load_dotenv(p)
    # .env가 없어도 환경변수로 직접 넣었을 수 있으므로 항상 맞춰 준다.
    export_krx_credentials()
    return found


# ---------------------------------------------------------------------------
# KRX 로그인 진단
# ---------------------------------------------------------------------------

# KRX 로그인 실패 코드별 해결 방법. 실패 사유 문구는 KRX 응답 원문을 그대로 쓰고,
# 여기에는 "그래서 무엇을 해야 하는지"만 둔다.
KRX_LOGIN_FIX = {
    "CD006": (".env의 KRX_LOGIN_PASSWORD 값이 KRX 계정 비밀번호와 다릅니다. "
              "재시도를 반복하면 계정이 잠기므로(CD007), 값을 먼저 확인한 뒤 다시 실행하세요."),
    "CD007": ("계정이 잠긴 상태입니다. .env의 비밀번호만 고쳐서는 풀리지 않습니다. "
              "data.krx.co.kr에서 비밀번호 재설정으로 잠금을 먼저 해제한 뒤 .env를 갱신하세요."),
    "CD010": "data.krx.co.kr에서 비밀번호를 변경한 뒤 .env를 갱신하세요.",
    "CD011": "다른 브라우저/기기에서 로그아웃한 뒤 재시도하세요.",
}


def probe_krx_login():
    """KRX 로그인 응답 코드를 직접 읽어 실패 원인을 특정한다.

    pykrx는 실패를 '자격 증명을 확인하세요'로만 알리고, 뒤이은 데이터 조회는
    무관해 보이는 KeyError로 죽는다. KRX가 돌려주는 코드와 메시지를 그대로
    보여줘 계정 잠금(CD007)과 단순 오타를 구분할 수 있게 한다.

    반환: (성공 여부, 코드, 메시지). 판정 불가 시 성공 여부는 None.
    """
    try:
        from pykrx.website.comm import auth
        import requests
    except ImportError:
        return None, None, "pykrx 미설치 (pip install -r requirements.txt)"

    login_id, login_pw = krx_credentials()
    if not (login_id and login_pw):
        return False, None, "KRX_LOGIN_ID / KRX_LOGIN_PASSWORD 미설정"

    try:
        s = requests.Session()
        auth.warmup_krx_session(s)
        resp = s.post(
            auth.LOGIN_URL, timeout=15,
            headers={"User-Agent": auth.USER_AGENT, "Referer": auth.LOGIN_PAGE},
            data={"mbrNm": "", "telNo": "", "di": "", "certType": "",
                  "mbrId": login_id, "pw": login_pw})
        data = resp.json()
    except Exception as e:
        return None, None, (f"로그인 응답 확인 실패 ({type(e).__name__}) "
                            "- 네트워크/방화벽/프록시를 확인하세요")

    code = data.get("_error_code", "") or ""
    msg = data.get("_error_message", "") or "(메시지 없음)"
    return code == "CD001", code, msg


def report_krx_login_failure(code, msg, emit=None):
    """로그인 실패 사유와 해결 방법을 한 덩어리로 내보낸다.

    emit 은 한 줄을 받는 함수다. 자가진단(--check)은 사람이 읽는 보고서라
    print 를, 생성 중 실패는 로그로 남아야 하므로 logger.error 를 준다.
    """
    emit = emit or print
    emit(f"    사유: {code + ': ' if code else ''}{msg}")
    fix = krx_login_fix(code, msg)
    if fix:
        emit(f"    조치: {fix}")


def krx_login_fix(code, msg):
    """실패 코드/메시지에 맞는 조치 문구. 알 수 없으면 None.

    KRX 는 90일마다 비밀번호 변경을 요구한다. 그 응답('패스워드 변경 필요')의
    코드가 KRX_LOGIN_FIX 에 없더라도 메시지로 알아보고 변경 안내를 준다.
    """
    fix = KRX_LOGIN_FIX.get(code)
    if fix:
        return fix
    if "변경" in (msg or ""):
        return KRX_LOGIN_FIX["CD010"]
    if code:
        return "data.krx.co.kr에 해당 계정으로 직접 로그인해 상태를 확인하세요."
    return None


def notify_krx_login_failure(date_str, code, msg):
    """KRX 로그인 실패를 텔레그램 실패 방(TELEGRAM_CHAT_TEST_ID)으로 알린다.

    cron 으로 돌면 로그를 보는 사람이 없어, 비밀번호 만료(90일)로 매일 조용히
    실패하게 된다. 알림 실패는 작업 실패가 아니므로 예외를 내지 않는다.
    --send 여부와 상관없이 보낸다. 실패 방 알림은 운영 게시물이 아니다.
    """
    try:
        from utils.telegram_util import TelegramUtil
    except ImportError as e:
        logger.error(f"텔레그램 알림 건너뜀: 패키지 미설치 ({e})")
        return
    lines = [f"기준일: {date_str}",
             f"사유: {code + ': ' if code else ''}{msg}"]
    fix = krx_login_fix(code, msg)
    if fix:
        lines.append(f"조치: {fix}")
    lines.append("보드 생성과 전송이 모두 중단되었습니다.")
    telegram = TelegramUtil()
    telegram.send_error(telegram.failure_message("KRX 로그인", "\n".join(lines)))


# ---------------------------------------------------------------------------
# 자가진단 (--check)
# ---------------------------------------------------------------------------

def selftest():
    """환경 점검: .env -> KRX 로그인 -> KRX ETF 데이터를 순서대로 확인한다."""
    print("=== 환경 자가진단 (main.py --check) ===")
    ok = True

    # [1] .env / 환경변수
    loaded = load_env()
    login_id, login_pw = krx_credentials()
    print(f"[1] .env 파일: {'로드됨' if loaded else '없음(환경변수 직접 설정 가정)'}"
          f" / KRX_LOGIN_ID: {'설정됨' if login_id else '비어있음'}"
          f" / KRX_LOGIN_PASSWORD: {'설정됨' if login_pw else '비어있음'}")
    if not (login_id and login_pw):
        print("    조치: data.krx.co.kr 가입 후 .env에 "
              "KRX_LOGIN_ID / KRX_LOGIN_PASSWORD를 채우세요.")
        ok = False

    # [2] KRX 로그인 - 데이터 조회보다 먼저, 실패 원인을 코드 단위로 특정한다
    logged_in, code, msg = probe_krx_login()
    if logged_in:
        print("[2] KRX 로그인: OK")
    else:
        ok = False
        print("[2] KRX 로그인: 실패")
        report_krx_login_failure(code, msg)

    # [3] KRX ETF 데이터 (시세 + 구성종목)
    if not logged_in:
        print("[3] KRX 데이터: 건너뜀 (로그인 성공 후에만 조회 가능)")
    else:
        try:
            from pykrx import stock
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
            ticker = "139270"   # TIGER 200 금융
            df = stock.get_etf_ohlcv_by_date(start, end, ticker)
            if df.empty:
                raise ValueError("ETF 시세 응답이 비어 있음")
            pdf = stock.get_etf_portfolio_deposit_file(ticker, df.index[-1].strftime("%Y%m%d"))
            if pdf.empty:
                raise ValueError("구성종목(PDF) 응답이 비어 있음")
            print(f"[3] KRX 데이터: OK - TIGER 200 금융 최근 {len(df)}개 거래일,"
                  f" 구성종목 {len(pdf)}개 (최종 {df.index[-1]:%Y-%m-%d})")
        except Exception as e:
            print(f"[3] KRX 데이터: 실패 - {type(e).__name__}: {str(e)[:110]}")
            print("    조치: 로그인은 됐으므로 네트워크(방화벽/프록시) 또는 pykrx 버전을 확인하세요.")
            ok = False

    # [4] wkhtmltoimage - PNG 는 --send 여부와 관계없이 매번 만든다
    exe = os.getenv("WKHTMLTOIMAGE_PATH", "").strip()
    if not exe:
        print("[4] wkhtmltoimage: 실패 - WKHTMLTOIMAGE_PATH 미설정")
        print("    조치: wkhtmltopdf 설치 후 .env 에 wkhtmltoimage.exe 경로를 적으세요.")
        ok = False
    elif not os.path.exists(exe):
        print(f"[4] wkhtmltoimage: 실패 - 파일이 없음 ({exe})")
        print("    조치: wkhtmltopdf 를 설치하거나 .env 의 경로를 고치세요.")
        ok = False
    else:
        print(f"[4] wkhtmltoimage: OK - {exe}")

    # [5][6] 전송 설정 - --send 에만 필요하므로 경고로만 다룬다
    warned = []
    try:
        from utils.api_util import MissingSettingError, mqway_endpoint
        print(f"[5] MQWAY 주소: OK - {mqway_endpoint()}")
    except MissingSettingError as e:
        print(f"[5] MQWAY 주소: 경고 - {e}")
        warned.append("MQWAY 주소")
    except ImportError as e:
        print(f"[5] MQWAY 주소: 경고 - 패키지 미설치 ({e})")
        warned.append("MQWAY 주소")

    try:
        from utils.telegram_util import TelegramUtil
        if TelegramUtil().is_configured():
            print("[6] 텔레그램: OK - 설정됨")
        else:
            print("[6] 텔레그램: 경고 - TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정")
            warned.append("텔레그램")
    except ImportError as e:
        print(f"[6] 텔레그램: 경고 - 패키지 미설치 ({e})")
        warned.append("텔레그램")

    print("\n진단 결과: " + ("모두 정상 - main.py 로 보드를 생성하세요"
                            if ok else "위 항목을 해결한 뒤 다시 실행하세요"))
    if warned:
        print(f"           {', '.join(warned)} 미설정이면 --send 를 쓸 수 없습니다")
    logger.info(f"환경 자가진단(--check) 결과: {'정상' if ok else '실패'}")
    return ok


# ---------------------------------------------------------------------------
# PNG / 전송 (--send)
# ---------------------------------------------------------------------------

# board-research 게시글 메타. 형제 프로젝트들이 카테고리로 주제를 나눈다
# ('시황', '유동성' 등). 이 보드는 krx-topsector-report 와 같은 '섹터분석'을 쓴다.
POST_CATEGORY = "섹터분석"
POST_WRITER = "admin"

# Threads 주제 태그. 게시물당 하나만 붙는다 (1~50자, 마침표와 & 불가).
THREADS_TOPIC_TAG = "ETF"


def make_png(html, path):
    """보드 PNG 를 만든다. 실패하면 사유를 남기고 None.

    PNG 실패는 보드 생성 실패가 아니다. HTML 은 이미 저장돼 있고 게시판 전송도
    PNG 없이 할 수 있으므로 여기서 멈추지 않는다.
    """
    try:
        render_png(html, path)
    except ImportError:
        logger.error("PNG 생성 건너뜀: imgkit 미설치 (pip install -r requirements.txt)")
        return None
    except Exception as e:
        logger.error(f"PNG 생성 실패: {type(e).__name__}: {str(e)[:200]}")
        logger.error("    점검: .venv\\Scripts\\python main.py --check")
        return None
    logger.info(f"PNG 생성 완료: {path}")
    return path


def save_threads_draft(b, html_path):
    """Threads 게시글 초안(본문)을 보드 옆에 저장한다. 게시는 threads_post.py 가 한다.

    이미지 주소는 MQWAY 게시가 끝나야 생기므로 deliver() 가 채운다. 초안 저장이
    실패해도 보드 생성은 성공이므로 경고만 남긴다.
    """
    try:
        from utils.threads_util import draft_path, update_draft
        path = draft_path(html_path)
        update_draft(path, title=report_title(b), date=b.date, text=threads_text(b),
                     topic_tag=THREADS_TOPIC_TAG, alt_text=f"{report_title(b)} 보드 이미지")
    except Exception as e:
        logger.warning(f"Threads 초안 저장 실패: {type(e).__name__}: {e}")
        return None
    logger.info(f"Threads 초안 저장: {path}")
    return path


def deliver(b, png_path, draft=None):
    """HTML 조각을 MQWAY 에 게시하고, PNG 를 텔레그램 운영 방으로 보낸다.

    둘은 서로 독립이다. 게시가 실패해도 PNG 는 링크 없이 보내고, 실패 사유는
    텔레그램 실패 방으로 따로 보낸다. 전송 실패는 생성 실패가 아니므로 종료 코드는
    0 을 유지한다. HTML/PNG 파일은 output/ 에 남아 있다.

    게시에 성공하면 MQWAY 가 돌려준 썸네일 주소를 Threads 초안(draft)에 적는다.
    Threads API 는 이미지를 공개 주소로만 받기 때문이다.
    """
    try:
        from utils.api_util import ApiError, ApiUtil, MissingSettingError
        from utils.telegram_util import TelegramUtil
        from utils.threads_util import update_draft
    except ImportError as e:
        logger.error(f"전송 건너뜀: 패키지 미설치 ({e})")
        logger.error("    조치: pip install -r requirements.txt")
        return

    telegram = TelegramUtil()
    title = report_title(b)
    facts = summary_lines(b)

    url = None
    try:
        api = ApiUtil()
        result = api.create_post(
            title=title,
            content=render_fragment(b),
            category=POST_CATEGORY,
            writer=POST_WRITER,
            # 게시판 목록 썸네일로 보드 PNG 를 쓴다. ApiUtil 이 800px 로 줄인다.
            thumbnail_path=Path(png_path) if png_path else None,
        )
        url = api.post_url(result)
        logger.info(f"MQWAY 게시 완료: {url or '(링크 확인 실패)'}")
        image_url = (result.get("data") or {}).get("thumbnail_url")
        if draft and image_url:
            update_draft(draft, image_url=image_url, post_url=url)
            logger.info(f"Threads 초안에 이미지 주소 기록: {image_url}")
        elif draft:
            logger.warning("게시 응답에 썸네일 주소가 없어 Threads 초안에 이미지 주소를 적지 못했다")
    except MissingSettingError as e:
        logger.error(f"MQWAY 게시 건너뜀: {e}")
        telegram.send_error(telegram.failure_message("MQWAY 설정", str(e)))
    except ApiError as e:
        logger.error(f"MQWAY 게시 실패: {e.message}")
        telegram.send_error(telegram.failure_message("MQWAY 전송", e.message))

    if png_path:
        telegram.send_photo(png_path, telegram.photo_caption(title, url, facts))
    else:
        telegram.send_error(telegram.failure_message(
            "PNG 생성", "이미지가 없어 텔레그램 사진 전송을 건너뛰었습니다. 로그를 확인하세요."))


def post_threads(draft):
    """deliver() 가 이미지 주소를 채운 초안을 Threads 에 게시한다 (--threads).

    deliver() 와 같은 규칙이다. 실패해도 보드 생성은 성공이므로 종료 코드는 0 을
    유지하고, 실패 사실은 텔레그램 실패 방으로 보낸다. 사유는 로그에 남는다.
    같은 날짜를 이미 게시했으면 publish_draft() 가 막는다.
    """
    try:
        from utils.telegram_util import TelegramUtil
        from utils.threads_util import publish_draft
    except ImportError as e:
        logger.error(f"Threads 게시 건너뜀: 패키지 미설치 ({e})")
        return
    if not draft:
        logger.error("Threads 게시 건너뜀: 초안이 없다")
        return
    if not publish_draft(draft, publish=True):
        telegram = TelegramUtil()
        telegram.send_error(telegram.failure_message(
            "Threads 게시", f"게시하지 못했습니다. 로그를 확인하세요. 초안: {draft}"))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="ETF 카드 보드 생성기")
    ap.add_argument("--date", help="기준 거래일 (YYYY-MM-DD, 기본: 오늘)")
    ap.add_argument("--out", help="출력 HTML 경로 (기본: output/etf_card_board_YYYYMMDD.html)")
    ap.add_argument("--demo", action="store_true", help="내장 샘플 데이터로 렌더링 테스트")
    ap.add_argument("--check", action="store_true", help="환경 자가진단 (.env/KRX 로그인/데이터 확인)")
    ap.add_argument("--send", action="store_true",
                    help="HTML 은 MQWAY 게시판에, PNG 는 텔레그램에 전송 (기본: 전송 안 함)")
    ap.add_argument("--threads", action="store_true",
                    help="--send 뒤에 Threads 에도 게시 (--send 와 함께만 쓸 수 있음)")
    args = ap.parse_args()

    if args.check:
        sys.exit(0 if selftest() else 1)

    # 샘플 데이터로 만든 보드가 실서버에 게시글로 남으면 지우기 전까지 되돌릴 수
    # 없다. 렌더링 확인용인 --demo 와 전송은 함께 쓰지 못하게 막는다.
    if args.demo and args.send:
        ap.error("--demo 는 샘플 데이터라 --send 와 함께 쓸 수 없습니다.")
    # Threads 는 MQWAY 썸네일 주소로 이미지를 받는다. 그 주소는 --send 게시가 끝나야 생긴다.
    if args.threads and not args.send:
        ap.error("--threads 는 --send 와 함께 써야 합니다 (이미지 주소가 MQWAY 게시에서 나옵니다).")

    if args.demo:
        # 데모도 PNG 를 찍으므로 WKHTMLTOIMAGE_PATH 를 읽어야 한다.
        load_env()
        b = demo_bundle()
    else:
        # 스케줄러가 매일 같은 명령으로 돌 수 있도록 생략 시 오늘로 잡는다.
        # (시스템 시간대 기준이므로 서버 TZ가 Asia/Seoul 이어야 날짜가 맞다.)
        date_str = args.date or datetime.now().strftime("%Y-%m-%d")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            ap.error("날짜 형식은 YYYY-MM-DD 입니다.")
        # --date로 명시한 실행(수동/보정)은 그대로 돌리고, cron처럼 오늘 날짜로
        # 돌 때만 휴장일이면 스킵한다. 수동 재실행을 막으면 복구를 못 하기 때문이다.
        if not args.date:
            load_env()
            try:
                holiday = HolidayUtil()
                if not holiday.is_today_trading_day():
                    logger.info("오늘은 KRX 휴장일입니다. 실행을 스킵합니다.")
                    sys.exit(0)
            except HolidayDataError as e:
                logger.error(f"휴장일 데이터 확인 실패: {e}")
                sys.exit(1)
            except Exception as e:
                # 거래일로 가정하지 말고 실패로 처리한다(holiday_util 설계 원칙).
                logger.error(f"휴장일 조회 중 오류: {e}")
                sys.exit(1)
        b = Bundle(date=date_str, date_krx=dt.strftime("%Y%m%d"))
        loaded = load_env()
        login_id, login_pw = krx_credentials()
        if not (login_id and login_pw):
            src = ".env 파일은 읽었으나 값이 비어 있습니다" if loaded else ".env 파일이 없습니다"
            logger.warning(f"KRX 계정 미설정 ({src}). data.krx.co.kr 무료 회원가입 후 "
                           "이 폴더의 .env에 KRX_LOGIN_ID / KRX_LOGIN_PASSWORD를 입력하세요.")
        logger.info(f"[1/3] KRX 데이터 수집 중... ({date_str}"
                    f"{'' if args.date else ', --date 생략 -> 오늘'})")
        try:
            collect_krx(b)
        except ImportError:
            logger.error("pykrx 미설치: pip install -r requirements.txt")
            sys.exit(1)
        except Exception as e:
            logger.error(f"KRX 수집 실패: {type(e).__name__}: {e}")
            # pykrx 는 로그인에 실패해도 예외를 내지 않고 빈 응답으로 넘어간다. 그래서
            # 로그인 실패가 빈 시세(RuntimeError, 휴장일처럼 보임)나 pykrx 내부의
            # KeyError 같은 조회 오류로 나타난다. 예외 종류로는 가릴 수 없으므로
            # 어떤 예외든 로그인을 직접 확인한다.
            logged_in, code, msg = probe_krx_login()
            if not logged_in:
                logger.error("실제 원인: KRX 로그인 실패 (휴장일/데이터 문제가 아닙니다)")
                report_krx_login_failure(code, msg, emit=logger.error)
                logger.error("    점검: .venv\\Scripts\\python main.py --check")
                notify_krx_login_failure(date_str, code, msg)
                sys.exit(1)
            if isinstance(e, RuntimeError):
                sys.exit(1)
            raise

    logger.info("[2/3] HTML 렌더링 중...")
    html = render(b)
    if args.out:
        out = args.out
    else:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        # 데모를 실제 보드와 같은 이름으로 쓰면 같은 날짜의 보드를 샘플로 덮어쓴다.
        name = "_demo.html" if args.demo else f"etf_card_board_{b.date_krx}.html"
        out = os.path.join(out_dir, name)
    if os.path.dirname(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"보드 생성 완료: {out}")
    if b.missing:
        logger.warning(f"수집 실패 {len(b.missing)}건 - 보드 하단 '데이터 수집 내역' 참조: "
                       + ", ".join(item for item, _ in b.missing))

    logger.info("[3/3] PNG 생성 중...")
    png_path = make_png(html, os.path.splitext(out)[0] + ".png")
    draft = save_threads_draft(b, out)

    if args.send:
        logger.info("MQWAY 게시 / 텔레그램 전송 중...")
        deliver(b, png_path, draft)
        if args.threads:
            logger.info("Threads 게시 중...")
            post_threads(draft)


if __name__ == "__main__":
    main()
