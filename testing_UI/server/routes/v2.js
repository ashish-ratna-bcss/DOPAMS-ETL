const express = require('express');
const router = express.Router();
const { queryV2 } = require('../db');

// GET /api/v2/crimes - Paginated Crimes list with linked entity counts
router.get('/crimes', async (req, res) => {
  try {
    const page = Math.max(1, parseInt(req.query.page) || 1);
    const limit = Math.min(100, Math.max(5, parseInt(req.query.limit) || 25));
    const offset = (page - 1) * limit;

    const { search, ps_code, status, year, major_head } = req.query;

    let whereClauses = [];
    let values = [];
    let paramIdx = 1;

    if (search && search.trim()) {
      const s = `%${search.trim()}%`;
      whereClauses.push(`(
        c.crime_id ILIKE $${paramIdx} OR 
        c.fir_reg_num ILIKE $${paramIdx} OR 
        c.fir_num ILIKE $${paramIdx} OR 
        c.ps_code ILIKE $${paramIdx} OR 
        c.acts_sections ILIKE $${paramIdx} OR 
        c.brief_facts ILIKE $${paramIdx} OR
        c.io_name ILIKE $${paramIdx}
      )`);
      values.push(s);
      paramIdx++;
    }

    if (ps_code && ps_code.trim()) {
      whereClauses.push(`c.ps_code = $${paramIdx}`);
      values.push(ps_code.trim());
      paramIdx++;
    }

    if (status && status.trim()) {
      whereClauses.push(`c.case_status ILIKE $${paramIdx}`);
      values.push(`%${status.trim()}%`);
      paramIdx++;
    }

    if (year && !isNaN(parseInt(year))) {
      whereClauses.push(`EXTRACT(YEAR FROM c.fir_date) = $${paramIdx}`);
      values.push(parseInt(year));
      paramIdx++;
    }

    if (major_head && major_head.trim()) {
      whereClauses.push(`c.major_head ILIKE $${paramIdx}`);
      values.push(`%${major_head.trim()}%`);
      paramIdx++;
    }

    const whereSql = whereClauses.length > 0 ? `WHERE ${whereClauses.join(' AND ')}` : '';

    // Total count query
    const countSql = `SELECT COUNT(*)::int as total FROM crimes c ${whereSql}`;
    const countRes = await queryV2(countSql, values);
    const total = countRes.rows[0]?.total || 0;

    // Data query with linked counts
    const dataSql = `
      SELECT 
        c.crime_id,
        c.ps_code,
        c.fir_num,
        c.fir_reg_num,
        c.fir_type,
        c.acts_sections,
        c.fir_date,
        c.case_status,
        c.major_head,
        c.minor_head,
        c.crime_type,
        c.io_name,
        c.io_rank,
        c.brief_facts,
        c.date_created,
        c.date_modified,
        h.ps_name,
        h.dist_name as district_name,
        h.circle_name,
        (SELECT COUNT(*)::int FROM accused a WHERE a.crime_id = c.crime_id) as accused_count,
        (SELECT COUNT(*)::int FROM arrests ar WHERE ar.crime_id = c.crime_id) as arrests_count,
        (SELECT COUNT(*)::int FROM interrogation_reports ir WHERE ir.crime_id = c.crime_id) as ir_count,
        (SELECT COUNT(*)::int FROM chargesheets cs WHERE cs.crime_id = c.crime_id) as chargesheets_count,
        (SELECT COUNT(*)::int FROM mo_seizures ms WHERE ms.crime_id = c.crime_id) as seizures_count,
        (SELECT COUNT(*)::int FROM properties p WHERE p.crime_id = c.crime_id) as properties_count,
        (SELECT COUNT(*)::int FROM file_media_bookkeeping fmb WHERE fmb.parent_id = c.crime_id OR (fmb.source_type = 'crime' AND fmb.parent_id = c.crime_id)) as media_count
      FROM crimes c
      LEFT JOIN hierarchy h ON h.ps_code = c.ps_code
      ${whereSql}
      ORDER BY c.fir_date DESC NULLS LAST, c.crime_id DESC
      LIMIT $${paramIdx} OFFSET $${paramIdx + 1}
    `;

    values.push(limit, offset);
    const dataRes = await queryV2(dataSql, values);

    res.json({
      page,
      limit,
      total,
      totalPages: Math.ceil(total / limit),
      data: dataRes.rows,
    });
  } catch (err) {
    console.error('Error fetching V2 crimes:', err);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/v2/crime/:crime_id - Full multi-entity composite inspector
router.get('/crime/:crime_id', async (req, res) => {
  try {
    const { crime_id } = req.params;

    // 1. Crime Master Record
    const crimeRes = await queryV2(`
      SELECT c.*, h.ps_name, h.dist_name as district_name, h.circle_name, h.sdpo_name, h.range_name
      FROM crimes c
      LEFT JOIN hierarchy h ON h.ps_code = c.ps_code
      WHERE c.crime_id = $1
    `, [crime_id]);

    if (crimeRes.rows.length === 0) {
      return res.status(404).json({ error: 'Crime record not found in CCTNS V2' });
    }
    const crime = crimeRes.rows[0];

    // 2. Accused joined with Persons details
    const accusedRes = await queryV2(`
      SELECT 
        a.accused_id,
        a.crime_id,
        a.person_id,
        a.accused_code,
        a.type as accused_type,
        a.seq_num,
        a.is_ccl,
        a.accused_status,
        a.beard,
        a.build,
        a.color,
        a.ear,
        a.eyes,
        a.face,
        a.hair,
        a.height,
        a.leucoderma,
        a.mole,
        a.mustache,
        a.nose,
        a.teeth,
        a.date_created as accused_date_created,
        a.date_modified as accused_date_modified,
        p.name,
        p.surname,
        p.full_name,
        p.alias,
        p.relation_type,
        p.relative_name,
        p.gender,
        p.date_of_birth,
        p.age,
        p.occupation,
        p.education_qualification,
        p.caste,
        p.sub_caste,
        p.religion,
        p.nationality,
        p.phone_number,
        p.email_id,
        p.domicile_classification,
        p.present_house_no,
        p.present_street_road_no,
        p.present_ward_colony,
        p.present_locality_village,
        p.present_area_mandal,
        p.present_district,
        p.present_state_ut,
        p.present_country,
        p.present_pin_code,
        p.permanent_house_no,
        p.permanent_street_road_no,
        p.permanent_ward_colony,
        p.permanent_locality_village,
        p.permanent_area_mandal,
        p.permanent_district,
        p.permanent_state_ut,
        p.permanent_country,
        p.permanent_pin_code,
        (SELECT fmb.file_url FROM file_media_bookkeeping fmb WHERE fmb.parent_id = a.person_id AND fmb.source_type = 'person' LIMIT 1) as person_photo_url,
        (SELECT fmb.file_id FROM file_media_bookkeeping fmb WHERE fmb.parent_id = a.person_id AND fmb.source_type = 'person' LIMIT 1) as person_photo_file_id
      FROM accused a
      LEFT JOIN persons p ON p.person_id = a.person_id
      WHERE a.crime_id = $1
      ORDER BY a.seq_num ASC NULLS LAST, a.accused_id ASC
    `, [crime_id]);

    // 3. Arrests
    const arrestsRes = await queryV2(`
      SELECT ar.*, p.full_name as person_name
      FROM arrests ar
      LEFT JOIN persons p ON p.person_id = ar.person_id
      WHERE ar.crime_id = $1
      ORDER BY ar.arrested_date DESC NULLS LAST
    `, [crime_id]);

    // 4. Interrogation Reports
    const irRes = await queryV2(`
      SELECT ir.*, p.full_name as subject_name
      FROM interrogation_reports ir
      LEFT JOIN persons p ON p.person_id = ir.person_id
      WHERE ir.crime_id = $1
      ORDER BY ir.interrogation_report_id ASC
    `, [crime_id]);

    // 5. Chargesheets
    const csRes = await queryV2(`
      SELECT cs.*
      FROM chargesheets cs
      WHERE cs.crime_id = $1
      ORDER BY cs.chargesheet_date DESC NULLS LAST
    `, [crime_id]);

    // 6. Charge Sheet Updates
    const csUpdatesRes = await queryV2(`
      SELECT csu.*
      FROM charge_sheet_updates csu
      WHERE csu.crime_id = $1
      ORDER BY csu.charge_sheet_date DESC NULLS LAST
    `, [crime_id]);

    // 7. MO Seizures
    const seizuresRes = await queryV2(`
      SELECT ms.*
      FROM mo_seizures ms
      WHERE ms.crime_id = $1
      ORDER BY ms.seized_at DESC NULLS LAST
    `, [crime_id]);

    // 8. FSL Case Property
    const fslRes = await queryV2(`
      SELECT fsl.*
      FROM fsl_case_property fsl
      WHERE fsl.crime_id = $1
      ORDER BY fsl.send_date DESC NULLS LAST
    `, [crime_id]);

    // 9. Properties
    const propertiesRes = await queryV2(`
      SELECT p.*
      FROM properties p
      WHERE p.crime_id = $1
      ORDER BY p.date_of_seizure DESC NULLS LAST
    `, [crime_id]);

    // 10. Disposal
    const disposalRes = await queryV2(`
      SELECT d.*
      FROM disposal d
      WHERE d.crime_id = $1
      ORDER BY d.disposed_at DESC NULLS LAST
    `, [crime_id]);

    // 11. Attached Media from file_media_bookkeeping (only records with actual files)
    const mediaRes = await queryV2(`
      SELECT *
      FROM file_media_bookkeeping
      WHERE (
        parent_id = $1 
        OR (source_type = 'crime' AND parent_id = $1)
        OR parent_id IN (SELECT person_id FROM accused WHERE crime_id = $1)
        OR parent_id IN (SELECT mo_seizure_id FROM mo_seizures WHERE crime_id = $1)
        OR parent_id IN (SELECT charge_sheet_id FROM chargesheets WHERE crime_id = $1)
        OR parent_id IN (SELECT property_id FROM properties WHERE crime_id = $1)
        OR file_id::text IN (SELECT fir_copy FROM crimes WHERE crime_id = $1 AND fir_copy IS NOT NULL)
      )
      AND (is_empty = false OR file_id IS NOT NULL OR file_url IS NOT NULL OR file_path IS NOT NULL OR media_url IS NOT NULL)
      ORDER BY is_downloaded DESC, created_at DESC NULLS LAST
    `, [crime_id]);

    res.json({
      crime,
      accused: accusedRes.rows,
      arrests: arrestsRes.rows,
      interrogation_reports: irRes.rows,
      chargesheets: csRes.rows,
      charge_sheet_updates: csUpdatesRes.rows,
      mo_seizures: seizuresRes.rows,
      fsl_case_property: fslRes.rows,
      properties: propertiesRes.rows,
      disposal: disposalRes.rows,
      media: mediaRes.rows,
    });
  } catch (err) {
    console.error('Error fetching V2 crime full details:', err);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/v2/hierarchy - Hierarchy list
router.get('/hierarchy', async (req, res) => {
  try {
    const hierRes = await queryV2(`
      SELECT DISTINCT dist_name as district_name, ps_code, ps_name, circle_name, sdpo_name
      FROM hierarchy
      WHERE dist_name IS NOT NULL AND ps_code IS NOT NULL
      ORDER BY dist_name, ps_name
    `);
    res.json(hierRes.rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
