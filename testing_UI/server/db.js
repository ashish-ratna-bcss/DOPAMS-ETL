const { Pool } = require('pg');
const path = require('path');
const dotenv = require('dotenv');

// Load .env from testing_UI/.env
dotenv.config({ path: path.join(__dirname, '..', '.env') });

const v1Url = process.env.CCTNS_V1_DB_URL || process.env.REACT_APP_CCTNS_V1_DB_URL || 'postgresql://dopams_bcss:dopams_bcss%402026@192.168.103.106:5432/cctns_v1';
const v2Url = process.env.CCTNS_V2_DB_URL || process.env.REACT_APP_CCTNS_V2_DB_URL || 'postgresql://dev_dopamas:ADevingpjveBCSS%40D2rkdoast4s@192.168.103.106:5432/cctns-v2';

const v1Pool = new Pool({
  connectionString: v1Url,
  max: 10,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,
});

const v2Pool = new Pool({
  connectionString: v2Url,
  max: 15,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,
});

// Helper for schema-aware query on V1 (supporting search_path = cctns, public)
async function queryV1(text, params) {
  const client = await v1Pool.connect();
  try {
    await client.query('SET search_path TO cctns, public');
    const res = await client.query(text, params);
    return res;
  } finally {
    client.release();
  }
}

// Helper for schema-aware query on V2 (search_path = public)
async function queryV2(text, params) {
  const client = await v2Pool.connect();
  try {
    await client.query('SET search_path TO public');
    const res = await client.query(text, params);
    return res;
  } finally {
    client.release();
  }
}

module.exports = {
  v1Pool,
  v2Pool,
  queryV1,
  queryV2,
};
