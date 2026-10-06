const express = require('express');
const router = express.Router();
const { queryV1, queryV2 } = require('../db');

router.get('/', async (req, res) => {
  const result = {
    v1: { status: 'unknown', error: null, counts: {} },
    v2: { status: 'unknown', error: null, counts: {} },
  };

  // Test V1
  try {
    const firCount = await queryV1('SELECT COUNT(*)::int as count FROM cctns_fir');
    const accusedCount = await queryV1('SELECT COUNT(*)::int as count FROM cctns_accused');
    const accusedDetailsCount = await queryV1('SELECT COUNT(*)::int as count FROM cctns_accused_details');
    const courtCount = await queryV1('SELECT COUNT(*)::int as count FROM cctns_court');

    result.v1.status = 'connected';
    result.v1.counts = {
      firs: firCount.rows[0].count,
      accused: accusedCount.rows[0].count,
      accused_details: accusedDetailsCount.rows[0].count,
      court: courtCount.rows[0].count,
    };
  } catch (err) {
    result.v1.status = 'error';
    result.v1.error = err.message;
  }

  // Test V2
  try {
    const crimesCount = await queryV2('SELECT COUNT(*)::int as count FROM crimes');
    const accusedCount = await queryV2('SELECT COUNT(*)::int as count FROM accused');
    const personsCount = await queryV2('SELECT COUNT(*)::int as count FROM persons');
    const arrestsCount = await queryV2('SELECT COUNT(*)::int as count FROM arrests');
    const irCount = await queryV2('SELECT COUNT(*)::int as count FROM interrogation_reports');
    const chargesheetsCount = await queryV2('SELECT COUNT(*)::int as count FROM chargesheets');
    const seizuresCount = await queryV2('SELECT COUNT(*)::int as count FROM mo_seizures');
    const propertiesCount = await queryV2('SELECT COUNT(*)::int as count FROM properties');
    const mediaCount = await queryV2('SELECT COUNT(*)::int as count FROM file_media_bookkeeping');

    result.v2.status = 'connected';
    result.v2.counts = {
      crimes: crimesCount.rows[0].count,
      accused: accusedCount.rows[0].count,
      persons: personsCount.rows[0].count,
      arrests: arrestsCount.rows[0].count,
      interrogation_reports: irCount.rows[0].count,
      chargesheets: chargesheetsCount.rows[0].count,
      mo_seizures: seizuresCount.rows[0].count,
      properties: propertiesCount.rows[0].count,
      media_files: mediaCount.rows[0].count,
    };
  } catch (err) {
    result.v2.status = 'error';
    result.v2.error = err.message;
  }

  res.json(result);
});

module.exports = router;
