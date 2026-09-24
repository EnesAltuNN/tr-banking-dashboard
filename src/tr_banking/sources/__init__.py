"""Data source clients (EVDS now; BDDK, BKM, bank sites later).

Every source returns observations in the same long format: one row per (code, date, value).
The repository stores exactly this shape, so new sources plug in without storage changes.
"""

OBSERVATION_COLUMNS = ["code", "date", "value"]
