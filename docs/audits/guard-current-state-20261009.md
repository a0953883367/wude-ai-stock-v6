# Observe current persisted Agent state on reruns

Actual guard run 37953373451 attempt 2 failed before external action: checkout used its older event commit and rebase conflicted in generated Agent reports after attempt 1 had already published current state. No silent refresh was dispatched by the failed attempt.

The state-observing guard now checks out main explicitly before inspection and reservation. This preserves the latest persisted retry/permission history and avoids replaying an obsolete report state on a delayed or rerun event. It does not clear attempts, widen repair recipes, skip testing or change any formal V6, shadow gate, ranking, weight or order behavior. Reservation must still publish successfully before external action. Concurrent changes still fail closed on a rebase conflict.

Calendar relay PR313 was already deployed and its authenticated capability was verified through health; unauthenticated calendar requests returned 403. Post-merge guard receipts remain required to establish actual GitHub calendar transport.
