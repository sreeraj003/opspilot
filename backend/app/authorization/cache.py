"""Per-process cache of resolved grants, validated against a database revision.

Consistency guarantee
---------------------
Every RBAC write (roles, permissions, assignments, user activation) increments
``authorization_state.revision`` in the same transaction as the change. Each
authorization decision first reads the current revision (one primary-key
lookup) and uses a cached entry only if it was loaded at that same revision;
otherwise it reloads. A decision therefore always reflects every change
committed before the decision started, across all backend processes, so the
cache can never serve stale permissions. Expiry is evaluated against the clock
on every decision, so expirations need no invalidation at all.

The cache only saves the role/permission join queries. It is bounded (LRU),
holds no credentials, and can be disabled with ``AUTHORIZATION_CACHE_SIZE=0``.
"""

import threading
import uuid
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.authorization.engine import ResolvedAccess


class AuthorizationCache:
    def __init__(self, max_entries: int = 1024) -> None:
        self._max_entries = max(0, max_entries)
        self._entries: OrderedDict[uuid.UUID, ResolvedAccess] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, user_id: uuid.UUID, revision: int) -> "ResolvedAccess | None":
        with self._lock:
            entry = self._entries.get(user_id)
            if entry is None or entry.revision != revision:
                self.misses += 1
                if entry is not None:
                    del self._entries[user_id]
                return None
            self._entries.move_to_end(user_id)
            self.hits += 1
            return entry

    def put(self, entry: "ResolvedAccess") -> None:
        if self._max_entries == 0:
            return
        with self._lock:
            self._entries[entry.user_id] = entry
            self._entries.move_to_end(entry.user_id)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
