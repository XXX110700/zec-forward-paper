# TP confirmation refresh

Only a newly simulated TP1/TP2 full exit creates `tp_confirmation_refresh`.
Both scores are calculated at that exit candle close using the existing closed
15m/1h/4h rules. The marker stores event identity, reason, decision time, both
scores, and a count since feature activation. Ordinary per-candle scoring and
5/6 entry gates remain unchanged, including new signals after SL.

The marker, closed trade, processing cursor and CSV outbox share the existing
atomic state commit. Repeated runs skip processed candles. Interrupted writes
replay the existing outbox without duplicating log rows. Invalid markers or
market data stop processing; insufficient TP indicators stop before commit.
Legacy states remain valid and historical TP exits are not replayed.
No state/log migration, account access, orders or deployment is performed.

Run `python -m unittest discover -s tests -v` and
`node tests/test_dashboard.cjs`. Tests use temporary state/logs and mocked data.
