"""Tools the personas can call mid-turn.

Everything here is read-only and free: no new API keys beyond Brave, no new
dependencies beyond httpx + the standard library.

Adding a tool: write an async handler that takes the parsed JSON arguments and
returns plain text, then register it in TOOLS. The engine picks up the schema,
dispatch, UI line, and transcript note from this file.
"""

from __future__ import annotations

import ast
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

from .config import BRAVE_KEY


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

# Everything is free and keyless except brave_search, which joins the table only
# when BRAVE_SEARCH_API_KEY is set — nobody should need a search key to experiment.
KEYLESS_TOOLS: tuple[Tool, ...] = (
    WEB_FETCH,
    CALCULATOR,
    ARXIV_SEARCH,
    STOCK_QUOTE,
    CURRENT_DATETIME,
)

TOOLS: tuple[Tool, ...] = (BRAVE_SEARCH, *KEYLESS_TOOLS) if BRAVE_KEY else KEYLESS_TOOLS
TOOL_BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}
TOOL_SCHEMAS: list[dict] = [t.schema for t in TOOLS]
