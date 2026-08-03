from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any
from zipfile import BadZipFile, ZipFile

import pandas as pd

from market_alarm.db import Store
from market_alarm.http import checked_json, polite_pause, session


def collect(config: dict[str, Any], store: Store) -> dict[str, Any]:
    cfg = config["binance"]
    client = session(config)
    summary: dict[str, Any] = {}
    for symbol, spec in cfg["symbols"].items():
        existing = store.latest("binance", "spot_close", symbol)
        if existing:
            start_at = pd.Timestamp(existing["ts"]).to_pydatetime() - timedelta(days=2)
        else:
            start_at = datetime(2017, 1, 1, tzinfo=timezone.utc)
        spot = _spot_klines(client, config, cfg["spot_base_url"], symbol, start_at)

        # A Binance API key cannot bypass HTTP 451 because the block is based
        # on the GitHub runner's egress location.  Prefer the live public REST
        # endpoint, then use Binance's official downloadable archive.  The
        # archive is genuinely independent of fapi.binance.com and is normally
        # available through T-1.
        futures_source = cfg["futures_base_url"]
        rest_error: Exception | None = None
        rest_frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame] | None = None
        try:
            rest_frames = (
                _oi_history(client, config, cfg["futures_base_url"], symbol),
                _funding(client, config, cfg["futures_base_url"], symbol),
                _long_short(client, config, cfg["futures_base_url"], symbol),
                _premium(client, config, cfg["futures_base_url"], symbol),
            )
        except Exception as exc:
            rest_error = exc

        existing_oi = store.frame("binance", "open_interest_value", symbol)["value"]
        backfill_days = int(cfg.get("archive_backfill_days", 190))
        history_short = (
            existing_oi.empty
            or (existing_oi.index.max() - existing_oi.index.min()).days < backfill_days - 7
        )
        archive_frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame] | None = None
        archive_error: Exception | None = None
        if rest_frames is None or history_short:
            try:
                archive_frames = _archive_futures(
                    client,
                    config,
                    cfg.get("archive_base_url", "https://data.binance.vision"),
                    symbol,
                    days=backfill_days if history_short else 7,
                    workers=int(cfg.get("archive_workers", 12)),
                )
            except Exception as exc:
                archive_error = exc

        if rest_frames is None and archive_frames is None:
            raise RuntimeError(
                f"Binance 선물 공개 데이터 수집 실패({symbol}). "
                f"REST={type(rest_error).__name__}: {rest_error}; "
                f"공식 아카이브={type(archive_error).__name__}: {archive_error}"
            )
        if rest_frames is None:
            assert archive_frames is not None
            oi, funding, ratio, premium = archive_frames
            futures_source = "official-archive:T-1"
        elif archive_frames is None:
            oi, funding, ratio, premium = rest_frames
        else:
            oi, funding, ratio, premium = tuple(
                _merge_frame(archive, live)
                for archive, live in zip(archive_frames, rest_frames, strict=True)
            )
            futures_source = "official-archive+live-rest"

        delivery_base = cfg.get("delivery_base_url", "https://dapi.binance.com")
        delivery_basis = _delivery_basis(
            client,
            config,
            delivery_base,
            symbol,
            float(spot["close"].iloc[-1]),
            premium,
        )

        _store(store, "binance", "spot_close", symbol, spot["close"])
        _store(store, "binance", "open_interest_value", symbol, oi["oi_value"])
        _store(store, "binance", "funding_rate", symbol, funding["funding_rate"])
        _store(store, "binance", "long_short_ratio", symbol, ratio["long_short_ratio"])
        _store(store, "binance", "mark_price", symbol, premium["mark_price"])
        _store(store, "binance", "index_price", symbol, premium["index_price"])
        # Keep the archive's perpetual-premium history as a continuity proxy,
        # then append the current quarterly value when COIN-M is reachable.
        basis_percent = _merge_series(premium["basis_percent"], delivery_basis["basis_percent"])
        basis_annualized = _merge_series(
            premium["basis_annualized"], delivery_basis["basis_annualized"]
        )
        _store(store, "binance", "basis_percent", symbol, basis_percent)
        _store(store, "binance", "basis_annualized", symbol, basis_annualized)
        summary[symbol] = {
            "name": spec["name"],
            "price": float(spot["close"].iloc[-1]),
            "oi_value": float(oi["oi_value"].iloc[-1]),
            "funding": float(funding["funding_rate"].iloc[-1]),
            "long_short_ratio": float(ratio["long_short_ratio"].iloc[-1]),
            "mark_price": float(premium["mark_price"].iloc[-1]),
            "index_price": float(premium["index_price"].iloc[-1]),
            "basis_percent": float(basis_percent.iloc[-1]),
            "basis_annualized": float(basis_annualized.iloc[-1]),
            "basis_contract": str(delivery_basis["contract"].iloc[-1]),
            "futures_source": futures_source,
            "reference_time": max(oi.index[-1], funding.index[-1]).isoformat(),
        }
    return summary


def _merge_series(history: pd.Series, current: pd.Series) -> pd.Series:
    result = pd.concat([history, current]).sort_index()
    return result[~result.index.duplicated(keep="last")].dropna()


def _merge_frame(history: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    result = pd.concat([history, current]).sort_index()
    return result[~result.index.duplicated(keep="last")].dropna(how="all")


def _spot_klines(
    client, config, base: str, symbol: str, start_at: datetime
) -> pd.DataFrame:
    # 최초 실행에는 전 사이클 고점을 찾을 수 있도록 2017년 이후 일봉을 백필한다.
    start = int(start_at.timestamp() * 1000)
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows: list[list[Any]] = []
    while start < end:
        response = client.get(
            f"{base.rstrip('/')}/api/v3/klines",
            params={"symbol": symbol, "interval": "1d", "startTime": start, "limit": 1000},
            timeout=config["http"]["timeout_seconds"],
        )
        payload = checked_json(response)
        if not isinstance(payload, list) or not payload:
            break
        rows.extend(payload)
        next_start = int(payload[-1][0]) + 86_400_000
        if next_start <= start or len(payload) < 1000:
            break
        start = next_start
        polite_pause()
    if not rows:
        raise RuntimeError(f"Binance 현물 일봉이 비어 있습니다: {symbol}")
    frame = pd.DataFrame(rows)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(frame[0], unit="ms", utc=True),
            "close": pd.to_numeric(frame[4], errors="coerce"),
        }
    ).dropna().drop_duplicates("date").set_index("date").sort_index()


def _oi_history(client, config, base: str, symbol: str) -> pd.DataFrame:
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp() * 1000)
    response = client.get(
        f"{base.rstrip('/')}/futures/data/openInterestHist",
        params={
            "symbol": symbol,
            "period": "4h",
            "startTime": start,
            "endTime": end,
            "limit": 500,
        },
        timeout=config["http"]["timeout_seconds"],
    )
    payload = checked_json(response)
    frame = pd.DataFrame(payload)
    if frame.empty:
        raise RuntimeError(f"Binance OI 이력이 비어 있습니다: {symbol}")
    return pd.DataFrame(
        {
            "date": pd.to_datetime(frame["timestamp"], unit="ms", utc=True),
            "oi_value": pd.to_numeric(frame["sumOpenInterestValue"], errors="coerce"),
        }
    ).dropna().set_index("date").sort_index()


def _funding(client, config, base: str, symbol: str) -> pd.DataFrame:
    start = int((datetime.now(timezone.utc) - timedelta(days=90)).timestamp() * 1000)
    response = client.get(
        f"{base.rstrip('/')}/fapi/v1/fundingRate",
        params={"symbol": symbol, "startTime": start, "limit": 1000},
        timeout=config["http"]["timeout_seconds"],
    )
    payload = checked_json(response)
    frame = pd.DataFrame(payload)
    if frame.empty:
        raise RuntimeError(f"Binance 펀딩비 이력이 비어 있습니다: {symbol}")
    return pd.DataFrame(
        {
            "date": pd.to_datetime(frame["fundingTime"], unit="ms", utc=True),
            "funding_rate": pd.to_numeric(frame["fundingRate"], errors="coerce"),
        }
    ).dropna().set_index("date").sort_index()


def _long_short(client, config, base: str, symbol: str) -> pd.DataFrame:
    response = client.get(
        f"{base.rstrip('/')}/futures/data/globalLongShortAccountRatio",
        params={"symbol": symbol, "period": "4h", "limit": 180},
        timeout=config["http"]["timeout_seconds"],
    )
    payload = checked_json(response)
    frame = pd.DataFrame(payload)
    if frame.empty:
        raise RuntimeError(f"Binance 롱·숏 비율이 비어 있습니다: {symbol}")
    return pd.DataFrame(
        {
            "date": pd.to_datetime(frame["timestamp"], unit="ms", utc=True),
            "long_short_ratio": pd.to_numeric(frame["longShortRatio"], errors="coerce"),
        }
    ).dropna().set_index("date").sort_index()


def _premium(client, config, base: str, symbol: str) -> pd.DataFrame:
    response = client.get(
        f"{base.rstrip('/')}/fapi/v1/premiumIndex",
        params={"symbol": symbol},
        timeout=config["http"]["timeout_seconds"],
    )
    payload = checked_json(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Binance premiumIndex 응답 오류: {symbol}")
    mark = float(payload["markPrice"])
    index = float(payload["indexPrice"])
    now = pd.to_datetime(int(payload["time"]), unit="ms", utc=True)
    basis_percent = (mark / index - 1) * 100
    # 무기한 선물은 만기가 없으므로 8시간 펀딩 구간을 고정해 비교용으로 연환산한다.
    annualized = basis_percent * 3 * 365
    return pd.DataFrame(
        {
            "mark_price": [mark],
            "index_price": [index],
            "basis_percent": [basis_percent],
            "basis_annualized": [annualized],
        },
        index=[now],
    )


def _archive_futures(
    client,
    config: dict[str, Any],
    base: str,
    symbol: str,
    days: int = 190,
    workers: int = 12,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read keyless USD-M data from Binance's official ZIP archive.

    `fapi.binance.com` can return 451 on a GitHub-hosted runner even though the
    endpoints are public.  `data.binance.vision` is Binance's separate public
    data archive.  Metrics are daily ZIPs, while funding is published as
    monthly ZIPs.  Completed months use monthly kline files and the current
    month uses daily files, so a successful fallback is generally current
    through the previous UTC day.
    """

    timeout = int(config["http"]["timeout_seconds"])
    end_date = datetime.now(timezone.utc).date() - timedelta(days=1)
    start_date = end_date - timedelta(days=max(days, 1) - 1)
    base = base.rstrip("/")

    metric_urls = [
        (
            f"{base}/data/futures/um/daily/metrics/{symbol}/"
            f"{symbol}-metrics-{day.isoformat()}.zip"
        )
        for day in _dates(start_date, end_date)
    ]
    metric_parts = _download_archive_many(client, metric_urls, timeout, workers)
    metrics = _concat_archive(metric_parts)
    if metrics.empty:
        raise RuntimeError(f"Binance 공식 아카이브 metrics가 비어 있습니다: {symbol}")
    metrics.index = pd.to_datetime(metrics["create_time"], utc=True, errors="coerce")
    metrics = metrics.loc[metrics.index.notna()]
    oi_ratio = pd.DataFrame(
        {
            "oi_value": pd.to_numeric(
                metrics["sum_open_interest_value"], errors="coerce"
            ).to_numpy(),
            "long_short_ratio": pd.to_numeric(
                metrics["count_long_short_ratio"], errors="coerce"
            ).to_numpy(),
        },
        index=metrics.index,
    ).dropna(how="all")
    oi_ratio = oi_ratio.resample("4h").last().dropna(how="all")
    oi = oi_ratio[["oi_value"]].dropna()
    ratio = oi_ratio[["long_short_ratio"]].dropna()

    months = list(_months(start_date, end_date))
    funding_urls = [
        (
            f"{base}/data/futures/um/monthly/fundingRate/{symbol}/"
            f"{symbol}-fundingRate-{month:%Y-%m}.zip"
        )
        for month in months
    ]
    funding_raw = _concat_archive(
        _download_archive_many(client, funding_urls, timeout, min(workers, 6))
    )
    if funding_raw.empty:
        raise RuntimeError(f"Binance 공식 아카이브 fundingRate가 비어 있습니다: {symbol}")
    funding = pd.DataFrame(
        {
            "date": pd.to_datetime(
                pd.to_numeric(funding_raw["calc_time"], errors="coerce"),
                unit="ms",
                utc=True,
            ),
            "funding_rate": pd.to_numeric(
                funding_raw["last_funding_rate"], errors="coerce"
            ),
        }
    ).dropna().drop_duplicates("date").set_index("date").sort_index()

    current_month = end_date.replace(day=1)
    completed_months = [month for month in months if month < current_month]
    current_days = list(_dates(max(start_date, current_month), end_date))
    kline_frames: dict[str, pd.DataFrame] = {}
    for kind in ("premiumIndexKlines", "markPriceKlines", "indexPriceKlines"):
        urls = [
            (
                f"{base}/data/futures/um/monthly/{kind}/{symbol}/1h/"
                f"{symbol}-1h-{month:%Y-%m}.zip"
            )
            for month in completed_months
        ]
        urls += [
            (
                f"{base}/data/futures/um/daily/{kind}/{symbol}/1h/"
                f"{symbol}-1h-{day.isoformat()}.zip"
            )
            for day in current_days
        ]
        raw = _concat_archive(
            _download_archive_many(client, urls, timeout, min(workers, 8))
        )
        if raw.empty:
            raise RuntimeError(f"Binance 공식 아카이브 {kind}가 비어 있습니다: {symbol}")
        frame = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    pd.to_numeric(raw["open_time"], errors="coerce"),
                    unit="ms",
                    utc=True,
                ),
                "close": pd.to_numeric(raw["close"], errors="coerce"),
            }
        ).dropna().drop_duplicates("date").set_index("date").sort_index()
        kline_frames[kind] = frame

    joined = pd.concat(
        [
            kline_frames["premiumIndexKlines"]["close"].rename("premium"),
            kline_frames["markPriceKlines"]["close"].rename("mark_price"),
            kline_frames["indexPriceKlines"]["close"].rename("index_price"),
        ],
        axis=1,
    ).dropna()
    joined["basis_percent"] = joined["premium"] * 100
    joined["basis_annualized"] = joined["basis_percent"] * 3 * 365
    premium = joined[
        ["mark_price", "index_price", "basis_percent", "basis_annualized"]
    ].resample("4h").last().dropna()
    # Funding archives are monthly, so during an ongoing month the latest
    # official settlement can lag.  Fill only the missing tail from Binance's
    # archived Premium Index using Binance's published 8-hour formula.  Hourly
    # archive candles make this an estimate; historical official settlements
    # always take precedence when they become available.
    estimated_funding = _estimated_funding_from_premium(joined["premium"])
    if not estimated_funding.empty:
        estimated_funding = estimated_funding.loc[
            estimated_funding.index > funding.index.max()
        ]
        funding = _merge_frame(funding, estimated_funding)
    return oi, funding, ratio, premium


def _estimated_funding_from_premium(premium: pd.Series) -> pd.DataFrame:
    hourly = premium.dropna().sort_index().resample("1h").last().dropna()
    rows: list[tuple[pd.Timestamp, float]] = []
    for start, values in hourly.resample("8h", origin="epoch"):
        values = values.dropna()
        if len(values) != 8:
            continue
        weights = list(range(1, 9))
        average = sum(float(value) * weight for value, weight in zip(values, weights)) / sum(weights)
        # BTCUSDT and SOLUSDT use the standard 8-hour interval and 0.01%
        # interest component.  Clamp limits are +/-0.05%.
        funding_rate = average + max(-0.0005, min(0.0005, 0.0001 - average))
        rows.append((pd.Timestamp(start) + pd.offsets.Hour(8), funding_rate))
    if not rows:
        return pd.DataFrame(columns=["funding_rate"])
    return pd.DataFrame(
        {"funding_rate": [value for _, value in rows]},
        index=pd.DatetimeIndex([stamp for stamp, _ in rows]),
    )


def _download_archive_many(
    client, urls: list[str], timeout: int, workers: int
) -> list[pd.DataFrame]:
    if not urls:
        return []
    results: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        future_map = {
            pool.submit(_read_archive_csv, client, url, timeout): url for url in urls
        }
        for future in as_completed(future_map):
            frame = future.result()
            if frame is not None and not frame.empty:
                results.append(frame)
    return results


def _read_archive_csv(client, url: str, timeout: int) -> pd.DataFrame | None:
    response = client.get(url, timeout=timeout)
    if getattr(response, "status_code", None) == 404:
        return None
    response.raise_for_status()
    try:
        with ZipFile(BytesIO(response.content)) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                raise RuntimeError(f"ZIP 안에 CSV가 없습니다: {url}")
            with archive.open(csv_names[0]) as stream:
                return pd.read_csv(stream)
    except BadZipFile as exc:
        raise RuntimeError(f"Binance 아카이브 ZIP 손상: {url}") from exc


def _concat_archive(parts: list[pd.DataFrame]) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _dates(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _months(start, end):
    month = start.replace(day=1)
    last = end.replace(day=1)
    while month <= last:
        yield month
        if month.month == 12:
            month = month.replace(year=month.year + 1, month=1)
        else:
            month = month.replace(month=month.month + 1)


def _delivery_basis(
    client,
    config: dict[str, Any],
    base: str,
    symbol: str,
    spot_price: float,
    fallback: pd.DataFrame,
) -> pd.DataFrame:
    """Return annualized delivery-futures basis, falling back for unsupported assets.

    Binance USD-M normally lists quarterly contracts for BTC, while SOL may not
    have one.  A missing delivery contract therefore uses the perpetual
    mark/index premium already collected instead of failing the whole job.
    """
    try:
        response = client.get(
            f"{base.rstrip('/')}/dapi/v1/exchangeInfo",
            timeout=config["http"]["timeout_seconds"],
        )
        payload = checked_json(response)
    except Exception:
        return _perpetual_basis_fallback(fallback)
    now = datetime.now(timezone.utc)
    candidates: list[tuple[datetime, str]] = []
    delivery_pair = symbol.removesuffix("USDT") + "USD"
    for item in payload.get("symbols", []) if isinstance(payload, dict) else []:
        contract = str(item.get("symbol", ""))
        if (
            contract.startswith(f"{delivery_pair}_")
            # The www gateway omits ``status`` although the dedicated dapi
            # host includes it.  A listed, unexpired quarterly contract is
            # tradable unless an explicit non-TRADING status is returned.
            and item.get("status") in (None, "TRADING")
            and item.get("contractType") in {"CURRENT_QUARTER", "NEXT_QUARTER"}
        ):
            expiry = datetime.fromtimestamp(
                int(item.get("deliveryDate", 0)) / 1000, tz=timezone.utc
            )
            if expiry > now:
                candidates.append((expiry, contract))
    if not candidates or spot_price <= 0:
        return _perpetual_basis_fallback(fallback)

    expiry, contract = min(candidates)
    try:
        ticker = checked_json(
            client.get(
                f"{base.rstrip('/')}/dapi/v1/ticker/price",
                params={"symbol": contract},
                timeout=config["http"]["timeout_seconds"],
            )
        )
    except Exception:
        return _perpetual_basis_fallback(fallback)
    # The www.binance.com gateway returns a one-item list for this endpoint,
    # while dapi.binance.com normally returns an object.
    if isinstance(ticker, list):
        if not ticker:
            return _perpetual_basis_fallback(fallback)
        ticker = ticker[0]
    futures_price = float(ticker["price"])
    basis_percent = (futures_price / spot_price - 1) * 100
    days = max((expiry - now).total_seconds() / 86_400, 1.0)
    annualized = basis_percent * 365 / days
    return pd.DataFrame(
        {
            "basis_percent": [basis_percent],
            "basis_annualized": [annualized],
            "contract": [contract],
        },
        index=[pd.Timestamp(now)],
    )


def _perpetual_basis_fallback(fallback: pd.DataFrame) -> pd.DataFrame:
    result = fallback[["basis_percent", "basis_annualized"]].copy()
    result["contract"] = "PERPETUAL_PROXY"
    return result


def _store(store: Store, source: str, series: str, symbol: str, values: pd.Series) -> None:
    # ``pd.Timestamp(NaT).isoformat()`` returns the literal string "NaT", which
    # SQLite happily accepts and no later read can parse back into a timestamp.
    # One such row poisons the whole series: it becomes a NaT in the index and
    # every time-based ``rolling`` window then raises
    # "ValueError: index values must not have NaT".  Drop unusable timestamps
    # at the write boundary instead.
    rows = [
        (pd.Timestamp(idx).isoformat(), float(value), None)
        for idx, value in values.dropna().items()
        if not pd.isna(idx)
    ]
    store.upsert_points(source, series, rows, symbol=symbol)
