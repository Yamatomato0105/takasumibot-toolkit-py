#!/usr/bin/env python3
"""Sample the live TakasumiBOT API and report drift from the OpenAPI document.

This tool checks API response drift, including nullable fields and optional
fields that may be omitted. It is **not** run in CI because it needs
network access to the real API; run it by hand when the API changes or when a new
parse error shows up in production:

    .venv/bin/python scripts/live_sweep.py [--sample 20]

Findings are the input for `scripts/generate_models.py`'s `NULLABLE_OVERRIDES` /
`OPTIONAL_OVERRIDES` tables — never hand-edit `models/generated/models.py`.
The offline regression suite lives in `tests/test_found_issues.py`; the network test
suite (marked `live`, deselected by default) in `tests/test_live.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import BaseModel

from takasumibot_toolkit import (
    KitClient,
    TakasumiBotToolkitError,
    TakasumiBotToolkitHttpError,
    TakasumiBotToolkitNetworkError,
    TakasumiBotToolkitResponseParseError,
    TakasumiBotToolkitRetryLimitError,
    TakasumiBotToolkitTimeoutError,
    create_kit_client,
)

DEFAULT_BASE_URL = "https://api.takasumibot.com"
DEFAULT_SAMPLE = 20
BREATH_S = 0.5


class Report:
    """Collects per-endpoint outcomes so drift shows up in one summary."""

    def __init__(self) -> None:
        self.statuses: Counter[str] = Counter()
        self.parse_failures: list[str] = []
        self.unknown_fields: list[str] = []
        self.unexpected: list[str] = []
        self.network: Counter[str] = Counter()
        self.nulls: Counter[str] = Counter()
        self.unset: Counter[str] = Counter()

    @property
    def successes(self) -> int:
        """Number of calls that produced a parsed model."""
        return sum(count for key, count in self.statuses.items() if ": ok" in key)

    def note(self, label: str, model: BaseModel, extra: str = "") -> None:
        """Record one parsed model: unknown fields, `None` fields, unset fields."""
        if model.model_extra:
            self.unknown_fields.append(f"{label}: {sorted(model.model_extra)}")
        for name in type(model).model_fields:
            if getattr(model, name) is None:
                self.nulls[f"{type(model).__name__}.{name}"] += 1
            elif name not in model.model_fields_set:
                self.unset[f"{type(model).__name__}.{name}"] += 1
        suffix = f" ({extra})" if extra else ""
        self.statuses[f"{label}: ok{suffix}"] += 1

    def note_many(self, label: str, models: Sequence[BaseModel], extra: str = "") -> None:
        for model in models:
            self.note(label, model, extra)

    def failure(self, label: str, error: Exception) -> None:
        """Classify one failed call.

        Transport problems are counted separately from drift: an unreachable API says
        nothing about the schema, so it must not be reported as a finding.
        """
        if isinstance(error, TakasumiBotToolkitResponseParseError):
            self.parse_failures.append(f"{label}: {error.code} {str(error.detail)[:200]!r}")
        elif isinstance(error, TakasumiBotToolkitHttpError):
            self.statuses[f"{label}: http {error.status}"] += 1
        elif isinstance(
            error,
            (
                TakasumiBotToolkitRetryLimitError,
                TakasumiBotToolkitNetworkError,
                TakasumiBotToolkitTimeoutError,
            ),
        ):
            self.network[f"{label}: {type(error).__name__}"] += 1
        else:
            self.unexpected.append(f"{label}: {type(error).__name__}: {error}")


async def _attempt(
    report: Report,
    label: str,
    call: Callable[[], Awaitable[Any]],
    *,
    tolerated: Sequence[int] = (),
) -> Any:
    """Run one endpoint, classifying the outcome into `report`. Returns `None` on error."""
    try:
        result = await call()
    except TakasumiBotToolkitHttpError as error:
        if error.status in tolerated:
            report.statuses[f"{label}: http {error.status} (expected for some ids)"] += 1
        else:
            report.failure(label, error)
        return None
    except TakasumiBotToolkitError as error:
        report.failure(label, error)
        return None
    if isinstance(result, BaseModel):
        report.note(label, result)
    else:
        report.note_many(label, result, f"{len(result)} entries")
    return result


async def _sweep(kit: KitClient, sample: int, report: Report) -> None:
    ranking = await _attempt(report, "ranking", kit.get_ranking) or []
    companies = await _attempt(report, "company_list", kit.get_company_list) or []
    stock_entries = await _attempt(report, "stock_list", kit.get_stock_list) or []
    await _attempt(report, "status", kit.get_status)
    await _attempt(report, "tax", kit.get_tax_info)
    await _attempt(report, "shard", kit.get_shard_info)
    await _attempt(report, "statistics", kit.get_statistics_info)

    for index, user in enumerate(ranking[:sample]):
        user_id = str(user.id)
        username = str(user.username)
        # A missing profile/history record is normal for some users, hence 404 is tolerated.
        await _attempt(
            report,
            f"profile[{index}]",
            lambda uid=user_id: kit.get_profile_by_id(uid),
            tolerated=(404,),
        )
        await _attempt(
            report,
            f"history[{index}]",
            lambda uid=user_id: kit.get_history_by_id(uid),
            tolerated=(404,),
        )
        await _attempt(
            report,
            f"discord_user[{index}]",
            lambda name=username: kit.get_discord_user_by_name(name),
        )

    for index, company in enumerate(companies[:sample]):
        company_id = str(company.id)
        owner_id = str(company.owner_id)
        await _attempt(
            report, f"company[{index}]", lambda cid=company_id: kit.get_company_by_id(cid)
        )
        # H-2: the same endpoint queried by owner id may omit `employees`.
        await _attempt(
            report,
            f"company_by_owner[{index}]",
            lambda oid=owner_id: kit.get_company_by_id(oid),
        )
        await _attempt(
            report,
            f"company_history[{index}]",
            lambda cid=company_id: kit.get_company_history_by_id(cid),
        )

    if stock_entries:
        try:
            stock = kit.get_stock().id(str(stock_entries[0].id))
            info = await stock.info()
            price = await stock.price()
            history = await stock.history()
        except TakasumiBotToolkitError as error:
            report.failure("stock_derivation", error)
        else:
            report.statuses[
                f"stock_derivation: ok (id={info.id} price={price} history={len(history)})"
            ] += 1

    for code in _gift_codes():
        prefix = code[:4]
        await _attempt(
            report,
            f"gift {prefix}…",
            lambda gift=code: kit.get_gift_info(gift),
            tolerated=(404,),
        )


def _gift_codes() -> list[str]:
    raw = os.environ.get("TAKASUMIBOT_LIVE_GIFT_CODE", "")
    return [code.strip() for code in raw.split(",") if code.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample the live TakasumiBOT API for drift.")
    parser.add_argument(
        "--sample", type=int, default=DEFAULT_SAMPLE, help="users/companies to sample"
    )
    args = parser.parse_args()

    report = Report()
    base_url = os.environ.get("TAKASUMIBOT_BASE_URL", DEFAULT_BASE_URL)
    print(f"sweeping {base_url} (sample={args.sample})")

    async def run() -> None:
        async with create_kit_client(timeout_ms=30_000) as kit:
            await _sweep(kit, args.sample, report)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"unreachable or fatal: {type(error).__name__}: {error}")
        return 2

    print()
    for key, count in sorted(report.statuses.items()):
        print(f"  {key}: {count}")
    for label, count in sorted(report.network.items()):
        print(f"  NETWORK {label}: {count}")
    for label, count in sorted(report.nulls.items()):
        print(f"  null    {label}: {count}")
    for label, count in sorted(report.unset.items()):
        print(f"  unset   {label}: {count}")
    for item in sorted(set(report.unknown_fields)):
        print(f"  UNKNOWN FIELD  {item}")
    for item in report.parse_failures:
        print(f"  PARSE FAILURE  {item}")
    for item in report.unexpected:
        print(f"  UNEXPECTED     {item}")

    if report.successes == 0:
        transport = sum(report.network.values())
        print(f"\nunreachable: no endpoint answered ({transport} transport failures)")
        return 2

    problems = len(report.parse_failures) + len(report.unexpected) + len(set(report.unknown_fields))
    if problems:
        print(
            f"\n{problems} drift finding(s): report upstream and add an override to "
            "scripts/generate_models.py"
        )
        return 1
    print(f"\n{report.successes} responses parsed; no parse failures and no unknown fields")
    return 0


if __name__ == "__main__":
    sys.exit(main())
