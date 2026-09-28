// ==============================================================================
// CCTNS API Client - Central Exports for All 4 APIs
// ==============================================================================

const { getAccusedDetails, ACCUSED_DETAILS_API_URL } = require('./getAccusedDetails');
const { getFir, FIR_API_URL } = require('./getFir');
const { getCourt, COURT_API_URL } = require('./getCourt');
const { getAccused, ACCUSED_API_URL } = require('./getAccused');

module.exports = {
  // 1. Accused Details (GET) -> uses ACCUSED_DETAILS_API_URL
  getAccusedDetails,
  ACCUSED_DETAILS_API_URL,

  // 2. FIR Data (GET) -> uses FIR_API_URL
  getFir,
  FIR_API_URL,

  // 3. Court Data (GET) -> uses COURT_API_URL
  getCourt,
  COURT_API_URL,

  // 4. Accused Date-Range (POST) -> uses ACCUSED_API_URL
  getAccused,
  ACCUSED_API_URL,
};
