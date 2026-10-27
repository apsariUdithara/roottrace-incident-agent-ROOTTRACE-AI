"""Response shapes shared by several routes."""

from pydantic import BaseModel


class Page[T](BaseModel):
    """One page of a long list. `total` counts every match, not just the items on this page.

    Subclass it per item type (`class LogPage(Page[LogEntryOut])`), so the generated TypeScript
    type gets a readable name.
    """

    items: list[T]
    total: int
    limit: int
    offset: int
