# Prediction history storage

The 2026-10-16 scheduled report generated successfully, but GitHub rejected its
100.07 MiB `reports/prediction_history.json` blob. Retrying the same commit cannot
repair the object-size limit.

`prediction_history_storage.py` keeps small ledgers at the legacy JSON path and
stores ledgers of at least 8 MiB as deterministic `prediction_history.json.gz`.
All production consumers resolve the logical JSON path through the common
reader. The format inside gzip is unchanged JSON, with all records, fields and
frozen integrity hashes preserved. The writer verifies exact byte restoration
before replacing the destination and only then removes the old plain copy.
It never truncates history to meet a size limit. Existing methodology/365-day
pipeline rules remain unchanged; this is not a new all-time retention policy.

Both forms with identical bytes are accepted during migration. Conflicting
copies or corrupt storage stop processing rather than resetting the ledger.
If replacement succeeds but old-file removal fails, both copies remain for
explicit reconciliation; the two-path migration is fail-closed, not a single
filesystem transaction. Never delete a conflicting copy without checking it.
A stored ledger at or above 80 MiB fails before replacement. The report workflow
also rejects any staged blob at or above 95 MiB before creating a commit. Future
capacity growth requires another lossless migration, not dropped records.

Small ordinary JSON fixtures and other reports are unchanged. Owner/friend
publication uses derived reports and does not consume this ledger. Existing
archive/Drive and SQLite backups have separate scopes. The generic bounded
context planner does not read gzip ledger bytes as text; use the logical reader
for explicit ledger analysis. External downloads should use the `.json.gz`
file after migration and decompress with standard gzip tooling.

## Verification

Run `python -m pytest -q tests/test_prediction_history_storage.py` and the full
repository CI. Tests cover legacy/gzip consumers, exact deterministic migration,
conflicts/corruption, capacity/replacement failures, same-session frozen receipts,
and staged-versus-worktree byte-size checks. Confirm the next scheduled workflow
successfully commits the gzip ledger and removes the plain ledger in that same
commit. No extra delivery or workflow schedule is introduced.

## Rollback

Do not simply revert the readers after a production migration. First restore
compatible storage losslessly, verifying decompression and frozen snapshot
hashes. A restored JSON blob may exceed GitHub's limit, so retain gzip readers
until an alternative lossless representation is deployed. No force-push or Git
history rewriting is necessary or allowed for this repair.
