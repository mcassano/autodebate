"""Tools the personas can call mid-turn.

Everything here is read-only, and every tool works with no API key at all
except brave_search, fred_series, and tmdb_search — those three simply don't
join the table until their key is set. No new dependencies beyond httpx + the
standard library.

Adding a tool: write an async handler that takes the parsed JSON arguments and
returns plain text, then register it in TOOLS. The engine picks up the schema,
dispatch, UI line, and transcript note from this file.
"""

from __future__ import annotations

import ast
import html
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from .config import BRAVE_KEY, FRED_KEY, TMDB_KEY


@dataclass(frozen=True)
class Tool:
    name: str
    schema: dict  # OpenAI function-tool schema sent to the model
    run: Callable[[dict[str, Any]], Awaitable[str]]
    hint: str  # short parenthetical for the system prompt, e.g. "live web"
    icon: str = "🔧"
    # one-line description of a call, for the UI and the transcript log
    describe: Callable[[dict[str, Any]], str] = field(
        default=lambda args: json.dumps(args, ensure_ascii=False)
    )


def _fn_schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


# ---------------------------------------------------------------- web search


async def _brave_search(args: dict[str, Any]) -> str:
    """One Brave web search → compact plain-text results for the tool message."""
    query = str(args.get("query", "")).strip()
    if not query:
        return "Empty query."
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": BRAVE_KEY, "Accept": "application/json"},
            params={"q": query, "count": 5},
        )
        r.raise_for_status()
        data = r.json()
    results = (data.get("web") or {}).get("results") or []
    lines = [
        f"- {item.get('title', '')} ({item.get('url', '')}): {item.get('description', '')}"
        for item in results[:5]
    ]
    return "\n".join(lines) or "No results found."


BRAVE_SEARCH = Tool(
    name="brave_search",
    hint="live web",
    icon="🔍",
    describe=lambda a: f'searched: "{a.get("query", "")}"',
    schema=_fn_schema(
        "brave_search",
        "Search the live web for recent facts, news, or data. Returns titles, URLs, and snippets.",
        {"query": {"type": "string", "description": "The web search query."}},
        ["query"],
    ),
    run=_brave_search,
)


# ---------------------------------------------------------------- wikipedia


def _strip_snippet(snippet: str) -> str:
    """Wikipedia search snippets carry <span class="searchmatch"> highlights."""
    return html.unescape(re.sub(r"<[^>]+>", "", snippet))


async def _wikipedia_search(args: dict[str, Any]) -> str:
    """Free, keyless Wikipedia full-text search — background and definitions."""
    query = str(args.get("query", "")).strip()
    if not query:
        return "Empty query."
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 5,
                "format": "json",
            },
            # Wikimedia's API etiquette policy rejects a bare UA with no contact info
            headers={"User-Agent": "autodebate/0.1 (https://github.com/mcassano/autodebate)"},
        )
        r.raise_for_status()
    hits = (r.json().get("query") or {}).get("search") or []
    lines = []
    for h in hits[:5]:
        title = h.get("title", "")
        url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
        lines.append(f"- {title} ({url}): {_strip_snippet(h.get('snippet', ''))}")
    return "\n".join(lines) or "No results found."


WIKIPEDIA_SEARCH = Tool(
    name="wikipedia_search",
    hint="encyclopedia",
    icon="📖",
    describe=lambda a: f'looked up: "{a.get("query", "")}"',
    schema=_fn_schema(
        "wikipedia_search",
        "Search Wikipedia for background, definitions, and history on a topic, "
        "person, or event. Returns titles, URLs, and snippets.",
        {"query": {"type": "string", "description": "The search query."}},
        ["query"],
    ),
    run=_wikipedia_search,
)


# ---------------------------------------------------------------- web fetch


class _TextExtractor(HTMLParser):
    """Bare-bones HTML → text: skip script/style, keep block boundaries."""

    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "article"}

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        return re.sub(r"\n\s*\n+", "\n\n", raw).strip()


MAX_FETCH_CHARS = 4000


async def _web_fetch(args: dict[str, Any]) -> str:
    """GET a public page and return its readable text, truncated."""
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return "Only http(s) URLs can be fetched."
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "autodebate/0.1"})
        r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    body = r.text[:200_000]
    if "html" in ctype:
        parser = _TextExtractor()
        parser.feed(body)
        body = parser.text()
    return body[:MAX_FETCH_CHARS] or "(page had no readable text)"


WEB_FETCH = Tool(
    name="web_fetch",
    hint="read a page",
    icon="📄",
    describe=lambda a: f"is reading {a.get('url', '')}",
    schema=_fn_schema(
        "web_fetch",
        "Fetch a URL and return its readable text (truncated). Use to read a page "
        "found via brave_search.",
        {"url": {"type": "string", "description": "The http(s) URL to read."}},
        ["url"],
    ),
    run=_web_fetch,
)


# ---------------------------------------------------------------- calculator

_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
}


def _eval_node(node) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        val = _eval_node(node.operand)
        return val if isinstance(node.op, ast.UAdd) else -val
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("exponent too large")
        return _ALLOWED_BINOPS[type(node.op)](left, right)
    raise ValueError("only arithmetic on numbers is allowed (+ - * / // % ** and parentheses)")


async def _calculator(args: dict[str, Any]) -> str:
    """Safe arithmetic via an AST whitelist — never eval()."""
    expr = str(args.get("expression", "")).strip()[:200]
    if not expr:
        return "Empty expression."
    try:
        result = _eval_node(ast.parse(expr, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as e:
        return f"Could not evaluate: {e}"
    return f"{expr} = {result:g}"


CALCULATOR = Tool(
    name="calculator",
    hint="arithmetic",
    icon="🧮",
    describe=lambda a: f"does the math: {a.get('expression', '')}",
    schema=_fn_schema(
        "calculator",
        "Evaluate an arithmetic expression (+ - * / // % ** and parentheses). "
        "Use for Fermi estimates and checking numbers before citing them.",
        {"expression": {"type": "string", "description": "The arithmetic expression."}},
        ["expression"],
    ),
    run=_calculator,
)


# ---------------------------------------------------------------- arXiv

_ATOM = "{http://www.w3.org/2005/Atom}"


async def _arxiv_search(args: dict[str, Any]) -> str:
    """Recent arXiv papers matching a query (free API, no key)."""
    query = str(args.get("query", "")).strip()
    if not query:
        return "Empty query."
    params = urllib.parse.urlencode(
        {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": 5,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"https://export.arxiv.org/api/query?{params}")
        r.raise_for_status()
    root = ET.fromstring(r.text)
    lines = []
    for entry in root.findall(f"{_ATOM}entry"):
        title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
        link = entry.findtext(f"{_ATOM}id") or ""
        published = (entry.findtext(f"{_ATOM}published") or "")[:10]
        summary = " ".join((entry.findtext(f"{_ATOM}summary") or "").split())[:300]
        lines.append(f"- {title} ({published}) {link}\n  {summary}…")
    return "\n".join(lines) or "No papers found."


ARXIV_SEARCH = Tool(
    name="arxiv_search",
    hint="papers",
    icon="📚",
    describe=lambda a: f'searched arXiv: "{a.get("query", "")}"',
    schema=_fn_schema(
        "arxiv_search",
        "Search arXiv for recent research papers. Returns titles, dates, links, abstracts.",
        {"query": {"type": "string", "description": "The paper search query."}},
        ["query"],
    ),
    run=_arxiv_search,
)


# ---------------------------------------------------------------- stock quote


async def _stock_quote(args: dict[str, Any]) -> str:
    """Latest quote for a ticker via Yahoo Finance's public chart endpoint."""
    symbol = str(args.get("symbol", "")).strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-^=]{1,12}", symbol):
        return "Invalid symbol."
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        r.raise_for_status()
    result = (r.json().get("chart") or {}).get("result") or []
    if not result:
        return f"No quote found for {symbol}."
    m = result[0]["meta"]
    price, prev = m.get("regularMarketPrice"), m.get("previousClose")
    if price is None:
        return f"No quote found for {symbol}."
    change = f", {(price / prev - 1) * 100:+.1f}% on the day" if prev else ""
    hi, lo = m.get("regularMarketDayHigh"), m.get("regularMarketDayLow")
    span = f", day range {lo}–{hi}" if hi and lo else ""
    exchange = m.get("exchangeName", "?")
    return f"{m['symbol']}: {price} {m.get('currency', '')}{change}{span} on {exchange}"


STOCK_QUOTE = Tool(
    name="stock_quote",
    hint="tickers",
    icon="📈",
    describe=lambda a: f"checks the ticker: {str(a.get('symbol', '')).upper()}",
    schema=_fn_schema(
        "stock_quote",
        "Get the latest quote for a stock/ETF ticker (e.g. NVDA, SPY) or index "
        "(e.g. ^GSPC). Data from Yahoo Finance, may be delayed.",
        {"symbol": {"type": "string", "description": "The ticker symbol."}},
        ["symbol"],
    ),
    run=_stock_quote,
)


# ---------------------------------------------------------------- world bank

_WB_INDICATORS = {
    "gdp": ("NY.GDP.MKTP.CD", "GDP (current US$)"),
    "gdp_growth": ("NY.GDP.MKTP.KD.ZG", "GDP growth (annual %)"),
    "inflation": ("FP.CPI.TOTL.ZG", "inflation, consumer prices (annual %)"),
    "unemployment": ("SL.UEM.TOTL.ZS", "unemployment (% of labor force)"),
    "population": ("SP.POP.TOTL", "population"),
}


async def _world_bank(args: dict[str, Any]) -> str:
    """Free, keyless World Bank indicator lookup — global economic data by country."""
    country = str(args.get("country", "")).strip()
    indicator = str(args.get("indicator", "")).strip().lower()
    if indicator not in _WB_INDICATORS:
        return f"Unknown indicator {indicator!r} — choose from {', '.join(_WB_INDICATORS)}."
    if not country:
        return "Empty country."
    code, label = _WB_INDICATORS[indicator]
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"https://api.worldbank.org/v2/country/{urllib.parse.quote(country)}/indicator/{code}",
            params={"format": "json", "per_page": 1, "mrnev": 1},
        )
        r.raise_for_status()
    data = r.json()
    points = data[1] if isinstance(data, list) and len(data) > 1 else None
    if not points or points[0].get("value") is None:
        return f"No {label} data for {country!r} — use an ISO country code, e.g. US, FR, CN."
    point = points[0]
    name = (point.get("country") or {}).get("value", country)
    value = point.get("value")
    value_str = f"{value:,.2f}" if isinstance(value, (int, float)) else str(value)
    return f"{name} — {label}, {point.get('date')}: {value_str}"


WORLD_BANK = Tool(
    name="world_bank_indicator",
    hint="global econ data",
    icon="🌍",
    describe=lambda a: f"checks {a.get('indicator', '')} for {a.get('country', '')}",
    schema=_fn_schema(
        "world_bank_indicator",
        "Get the latest World Bank figure for a country: gdp, gdp_growth, inflation, "
        "unemployment, or population. Good for cross-country comparisons.",
        {
            "country": {
                "type": "string",
                "description": "ISO-3166 country code (e.g. US, FR, CN) or aggregate (e.g. WLD).",
            },
            "indicator": {
                "type": "string",
                "enum": list(_WB_INDICATORS),
                "description": "Which indicator to fetch.",
            },
        },
        ["country", "indicator"],
    ),
    run=_world_bank,
)


# ---------------------------------------------------------------- hacker news


async def _hn_search(args: dict[str, Any]) -> str:
    """Free, keyless Hacker News search (Algolia) — what the tech crowd is discussing."""
    query = str(args.get("query", "")).strip()
    if not query:
        return "Empty query."
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": query, "tags": "story", "hitsPerPage": 5},
        )
        r.raise_for_status()
    hits = r.json().get("hits") or []
    lines = [
        f"- {h.get('title', '')} ({h.get('points', 0)} pts, {h.get('num_comments', 0)} "
        f"comments) https://news.ycombinator.com/item?id={h.get('objectID', '')}"
        for h in hits[:5]
    ]
    return "\n".join(lines) or "No results found."


HN_SEARCH = Tool(
    name="hn_search",
    hint="tech pulse",
    icon="💻",
    describe=lambda a: f'searched Hacker News: "{a.get("query", "")}"',
    schema=_fn_schema(
        "hn_search",
        "Search Hacker News for what the tech/startup crowd is discussing on a topic. "
        "Returns titles, points, comment counts, and links.",
        {"query": {"type": "string", "description": "The search query."}},
        ["query"],
    ),
    run=_hn_search,
)


# ---------------------------------------------------------------- FRED


async def _fred_series(args: dict[str, Any]) -> str:
    """Latest observation for a FRED economic series (e.g. UNRATE, CPIAUCSL, FEDFUNDS)."""
    series_id = str(args.get("series_id", "")).strip().upper()
    if not series_id:
        return "Empty series_id."
    params = {"series_id": series_id, "api_key": FRED_KEY, "file_type": "json"}
    async with httpx.AsyncClient(timeout=20) as client:
        obs_r = await client.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={**params, "sort_order": "desc", "limit": 1},
        )
        obs_r.raise_for_status()
        obs = obs_r.json().get("observations") or []
        if not obs:
            return f"No data for FRED series {series_id!r} — check the ID at fred.stlouisfed.org."
        meta_r = await client.get("https://api.stlouisfed.org/fred/series", params=params)
        meta_r.raise_for_status()
        series = (meta_r.json().get("seriess") or [{}])[0]
    point = obs[0]
    title = series.get("title", series_id)
    units = series.get("units", "")
    return f"{title} ({series_id}): {point.get('value')} {units} as of {point.get('date')}"


FRED_SERIES = Tool(
    name="fred_series",
    hint="US econ data",
    icon="📊",
    describe=lambda a: f"pulls FRED series: {str(a.get('series_id', '')).upper()}",
    schema=_fn_schema(
        "fred_series",
        "Get the latest reading for a US economic data series from the Federal "
        "Reserve (FRED). Common series: UNRATE (unemployment), CPIAUCSL (CPI), "
        "FEDFUNDS (fed funds rate), GDP, DGS10 (10-year yield).",
        {"series_id": {"type": "string", "description": "The FRED series ID."}},
        ["series_id"],
    ),
    run=_fred_series,
)


# ---------------------------------------------------------------- TMDb


async def _tmdb_search(args: dict[str, Any]) -> str:
    """Movie/TV lookup via The Movie Database — title, year, rating, synopsis."""
    query = str(args.get("query", "")).strip()
    if not query:
        return "Empty query."
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            "https://api.themoviedb.org/3/search/movie",
            params={"api_key": TMDB_KEY, "query": query},
        )
        r.raise_for_status()
    results = r.json().get("results") or []
    lines = []
    for m in results[:5]:
        year = (m.get("release_date") or "")[:4]
        overview = (m.get("overview") or "")[:200]
        lines.append(
            f"- {m.get('title', '')} ({year}), rating {m.get('vote_average', '?')}/10: {overview}"
        )
    return "\n".join(lines) or "No results found."


TMDB_SEARCH = Tool(
    name="tmdb_search",
    hint="film & TV",
    icon="🎬",
    describe=lambda a: f'looked up film: "{a.get("query", "")}"',
    schema=_fn_schema(
        "tmdb_search",
        "Search for a movie by title. Returns release year, rating, and synopsis.",
        {"query": {"type": "string", "description": "The movie title to search for."}},
        ["query"],
    ),
    run=_tmdb_search,
)


# ---------------------------------------------------------------- date & time


async def _current_datetime(args: dict[str, Any]) -> str:
    now = datetime.now()
    utc = datetime.now(UTC)
    return (
        f"Local: {now:%A, %Y-%m-%d %H:%M} ({now.astimezone().tzname()}) · UTC: {utc:%Y-%m-%d %H:%M}"
    )


CURRENT_DATETIME = Tool(
    name="current_datetime",
    hint="date & time",
    icon="🕐",
    describe=lambda a: "checks the date",
    schema=_fn_schema(
        "current_datetime",
        "Get the current local and UTC date and time. Your training data has a "
        "cutoff — check this before reasoning about 'recent' events.",
        {},
        [],
    ),
    run=_current_datetime,
)


# ---------------------------------------------------------------- registry

# Everything is free and keyless except the three below, which join the table
# only when their key is set — nobody should need any key to experiment.
KEYLESS_TOOLS: tuple[Tool, ...] = (
    WEB_FETCH,
    CALCULATOR,
    ARXIV_SEARCH,
    STOCK_QUOTE,
    WIKIPEDIA_SEARCH,
    WORLD_BANK,
    HN_SEARCH,
    CURRENT_DATETIME,
)

_KEYED_TOOLS: tuple[tuple[str | None, Tool], ...] = (
    (BRAVE_KEY, BRAVE_SEARCH),
    (FRED_KEY, FRED_SERIES),
    (TMDB_KEY, TMDB_SEARCH),
)

TOOLS: tuple[Tool, ...] = KEYLESS_TOOLS + tuple(tool for key, tool in _KEYED_TOOLS if key)
TOOL_BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}
TOOL_SCHEMAS: list[dict] = [t.schema for t in TOOLS]
