# -*- coding: utf-8 -*-
"""데이터 수집 - KRX(pykrx)

ETF 카드 한 장에 들어가는 값을 모아 Bundle 하나에 담는다.
  - ETF 당일 등락률
  - 6개월 종가 흐름과 6개월 전 종가 (미니차트, 점선)
  - ETF 비중 상위 종목과 각 종목의 당일 등락률

수집 실패는 예외로 터뜨리지 않고 Bundle.miss()에 사유와 함께 남긴다. 보드 하단의
'데이터 수집 내역'에 그대로 실려, 카드가 비어 있는 이유를 확인할 수 있게 하기
위해서다. 기준일 전 종목 시세만은 예외로 RuntimeError를 올린다. 그 값이 없으면
휴장일이거나 KRX 로그인 실패라 보드 자체가 성립하지 않는다.

pykrx는 함수 안에서 import한다. 설치되지 않은 패키지 때문에 모듈 로드 단계에서
죽지 않게 하고, 호출한 쪽이 ImportError를 따로 처리할 수 있도록 하기 위함이다.
"""

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

# 카드 순서대로 나열한다. 표시명은 카드 제목에 그대로 쓰이므로 KRX 정식 명칭이
# 너무 길면 줄여 적어도 된다 (예: 'TIGER 코리아휴머노이드로봇산업').
ETF_TICKERS = {
    "RISE 네트워크인프라":           "367760",
    "SOL 반도체전공정":              "475300",
    "SOL 반도체후공정":              "475310",
    "KODEX AI전력핵심설비":          "487240",
    "HANARO 원자력iSelect":          "434730",
    "PLUS K방산":                    "449450",
    "PLUS K방산소부장":              "0090B0",
    "KODEX 2차전지핵심소재10":       "461950",
    "ACE KPOP포커스":                "475050",
    "TIGER 화장품":                  "228790",
    "TIGER 여행레저":                "228800",
    "TIGER 미디어컨텐츠":            "228810",
    "KODEX 로봇액티브":              "445290",
    "TIGER 코리아휴머노이드로봇산업": "0148J0",
    "SOL 조선TOP3플러스":            "466920",
    "TIME K신재생에너지액티브":      "404120",
    "TIGER 증권":                    "157500",
    "TIGER 은행":                    "091220",
    "TIGER 200 금융":                "139270",
    "KODEX 건설":                    "117700",
    "TIGER 게임TOP10":               "364990",
    "TIGER 바이오TOP10":             "364970",
    "TIGER 기술이전바이오액티브":    "0168K0",
    "KoAct 광통신&위성네트워크액티브": "0219B0",
    "PLUS 태양광&ESS":               "457990",
    "KODEX 전고체배터리ESS TOP2플러스": "0209D0",
    "PLUS 우주항공":                 "421320",
    "SOL 우주항공밸류체인":          "0207G0",
}

# Threads 본문에 쓰는 짧은 이름. 한 줄에 4종을 이어 적으므로 운용사와 TOP10/액티브
# 같은 꼬리를 뗀다. 서로 헷갈리지 않게 반도체 전/후공정, 방산/방산소부장, 우주항공/
# 우주항공밸류체인은 구분해 둔다. 여기 없는 ETF 는 표시명에서 운용사만 떼어 쓴다.
ETF_SHORT_NAMES = {
    "RISE 네트워크인프라":           "네트워크인프라",
    "SOL 반도체전공정":              "반도체전공정",
    "SOL 반도체후공정":              "반도체후공정",
    "KODEX AI전력핵심설비":          "AI전력설비",
    "HANARO 원자력iSelect":          "원자력",
    "PLUS K방산":                    "방산",
    "PLUS K방산소부장":              "방산소부장",
    "KODEX 2차전지핵심소재10":       "2차전지소재",
    "ACE KPOP포커스":                "K팝",
    "TIGER 화장품":                  "화장품",
    "TIGER 여행레저":                "여행레저",
    "TIGER 미디어컨텐츠":            "미디어",
    "KODEX 로봇액티브":              "로봇",
    "TIGER 코리아휴머노이드로봇산업": "휴머노이드",
    "SOL 조선TOP3플러스":            "조선",
    "TIME K신재생에너지액티브":      "신재생에너지",
    "TIGER 증권":                    "증권",
    "TIGER 은행":                    "은행",
    "TIGER 200 금융":                "금융",
    "KODEX 건설":                    "건설",
    "TIGER 게임TOP10":               "게임",
    "TIGER 바이오TOP10":             "바이오",
    "TIGER 기술이전바이오액티브":    "기술이전바이오",
    "KoAct 광통신&위성네트워크액티브": "광통신",
    "PLUS 태양광&ESS":               "태양광&ESS",
    "KODEX 전고체배터리ESS TOP2플러스": "전고체배터리",
    "PLUS 우주항공":                 "우주항공",
    "SOL 우주항공밸류체인":          "우주항공밸류체인",
}
TOP_N_HOLDINGS = 5     # 카드 오른쪽에 싣는 비중 상위 종목 수
CHART_MONTHS = 6       # 미니차트 기간 (개월)

# PDF(구성종목)에는 현금/예금/선물 같은 비주식 행이 6자리 코드로 섞여 나온다
# (예: '010010 원화현금'). 이름에 아래 단어가 들어간 행은 상위 종목에서 뺀다.
NON_STOCK_WORDS = ("현금", "예금", "선물", "스왑", "콜론")


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------

@dataclass
class Holding:
    code: str
    name: str
    weight: float                  # ETF 내 비중 (%)
    chg_pct: float | None = None   # 당일 등락률 (%)


@dataclass
class EtfCard:
    code: str
    name: str
    chg_pct: float | None = None                       # 당일 등락률 (%)
    closes: list = field(default_factory=list)         # 6개월 종가, 첫 값이 6개월 전 종가
    holdings: list = field(default_factory=list)       # Holding 목록, 비중 내림차순

    @property
    def base_close(self):
        """미니차트 점선 위치. 6개월 전 종가."""
        return self.closes[0] if self.closes else None

    @property
    def period_return(self):
        """6개월 수익률 (%). 종가가 2개 미만이면 None."""
        if len(self.closes) < 2 or not self.closes[0]:
            return None
        return (self.closes[-1] / self.closes[0] - 1) * 100


@dataclass
class Bundle:
    date: str          # 기준 거래일 YYYY-MM-DD
    date_krx: str      # 같은 날짜 YYYYMMDD (pykrx 인자)
    pdf_date: str = ""  # 구성종목 기준일 YYYY-MM-DD (기준일의 전 거래일)
    cards: list = field(default_factory=list)
    missing: list = field(default_factory=list)   # (항목, 사유)

    def miss(self, item, reason):
        self.missing.append((item, reason))


# ---------------------------------------------------------------------------
# KRX 수집
# ---------------------------------------------------------------------------

def collect_krx(b):
    """ETF_TICKERS 의 모든 ETF 카드를 채운다.

    구성종목은 기준일의 전 거래일 PDF를 쓴다. 장중에 돌려도 확정된 값이 있고,
    전일 종가로 평가한 비중이라 당일 등락에 따라 순위가 흔들리지 않는다.
    """
    from pykrx import stock

    # 구성종목 등락률은 종목마다 부르지 않고 전 종목 시세 한 번으로 찾는다.
    stocks = stock.get_market_ohlcv_by_ticker(b.date_krx, market="ALL")
    if stocks.empty:
        raise RuntimeError(f"{b.date} 전 종목 시세가 비어 있습니다 "
                           "(휴장일이거나 KRX 로그인 실패)")
    stock_chg = stocks["등락률"]

    end = datetime.strptime(b.date_krx, "%Y%m%d")
    chart_start = _months_before(end, CHART_MONTHS)
    # 6개월 전 날짜가 휴장일이면 그 직전 거래일 종가를 기준으로 삼기 위해 여유를 둔다.
    fetch_from = (chart_start - timedelta(days=14)).strftime("%Y%m%d")

    for name, code in ETF_TICKERS.items():
        card = EtfCard(code=code, name=name)
        b.cards.append(card)

        try:
            ohlcv = stock.get_etf_ohlcv_by_date(fetch_from, b.date_krx, code)
        except Exception as e:
            b.miss(f"{name} 시세", f"{type(e).__name__}: {str(e)[:60]}")
            continue
        if ohlcv.empty:
            b.miss(f"{name} 시세", "응답이 비어 있음")
            continue

        closes = ohlcv["종가"]
        if closes.index[-1].strftime("%Y%m%d") != b.date_krx:
            b.miss(f"{name} 당일 등락률",
                   f"기준일 시세 없음 (최종 {closes.index[-1]:%Y-%m-%d})")
        elif len(closes) >= 2:
            card.chg_pct = (closes.iloc[-1] / closes.iloc[-2] - 1) * 100

        base_pos = closes.index.searchsorted(chart_start, side="right") - 1
        card.closes = [float(x) for x in closes.iloc[max(base_pos, 0):]]

        if len(closes) < 2:
            b.miss(f"{name} 구성종목", "전 거래일을 찾지 못함")
            continue
        prev_day = closes.index[-2]
        if not b.pdf_date:
            b.pdf_date = prev_day.strftime("%Y-%m-%d")
        card.holdings = _top_holdings(stock, code, prev_day.strftime("%Y%m%d"),
                                      stock_chg, b, name)


def _top_holdings(stock, code, date_krx, stock_chg, b, name):
    """PDF에서 비주식 행을 빼고 비중 상위 TOP_N_HOLDINGS 종목을 돌려준다."""
    try:
        pdf = stock.get_etf_portfolio_deposit_file(code, date_krx)
    except Exception as e:
        b.miss(f"{name} 구성종목", f"{type(e).__name__}: {str(e)[:60]}")
        return []
    if pdf.empty:
        b.miss(f"{name} 구성종목", "PDF가 비어 있음")
        return []

    pattern = "|".join(NON_STOCK_WORDS)
    pdf = pdf[~pdf["구성종목명"].astype(str).str.contains(pattern)]
    pdf = pdf.sort_values("비중", ascending=False).head(TOP_N_HOLDINGS)

    holdings = []
    for ticker, row in pdf.iterrows():
        chg = stock_chg.get(ticker)
        # 해외 종목이나 비상장 자산은 국내 시세표에 없다. 값을 지어내지 않고 비워 둔다.
        holdings.append(Holding(code=str(ticker), name=str(row["구성종목명"]),
                                weight=float(row["비중"]),
                                chg_pct=None if chg is None else float(chg)))
    return holdings


def _months_before(d, months):
    """d 에서 months 개월 전 날짜. 말일이 없는 달이면 그 달의 마지막 날로 맞춘다."""
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    m += 1
    for day in (d.day, 30, 29, 28):
        try:
            return d.replace(year=y, month=m, day=day)
        except ValueError:
            continue
    raise ValueError(f"날짜 계산 실패: {d} - {months}개월")


# ---------------------------------------------------------------------------
# 데모 데이터
# ---------------------------------------------------------------------------

def demo_bundle():
    """네트워크 없이 렌더링을 확인하기 위한 샘플. 값은 난수이며 실제 시세가 아니다."""
    rng = random.Random(20261028)
    b = Bundle(date="2026-10-28", date_krx="20261028", pdf_date="2026-10-27")
    for name, code in ETF_TICKERS.items():
        price, closes = 10000.0, []
        drift = rng.uniform(-0.006, 0.004)
        for _ in range(122):
            price *= 1 + drift + rng.gauss(0, 0.02)
            closes.append(round(price))
        holdings = [Holding(code=f"{i:06d}", name=f"샘플종목{i}",
                            weight=round(rng.uniform(5, 20), 2),
                            chg_pct=round(rng.uniform(-4, 9), 2))
                    for i in range(1, TOP_N_HOLDINGS + 1)]
        b.cards.append(EtfCard(code=code, name=name,
                               chg_pct=round(rng.uniform(-1, 9), 2),
                               closes=closes, holdings=holdings))
    # 수집 실패 표시도 렌더링되는지 확인한다.
    b.cards[-1].holdings[-1].chg_pct = None
    b.miss("SOL 우주항공밸류체인 구성종목 1건 등락률", "데모: 국내 시세표에 없음")
    return b
