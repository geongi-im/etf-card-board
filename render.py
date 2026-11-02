# -*- coding: utf-8 -*-
"""HTML/PNG 렌더링 - 수집한 Bundle을 보드 한 장으로 만든다.

여기서는 새로운 값을 만들지 않는다. collect가 모아 온 값을 옮기기만 하고,
값이 없는 항목은 지어내지 않고 '-'로 비워 둔다.

결과물은 세 가지다.
  render()          완결 HTML 문서. output/ 에 저장하고 PNG 의 원본으로 쓴다
  render_fragment() MQWAY 게시판 본문 조각. <!DOCTYPE>/<head> 없이 보낸다
  render_png()      wkhtmltoimage(imgkit)로 찍은 PNG. 텔레그램으로 보낸다

이미지와 게시판은 같은 레이아웃이다. 상승/하락 상위 FOCUS_N 종씩만 카드로 크게
싣고 나머지는 표로 줄인다. 게시판은 본문 폭(800px)에 맞춰 나머지 표를 1열로 두고,
상위 카드의 구성종목에 뉴스 링크를 건다 (render_board 의 post).

PNG 는 wkhtmltoimage 가 찍는다. 이 엔진은 2011년 무렵의 QtWebKit 이라 CSS grid,
flex gap, CSS 변수(var(--x)), font-variant-numeric 을 모른다. 그래서 레이아웃은
inline-block 과 table 로만 짜고, 색은 CSS 변수 대신 파이썬에서 PALETTE 값을 끼워
넣는다.

게시판 본문은 페이지에 그대로 삽입되므로 모든 선택자를 .eci 아래로 한정해
게시판 페이지 스타일을 덮지 않게 한다.
"""

import html
import os
from datetime import datetime
from string import Template

from collect import CHART_MONTHS, Bundle

BOARD_TITLE = "섹터 ETF 현황"
BOARD_HANDLE = "@mq_way.finance"    # 제목 위(머리글 왼쪽 위)에 싣는 계정 이름
SUMMARY_TOP_N = 3       # 텔레그램 캡션에 싣는 상승/하락 상위 ETF 수
FOCUS_N = 4             # 카드로 크게 싣는 상승/하락 상위 ETF 수 (각각)
FOCUS_HOLDINGS = 4      # 상위 카드에 싣는 비중 상위 종목 수

# 색. CSS 와 SVG 속성에 같은 값을 쓴다. CSS 에는 $up 처럼 적고 css() 가 바꿔 넣는다.
# 상승 빨강 / 하락 파랑은 국내 시장 관례라 바꾸지 않는다.
PALETTE = {
    "ink": "#1b2433",       # 제목, 본문 글자, 머리글 밑줄
    "sub": "#5d6675",       # 보조 글자 (구성종목 이름, 기준일)
    "muted": "#9aa2ae",     # 흐린 글자, 미니차트 점선
    "line": "#e2e6ec",      # 카드 테두리
    "rule": "#edf0f4",      # 표 행 구분선
    "panel": "#f4f6f9",     # 이미지 바탕
    "up": "#e5404b",
    "up_bg": "#fde2e4",     # 상승 섹션 머리 배경
    "down": "#2a6be0",
    "down_bg": "#e1ebfd",   # 하락 섹션 머리 배경
}

# 게시판 본문에서 구성종목 이름에 거는 링크. 네이버 증권 종목 뉴스 탭으로 간다.
# 국내 시세표에 있는 종목(등락률이 잡힌 종목)에만 건다. 해외 종목 코드는 네이버
# 국내 종목 페이지가 없다.
NAVER_NEWS_URL = "https://finance.naver.com/item/news.naver?code={code}"

# 텔레그램 캡션에서 ETF 이름에 거는 링크. 네이버 증권 종목 페이지로 간다.
# 지금은 stock.naver.com/domestic/stock/{code} 로 넘겨 주며, 0090B0 처럼 영문이
# 섞인 신규 코드도 같은 방식으로 넘어간다.
NAVER_ITEM_URL = "https://finance.naver.com/item/main.naver?code={code}"

# PNG 캡처 폭 (px). 이미지 문서 폭(CSS 의 .eci)과 맞춘다.
PNG_WIDTH = 1080


# ---------------------------------------------------------------------------
# 포맷 헬퍼
# ---------------------------------------------------------------------------

def esc(s):
    return html.escape(str(s), quote=True)


def signed(x, digits=2):
    """부호를 붙인 등락률. 예: +5.39% / -3.74%. 반올림해서 0 이면 부호 없이 0.00%."""
    if x is None:
        return "-"
    if round(x, digits) == 0:
        return f"{0:.{digits}f}%"
    return f"{x:+.{digits}f}%"


def tone(x):
    """등락 방향 클래스. 상승 up / 하락 down / 보합이나 값 없음은 빈 문자열."""
    if not x:
        return ""
    return "up" if x > 0 else "down"


def css(template):
    """CSS 안의 $ink, $up 같은 자리에 PALETTE 색을 넣는다."""
    return Template(template).substitute(PALETTE)


# ---------------------------------------------------------------------------
# 미니차트
# ---------------------------------------------------------------------------

def sparkline(closes, w, h, color, pad=2):
    """종가 목록을 인라인 SVG로 그린다. 점선은 첫 값(6개월 전 종가).

    게시판 본문은 64KB 상한이라 좌표를 정수로 반올림하고, 절대 좌표 polyline 대신
    직전 점 기준 상대 좌표 path(l 명령)로 적는다. x 간격이 1px 안팎이라 점 하나가
    '123,45 ' 7자에서 '1-2 ' 4자 남짓으로 준다.
    """
    if len(closes) < 2:
        return f'<svg width="{w}" height="{h}"></svg>'

    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1
    n = len(closes)
    inner_w = w - pad * 2
    inner_h = h - pad * 2

    def y_of(v):
        return round(pad + (hi - v) * inner_h / span)

    pts = [(round(pad + i * inner_w / (n - 1)), y_of(v)) for i, v in enumerate(closes)]
    # 음수 앞에는 구분자가 필요 없다 ('1-2' = 1, -2).
    steps = " ".join(f"{x1 - x0}{y1 - y0}" if y1 < y0 else f"{x1 - x0} {y1 - y0}"
                     for (x0, y0), (x1, y1) in zip(pts, pts[1:]))
    path = f"M{pts[0][0]} {pts[0][1]}l{steps}"
    base_y = y_of(closes[0])
    # 색과 선 속성은 CSS 가 아니라 속성으로 준다. 게시판 페이지의 svg 스타일이나
    # 구형 렌더러의 CSS 해석 차이에 영향받지 않게 하기 위해서다.
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<line x1="{pad}" y1="{base_y}" x2="{w - pad}" y2="{base_y}" '
        f'stroke="{PALETTE["muted"]}" stroke-width="1" stroke-dasharray="3 3"/>'
        f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.4" '
        'stroke-linejoin="round"/>'
        "</svg>"
    )


# ---------------------------------------------------------------------------
# 요약 / 캡션
# ---------------------------------------------------------------------------

def movers(b):
    """(상승 목록, 하락 목록, 보합 수). 상승/하락은 등락폭이 큰 순서다."""
    moved = [c for c in b.cards if c.chg_pct is not None]
    up = sorted((c for c in moved if c.chg_pct > 0), key=lambda c: -c.chg_pct)
    down = sorted((c for c in moved if c.chg_pct < 0), key=lambda c: c.chg_pct)
    return up, down, len(moved) - len(up) - len(down)


def summary_lines(b):
    """텔레그램 캡션 본문. 등락 개수 한 줄과 상승/하락 상위를 개조식으로 적는다.

    텔레그램 HTML(parse_mode=HTML)로 만든다. ETF 이름에 네이버 증권 링크를 걸고
    글자는 여기서 escape 하므로, 받는 쪽(TelegramUtil.photo_caption)은 다시
    escape 하지 않는다.

    날짜는 캡션 제목(report_title)에 있으므로 다시 적지 않는다. 휴대폰에서
    훑어보는 알림이라 ETF 를 쉼표로 잇지 않고 한 줄에 하나씩 둔다. 빈 문자열은
    빈 줄이다.
    """
    up, down, flat = movers(b)
    lines = [f"상승 {len(up)} / 하락 {len(down)} / 보합 {flat}"]
    for mark, label, cards in (("▲", "상승 상위", up), ("▼", "하락 상위", down)):
        if cards:
            lines += ["", f"{mark} {label}"]
            lines += [f'- <a href="{NAVER_ITEM_URL.format(code=esc(c.code))}">{esc(c.name)}</a> '
                      f"{signed(c.chg_pct)}" for c in cards[:SUMMARY_TOP_N]]
    return lines


def threads_text(b):
    """Threads 게시글 본문. 평문이다 (Threads 는 HTML 을 해석하지 않는다).

    500자 상한이 있어 텔레그램 캡션처럼 ETF 마다 링크를 걸지 않는다. 상위 ETF 는
    이미지의 카드와 같은 FOCUS_N 종을 싣는다. 해시태그는 본문에 적지 않고 게시할 때
    topic_tag 로 붙인다 (utils/threads_util.py).
    """
    up, down, flat = movers(b)
    lines = [f"{BOARD_TITLE} ({b.date} 기준)", "",
             f"{len(up) + len(down) + flat}종 중 상승 {len(up)} / 하락 {len(down)} / 보합 {flat}"]
    for mark, label, cards in (("▲", "상승 상위", up), ("▼", "하락 상위", down)):
        if cards:
            lines += ["", f"{mark} {label}"]
            lines += [f"{c.name} {signed(c.chg_pct)}" for c in cards[:FOCUS_N]]
    lines += ["", f"카드 속 차트는 {CHART_MONTHS}개월 흐름, 오른쪽은 비중 상위 "
                  f"{FOCUS_HOLDINGS}종목의 당일 등락률입니다.",
              "출처: KRX"]
    return "\n".join(lines)


def report_title(b):
    """게시글 제목과 텔레그램 캡션 첫 줄."""
    return f"{BOARD_TITLE} ({b.date})"


# ---------------------------------------------------------------------------
# 보드
# ---------------------------------------------------------------------------

def holding_name(h, link):
    """구성종목 이름. link 면 네이버 증권 뉴스 링크를 건다 (국내 종목만).

    target 은 붙이지 않는다. 게시물은 뒤로 가기로 돌아오면 된다.
    """
    if not link or h.chg_pct is None:
        return esc(h.name)
    return f'<a href="{NAVER_NEWS_URL.format(code=esc(h.code))}">{esc(h.name)}</a>'


def focus_card(c, link):
    """상위 카드. 큰 등락률, 미니차트, 비중 상위 종목."""
    rows = "\n".join(
        f'<tr><td class="n">{holding_name(h, link)}</td>\n'
        f'<td class="c {tone(h.chg_pct)}">{signed(h.chg_pct)}</td></tr>'
        for h in c.holdings[:FOCUS_HOLDINGS])
    # 선 색은 6개월 방향을 따른다. 당일 등락과 6개월 흐름이 반대인 ETF 가 한눈에 보인다.
    color = PALETTE["down"] if (c.period_return or 0) < 0 else PALETTE["up"]
    return f"""<div class="fc">
<table class="fc-h"><tr><td class="nm">{esc(c.name)}</td>
<td class="v {tone(c.chg_pct)}">{signed(c.chg_pct)}</td></tr></table>
<table class="fc-b"><tr><td class="sp">{sparkline(c.closes, 140, 78, color)}
<div class="p {tone(c.period_return)}">{CHART_MONTHS}개월 {signed(c.period_return, 1)}</div></td>
<td><table class="hl">{rows}</table></td></tr></table>
</div>"""


def focus_column(mark, label, cls, cards, empty, link):
    """상승 상위 또는 하락 상위 한 열. 해당 방향 ETF 가 없으면 empty 문구를 둔다."""
    body = "\n".join(focus_card(c, link) for c in cards) or f'<div class="fc none">{empty}</div>'
    return f'<div class="sec {cls}">{mark} {label}</div>\n{body}'


def rest_table(cards):
    """나머지 ETF 표. 이름, 작은 미니차트, 당일 등락률, 6개월 수익률."""
    rows = "\n".join(
        f'<tr><td class="n">{esc(c.name)}</td>'
        f'<td class="sp2">{sparkline(c.closes, 80, 24, PALETTE["muted"])}</td>'
        f'<td class="v {tone(c.chg_pct)}">{signed(c.chg_pct)}</td>'
        f'<td class="p {tone(c.period_return)}">{signed(c.period_return, 1)}</td></tr>'
        for c in cards)
    return f'<table class="rest">{rows}</table>'


def render_missing(b):
    if not b.missing:
        return ""
    items = "".join(f"<li>{esc(item)}: {esc(reason)}</li>" for item, reason in b.missing)
    return f"""
<div class="missing">
  <p class="missing-title">데이터 수집 내역 (미수집 {len(b.missing)}건)</p>
  <ul>{items}</ul>
</div>"""


def render_board(b, post=False):
    """.eci 로 감싼 보드 본체. 상승/하락 상위 FOCUS_N 종은 카드, 나머지는 표.

    나머지는 등락률 순이고, 등락률을 못 구한 ETF 는 맨 뒤에 둔다.
    post 는 게시판 본문용이다. 나머지 표를 2열이 아니라 1열로 두고(본문 폭 800px 에
    2열이면 ETF 이름이 잘린다), 상위 카드 구성종목에 뉴스 링크를 건다.

    게시판 목록은 본문을 strip_tags() 해서 미리보기를 만든다. 태그가 벗겨져도
    단어가 붙지 않게 요소 사이에 줄바꿈을 둔다.
    """
    up, down, flat = movers(b)
    top, bottom = up[:FOCUS_N], down[:FOCUS_N]
    shown = {id(c) for c in top + bottom}
    rest = sorted((c for c in b.cards if id(c) not in shown),
                  key=lambda c: (c.chg_pct is None, -(c.chg_pct or 0)))

    counts = f'상승 <b class="up">{len(up)}</b> / 하락 <b class="down">{len(down)}</b>'
    if flat:
        counts += f" / 보합 <b>{flat}</b>"

    rest_html = ""
    if rest:
        if post:
            table = rest_table(rest)
        else:
            half = (len(rest) + 1) // 2
            table = (f'<table class="two"><tr>\n<td>{rest_table(rest[:half])}</td>\n'
                     f'<td class="gap"></td>\n<td>{rest_table(rest[half:])}</td>\n</tr></table>')
        rest_html = (f'<div class="sec rest-sec">나머지 {len(rest)}종 (등락률 순)</div>\n'
                     f'<div class="panel">{table}</div>')

    return f"""<div class="eci{' post' if post else ''}">
<table class="hd"><tr>
<td class="t"><div class="id">{esc(BOARD_HANDLE)}</div>
{esc(BOARD_TITLE)}</td>
<td class="m">
{b.date.replace("-", ".")} 기준 / 당일 등락률<br>
{counts}</td>
</tr></table>
<table class="two cols"><tr>
<td>{focus_column("▲", "상승 상위", "up-sec", top, "상승한 ETF 없음", post)}</td>
<td class="gap"></td>
<td>{focus_column("▼", "하락 상위", "down-sec", bottom, "하락한 ETF 없음", post)}</td>
</tr></table>
{rest_html}
<table class="ft"><tr>
<td>상위 카드: 미니차트(점선 = {CHART_MONTHS}개월 전 종가), 비중 상위 {FOCUS_HOLDINGS}종목 /
표: 미니차트, 당일 등락률, {CHART_MONTHS}개월 수익률</td>
<td class="r">{foot_meta(b)}</td>
</tr></table>{render_missing(b)}
</div>"""


def foot_meta(b):
    """보드 아래 오른쪽 출처 줄."""
    pdf_note = f"구성종목 기준 {b.pdf_date}" if b.pdf_date else "구성종목 기준일 미확인"
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"{pdf_note} / 생성 {generated} / 출처 KRX"


def render(b: Bundle):
    """완결 HTML 문서. 파일 저장과 PNG 캡처에 쓴다."""
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{esc(BOARD_TITLE)} {esc(b.date)}</title>
<style>body {{ margin: 0; background: {PALETTE["panel"]}; }}{css(CSS)}</style>
</head>
<body>
{render_board(b)}
</body>
</html>
"""


def render_fragment(b: Bundle):
    """MQWAY 게시판 본문 조각. <style> 은 맨 뒤에 둔다.

    <style> 을 앞에 두면 strip_tags() 미리보기가 CSS 텍스트로 채워진다.
    """
    return f"{render_board(b, post=True)}\n<style>{css(CSS)}</style>\n"


def render_png(html_str, out_path):
    """완결 HTML 을 wkhtmltoimage 로 PNG 로 찍는다. 성공하면 out_path, 아니면 예외.

    wkhtmltoimage 실행 파일 경로는 WKHTMLTOIMAGE_PATH 환경변수로 받는다.
    형제 프로젝트(krx-topsector-report 등)와 같은 이름이라 .env 값을 그대로 옮겨도 된다.
    """
    import imgkit

    exe = os.getenv("WKHTMLTOIMAGE_PATH", "").strip()
    if not exe:
        raise RuntimeError("WKHTMLTOIMAGE_PATH 환경변수가 없습니다 (.env.example 참고)")
    if not os.path.exists(exe):
        raise RuntimeError(f"wkhtmltoimage 실행 파일이 없습니다: {exe}")

    options = {
        "format": "png",
        "encoding": "UTF-8",
        "width": PNG_WIDTH,
        # 높이를 주지 않으면 문서 높이에 맞춰 찍는다.
        "disable-smart-width": None,
        "enable-local-file-access": None,
        "quiet": None,
    }
    config = imgkit.config(wkhtmltoimage=exe)
    imgkit.from_string(html_str, out_path, options=options, config=config)
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("wkhtmltoimage 가 이미지를 만들지 못했습니다")

    # wkhtmltoimage 는 PNG 를 거의 압축하지 않는다 (예전 4열 보드 1400x1571 실측 8.8MB).
    # 텔레그램 사진 상한이 10MB 라 그대로 두면 아슬아슬하므로, 무손실로 다시
    # 저장한다. 투명 영역이 없으니 RGB 로 바꿔도 같다. 1080px 이미지는 200KB 남짓이 된다.
    from PIL import Image
    with Image.open(out_path) as img:
        img = img.convert("RGB")
    img.save(out_path, format="PNG", optimize=True)
    return out_path


# wkhtmltoimage(QtWebKit) 호환 CSS. grid / flex gap / var() 를 쓰지 않는다.
# box-sizing 은 구형 WebKit 용 -webkit- 접두어를 함께 적는다. 색은 $ink 처럼 적고
# css() 가 PALETTE 값으로 바꾼다. 그래서 CSS 안에 $ 를 다른 뜻으로 쓰면 안 된다.
#
# 이 문자열은 게시판 본문에도 그대로 실려 64KB 상한을 같이 쓴다. 설명은 CSS 주석이
# 아니라 여기 파이썬 주석으로 적는다. body 같은 페이지 전체 선택자는 넣지 않는다
# (게시판 페이지를 덮는다). 완결 문서의 body 스타일은 render() 가 따로 붙인다.
#
# 클래스 이름은 Tailwind 유틸리티와 겹치면 안 된다. 게시판은 Tailwind CDN 이라
# 페이지에 쓰인 클래스 이름을 보고 CSS 를 만들어 넣는다. 예전에 카드 묶음을 grid 로
# 했을 때 .grid { display: grid } 가 붙어 카드가 한 줄에 하나씩 쌓였다.
#
# 이미지 폭 1080px 는 휴대폰 화면에서 줄여 봐도 글자가 읽히는 크기다 (PNG_WIDTH 와
# 같게). 바탕을 옅은 회색($panel)으로 두고 카드와 표만 흰색으로 띄운다.
# 게시판(.eci.post)은 본문 폭 800px 에 맞춰 폭/여백/바탕을 풀고, 좁은 화면에서는
# 상승/하락 두 열을 위아래로 쌓는다.
CSS = """
.eci { width: 1080px; padding: 36px 40px 28px; color: $ink; background: $panel;
  font-family: "Pretendard", "Noto Sans KR", "Malgun Gothic", sans-serif; }
.eci, .eci * { -webkit-box-sizing: border-box; box-sizing: border-box; }
.eci table { border-collapse: collapse; border-spacing: 0; }
.eci td { padding: 0; }
.eci .up { color: $up; }
.eci .down { color: $down; }
.eci .hd { width: 100%; border-bottom: 3px solid $ink; margin-bottom: 20px; }
.eci .hd td { padding-bottom: 12px; vertical-align: bottom; }
.eci .hd .t { font-size: 34px; font-weight: 800; letter-spacing: -0.5px; }
.eci .hd .m { text-align: right; font-size: 16px; color: $sub; line-height: 1.5; }
.eci .hd .m b { font-size: 18px; }
.eci .hd .id { margin-bottom: 2px; font-size: 15px; font-weight: 700; letter-spacing: 0;
  color: $sub; }
.eci .two { width: 100%; table-layout: fixed; }
.eci .two td { vertical-align: top; }
.eci .two td.gap { width: 20px; }
.eci .sec { display: inline-block; margin: 0 0 10px; padding: 3px 12px; border-radius: 999px;
  font-size: 14px; font-weight: 700; }
.eci .up-sec { color: $up; background: $up_bg; }
.eci .down-sec { color: $down; background: $down_bg; }
.eci .rest-sec { margin-top: 6px; padding-left: 0; color: $sub; }
.eci .fc { margin-bottom: 12px; padding: 12px 14px; background: #fff; border: 1px solid $line;
  border-radius: 12px; }
.eci .fc.none { padding: 28px 14px; text-align: center; font-size: 14px; color: $muted; }
.eci .fc-h { width: 100%; table-layout: fixed; margin-bottom: 6px; }
.eci .fc-h .nm { font-size: 16px; font-weight: 700; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.eci .fc-h .v { width: 100px; text-align: right; font-size: 22px; font-weight: 800; }
.eci .fc-b { width: 100%; table-layout: fixed; }
.eci .fc-b .sp { width: 150px; vertical-align: top; }
.eci .fc-b .sp svg { display: block; }
.eci .fc-b .p { margin-top: 2px; font-size: 12px; text-align: center; }
.eci .hl { width: 100%; table-layout: fixed; }
.eci .hl td { height: 24px; font-size: 14px; }
.eci .hl td.n { color: $sub; white-space: nowrap; overflow: hidden; }
.eci .hl td.c { width: 70px; text-align: right; }
.eci .hl a { color: inherit; text-decoration: none; }
.eci .hl a:hover { color: $ink; text-decoration: underline; }
.eci .panel { padding: 4px 16px; background: #fff; border: 1px solid $line; border-radius: 12px; }
.eci .rest { width: 100%; table-layout: fixed; }
.eci .rest td { height: 32px; font-size: 14px; vertical-align: middle; border-bottom: 1px solid $rule; }
.eci .rest tr:last-child td { border-bottom: 0; }
.eci .rest td.n { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.eci .rest td.sp2 { width: 84px; }
.eci .rest td.sp2 svg { display: block; }
.eci .rest td.v { width: 76px; text-align: right; font-weight: 700; }
.eci .rest td.p { width: 72px; text-align: right; font-size: 12.5px; }
.eci .ft { width: 100%; margin-top: 14px; font-size: 12px; color: $muted; }
.eci .ft .r { text-align: right; }
.eci .missing { margin-top: 14px; font-size: 12px; color: $sub; }
.eci .missing-title { font-size: 13px; font-weight: 700; margin: 0 0 4px; }
.eci .missing ul { margin: 0; padding-left: 18px; }
.eci.post { width: auto; padding: 0; background: transparent; }
@media (max-width: 600px) {
  .eci.post .cols, .eci.post .cols > tbody, .eci.post .cols > tbody > tr,
  .eci.post .cols > tbody > tr > td, .eci.post .ft td { display: block; width: auto; }
  .eci.post .cols > tbody > tr > td.gap { display: none; }
  .eci.post .ft .r { text-align: left; }
  .eci.post .hd .t { font-size: 26px; }
}
"""
