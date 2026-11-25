const express = require('express');
const router = express.Router();
const { queryV1, queryV2 } = require('../db');

// GET /api/compare/:query - Fetch matching records from both V1 and V2
router.get('/:query', async (req, res) => {
  try {
    const { query } = req.params;
    const term = query.trim();

    // 1. Fetch from V1
    let v1Data = null;
    const v1FirRes = await queryV1(`
      SELECT * FROM cctns_fir 
      WHERE fir_reg_num = $1 OR fir_no = $1
      LIMIT 1
    `, [term]);

    if (v1FirRes.rows.length > 0) {
      const fir = v1FirRes.rows[0];
      const accused = await queryV1('SELECT * FROM cctns_accused WHERE fir_reg_num = $1', [fir.fir_reg_num]);
      const accused_details = await queryV1('SELECT * FROM cctns_accused_details WHERE fir_reg_num = $1', [fir.fir_reg_num]);
      const court = await queryV1('SELECT * FROM cctns_court WHERE fir_reg_num = $1', [fir.fir_reg_num]);

      v1Data = {
        fir,
        accused: accused.rows,
        accused_details: accused_details.rows,
        court: court.rows,
      };
    }

    // 2. Fetch from V2
    let v2Data = null;
    const v2CrimeRes = await queryV2(`
      SELECT c.*, h.ps_name, h.dist_name as district_name
      FROM crimes c
      LEFT JOIN hierarchy h ON h.ps_code = c.ps_code
      WHERE c.fir_reg_num = $1 OR c.fir_num = $1 OR c.crime_id = $1
      LIMIT 1
    `, [term]);

    if (v2CrimeRes.rows.length > 0) {
      const crime = v2CrimeRes.rows[0];
      const crime_id = crime.crime_id;

      const accused = await queryV2(`
        SELECT a.*, p.full_name, p.gender, p.age, p.mobile_number, p.domicile_classification, p.present_city
        FROM accused a
        LEFT JOIN persons p ON p.person_id = a.person_id
        WHERE a.crime_id = $1
      `, [crime_id]);

      const arrests = await queryV2('SELECT * FROM arrests WHERE crime_id = $1', [crime_id]);
      const ir = await queryV2('SELECT * FROM interrogation_reports WHERE crime_id = $1', [crime_id]);
      const chargesheets = await queryV2('SELECT * FROM chargesheets WHERE crime_id = $1', [crime_id]);
      const seizures = await queryV2('SELECT * FROM mo_seizures WHERE crime_id = $1', [crime_id]);
      const properties = await queryV2('SELECT * FROM properties WHERE crime_id = $1', [crime_id]);
      const media = await queryV2('SELECT * FROM file_media_bookkeeping WHERE parent_id = $1', [crime_id]);

      v2Data = {
        crime,
        accused: accused.rows,
        arrests: arrests.rows,
        interrogation_reports: ir.rows,
        chargesheets: chargesheets.rows,
        mo_seizures: seizures.rows,
        properties: properties.rows,
        media: media.rows,
      };
    }

    res.json({
      query: term,
      v1: v1Data,
      v2: v2Data,
      foundInV1: !!v1Data,
      foundInV2: !!v2Data,
    });
  } catch (err) {
    console.error('Error in compare:', err);
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
