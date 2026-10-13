"""SEC ticker/exchange reference-data adapter.

Sources:
- https://www.sec.gov/files/company_tickers_exchange.json  (fields: cik, name, ticker, exchange)
- https://www.sec.gov/files/company_tickers.json           (cik_str, ticker, title)

Network access goes through the injected ``fetch`` callable, which must be the governed SEC
client in live mode.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime

from filinglens.application.issuer_resolver import ReferenceDataset, TickerRecord
from filinglens.application.ports import SecurityIdentifierRepository
from filinglens.domain.models import SecurityIdentifier
from filinglens.domain.time import utcnow

TICKERS_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ADAPTER_VERSION = "sec-reference/1"


def parse_reference_json(raw: bytes) -> tuple[TickerRecord, ...]:
    data = json.loads(raw)
    records: list[TickerRecord] = []
    if isinstance(data, dict) and "fields" in data and "data" in data:
        idx = {name: i for i, name in enumerate(data["fields"])}
        for row in data["data"]:
            if row[idx["ticker"]] is None:
                continue
            records.append(
                TickerRecord(
                    cik=int(row[idx["cik"]]),
                    name=str(row[idx["name"]]),
                    ticker=str(row[idx["ticker"]]).upper(),
                    exchange=row[idx["exchange"]] if "exchange" in idx else None,
                )
            )
    elif isinstance(data, dict):
        for item in data.values():
            records.append(
                TickerRecord(
                    cik=int(item["cik_str"]),
                    name=str(item["title"]),
                    ticker=str(item["ticker"]).upper(),
                )
            )
    else:
        raise ValueError("unrecognized SEC ticker reference format")
    return tuple(records)


def dataset_from_bytes(
    raw: bytes, *, source: str, retrieved_at: datetime | None = None
) -> ReferenceDataset:
    return ReferenceDataset(
        records=parse_reference_json(raw),
        source=source,
        source_version="sha256:" + hashlib.sha256(raw).hexdigest(),
        retrieved_at=retrieved_at or utcnow(),
    )


class SecTickerReferenceAdapter:
    def __init__(self, fetch: Callable[[str], bytes], url: str = TICKERS_EXCHANGE_URL) -> None:
        self._fetch = fetch
        self._url = url

    def load(self) -> ReferenceDataset:
        raw = self._fetch(self._url)
        return dataset_from_bytes(raw, source=self._url)


def persist_identifiers(dataset: ReferenceDataset, repo: SecurityIdentifierRepository) -> int:
    """Record each ticker observation with its dataset version and retrieval time."""
    for r in dataset.records:
        repo.add(
            SecurityIdentifier(
                cik=r.cik,
                ticker=r.ticker,
                exchange=r.exchange,
                source=dataset.source,
                source_version=dataset.source_version,
                retrieved_at=dataset.retrieved_at,
            )
        )
    return len(dataset.records)
