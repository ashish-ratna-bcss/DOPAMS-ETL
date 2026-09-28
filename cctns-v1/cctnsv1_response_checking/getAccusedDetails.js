// ==============================================================================
// 1. Accused Details API - GET Method
// Uses environment variable: ACCUSED_DETAILS_API_URL
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
const ACCUSED_DETAILS_API_URL = process.env.ACCUSED_DETAILS_API_URL;

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
 * Fetches Accused details using HTTP GET method from ACCUSED_DETAILS_API_URL.
 *
 * @param {Object} [params={}] - Optional query parameters (e.g. firNum, startDate, endDate, psName)
 * @param {Object} [customHeaders={}] - Optional custom headers
 * @returns {Promise<any>}
 */
async function getAccusedDetails(params = {}, customHeaders = {}) {
  const endpoint = process.env.ACCUSED_DETAILS_API_URL;

  if (!endpoint) {
    throw new Error('ACCUSED_DETAILS_API_URL is not set. Please add ACCUSED_DETAILS_API_URL to your .env file.');
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
    query.fir_no = query.firNum;
    query.fir_num = query.firNum;
  }
  if (query.accusedName) {
    query.accused_name = query.accusedName;
  }

  // Append non-empty query params
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

  console.log(`\n[ACCUSED_DETAILS_API_URL] Sending GET request to: ${url.toString()}`);

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
    console.error('[ACCUSED_DETAILS_API_URL] GET Request failed:', error.message);
    throw error;
  }
}

// ------------------------------------------------------------------------------
// Run independently: node getAccusedDetails.js [firNum]
// ------------------------------------------------------------------------------
if (require.main === module) {
  const fs = require('fs');

  (async () => {
    try {
      const cliArgs = process.argv.slice(2);
      const firNum = cliArgs[0] || '';

      console.log('Fetching Accused Details (GET)...');
      const result = await getAccusedDetails({ firNum });

      const records = result.data || (Array.isArray(result) ? result : [result]);
      console.log(`\n Successfully received data (${Array.isArray(records) ? records.length : 1} items).`);

      if (Array.isArray(records) && records.length > 0) {
        console.table(
          records.slice(0, 10).map((rec) => ({
            UNIT: rec.UNIT || '',
            PS_NAME: rec.PS_NAME || '',
            FIR_NO: rec.FIR_NO || '',
            REG_YEAR: rec.REG_YEAR || '',
            ACCUSED_NAME: rec.ACCUSED_NAME?.trim() || '',
            GENDER: rec.GENDER || '',
            AGE: rec.AGE || '',
            SECTION_OF_LAW: rec.SECTION_OF_LAW || '',
          }))
        );

        const outputFile = 'accused_details_records.json';
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
  getAccusedDetails,
  ACCUSED_DETAILS_API_URL,
};
