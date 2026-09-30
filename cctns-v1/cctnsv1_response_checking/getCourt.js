// ==============================================================================
// 3. Court Details API - GET Method
// Uses environment variable: COURT_API_URL
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
const COURT_API_URL = process.env.COURT_API_URL;

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
 * Fetches Court details using HTTP GET method from COURT_API_URL.
 *
 * @param {Object} [params={}] - Query parameters to send in the GET request.
 * @param {string} [params.caseNum] - Case Number / CC Number
 * @param {string} [params.firNum] - FIR Number (fir_num or fir_no)
 * @param {string} [params.courtName] - Court Name
 * @param {string|Date} [params.startDate] - Hearing or filing start date
 * @param {string|Date} [params.endDate] - Hearing or filing end date
 * @param {string} [params.psCd] - Police Station Code
 * @param {Object} [customHeaders={}] - Optional custom request headers
 * @returns {Promise<any>}
 */
async function getCourt(params = {}, customHeaders = {}) {
  const endpoint = process.env.COURT_API_URL;

  if (!endpoint) {
    throw new Error('COURT_API_URL is not set. Please add COURT_API_URL to your .env file.');
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
  if (query.caseNum) {
    query.case_num = query.caseNum;
    query.case_no = query.caseNum;
  }
  if (query.courtName) {
    query.court_name = query.courtName;
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

  console.log(`\n[COURT_API_URL] Sending GET request to: ${url.toString()}`);

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
    console.error('[COURT_API_URL] GET Request failed:', error.message);
    throw error;
  }
}

// ------------------------------------------------------------------------------
// Run independently: node getCourt.js [caseNum] [firNum]
// ------------------------------------------------------------------------------
if (require.main === module) {
  const fs = require('fs');

  (async () => {
    try {
      const cliArgs = process.argv.slice(2);
      const caseNum = cliArgs[0] || '';
      const firNum = cliArgs[1] || '';

      console.log('Fetching Court Details (GET)...');
      const result = await getCourt({
        caseNum,
        firNum,
      });

      const records = result.data || (Array.isArray(result) ? result : [result]);
      console.log(`\n Successfully received data (${Array.isArray(records) ? records.length : 1} items).`);

      if (Array.isArray(records) && records.length > 0) {
        console.table(
          records.slice(0, 10).map((rec) => ({
            CASE_NO: rec.CASE_NO || rec.case_no || rec.CC_NO || '',
            COURT_NAME: rec.COURT_NAME || rec.court_name || '',
            FIR_NO: rec.FIR_NO || rec.fir_no || '',
            HEARING_DATE: rec.HEARING_DATE || rec.hearing_date || rec.NEXT_DATE || '',
            STATUS: rec.STATUS || rec.status || '',
          }))
        );

        const outputFile = 'court_records.json';
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
  getCourt,
  getCourtDetails: getCourt,
  COURT_API_URL,
};
