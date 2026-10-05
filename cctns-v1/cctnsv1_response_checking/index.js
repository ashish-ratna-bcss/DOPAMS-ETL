// ==============================================================================
// CCTNS API Client - Central Exports for All APIs
// ==============================================================================

const { getAccusedDetails, ACCUSED_DETAILS_API_URL } = require('./getAccusedDetails');
const { getFir, FIR_API_URL } = require('./getFir');
const { getCourt, COURT_API_URL } = require('./getCourt');
const { getAccused, ACCUSED_API_URL } = require('./getAccused');
const {
  getAlfrescoDownload,
  downloadAlfrescoDocument,
  ALFRESCO_DOWNLOAD_API_URL,
} = require('./getAlfrescoDownload');

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

  // 5. Alfresco Download (GET) -> uses ALFRESCO_DOWNLOAD_API_URL
  getAlfrescoDownload,
  downloadAlfrescoDocument,
  ALFRESCO_DOWNLOAD_API_URL,
};

