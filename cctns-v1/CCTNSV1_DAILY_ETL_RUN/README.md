# CCTNS V1 Daily ETL Run

Nightly extract/load from CCTNS V1 APIs into Postgres, orchestrated by Airflow.

| Doc | Contents |
|-----|----------|
| **[`pipeline.md`](pipeline.md)** | Full ETL design — APIs, Postgres schemas, upsert, entity status, diagrams |
| **[`dags/README.md`](dags/README.md)** | Airflow DAGs — tasks, schedules, what “green” means, how to trigger |
| **[`deploy/README.md`](deploy/README.md)** | dopams-new deploy, PM2, `.env`, ops |

Quick start (local or server): copy `.env.example` → `.env`, install `requirements.txt`, see deploy README for Airflow.
