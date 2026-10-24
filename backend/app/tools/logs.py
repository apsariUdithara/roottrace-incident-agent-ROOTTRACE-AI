"""Structured log search: aggregates first, then a few sample lines.

Thousands of log lines would not fit in the model's context, and most are normal traffic. So
the tool returns what an engineer looks at first: how many of each error, where, and when they
started, plus a handful of real lines (errors first) to cite.
"""

from datetime import datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.models import LogEntry
from app.tools.registry import ToolContext, tool

SAMPLES = 8
DEPLOY_LOOKBACK_H = 6  # also show deploys shortly before the window
BUCKET_S = 600  # timeline resolution: 10 minutes


class LogSearchArgs(BaseModel):
    since: datetime | None = Field(default=None, description="Start time (ISO 8601, UTC).")
    until: datetime | None = Field(default=None, description="End time (ISO 8601, UTC).")
    level: str | None = Field(default=None, description="INFO, WARN or ERROR.")
    route: str | None = Field(default=None, description="HTTP route or part of it, e.g. /pay")
    error_type: str | None = Field(default=None, description="e.g. OperationalError.")
    text: str | None = Field(
        default=None,
        description="Text to find, case-insensitively, in the message or the error type.",
    )
    service: str | None = Field(default=None, description="Service name.")


def _filters(ctx: ToolContext, args: LogSearchArgs) -> list:
    f = [LogEntry.project_id == ctx.project_id]
    if args.since:
        f.append(LogEntry.ts >= args.since)
    if args.until:
        f.append(LogEntry.ts <= args.until)
    if args.level:
        f.append(func.upper(LogEntry.level) == args.level.upper())
    if args.route:
        f.append(LogEntry.route.ilike(f"%{args.route}%"))
    if args.error_type:
        f.append(LogEntry.error_type == args.error_type)
    if args.text:
        # Case-insensitive, in the message OR the error type: "ReconciliationMismatch" is the
        # error type of lines whose message says "reconciliation mismatch".
        pattern = f"%{args.text}%"
        f.append(or_(LogEntry.message.ilike(pattern), LogEntry.error_type.ilike(pattern)))
    if args.service:
        f.append(LogEntry.service == args.service)
    return f


async def available_values(ctx: ToolContext) -> dict:
    """What the logs contain, to correct a filter that matched nothing."""
    project = LogEntry.project_id == ctx.project_id

    async def distinct(column, limit: int) -> list:
        rows = await ctx.session.execute(
            select(column, func.count())
            .where(project, column.is_not(None))
            .group_by(column)
            .order_by(func.count().desc())
            .limit(limit)
        )
        return [value for value, _ in rows.all()]

    first, last = (
        await ctx.session.execute(
            select(func.min(LogEntry.ts), func.max(LogEntry.ts)).where(project)
        )
    ).one()
    return {
        "services": await distinct(LogEntry.service, 10),
        "levels": await distinct(LogEntry.level, 10),
        "routes": await distinct(LogEntry.route, 15),
        "error_types": await distinct(LogEntry.error_type, 15),
        "logs_from": first,
        "logs_until": last,
    }


@tool(
    "search_logs",
    "Search structured application logs. Returns totals, counts per level, per error type "
    "(with first and last time seen) and per route, a 10-minute timeline of warnings and "
    "errors, deploy events (always, whatever the filters), and a few sample lines with trace "
    "IDs. Narrow with a time window around the incident; first_seen of an error type shows "
    "when a problem started, and deploys show what was released when.",
    LogSearchArgs,
)
async def search_logs(ctx: ToolContext, args: LogSearchArgs) -> dict:
    where = _filters(ctx, args)
    s = ctx.session

    total = await s.scalar(select(func.count()).select_from(LogEntry).where(*where))
    if not total:
        # An empty result should say why: show the values that DO exist, so the next call can
        # use a real service or route name instead of guessing again.
        return {
            "total": 0,
            "note": "no log lines match. Check the filters against the values that exist below, "
            "or widen the time window.",
            "available": await available_values(ctx),
        }

    by_level = await s.execute(
        select(LogEntry.level, func.count()).where(*where).group_by(LogEntry.level)
    )
    by_error = await s.execute(
        select(
            LogEntry.error_type,
            func.count(),
            func.min(LogEntry.ts),
            func.max(LogEntry.ts),
        )
        .where(*where, LogEntry.error_type.is_not(None))
        .group_by(LogEntry.error_type)
        .order_by(func.count().desc())
        .limit(10)
    )
    by_route = await s.execute(
        select(LogEntry.route, LogEntry.status, func.count())
        .where(*where)
        .group_by(LogEntry.route, LogEntry.status)
        .order_by(func.count().desc())
        .limit(12)
    )
    bucket = func.to_timestamp(
        func.floor(func.extract("epoch", LogEntry.ts) / BUCKET_S) * BUCKET_S
    ).label("bucket")
    timeline = await s.execute(
        select(bucket, func.count())
        .where(*where, func.upper(LogEntry.level) != "INFO")
        .group_by(bucket)
        .order_by(bucket)
    )
    # Deploys are context for every incident, so they are shown whatever the level filter.
    window = [LogEntry.project_id == ctx.project_id]
    if args.since:
        window.append(LogEntry.ts >= args.since - timedelta(hours=DEPLOY_LOOKBACK_H))
    if args.until:
        window.append(LogEntry.ts <= args.until)
    deploys = await s.execute(
        select(LogEntry.ts, LogEntry.message)
        .where(*window, LogEntry.message.ilike("deploy%"))
        .order_by(LogEntry.ts)
        .limit(10)
    )
    severity = func.upper(LogEntry.level) == "ERROR"
    samples = await s.execute(
        select(LogEntry)
        .where(*where)
        .order_by(severity.desc(), (func.upper(LogEntry.level) == "WARN").desc(), LogEntry.ts)
        .limit(SAMPLES)
    )

    return {
        "total": total,
        "by_level": dict(by_level.all()),
        "by_error_type": [
            {"error_type": e, "count": n, "first_seen": first, "last_seen": last}
            for e, n, first, last in by_error.all()
        ],
        "by_route": [{"route": r, "status": st, "count": n} for r, st, n in by_route.all()],
        "warn_error_timeline": [{"from": b, "count": n} for b, n in timeline.all()],
        "deploys": [
            {"ref": f"log deploy at {ts.isoformat()}", "ts": ts, "message": m}
            for ts, m in deploys.all()
        ],
        "samples": [
            {
                "ref": f"log trace_id={e.trace_id}",
                "ts": e.ts,
                "level": e.level,
                "instance": e.instance,
                "route": e.route,
                "status": e.status,
                "latency_ms": e.latency_ms,
                "message": e.message[:500],
            }
            for e in samples.scalars()
        ],
    }
