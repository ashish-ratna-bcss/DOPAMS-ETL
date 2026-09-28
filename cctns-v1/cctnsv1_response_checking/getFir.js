// ==============================================================================
// 2. FIR Details API - GET Method
// Uses environment variable: FIR_API_URL
// ==============================================================================

// Load environment variables from .env
if (typeof process.loadEnvFile === 'function') {
  try {
    process.loadEnvFile();
  } catch (err) {
    // Continue if .env already loaded or missing
  }
} else {
  try {
    require('dotenv').config();
  } catch (err) {
    // dotenv not installed
  }
}

// ------------------------------------------------------------------------------
// API Endpoint (loaded directly from .env)
// ------------------------------------------------------------------------------
const FIR_API_URL = process.env.FIR_API_URL;

/**
 * Converts dates (Date object, 'YYYY-MM-DD', or 'DD-MM-YYYY') to 'DD-MM-YYYY' format.
 */
function toDDMMYYYY(dateInput) {
  if (!dateInput) return '';
  if (dateInput instanceof Date) {
    const day = String(dateInput.getDate()).padStart(2, '0');
    const month = String(dateInput.getMonth() + 1).padStart(2, '0');
    const year = dateInput.getFullYear();
    return `${day}-${month}-${year}`;
  }
  const str = String(dateInput).trim();
  const ymdMatch = str.match(/^(\d{4})[-/](\d{2})[-/](\d{2})$/);
  if (ymdMatch) {
    return `${ymdMatch[3]}-${ymdMatch[2]}-${ymdMatch[1]}`;
  }
  return str;
}

/**
 * Fetches FIR details using HTTP GET method from FIR_API_URL.
 *
 * @param {Object} [params={}] - Query parameters to send in the GET request.
 * @param {string} [params.firNum] - FIR Number (fir_num / fir_no)
 * @param {string|Date} [params.startDate] - Start date (converted to DD-MM-YYYY)
 * @param {string|Date} [params.endDate] - End date (converted to DD-MM-YYYY)
 * @param {string} [params.psCd] - Police Station Code (ps_cd)
 * @param {string} [params.district] - District Name / Code
 * @param {Object} [customHeaders={}] - Optional custom request headers
 * @returns {Promise<any>}
 */
async function getFir(params = {}, customHeaders = {}) {
  const endpoint = process.env.FIR_API_URL;

  if (!endpoint) {
    throw new Error('FIR_API_URL is not set. Please add FIR_API_URL to your .env file.');
  }

  const url = new URL(endpoint);

  // Normalize query parameters
  const query = { ...params };

  if (query.startDate) {
    const formatted = toDDMMYYYY(query.startDate);
    query.from_date = formatted;
    delete query.startDate;
  }
  if (query.endDate) {
    const formatted = toDDMMYYYY(query.endDate);
    query.to_date = formatted;
    delete query.endDate;
  }
  if (query.firNum) {
    query.fir_num = query.firNum;
    query.fir_no = query.firNum;
  }
  if (query.psCd) {
    query.ps_cd = query.psCd;
  }

  // Append non-empty query parameters to the URL
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.append(key, String(value));
    }
  });

  const headers = {
    Accept: 'application/json',
    ...customHeaders,
  };

  if (process.env.AUTH_TOKEN) {
    headers['Authorization'] = `Bearer ${process.env.AUTH_TOKEN}`;
  } else if (process.env.API_KEY) {
    headers['x-api-key'] = process.env.API_KEY;
  }

  console.log(`\n[FIR_API_URL] Sending GET request to: ${url.toString()}`);

  try {
    const response = await fetch(url.toString(), {
      method: 'GET',
      headers,
    });

    const contentType = response.headers.get('content-type') || '';
    let data;

    if (contentType.includes('application/json')) {
      data = await response.json();
    } else {
      data = await response.text();
    }

    if (!response.ok) {
      throw new Error(`HTTP Error ${response.status} (${response.statusText}): ${typeof data === 'string' ? data : JSON.stringify(data)}`);
    }

    return data;
  } catch (error) {
    console.error('[FIR_API_URL] GET Request failed:', error.message);
    throw error;
  }
}

// ------------------------------------------------------------------------------
// Run independently: node getFir.js [firNum] [startDate] [endDate]
// ------------------------------------------------------------------------------
if (require.main === module) {
  const fs = require('fs');

  (async () => {
    try {
      const cliArgs = process.argv.slice(2);
      const firNum = cliArgs[0] || '';
      const startDate = cliArgs[1] || '';
      const endDate = cliArgs[2] || '';

      console.log('Fetching FIR Details (GET)...');
      const result = await getFir({
        firNum,
        startDate,
        endDate,
      });

      const records = result.data || (Array.isArray(result) ? result : [result]);
      console.log(`\n Successfully received data (${Array.isArray(records) ? records.length : 1} items).`);

      if (Array.isArray(records) && records.length > 0) {
        console.table(
          records.slice(0, 10).map((rec) => ({
            DISTRICT: rec.DISTRICT || rec.district || '',
            PS: rec.PS || rec.ps || '',
            FIR_NO: rec.FIR_NO || rec.fir_no || '',
            REG_DT: rec.REG_DT || rec.reg_dt || '',
            ACT_SEC: rec.ACT_SEC || rec.act_sec || '',
          }))
        );

        const outputFile = 'fir_records.json';
        fs.writeFileSync(outputFile, JSON.stringify(result, null, 2));
        console.log(`\n Response saved to: ${outputFile}`);
      } else {
        console.log('Response:', JSON.stringify(result, null, 2));
      }
    } catch (err) {
      console.error('Execution finished with error:', err.message);
    }
  })();
}

module.exports = {
  getFir,
  getFirDetails: getFir,
  FIR_API_URL,
};
