# V6.3 data safety changes

Branch: optimize-v63-safety-hardening (based on optimize-v63-data-safety).

- Invalid JSON or invalid required state fields: keep original, make timestamped `.corrupt-*.bak`, raise an error. Never initialize over a damaged file. Missing state alongside an existing CSV also stops execution.
- State and CSV use same-directory temporary files, flush/fsync, then atomic replace. JSON contains a replayable CSV outbox; restart replays only missing complete rows before processing more candles. This is recovery across two files, not a single filesystem transaction.
- All 15m/1H/4H candles must have valid positive OHLC, aligned timestamps, continuous history and a latest closed candle matching the expected boundary (3 minutes publication grace). Future or stale candles stop the run before new simulated events. A processing cursor beyond the downloaded history also stops rather than silently skipping missing bars. Manual history recovery is required in that case.
- Dashboard displays V6.3, Beijing time, candle CLOSE time, and actual successful backend check time separately. Old updated_at values are not represented as real task execution times. All four statistics use the same closed_trades array; invalid R values fail explicitly.
- Active workflow no longer cancels an in-flight writer, runs regression checks, stages only state/log, and uploads state/log/corruption backups on failure. `.github/zec_v63_action.yml` is only a synchronized reference; GitHub executes `.github/workflows/zec_v63_action.yml`.
- Existing state and CSV are unchanged. ATR, confirmations, exits, fee and slippage rules are unchanged. Public market data only; no credentials, account API or order API added.

## Validation

8 Python unittest cases pass: damaged JSON/schema backup, missing state with history, failed atomic replacement, interrupted CSV outbox replay with no duplicate, freshness/future/gap checks for all three periods, fresh first run and repeat, frozen state/CSV agreement, stale run without event/file changes.

Node dashboard test passes: fixture contains 9 closed trades, +1.76R, 55.6% wins, 2.20R drawdown; UTC parsing and Beijing label pass. This is script verification with a minimal DOM, not a browser layout test.

Existing data: 9 trades, total 1.758447R, average 0.195383R, maximum drawdown 2.197278R, maximum losing streak 2. No real-time OKX or GitHub Actions execution was performed. Tests use temporary files and frozen fixtures.

## GitHub web steps

1. Review this branch against optimize-v63-data-safety. Merge only after reviewing the diff. If the running default branch is different, ultimately merge the reviewed change into that default branch.
2. Under Actions > ZEC V6.3 Forward Paper > Run workflow, select the merged target branch and run once. Inspect regression and simulation steps. Do not reset state or delete the CSV to make a failure disappear.
3. If state is corrupt, download `zec-state-diagnostics-<run-id>` from the failed run and restore the latest verified state from repository history, reconciling the CSV. Rerun only after recovery.
4. Under Settings > Pages, confirm the publishing branch/folder contains the updated index and JSON. Wait for deployment, refresh, and check V6.3, Beijing time, candle close and backend check time.
5. Scheduled workflows run from the default branch; GitHub scheduling can be delayed. Refreshing Pages does not trigger a market-data run.
