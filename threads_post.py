#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 카드 보드를 Threads 에 게시한다.

Threads 관련 처리는 모두 utils/threads_util.py 가 한다 (계정 연결, 토큰, 게시).
여기서는 기준일로 이 프로젝트의 초안 파일(output/etf_card_board_YYYYMMDD.threads.json)을
찾아 넘기는 일만 한다. 초안의 본문은 main.py 가, 이미지 주소는 main.py --send 가 채운다.

  .venv\\Scripts\\python threads_post.py                    가장 최근 초안 미리보기
  .venv\\Scripts\\python threads_post.py --publish          게시
  .venv\\Scripts\\python threads_post.py --date 2026-10-30  날짜 지정

토큰 발급과 연장은 python -m utils.threads_util login / refresh 로 한다.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from utils.threads_util import draft_path, publish_draft

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# main.py 와 같은 이유로 cp949 콘솔에서 죽지 않게 한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass


def find_draft(date_str):
    """기준일의 초안 경로. 날짜가 없으면 가장 최근 초안. 데모 초안(_demo)은 고르지 않는다."""
    if date_str:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise SystemExit("날짜 형식은 YYYY-MM-DD 입니다.")
        return draft_path(OUTPUT_DIR / f"etf_card_board_{dt:%Y%m%d}.html")
    drafts = sorted(OUTPUT_DIR.glob("etf_card_board_*.threads.json"))
    if not drafts:
        raise SystemExit("Threads 초안이 없습니다. main.py 로 보드를 먼저 만드세요.")
    return drafts[-1]


def main():
    ap = argparse.ArgumentParser(description="ETF 카드 보드를 Threads 에 게시")
    ap.add_argument("--date", help="보드 기준일 (YYYY-MM-DD, 기본: 가장 최근 초안)")
    ap.add_argument("--publish", action="store_true", help="실제로 게시 (기본: 미리보기)")
    args = ap.parse_args()

    ok = publish_draft(find_draft(args.date), publish=args.publish)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
