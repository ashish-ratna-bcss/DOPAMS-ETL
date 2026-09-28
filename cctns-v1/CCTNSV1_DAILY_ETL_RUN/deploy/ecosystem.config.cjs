/**
 * PM2 apps for CCTNS V1 Airflow (scheduler + webserver).
 * Start via: ./deploy/reload_pm2.sh  (sources .env + sets Postgres metadata URL)
 */
const path = require("path");

const ETL_ROOT = path.resolve(__dirname, "..");

const airflowEnv = {
  AIRFLOW_HOME: path.join(ETL_ROOT, "airflow_home"),
  AIRFLOW__CORE__DAGS_FOLDER: path.join(ETL_ROOT, "dags"),
  AIRFLOW__CORE__LOAD_EXAMPLES: "False",
  AIRFLOW__CORE__EXECUTOR: "LocalExecutor",
  AIRFLOW__CORE__PARALLELISM: "4",
  AIRFLOW__CORE__MAX_ACTIVE_TASKS_PER_DAG: "4",
  // AIRFLOW__DATABASE__SQL_ALCHEMY_CONN set by reload_pm2.sh from PG_* in .env
};

module.exports = {
  apps: [
    {
      name: "cctnsv1-airflow-scheduler",
      script: path.join(ETL_ROOT, "venv/bin/airflow"),
      args: "scheduler",
      cwd: ETL_ROOT,
      interpreter: "none",
      env: airflowEnv,
    },
    {
      name: "cctnsv1-airflow-webserver",
      script: path.join(ETL_ROOT, "venv/bin/airflow"),
      args: "webserver --port 9001",
      cwd: ETL_ROOT,
      interpreter: "none",
      env: airflowEnv,
    },
  ],
};
