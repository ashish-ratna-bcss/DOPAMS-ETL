// ==============================================================================
// 4. Accused Date-Range API (with Automatic Monthly Batching)
// Uses environment variable: ACCUSED_API_URL
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
const ACCUSED_API_URL = process.env.ACCUSED_API_URL;

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
 * Parses date string in DD-MM-YYYY or YYYY-MM-DD format into a Date object.
 */
function parseDate(dateStr) {
  if (dateStr instanceof Date) return dateStr;
  const str = String(dateStr).trim();
  const parts = str.split(/[-/]/);
  if (parts.length === 3) {
    if (parts[0].length === 4) {
      return new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
    }
    return new Date(parseInt(parts[2], 10), parseInt(parts[1], 10) - 1, parseInt(parts[0], 10));
  }
  return new Date(str);
}

/**
 * Splits a date range into safe month-by-month chunks to avoid
 * Oracle DB's "ORA-06502: character string buffer too small" error.
 */
function generateMonthlyRanges(startInput, endInput) {
  const start = parseDate(startInput);
  const end = parseDate(endInput);
  const ranges = [];

  let current = new Date(start.getFullYear(), start.getMonth(), 1);
  const endMonth = new Date(end.getFullYear(), end.getMonth(), 1);

  while (current <= endMonth) {
    const year = current.getFullYear();
    const month = current.getMonth();

    const firstDay =
      current.getFullYear() === start.getFullYear() && current.getMonth() === start.getMonth()
        ? start.getDate()
        : 1;

    const lastDayOfMonth = new Date(year, month + 1, 0).getDate();
    const lastDay =
      current.getFullYear() === end.getFullYear() && current.getMonth() === end.getMonth()
        ? end.getDate()
        : lastDayOfMonth;

    const rangeStart = `${String(firstDay).padStart(2, '0')}-${String(month + 1).padStart(2, '0')}-${year}`;
    const rangeEnd = `${String(lastDay).padStart(2, '0')}-${String(month + 1).padStart(2, '0')}-${year}`;

    ranges.push({ start: rangeStart, end: rangeEnd });
    current = new Date(year, month + 1, 1);
  }

  return ranges;
}

/**
 * Performs a single POST request for a specific date chunk.
 */
async function fetchSingleChunk({
  startDate,
  endDate,
  firNum,
  psCd,
  extraPayload = {},
  customHeaders = {},
}) {
  const endpoint = process.env.ACCUSED_API_URL;
  if (!endpoint) {
    throw new Error('ACCUSED_API_URL is not set. Please add ACCUSED_API_URL to your .env file.');
  }

  const formattedStart = toDDMMYYYY(startDate);
  const formattedEnd = toDDMMYYYY(endDate);

  const payload = {
    from_date: formattedStart,
    to_date: formattedEnd,
    ...(firNum ? { fir_num: firNum } : {}),
    ...(psCd ? { ps_cd: psCd } : {}),
    startDate: formattedStart,
    endDate: formattedEnd,
    fromDate: formattedStart,
    toDate: formattedEnd,
    ...extraPayload,
  };

  const headers = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
    ...customHeaders,
  };

  if (process.env.AUTH_TOKEN) {
    headers['Authorization'] = `Bearer ${process.env.AUTH_TOKEN}`;
  } else if (process.env.API_KEY) {
    headers['x-api-key'] = process.env.API_KEY;
  }

  const response = await fetch(endpoint, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
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
}

/**
 * Fetches a date chunk safely. If Oracle throws ORA-06502 buffer overflow,
 * automatically sub-divides the range in half and fetches each half recursively.
 */
async function fetchSafeChunk({
  startDate,
  endDate,
  firNum,
  psCd,
  extraPayload = {},
  customHeaders = {},
  depth = 0,
}) {
  const start = parseDate(startDate);
  const end = parseDate(endDate);

  try {
    const result = await fetchSingleChunk({
      startDate: toDDMMYYYY(start),
      endDate: toDDMMYYYY(end),
      firNum,
      psCd,
      extraPayload,
      customHeaders,
    });

    if (result && result.message && result.message.includes('ORA-06502')) {
      // Buffer overflow: split in half if range is more than 1 day
      const diffDays = Math.ceil((end - start) / (1000 * 60 * 60 * 24));
      if (diffDays > 1 && depth < 5) {
        const midTime = start.getTime() + Math.floor((end.getTime() - start.getTime()) / 2);
        const midDate1 = new Date(midTime);
        const midDate2 = new Date(midTime + 24 * 60 * 60 * 1000);

        const half1 = await fetchSafeChunk({
          startDate: start,
          endDate: midDate1,
          firNum,
          psCd,
          extraPayload,
          customHeaders,
          depth: depth + 1,
        });

        const half2 = await fetchSafeChunk({
          startDate: midDate2,
          endDate: end,
          firNum,
          psCd,
          extraPayload,
          customHeaders,
          depth: depth + 1,
        });

        return [...half1, ...half2];
      }
      return [];
    }

    const records = result.data || (Array.isArray(result) ? result : []);
    return records;
  } catch (err) {
    console.log(` Error: ${err.message}`);
    return [];
  }
}

/**
 * Fetches Accused records from ACCUSED_API_URL with automatic monthly & adaptive batching.
 * Prevents Oracle ORA-06502 buffer overflow errors on large date ranges.
 *
 * @param {Object} options
 * @param {string|Date} [options.startDate='01-01-2014'] - Start date
 * @param {string|Date} [options.endDate='31-12-2015'] - End date
 * @param {string} [options.firNum] - Optional FIR Number
 * @param {string} [options.psCd] - Optional Police Station Code
 * @param {Object} [options.extraPayload={}] - Extra fields for request body
 * @param {Object} [options.customHeaders={}] - Custom headers
 * @returns {Promise<any>} Merged results object
 */
async function getAccused({
  startDate = '01-01-2014',
  endDate = '31-12-2015',
  firNum,
  psCd,
  extraPayload = {},
  customHeaders = {},
} = {}) {
  const chunks = generateMonthlyRanges(startDate, endDate);

  console.log(`\n[ACCUSED_API_URL] Querying range ${toDDMMYYYY(startDate)} to ${toDDMMYYYY(endDate)}`);
  console.log(`[ACCUSED_API_URL] Split into ${chunks.length} monthly request(s) with adaptive auto-splitting.`);

  const allRecords = [];

  for (let i = 0; i < chunks.length; i++) {
    const chunk = chunks[i];
    process.stdout.write(`  [${i + 1}/${chunks.length}] Fetching ${chunk.start} to ${chunk.end}... `);

    const records = await fetchSafeChunk({
      startDate: chunk.start,
      endDate: chunk.end,
      firNum,
      psCd,
      extraPayload,
      customHeaders,
    });

    allRecords.push(...records);
    console.log(`✓ (${records.length} records)`);
  }

  return {
    statusCode: 200,
    totalCount: allRecords.length,
    data: allRecords,
  };
}

// ------------------------------------------------------------------------------
// Run independently: node getAccused.js [startDate] [endDate] [firNum]
// ------------------------------------------------------------------------------
if (require.main === module) {
  const fs = require('fs');

  (async () => {
    try {
      const cliArgs = process.argv.slice(2);
      const startDate = cliArgs[0] || '01-01-2014';
      const endDate = cliArgs[1] || '31-12-2015';
      const firNum = cliArgs[2] || undefined;

      console.log(`Fetching Accused Data for ${startDate} to ${endDate}...`);
      const result = await getAccused({
        startDate,
        endDate,
        firNum,
      });

      const records = result.data || [];
      console.log(`\n🎉 Successfully fetched ${records.length} total records across all months!\n`);

      if (records.length > 0) {
        console.table(
          records.slice(0, 10).map((rec) => ({
            DISTRICT: rec.DISTRICT || '',
            PS: rec.PS || '',
            FIR_NO: rec.FIR_NO || '',
            REG_DT: rec.REG_DT?.split(' ')[0] || rec.REG_DT || '',
            ACCUSED_NAME: rec.ACCUSED_NAME?.trim() || '',
            AGE: rec.AGE || '',
            GENDER: rec.GENDER || '',
            ACT_SEC: rec.ACT_SEC || '',
          }))
        );

        const outputFile = 'accused_records.json';
        fs.writeFileSync(outputFile, JSON.stringify(records, null, 2));
        console.log(`\n✓ Full records saved to: ${outputFile} (${records.length} records)`);
      }
    } catch (err) {
      console.error('Execution finished with error:', err.message);
    }
  })();
}

module.exports = {
  getAccused,
  fetchDataByDateRange: getAccused,
  ACCUSED_API_URL,
};
