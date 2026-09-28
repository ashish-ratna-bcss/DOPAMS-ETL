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
      script: WRAPPER,
      args: "webserver --port 9001 --hostname 0.0.0.0",
      cwd: ETL_ROOT,
      interpreter: "bash",
    },
  ],
};
