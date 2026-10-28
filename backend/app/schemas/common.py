"""Shapes shared by several routes."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel

# A time without a timezone is UTC, as when incidents and logs are ingested.
UtcDatetime = Annotated[
    datetime, AfterValidator(lambda value: value if value.tzinfo else value.replace(tzinfo=UTC))
]


class Page[T](BaseModel):
    """One page of a long list. `total` counts every match, not just the items on this page.

    Subclass it per item type (`class LogPage(Page[LogEntryOut])`), so the generated TypeScript
    type gets a readable name.
    """

    items: list[T]
    total: int
    limit: int
    offset: int
