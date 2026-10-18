"""Generated response models. DO NOT EDIT BY HAND.

Regenerate with: python scripts/generate_models.py

Deviations from the OpenAPI document are declared in that script
(NULLABLE_OVERRIDES / OPTIONAL_OVERRIDES); each one is marked with a comment.
OpenAPI enums are LooseEnum fields (any non-empty str, like the NPM looseEnum);
their documented values are the Literal aliases / tuples below.
"""

from __future__ import annotations

from typing import Final, Literal, TypeAlias

from pydantic import Field

from takasumibot_toolkit.models._base import AwareDatetime, LooseEnum, TakasumiBotModel

GiftType: TypeAlias = Literal["gift"]
"""Documented values of ``GiftResponse.type`` (OpenAPI enum)."""
GIFT_TYPES: Final[tuple[GiftType, ...]] = ("gift",)
"""``GiftResponse.type`` values listed by the OpenAPI document."""

GiftStatus: TypeAlias = Literal["received", "unused"]
"""Documented values of ``GiftResponse.status`` (OpenAPI enum)."""
GIFT_STATUSES: Final[tuple[GiftStatus, ...]] = ("received", "unused")
"""``GiftResponse.status`` values listed by the OpenAPI document."""

StockId: TypeAlias = Literal[
    "JTTI",
    "TENOMU",
    "KAKAPO",
    "TOMOTA",
    "NITIMOTO",
    "DEEDLE",
    "RIPPLE",
    "NIMURA",
    "TAKASUMI",
    "KENTAI",
]
"""Documented values of ``StockEntry.id`` (OpenAPI enum)."""
STOCK_IDS: Final[tuple[StockId, ...]] = (
    "JTTI",
    "TENOMU",
    "KAKAPO",
    "TOMOTA",
    "NITIMOTO",
    "DEEDLE",
    "RIPPLE",
    "NIMURA",
    "TAKASUMI",
    "KENTAI",
)
"""``StockEntry.id`` values listed by the OpenAPI document."""


class GiftResponse(TakasumiBotModel):
    """Generated from OpenAPI schema ``GiftResponse``."""

    # LooseEnum: documented values are GIFT_TYPES; others are accepted.
    type: LooseEnum = Field(description="オブジェクトの種類")
    id: str = Field(description="ギフトコード（A-Za-z0-9 10文字）")
    user_id: str = Field(alias="userId", description="ギフト送信者のDiscordユーザーID")
    # LooseEnum: documented values are GIFT_STATUSES; others are accepted.
    status: LooseEnum = Field(description="ギフトの状態")
    receiver_id: str | None = Field(
        alias="receiverId",
        description="ギフト受取者のDiscordユーザーID（受け取られていない場合はnull）",
    )
    amount: int = Field(description="ギフトの金額")
    bought_at: AwareDatetime | None = Field(
        alias="boughtAt",
        description="ギフトが受け取られた時刻（受け取られていない場合はnull）",
    )
    created_at: AwareDatetime = Field(alias="createdAt", description="ギフトが作成された時刻")


class RankingEntry(TakasumiBotModel):
    """Generated from OpenAPI schema ``RankingEntry``."""

    id: str = Field(description="DiscordユーザーID")
    username: str
    avatar_url: str | None = Field(alias="avatarURL", description="DiscordアバターURL")
    assets: int
    chips: int
    job_type: str = Field(alias="jobType")


class ProfileResponse(TakasumiBotModel):
    """Generated from OpenAPI schema ``ProfileResponse``."""

    assets: int
    chips: int
    job_type: str = Field(alias="jobType")


class HistoryEntry(TakasumiBotModel):
    """Generated from OpenAPI schema ``HistoryEntry``."""

    id: int
    user_id: str = Field(alias="userId", description="DiscordユーザーID")
    amount: int
    reason: str
    traded_at: AwareDatetime = Field(alias="tradedAt")


class CompanyListEntry(TakasumiBotModel):
    """Generated from OpenAPI schema ``CompanyListEntry``."""

    id: str = Field(description="会社ID")
    name: str
    description: str
    assets: int
    salary: int
    job_type: str = Field(alias="jobType")
    owner_id: str = Field(alias="ownerId", description="DiscordユーザーID")
    updated_at: AwareDatetime = Field(alias="updatedAt")
    created_at: AwareDatetime = Field(alias="createdAt")


class CompanyEmployeeEntry(TakasumiBotModel):
    """Generated from OpenAPI schema ``CompanyEmployeeEntry``."""

    user_id: str = Field(alias="userId", description="DiscordユーザーID")
    company_id: str = Field(alias="companyId", description="会社ID")
    updated_at: AwareDatetime = Field(alias="updatedAt")
    joined_at: AwareDatetime = Field(alias="joinedAt")


class CompanyStatistics(TakasumiBotModel):
    """Generated from OpenAPI schema ``CompanyStatistics``."""

    company_id: str = Field(alias="companyId", description="会社ID")
    total_earn: int = Field(alias="totalEarn")
    total_use: int = Field(alias="totalUse")
    total_tax: int = Field(alias="totalTax")
    updated_at: AwareDatetime = Field(alias="updatedAt")


class CompanyDetailResponse(TakasumiBotModel):
    """Generated from OpenAPI schema ``CompanyDetailResponse``."""

    id: str = Field(description="会社ID")
    name: str
    description: str
    assets: int
    salary: int
    job_type: str = Field(alias="jobType")
    owner_id: str = Field(alias="ownerId", description="DiscordユーザーID")
    updated_at: AwareDatetime = Field(alias="updatedAt")
    created_at: AwareDatetime = Field(alias="createdAt")
    statistics: CompanyStatistics
    # OPTIONAL_OVERRIDES: absent from some live responses (see scripts/generate_models.py).
    employees: list[CompanyEmployeeEntry] | None = Field(default=None)


class StockEntry(TakasumiBotModel):
    """Generated from OpenAPI schema ``StockEntry``."""

    name: str
    # LooseEnum: documented values are STOCK_IDS; others are accepted.
    id: LooseEnum = Field(description="株式コード")
    description: str
    dividend_amount: int = Field(alias="dividendAmount")
    dividend_rate: float = Field(alias="dividendRate")
    prices: list[int]


class TaxRateEntry(TakasumiBotModel):
    """Generated from OpenAPI schema ``TaxRateEntry``."""

    threshold: float
    rate: float


class TaxResponse(TakasumiBotModel):
    """Generated from OpenAPI schema ``TaxResponse``."""

    benefit_tax: list[TaxRateEntry] = Field(alias="benefitTax")
    company_benefit_tax: list[TaxRateEntry] = Field(alias="companyBenefitTax")
    debt_interest_rate: float = Field(alias="debtInterestRate")
    deposit_interest_rate: float = Field(alias="depositInterestRate")
    deposit_tax_rate: float = Field(alias="depositTaxRate")
    exchange_tax_rate: float = Field(alias="exchangeTaxRate")
    gift_tax_rate: float = Field(alias="giftTaxRate")
    idle_tax: int = Field(alias="idleTax")
    income_tax_rate: float = Field(alias="incomeTaxRate")
    trade_tax_rate: float = Field(alias="tradeTaxRate")


class ShardDataEntry(TakasumiBotModel):
    """Generated from OpenAPI schema ``ShardDataEntry``."""

    shard_id: str = Field(alias="shardId")
    guild_count: str = Field(alias="guildCount")
    user_count: str = Field(alias="userCount")
    ping: str


class ShardResponse(TakasumiBotModel):
    """Generated from OpenAPI schema ``ShardResponse``."""

    data: list[ShardDataEntry]
    logged_at: AwareDatetime = Field(alias="loggedAt")


class StatusEntry(TakasumiBotModel):
    """Generated from OpenAPI schema ``StatusEntry``."""

    id: int
    ping: int
    total_user: int = Field(alias="totalUser")
    total_guild: int = Field(alias="totalGuild")
    total_command: int = Field(alias="totalCommand")
    cpu_usage: int = Field(alias="cpuUsage")
    memory_usage: int = Field(alias="memoryUsage")
    logged_at: AwareDatetime = Field(alias="loggedAt")


class DiscordUserSearchResponse(TakasumiBotModel):
    """Generated from OpenAPI schema ``DiscordUserSearchResponse``."""

    # NULLABLE_OVERRIDES: null in some live responses (see scripts/generate_models.py).
    accent_color: int | None = Field(alias="accentColor")
    avatar: str | None
    banner: str | None
    bot: bool
    flags: int
    global_name: str | None = Field(alias="globalName")
    id: str = Field(description="DiscordユーザーID")
    username: str


class CompanyHistoryEntry(TakasumiBotModel):
    """Generated from OpenAPI schema ``CompanyHistoryEntry``."""

    id: int
    company_id: str = Field(alias="companyId", description="会社ID")
    user_id: str = Field(alias="userId", description="DiscordユーザーID")
    amount: int
    reason: str
    traded_at: AwareDatetime = Field(alias="tradedAt")


class CompanyAggregate(TakasumiBotModel):
    """Generated from OpenAPI schema ``CompanyAggregate``."""

    total_earn: int = Field(alias="totalEarn")
    total_use: int = Field(alias="totalUse")
    total_tax: int = Field(alias="totalTax")


class EconomyStatistics(TakasumiBotModel):
    """Generated from OpenAPI schema ``EconomyStatistics``."""

    treasury: int
    debt: int


class EventCounterStatistics(TakasumiBotModel):
    """Generated from OpenAPI schema ``EventCounterStatistics``."""

    one_day: int = Field(alias="oneDay")
    difference: int


class EventWindowStatistics(TakasumiBotModel):
    """Generated from OpenAPI schema ``EventWindowStatistics``."""

    one_day: int = Field(alias="oneDay")
    difference: int
    one_day_only_user: int = Field(alias="oneDayOnlyUser")
    difference_only_user: int = Field(alias="differenceOnlyUser")


class UserStatistics(TakasumiBotModel):
    """Generated from OpenAPI schema ``UserStatistics``."""

    total_earn: int = Field(alias="totalEarn")
    total_use: int = Field(alias="totalUse")
    total_tax: int = Field(alias="totalTax")
    total_work: int = Field(alias="totalWork")
    total_command: int = Field(alias="totalCommand")


class EventStatistics(TakasumiBotModel):
    """Generated from OpenAPI schema ``EventStatistics``."""

    total: int
    total_only_user: int = Field(alias="totalOnlyUser")
    message_create: EventWindowStatistics = Field(alias="messageCreate")
    interaction_create: EventWindowStatistics = Field(alias="interactionCreate")
    guild_member_add: EventCounterStatistics = Field(alias="guildMemberAdd")
    guild_member_remove: EventCounterStatistics = Field(alias="guildMemberRemove")
    guild_create: EventCounterStatistics = Field(alias="guildCreate")
    guild_delete: EventCounterStatistics = Field(alias="guildDelete")


class StatisticsResponse(TakasumiBotModel):
    """Generated from OpenAPI schema ``StatisticsResponse``."""

    user: UserStatistics
    company: CompanyAggregate
    economy: EconomyStatistics
    event: EventStatistics


__all__ = [
    "GiftType",
    "GIFT_TYPES",
    "GiftStatus",
    "GIFT_STATUSES",
    "StockId",
    "STOCK_IDS",
    "GiftResponse",
    "RankingEntry",
    "ProfileResponse",
    "HistoryEntry",
    "CompanyListEntry",
    "CompanyEmployeeEntry",
    "CompanyStatistics",
    "CompanyDetailResponse",
    "StockEntry",
    "TaxRateEntry",
    "TaxResponse",
    "ShardDataEntry",
    "ShardResponse",
    "StatusEntry",
    "DiscordUserSearchResponse",
    "CompanyHistoryEntry",
    "CompanyAggregate",
    "EconomyStatistics",
    "EventCounterStatistics",
    "EventWindowStatistics",
    "UserStatistics",
    "EventStatistics",
    "StatisticsResponse",
]
