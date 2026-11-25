"""Service-level transaction boundaries.

Rules:

* Repositories never commit. They ``flush`` so constraint violations surface
  early, and leave the decision to commit to the caller.
* A service method that changes data wraps the whole operation in
  ``async with transaction(session):``. It commits once at the end, or rolls
  back everything if any step raises.
* Transactions nest by joining: when a service that opened ``transaction``
  calls another service that also opens one, the inner block only flushes and
  the outermost block commits or rolls back. A nested call therefore can never
  commit half of its caller's work.
* An exception from an inner block must be allowed to propagate. A caller that
  wants to recover from a failed sub-step and keep going needs a savepoint
  (``session.begin_nested()``) around that sub-step instead.

Read-only service methods do not need ``transaction``: the request's session is
closed (and its implicit read transaction rolled back) when the request ends.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

_DEPTH_KEY = "opspilot.transaction_depth"


def transaction_depth(session: AsyncSession) -> int:
    """How many ``transaction`` blocks are currently open on this session."""
    return int(session.info.get(_DEPTH_KEY, 0))


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Commit on success and roll back on failure, unless an outer block owns the transaction."""
    depth = transaction_depth(session)
    owner = depth == 0
    session.info[_DEPTH_KEY] = depth + 1
    try:
        yield session
        if owner:
            await session.commit()
        else:
            await session.flush()
    except BaseException:
        if owner:
            await session.rollback()
        raise
    finally:
        session.info[_DEPTH_KEY] = depth
