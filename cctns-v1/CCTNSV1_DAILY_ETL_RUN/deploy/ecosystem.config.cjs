/**
 * PM2 apps for CCTNS V1 Airflow. Always start via ./deploy/reload_pm2.sh
 * (uses deploy/airflow_with_env.sh so Postgres metadata + .env are applied).
 */
const path = require("path");

const ETL_ROOT = path.resolve(__dirname, "..");
const WRAPPER = path.join(ETL_ROOT, "deploy/airflow_with_env.sh");

// Exponential backoff on crash: Postgres (192.168.103.106) is a separate host
// from this one, so on a cold boot it or the network may not be reachable
// yet when PM2 first starts these apps. Without backoff, PM2's default
// (0ms restart_delay, max_restarts=16) can crash-loop to "errored" within
// seconds -- before the DB host has had a chance to come up.
const RESTART_POLICY = {
  exp_backoff_restart_delay: 2000,
  max_restarts: 30,
};

module.exports = {
  apps: [
    {
      name: "cctnsv1-airflow-scheduler",
      script: WRAPPER,
      args: "scheduler",
      cwd: ETL_ROOT,
      interpreter: "bash",
      ...RESTART_POLICY,
    },
    {
      name: "cctnsv1-airflow-webserver",
      // Port/host come from .env via airflow_with_env.sh
      // (AIRFLOW_WEBSERVER_PORT / AIRFLOW_WEBSERVER_HOST).
      script: WRAPPER,
      args: "webserver",
      cwd: ETL_ROOT,
      interpreter: "bash",
      ...RESTART_POLICY,
    },
  ],
};
