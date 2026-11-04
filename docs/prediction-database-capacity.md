# Private prediction database capacity

The default private SQLite limit is 2 GiB (2147483648 bytes), replacing 500 MiB.
`prediction_engine.storage.configured_max_bytes()` is shared by the engine,
unit-learning entry point, direct stores and backup CLI. A positive integer
`PREDICTION_ENGINE_MAX_BYTES` overrides the default; explicit constructor limits
are still supported. Invalid/nonpositive values fail before DB creation.
The existing hard insertion limit and 80% capacity warning remain enabled.
No platform plan, paid storage, cache quota, credentials or permissions change.

The 2026-11-04 19:49:21 public health snapshot reported 520814592 bytes (99.34%
of the old cap). That would use about 24.25% of the new limit. This is headroom,
not a prediction of how many days remain: retaining evidence can increase growth.

Maintenance now checkpoints SQLite without clearing matured evidence, deleting
old predictions/unit rows/prices, or vacuuming as a cleanup shortcut. The legacy
`keep_matured_sessions` argument remains accepted but no longer permits pruning.
Previously removed evidence cannot be recovered by this change. No migration,
reset, restore or producer rerun is required or performed as part of release.

## Verification and recovery

CI and the existing report workflow print read-only storage metadata using
`tools/prediction_capacity_status.py`. The latter runs after cache restoration.
It does not open the DB, read private content, initialize a new store, or assert
that the provider's cache quota was verified. Engine health reports filesystem
free/total bytes and the explicit preserve-all-history retention policy.

The developer workspace measured about 28 GiB free, with no private DB present.
This is not the production runner or proof of private-DB integrity. CI runner
space is likewise not a measurement of the report job's restored cache.
Production acceptance requires a naturally scheduled report's health to show:

- configured_max_bytes=2147483648 (or an intentional environment override);
- nonzero database size and table counts continuing from the previous snapshot;
- sufficient filesystem free bytes and no destructive maintenance counters;
- a successful verified backup health report and unchanged delivery/hash guards.

The existing private gzip backup and manifest remain inside `.prediction_engine`
and use the existing Actions cache key. Hash, schema, table-count and exact
round-trip verification remain intact. Restoration still refuses a nonempty DB.
Public backup health is evidence reported by the runner, not independent recovery
of the private backup here. Tests restore temporary fixtures only.

If space or cache persistence fails, stop further writes and preserve the existing
DB/backup; inspect the known run's logs. Do not delete caches/history or blindly
roll back to the old destructive maintenance implementation. A capacity override
must remain above actual DB size; raising the code limit does not buy disk space.
