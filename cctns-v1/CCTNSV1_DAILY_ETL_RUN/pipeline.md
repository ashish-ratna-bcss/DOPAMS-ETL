# CCTNS V1 Daily ETL — how it works

## Flow

```mermaid
flowchart TB
    subgraph SRC["CCTNS V1 API — no auth required"]
        FIR["FIR endpoint\nGET"]
        COURT["Court endpoint\nGET"]
        AD["Accused Details endpoint\nGET"]
        ACC["Accused date-range endpoint\nPOST"]
    end

    subgraph DAG1["DAG: cctnsv1_simple_apis_etl — 00:30 daily"]
        FIR --> E1["fetch_fir()\n~7,300 rows"]
        COURT --> E2["fetch_court()\n~7,700 rows"]
        AD --> E3["fetch_accused_details()\n~20,200 rows"]
    end

    subgraph DAG2["DAG: cctnsv1_accused_yearly_etl — 00:30 daily"]
        ACC --> E4["fetch_accused()\nmonth-by-month, adaptive halving\non Oracle ORA-06502 buffer errors"]
    end

    E1 --> L
    E2 --> L
    E3 --> L
    E4 --> L

    L{{"upsert_records()\nON CONFLICT (key) DO UPDATE\nWHERE row IS DISTINCT FROM EXCLUDED"}}

    L -->|"key not seen before"| INS[("INSERT")]
    L -->|"key seen, content changed"| UPD[("UPDATE")]
    L -->|"key seen, nothing changed"| SKIP["no write"]

    INS --> DB[("cctns_v1 (Postgres)\ncctns_fir / cctns_court /\ncctns_accused_details / cctns_accused")]
    UPD --> DB
    UPD --> AUDIT[("cctns_v1_audit_log\nfield, old_value, new_value, changed_at")]

    E1 -.-> RUNLOG
    E2 -.-> RUNLOG
    E3 -.-> RUNLOG
    E4 -.-> RUNLOG
    L -.-> RUNLOG[("cctns_v1_etl_run_log\nfetched / inserted / updated / unchanged\nfailed date windows, status")]
```

The key used to detect "have I seen this record before": `fir_reg_num` for `cctns_fir`, `natural_key` for the other 3 tables (see `db/sql/001_schema_fix.sql`).

Both DAGs pull the **full dataset every run** — there's no reliable "give me what changed since yesterday" filter on this API, so instead the pipeline re-fetches everything and lets Postgres decide what actually changed via the `ON CONFLICT ... WHERE IS DISTINCT FROM` upsert in `db/upsert.py`. No hash column, no manual diff code — Postgres compares the real row values directly.

Nothing is written to local disk. Data goes straight from the API into Postgres in memory; logging goes to stdout, captured by Airflow as each task's log.

## Why two DAGs, not one

`fetch_accused()` is the one endpoint that needs date-chunking — its Oracle backend throws buffer errors on wide ranges, confirmed from real captured runs. The other 3 endpoints are a single unfiltered GET each, no chunking needed. Kept as separate DAGs since the accused pull is slower and more failure-prone — easier to monitor and retry on its own without blocking the other 3.

## Current status

| Entity | Loads to Postgres? |
|---|---|
| `fir` | ✅ yes — real primary key, no duplicates |
| `court`, `accused_details`, `accused` | ⛔ fetched every run, but not loaded yet — the business key (`natural_key`) hasn't been finalized (see `db/sql/001_schema_fix.sql`) |

Setup, deployment, and verification steps live in `deploy/README.md`, not here.
