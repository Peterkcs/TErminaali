"""Bloomberg TOP <GO> -tyylinen markkinauutisagentti.

Hakee markkinadatan (yfinance) ja uutiset (RSS), suodattaa ne Claudella
ja kirjoittaa päiväraportin stdoutiin sekä tiedostoon latest_brief.txt.
"""

import os
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import feedparser
import yfinance as yf
from dotenv import load_dotenv

# yfinance tuottaa runsaasti pandasin deprecation-varoituksia
warnings.filterwarnings("ignore", module="yfinance")

MODEL = "claude-sonnet-5-5"
OUTPUT_FILE = Path(__file__).parent / "latest_brief.txt"
NEWS_PER_FEED = 12

INSTRUMENTS = [
    ("Indeksit", "S&P 500", "^GSPC"),
    ("Indeksit", "Nasdaq 100", "^NDX"),
    ("Indeksit", "STOXX 600", "^STOXX"),
    ("Indeksit", "OMXH25", "^OMXH25"),
    ("Korot & VIX", "US 10Y Yield", "^TNX"),
    ("Korot & VIX", "VIX", "^VIX"),
    ("FX & Raaka-aineet", "EUR/USD", "EURUSD=X"),
    ("FX & Raaka-aineet", "Brent Crude", "BZ=F"),
    ("FX & Raaka-aineet", "Gold", "GC=F"),
]

FEEDS = {
    "CNBC Finance": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    "MarketWatch Top Stories": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Investing.com News": "https://www.investing.com/rss/news.rss",
    "Kauppalehti Uutiset": "https://feeds.kauppalehti.fi/rss/main",
}

SYSTEM_PROMPT = """Olet Bloomberg Terminalin uutisalgoritmi (TOP <GO>). Tehtäväsi on suodattaa raakauutisista 8–10 tärkeintä rahoitusmarkkinauutista (globaali makro + Pohjoismaat/Suomi).
SÄÄNNÖT:
1. Suodata pois klikkiotsikot ja mielipidekirjoitukset. Priorisoi: keskuspankit (Fed, EKP), makrotalousdata (CPI, BKT, PMI, työllisyys), korkomarkkinat, merkittävät tulosjulkistukset, M&A ja geopoliittiset markkinashokit.
2. Käytä äärimmäisen tiivistä, numeerista ja analyyttistä suomen kieltä. Ei täytesanoja tai johdantoja.
3. Merkitse jokaisen uutisen alkuun kategoria: [MACRO], [RATES], [EQTY], [FX/CMDTY] tai [M&A].
4. Kirjoita otsikko ISOIN KIRJAIMIN.
5. Tiivistä faktat ja luvut max 2 riville.
6. Lisää jokaisen uutisen perään välitön markkinavaikutus muodossa: -> VAIKUTUS: [Instrumentti/Sektori + suunta (+/-)]."""

OUTPUT_FRAME = """======================================================================
BLOOMBERG TERMINAL BRIEF // [PÄIVÄMÄÄRÄ JA KELLONAIKA UTC]
======================================================================
[MARKET SNAPSHOT]
<Markkinadata 2 sarakkeen tasalevyisenä taulukkona>

[TOP <GO> HEADLINES]
1. [KATEGORIA] OTSIKKO
   - Tiivistetyt faktat ja luvut.
   -> VAIKUTUS: ...
======================================================================"""


def _fetch_quote(ticker: str) -> tuple[float, float]:
    """Palauttaa (viimeisin päätöskurssi, päivämuutos %)."""
    closes = yf.Ticker(ticker).history(period="7d", interval="1d")["Close"].dropna()
    if len(closes) < 2:
        raise ValueError("liian vähän dataa")
    last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
    return last, (last / prev - 1) * 100


def get_market_snapshot() -> str:
    """Hakee markkinadatan ja palauttaa sen tasalevyisenä ASCII-taulukkona."""
    sep = "+" + "-" * 20 + "+" + "-" * 14 + "+" + "-" * 10 + "+"
    lines = [sep, f"| {'INSTRUMENTTI':<18} | {'VIIMEISIN':>12} | {'MUUTOS':>8} |", sep]
    group = None
    for grp, name, ticker in INSTRUMENTS:
        if grp != group:
            if group is not None:
                lines.append(sep)
            lines.append(f"| {grp.upper():<18} | {'':>12} | {'':>8} |")
            group = grp
        try:
            last, change = _fetch_quote(ticker)
            decimals = 4 if ticker == "EURUSD=X" else 2
            lines.append(f"| {name:<18} | {last:>12,.{decimals}f} | {change:>+7.2f}% |")
        except Exception as exc:  # yksittäinen ticker ei saa kaataa ajoa
            print(f"[WARN] {ticker}: {exc}", file=sys.stderr)
            lines.append(f"| {name:<18} | {'N/A':>12} | {'N/A':>8} |")
    lines.append(sep)
    return "\n".join(lines)


def get_raw_news() -> list[dict]:
    """Hakee uusimmat uutiset RSS-syötteistä ja poistaa duplikaattiotsikot."""
    news, seen = [], set()
    for source, url in FEEDS.items():
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0 (market-brief-agent)")
            if feed.bozo and not feed.entries:
                raise ValueError(feed.get("bozo_exception", "tyhjä syöte"))
        except Exception as exc:
            print(f"[WARN] {source}: {exc}", file=sys.stderr)
            continue
        for entry in feed.entries[:NEWS_PER_FEED]:
            title = " ".join(entry.get("title", "").split())
            key = title.casefold()
            if not title or key in seen:
                continue
            seen.add(key)
            parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            published = (
                time.strftime("%Y-%m-%d %H:%M UTC", parsed)
                if parsed
                else entry.get("published", "")
            )
            news.append(
                {
                    "source": source,
                    "title": title,
                    "published": published,
                    "summary": " ".join(entry.get("summary", "").split())[:500],
                }
            )
    return news


def generate_terminal_brief(snapshot: str, news: list[dict]) -> str:
    """Suodattaa uutiset Claudella ja palauttaa valmiin raportin."""
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY puuttuu (ympäristömuuttuja tai .env)")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    news_text = "\n".join(
        f"- [{n['source']}] ({n['published']}) {n['title']}"
        + (f" :: {n['summary']}" if n["summary"] else "")
        for n in news
    )
    user_prompt = (
        f"PÄIVÄMÄÄRÄ JA KELLONAIKA: {timestamp}\n\n"
        f"MARKKINADATA:\n{snapshot}\n\n"
        f"RAAKAUUTISET ({len(news)} kpl):\n{news_text}\n\n"
        "Palauta raportti täsmälleen tässä ASCII-kehyksessä. Sijoita markkinadata "
        "[MARKET SNAPSHOT]-osioon sellaisenaan tasalevyisenä taulukkona. "
        "Älä lisää mitään kehyksen ulkopuolelle.\n\n"
        f"{OUTPUT_FRAME}"
    )

    client = anthropic.Anthropic(api_key=api_key)
    # temperature-parametria ei lähetetä: Claude Sonnet 5.5 hylkää sen (400).
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Malli kieltäytyi: {response.stop_details}")
    return "".join(b.text for b in response.content if b.type == "text").strip()


def main() -> None:
    snapshot = get_market_snapshot()
    news = get_raw_news()
    if not news:
        raise RuntimeError("Yhtään uutista ei saatu haettua")
    brief = generate_terminal_brief(snapshot, news)
    print(brief)
    OUTPUT_FILE.write_text(brief + "\n", encoding="utf-8")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
