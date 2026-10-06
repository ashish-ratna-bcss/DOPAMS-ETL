"""Retired.

The FIR / court / accused-details schedule is now the single DAG
`daily_cycle.py` (`cctns_v1_daily_cycle`, 00:00 / 06:00 / 12:00 / 18:00 IST).

This module must not register an Airflow DAG. A second schedule would run
those entities outside the cycle lock and could mint a different run_id.
"""
