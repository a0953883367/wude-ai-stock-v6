# Recheck independent direction repair at the silent refresh gate

Actual Agent run 37954143326 reserved and dispatched the second bounded evening silent refresh. Run 37954229274 succeeded only at its gate: briefing was skipped with `data refresh no longer required`. Its latest price report was fresh, but the verified calendar/direction ledger was still missing. A successful gate therefore did not prove calendar transport or direction recovery.

The silent refresh gate now recomputes the existing read-only direction diagnosis alongside price freshness. Calendar bootstrap or a missing immutable snapshot inside the legal forecast window may continue; an unfinished session, missed forecast window, failed calendar attempt or verified snapshot does not force a direction refresh. All existing schedule, retry, ownership and no-Telegram rules remain in place. No attempts are cleared, no historical forecast is reconstructed, and no V6/ranking/weight/shadow-gate/order changes occur.

The gate installs its existing requests dependency only for a non-validation silent refresh. Railway health confirmed supported/available/verified_alpaca calendar relay; unauthenticated requests returned 403. The next normal execution must establish a real calendar cache receipt. Today's two recovery attempts are retained; no third attempt is authorized by this change.
