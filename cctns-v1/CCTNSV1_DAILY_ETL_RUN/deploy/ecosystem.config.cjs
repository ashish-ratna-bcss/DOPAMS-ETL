/**
 * PM2 apps for CCTNS V1 Airflow. Always start via ./deploy/reload_pm2.sh
 * (uses deploy/airflow_with_env.sh so Postgres metadata + .env are applied).
 */
const path = require("path");

const ETL_ROOT = path.resolve(__dirname, "..");
const WRAPPER = path.join(ETL_ROOT, "deploy/airflow_with_env.sh");

module.exports = {
  apps: [
    {
      name: "cctnsv1-airflow-scheduler",
      script: WRAPPER,
      args: "scheduler",
      cwd: ETL_ROOT,
      interpreter: "bash",
    },
    {
      name: "cctnsv1-airflow-webserver",
      // Port/host come from .env via airflow_with_env.sh
      // (AIRFLOW_WEBSERVER_PORT / AIRFLOW_WEBSERVER_HOST).
      script: WRAPPER,
      args: "webserver",
      cwd: ETL_ROOT,
      interpreter: "bash",
    },
  ],
};
