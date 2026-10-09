# Existing Agent owns US direction-ledger recovery

The guard now independently checks official US source dates against eligible,
current-schema, hash-verified prediction_history receipts and their actual capture
window. Telegram success, fresh latest.json, other 60-day ledgers, and accepted
workflow dispatches cannot certify this ledger's recovery.

Missing receipt inside the official forecast window uses the existing silent data
refresh action, not a fixed report resend. Active-run avoidance, persisted two-attempt
budgets, cooldown and reservation-owner rechecks are unchanged. Reservation execution
rechecks the direction receipt, so another run's success cancels the dispatch even
when prices were fresh before repair. An unknown calendar can request one bootstrap
via the normal existing credential-bearing workflow; failed calendar attempts stop.
After the window closes the Agent waits for a new official close instead of backfill.
Invalid immutable receipt evidence requires manual review; it is never rewritten.

agent_recovery.json records us_direction_verification; system_guard.json has a
separate us_direction_ledger check; agent_runtime.json exposes both that verification
and the existing data_incidents attempt history. These are derived diagnostics only.
No new autonomous model/code generator, no formal model/ranking/weight/gate changes,
no orders and no test sends. The normal workflow remains responsible for all quality
checks and for obtaining an official calendar before a new receipt may be created.

Local validation: 927 Python tests, 23 Node checks, compile checks passed. New tests
cover tampered/stale/late receipts, bounded silent dispatch without Telegram receipts,
active-run avoidance, post-reservation dedup, failed-calendar stop, immutable budgets
and runtime exposure. Existing healthy guard fixtures now explicitly include the
simulated immutable receipt and simulated official calendar required by this check.
