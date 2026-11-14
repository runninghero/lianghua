#!/usr/bin/env python3
"""Build data/returns.json for the US sector ETF trailing-return dashboard.

Prices are dividend-reinvested total-return levels: Yahoo Finance chart v8
adjusted close. An optional seed CSV is used only when every live attempt
fails; it is not required and is not part of the published site.

N-month return as of the latest completed session T:

    anchor = T minus N calendar months (day clamped to the target month)
    start  = last trading day on or before that anchor
    return = adjClose(T) / adjClose(start) - 1

Ranks are competition ranks (ties take the minimum rank) among the 11 ETFs
that have a price on or before the start date.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import gzip
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "returns.json"

NY = ZoneInfo("America/New_York")
SHANGHAI = ZoneInfo("Asia/Shanghai")

TICKERS: list[tuple[str, str]] = [
    ("XLB", "原材料"),
    ("XLC", "通信服务"),
    ("XLE", "能源"),
    ("XLF", "金融"),
    ("XLI", "工业"),
    ("XLK", "科技"),
    ("XLP", "必需消费"),
    ("XLRE", "房地产"),
    ("XLU", "公用事业"),
    ("XLV", "医疗"),
    ("XLY", "可选消费"),
]
NAMES = dict(TICKERS)
WINDOWS = (1, 3, 6, 9, 12)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
TIMEOUT = 30
ATTEMPTS = 4


def subtract_months(day: date, months: int) -> date:
    """Calendar date `months` before `day`, clamping the day-of-month."""
    month = day.month - months
    year = day.year
    while month <= 0:
        month += 12
        year -= 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def last_on_or_before(days: list[date], target: date) -> date | None:
    """Last entry of a sorted date list that is on or before `target`."""
    lo, hi = 0, len(days)
    while lo < hi:
        mid = (lo + hi) // 2
        if days[mid] <= target:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    return days[lo - 1]


def min_ranks(pairs: list[tuple[str, float | None]]) -> dict[str, int | None]:
    """Competition rank, 1 = highest. Missing values are left unranked."""
    valid = [(ticker, value) for ticker, value in pairs if value is not None]
    valid.sort(key=lambda item: (-item[1], item[0]))
    ranks: dict[str, int | None] = {ticker: None for ticker, _ in pairs}
    index = 0
    while index < len(valid):
        end = index + 1
        while end < len(valid) and valid[end][1] == valid[index][1]:
            end += 1
        rank = index + 1
        for cursor in range(index, end):
            ranks[valid[cursor][0]] = rank
        index = end
    return ranks


def _round_price(value: float) -> float:
    return round(float(value), 4)


def build_payload(
    prices: dict[str, dict[date, float]],
    source: str,
    updated_at: datetime | None = None,
) -> dict:
    missing = [ticker for ticker, _ in TICKERS if ticker not in prices or not prices[ticker]]
    if missing:
        raise SystemExit(f"missing prices for: {', '.join(missing)}")

    normalized: dict[str, dict[date, float]] = {}
    for ticker, _name in TICKERS:
        series = {
            day: _round_price(px)
            for day, px in prices[ticker].items()
            if px is not None and px > 0
        }
        if not series:
            raise SystemExit(f"{ticker} has no positive adjusted closes")
        normalized[ticker] = series

    as_of = min(max(series) for series in normalized.values())
    # Drop anything after the common as-of date so every window shares one T.
    for ticker in normalized:
        normalized[ticker] = {
            day: px for day, px in normalized[ticker].items() if day <= as_of
        }
    missing_as_of = [ticker for ticker, series in normalized.items() if as_of not in series]
    if missing_as_of:
        raise SystemExit(
            f"no adjusted close on {as_of.isoformat()} for {', '.join(missing_as_of)}"
        )

    ordered = {ticker: sorted(series) for ticker, series in normalized.items()}
    rows = []
    window_values: dict[int, list[tuple[str, float | None]]] = {n: [] for n in WINDOWS}

    for ticker, name in TICKERS:
        series = normalized[ticker]
        days = ordered[ticker]
        end_px = series[as_of]
        returns: dict[str, float | None] = {}
        starts: dict[str, str | None] = {}
        start_px: dict[str, float | None] = {}
        for months in WINDOWS:
            anchor = subtract_months(as_of, months)
            start = last_on_or_before(days, anchor)
            key = str(months)
            if start is None:
                returns[key] = None
                starts[key] = None
                start_px[key] = None
                window_values[months].append((ticker, None))
                continue
            start_price = series[start]
            ret = round(end_px / start_price - 1.0, 8)
            returns[key] = ret
            starts[key] = start.isoformat()
            start_px[key] = start_price
            window_values[months].append((ticker, ret))
        rows.append(
            {
                "ticker": ticker,
                "name": name,
                "adjClose": end_px,
                "returns": returns,
                "startDate": starts,
                "startAdjClose": start_px,
            }
        )

    rank_by_window = {months: min_ranks(window_values[months]) for months in WINDOWS}
    for row in rows:
        row["ranks"] = {str(months): rank_by_window[months][row["ticker"]] for months in WINDOWS}

    def sort_key(row: dict) -> tuple:
        value = row["returns"].get("9")
        # None sorts last under a descending 9-month order.
        return (value is None, -(value or 0.0), row["ticker"])

    rows.sort(key=sort_key)
    stamp = updated_at or datetime.now(SHANGHAI).replace(microsecond=0)
    return {
        "asOf": as_of.isoformat(),
        "updatedAt": stamp.isoformat(),
        "timezone": "Asia/Shanghai",
        "source": source,
        "priceField": "adjclose",
        "windows": list(WINDOWS),
        "rows": rows,
    }


def _self_test() -> None:
    assert subtract_months(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert subtract_months(date(2024, 3, 31), 1) == date(2024, 2, 29)
    assert subtract_months(date(2026, 1, 31), 1) == date(2025, 12, 31)
    assert subtract_months(date(2026, 5, 31), 3) == date(2026, 2, 28)
    assert subtract_months(date(2026, 11, 13), 1) == date(2026, 10, 13)
    assert subtract_months(date(2026, 11, 13), 9) == date(2026, 2, 13)
    assert subtract_months(date(2026, 11, 13), 12) == date(2025, 11, 13)
    assert last_on_or_before(
        [date(2026, 2, 26), date(2026, 2, 27), date(2026, 3, 2)],
        date(2026, 2, 28),
    ) == date(2026, 2, 27)
    assert last_on_or_before([date(2026, 3, 2)], date(2026, 2, 28)) is None
    ranks = min_ranks([("A", 0.2), ("B", 0.2), ("C", 0.1), ("D", None)])
    assert ranks == {"A": 1, "B": 1, "C": 3, "D": None}

    synthetic: dict[str, dict[date, float]] = {}
    sessions = [date(2026, 2, 27), date(2026, 3, 31), date(2026, 5, 29), date(2026, 8, 31)]
    for index, (ticker, _name) in enumerate(TICKERS):
        level = 100.0 + index
        synthetic[ticker] = {}
        for step, day in enumerate(sessions):
            # Distinct terminal levels so the 1-month ranking is unambiguous.
            growth = 1.0 + (index + 1) * 0.01 * (step + 1)
            synthetic[ticker][day] = round(level * growth, 4)
    payload = build_payload(synthetic, source="test", updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=SHANGHAI))
    assert payload["asOf"] == "2026-08-31"
    by_ticker = {row["ticker"]: row for row in payload["rows"]}
    # 1-month anchor is 2026-07-31; last session on or before that is 2026-05-29.
    assert by_ticker["XLB"]["startDate"]["1"] == "2026-05-29"
    start_px = by_ticker["XLB"]["startAdjClose"]["1"]
    end_px = by_ticker["XLB"]["adjClose"]
    assert by_ticker["XLB"]["returns"]["1"] == round(end_px / start_px - 1.0, 8)
    assert by_ticker["XLY"]["ranks"]["1"] == 1
    assert by_ticker["XLB"]["ranks"]["1"] == 11
    # A name with no history on or before the anchor is left out of the rank set.
    short = {ticker: dict(series) for ticker, series in synthetic.items()}
    short["XLC"] = {date(2026, 8, 31): 50.0}
    partial = build_payload(short, source="test", updated_at=datetime(2026, 9, 1, 8, 0, tzinfo=SHANGHAI))
    partial_rows = {row["ticker"]: row for row in partial["rows"]}
    assert partial_rows["XLC"]["returns"]["1"] is None
    assert partial_rows["XLC"]["ranks"]["1"] is None
    ranked = [row["ranks"]["1"] for row in partial["rows"] if row["ticker"] != "XLC"]
    assert None not in ranked
    assert max(ranked) == 10


class YahooError(RuntimeError):
    pass


def _decode_body(raw: bytes) -> bytes:
    if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B:
        return gzip.decompress(raw)
    return raw


def _looks_like_html(raw: bytes) -> bool:
    sample = raw.lstrip()[:400].lower()
    return sample.startswith(b"<") or sample.startswith(b"<!doctype") or b"challenge" in sample or b"<html" in sample


class YahooClient:
    def __init__(self) -> None:
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.crumb: str | None = None

    def _request(self, url: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with self.opener.open(request, timeout=TIMEOUT) as response:
            return _decode_body(response.read())

    def _prime_crumb(self) -> None:
        if self.crumb:
            return
        try:
            self._request("https://fc.yahoo.com")
        except urllib.error.HTTPError:
            # fc.yahoo.com commonly answers 404 after setting the cookie.
            pass
        raw = self._request("https://query1.finance.yahoo.com/v1/test/getcrumb")
        if _looks_like_html(raw):
            raise YahooError("Yahoo crumb endpoint returned HTML")
        crumb = raw.decode("utf-8", "replace").strip().strip('"')
        if not crumb or any(ch.isspace() for ch in crumb):
            raise YahooError("Yahoo crumb was empty")
        self.crumb = crumb

    def chart(self, ticker: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(ATTEMPTS):
            host = YAHOO_HOSTS[attempt % len(YAHOO_HOSTS)]
            query = {
                "interval": "1d",
                "range": "2y",
                "events": "div,split",
                "includeAdjustedClose": "true",
            }
            if self.crumb:
                query["crumb"] = self.crumb
            url = f"https://{host}/v8/finance/chart/{urllib.parse.quote(ticker)}?{urllib.parse.urlencode(query)}"
            try:
                raw = self._request(url)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in (401, 403) and self.crumb is None:
                    try:
                        self._prime_crumb()
                    except Exception as crumb_error:  # noqa: BLE001 - surface after retries
                        last_error = crumb_error
                time.sleep(min(8, 2**attempt))
                continue
            except Exception as exc:  # noqa: BLE001 - network failures are retried
                last_error = exc
                time.sleep(min(8, 2**attempt))
                continue
            if _looks_like_html(raw):
                last_error = YahooError(f"{ticker} returned an HTML challenge from {host}")
                time.sleep(min(8, 2**attempt))
                continue
            return raw
        raise YahooError(f"{ticker} chart request failed: {last_error}")


def parse_yahoo_chart(raw: bytes, ticker: str) -> dict[date, float]:
    if _looks_like_html(raw):
        raise YahooError(f"{ticker} response was HTML, not JSON")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise YahooError(f"{ticker} response was not JSON") from exc
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise YahooError(f"{ticker} chart error: {chart['error']}")
    result = (chart.get("result") or [None])[0]
    if not result:
        raise YahooError(f"{ticker} chart result was empty")
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    adj_blocks = indicators.get("adjclose") or []
    adj = None
    if adj_blocks and isinstance(adj_blocks[0], dict):
        adj = adj_blocks[0].get("adjclose")
    if not adj:
        raise YahooError(f"{ticker} response did not include adjclose")
    series: dict[date, float] = {}
    for stamp, price in zip(timestamps, adj):
        if stamp is None or price is None:
            continue
        session = datetime.fromtimestamp(int(stamp), NY).date()
        px = float(price)
        if px <= 0:
            continue
        series[session] = px
    if len(series) < 250:
        raise YahooError(f"{ticker} returned only {len(series)} adjusted closes")
    return series


def drop_incomplete_session(prices: dict[str, dict[date, float]]) -> None:
    """Ignore today's bar when the US cash session has not closed yet."""
    now = datetime.now(NY)
    if now.weekday() >= 5:
        return
    close_at = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now >= close_at:
        return
    today = now.date()
    for series in prices.values():
        series.pop(today, None)


def fetch_yahoo() -> dict[str, dict[date, float]]:
    client = YahooClient()
    prices: dict[str, dict[date, float]] = {}
    errors: list[str] = []
    for ticker, _name in TICKERS:
        try:
            prices[ticker] = parse_yahoo_chart(client.chart(ticker), ticker)
        except Exception as exc:  # noqa: BLE001 - collected and reported together
            errors.append(f"{ticker}: {exc}")
        else:
            time.sleep(0.15)
    if errors:
        detail = "; ".join(errors)
        raise YahooError(detail)
    drop_incomplete_session(prices)
    return prices


def load_seed(path: Path) -> dict[str, dict[date, float]]:
    prices: dict[str, dict[date, float]] = {ticker: {} for ticker, _ in TICKERS}
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing_cols = [ticker for ticker, _ in TICKERS if ticker not in fieldnames]
        if "date" not in fieldnames or missing_cols:
            raise SystemExit(f"seed {path} is missing columns: {missing_cols or 'date'}")
        for record in reader:
            try:
                day = date.fromisoformat(record["date"])
            except ValueError:
                continue
            for ticker, _name in TICKERS:
                raw = (record.get(ticker) or "").strip()
                if not raw:
                    continue
                px = float(raw)
                if px > 0:
                    prices[ticker][day] = px
    empty = [ticker for ticker, series in prices.items() if not series]
    if empty:
        raise SystemExit(f"seed has no prices for: {', '.join(empty)}")
    return prices


def validate(payload: dict) -> None:
    rows = payload["rows"]
    if len(rows) != len(TICKERS):
        raise SystemExit(f"expected {len(TICKERS)} rows, got {len(rows)}")
    seen = [row["ticker"] for row in rows]
    if sorted(seen) != sorted(NAMES):
        raise SystemExit(f"ticker set mismatch: {seen}")
    date.fromisoformat(payload["asOf"])
    for months in WINDOWS:
        key = str(months)
        values = [(row["ticker"], row["returns"][key]) for row in rows]
        expected = min_ranks(values)
        for row in rows:
            if row["ranks"][key] != expected[row["ticker"]]:
                raise SystemExit(f"rank mismatch for {row['ticker']} window {key}")
            ret = row["returns"][key]
            if ret is None:
                if row["startDate"][key] is not None:
                    raise SystemExit(f"{row['ticker']} {key}m is n/a but has a start date")
                continue
            start_px = row["startAdjClose"][key]
            end_px = row["adjClose"]
            if start_px is None or start_px <= 0:
                raise SystemExit(f"{row['ticker']} {key}m missing start price")
            rebuilt = round(end_px / start_px - 1.0, 8)
            if abs(rebuilt - ret) > 1e-8:
                raise SystemExit(f"{row['ticker']} {key}m return does not match prices")
            if abs(ret) > 3:
                print(f"warning: {row['ticker']} {key}m return is {ret:.2%}", file=sys.stderr)


def write_payload(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def print_summary(payload: dict) -> None:
    print(f"asOf={payload['asOf']} source={payload['source']} updatedAt={payload['updatedAt']}")
    header = f"{'ticker':<6} {'name':<8} {'px':>10} " + " ".join(f"{m:>8}m {'r':>3}" for m in WINDOWS)
    print(header)
    for row in payload["rows"]:
        cells = []
        for months in WINDOWS:
            key = str(months)
            ret = row["returns"][key]
            rank = row["ranks"][key]
            ret_txt = "n/a" if ret is None else f"{ret * 100:+.2f}%"
            rank_txt = "-" if rank is None else str(rank)
            cells.append(f"{ret_txt:>9} {rank_txt:>2}")
        print(f"{row['ticker']:<6} {row['name']:<8} {row['adjClose']:>10.2f} " + " ".join(cells))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update sector ETF trailing total returns.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output JSON path")
    parser.add_argument(
        "--seed",
        type=Path,
        default=None,
        help="CSV of adjusted closes used only if Yahoo Finance cannot be fetched",
    )
    parser.add_argument(
        "--only-seed",
        action="store_true",
        help="Skip the live fetch and build from --seed (local fallback only)",
    )
    return parser.parse_args()


def main() -> int:
    _self_test()
    args = parse_args()
    if args.only_seed:
        if args.seed is None:
            raise SystemExit("--only-seed requires --seed")
        prices = load_seed(args.seed)
        source = "seed"
    else:
        try:
            prices = fetch_yahoo()
            source = "yahoo"
        except Exception as exc:
            if args.seed is None:
                print(f"Yahoo Finance fetch failed: {exc}", file=sys.stderr)
                return 1
            print(f"Yahoo Finance fetch failed ({exc}); using seed {args.seed}", file=sys.stderr)
            prices = load_seed(args.seed)
            source = "seed"
    payload = build_payload(prices, source=source)
    validate(payload)
    write_payload(payload, args.out)
    print_summary(payload)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
