const express = require('express');
const router = express.Router();
const { queryV1 } = require('../db');

// GET /api/v1/firs - Paginated FIR listing with search & filters
router.get('/firs', async (req, res) => {
  try {
    const page = Math.max(1, parseInt(req.query.page) || 1);
    const limit = Math.min(100, Math.max(5, parseInt(req.query.limit) || 25));
    const offset = (page - 1) * limit;

    const { search, district, ps, year, status } = req.query;

    let whereClauses = [];
    let values = [];
    let paramIdx = 1;

    if (search && search.trim()) {
      const s = `%${search.trim()}%`;
      whereClauses.push(`(
        f.fir_reg_num ILIKE $${paramIdx} OR 
        f.fir_no ILIKE $${paramIdx} OR 
        f.ps_name ILIKE $${paramIdx} OR 
        f.unit ILIKE $${paramIdx} OR
        f.section_of_law ILIKE $${paramIdx} OR
        f.fir_contents ILIKE $${paramIdx}
      )`);
      values.push(s);
      paramIdx++;
    }

    if (district && district.trim()) {
      whereClauses.push(`f.unit = $${paramIdx}`);
      values.push(district.trim());
      paramIdx++;
    }

    if (ps && ps.trim()) {
      whereClauses.push(`f.ps_name = $${paramIdx}`);
      values.push(ps.trim());
      paramIdx++;
    }

    if (year && !isNaN(parseInt(year))) {
      whereClauses.push(`f.reg_year = $${paramIdx}`);
      values.push(parseInt(year));
      paramIdx++;
    }

    if (status && status.trim()) {
      whereClauses.push(`f.fir_status ILIKE $${paramIdx}`);
      values.push(`%${status.trim()}%`);
      paramIdx++;
    }

    const whereSql = whereClauses.length > 0 ? `WHERE ${whereClauses.join(' AND ')}` : '';

    // Count query
    const countSql = `SELECT COUNT(*)::int as total FROM cctns_fir f ${whereSql}`;
    const countRes = await queryV1(countSql, values);
    const total = countRes.rows[0]?.total || 0;

    // Data query with linked counts
    const dataSql = `
      SELECT 
        f.*,
        (SELECT COUNT(*)::int FROM cctns_accused a WHERE a.fir_reg_num = f.fir_reg_num) as accused_count,
        (SELECT COUNT(*)::int FROM cctns_accused_details ad WHERE ad.fir_reg_num = f.fir_reg_num) as accused_details_count,
        (SELECT COUNT(*)::int FROM cctns_court c WHERE c.fir_reg_num = f.fir_reg_num) as court_records_count
      FROM cctns_fir f
      ${whereSql}
      ORDER BY f.reg_dt DESC NULLS LAST, f.fir_reg_num DESC
      LIMIT $${paramIdx} OFFSET $${paramIdx + 1}
    `;

    values.push(limit, offset);
    const dataRes = await queryV1(dataSql, values);

    res.json({
      page,
      limit,
      total,
      totalPages: Math.ceil(total / limit),
      data: dataRes.rows,
    });
  } catch (err) {
    console.error('Error fetching V1 FIRs:', err);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/v1/fir/:fir_reg_num - Complete FIR Dossier with all linked sub-tables
router.get('/fir/:fir_reg_num', async (req, res) => {
  try {
    const { fir_reg_num } = req.params;

    // 1. Master FIR record
    const firRes = await queryV1('SELECT * FROM cctns_fir WHERE fir_reg_num = $1', [fir_reg_num]);
    if (firRes.rows.length === 0) {
      return res.status(404).json({ error: 'FIR not found in CCTNS V1' });
    }
    const fir = firRes.rows[0];

    // 2. Linked cctns_accused (flat wide dossier with all 60 interrogation-relative columns)
    const accusedRes = await queryV1(
      'SELECT * FROM cctns_accused WHERE fir_reg_num = $1 ORDER BY accused_id ASC',
      [fir_reg_num]
    );

    // 3. Linked cctns_accused_details (secondary accused table)
    const accusedDetailsRes = await queryV1(
      'SELECT * FROM cctns_accused_details WHERE fir_reg_num = $1 ORDER BY accused_id ASC',
      [fir_reg_num]
    );

    // 4. Linked cctns_court (chargesheet & disposal details)
    const courtRes = await queryV1(
      'SELECT * FROM cctns_court WHERE fir_reg_num = $1 ORDER BY court_id ASC',
      [fir_reg_num]
    );

    res.json({
      fir,
      accused: accusedRes.rows,
      accused_details: accusedDetailsRes.rows,
      court: courtRes.rows,
    });
  } catch (err) {
    console.error('Error fetching V1 FIR details:', err);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/v1/districts - Unique units and police stations for filters
router.get('/districts', async (req, res) => {
  try {
    const distRes = await queryV1(`
      SELECT DISTINCT unit as district, ps_name 
      FROM cctns_fir 
      WHERE unit IS NOT NULL AND ps_name IS NOT NULL
      ORDER BY unit, ps_name
    `);
    
    const hierarchy = {};
    distRes.rows.forEach(r => {
      if (!hierarchy[r.district]) hierarchy[r.district] = [];
      if (!hierarchy[r.district].includes(r.ps_name)) {
        hierarchy[r.district].push(r.ps_name);
      }
    });

    res.json(hierarchy);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /api/v1/media/pdf - Stream PDF directly for inline browser rendering or download
router.get('/media/pdf', async (req, res) => {
  try {
    const { path: attach_path, name: dms_file_name, download } = req.query;

    if (!attach_path || !dms_file_name) {
      return res.status(400).json({ error: 'Missing path or name query parameter' });
    }

    const fs = require('fs');
    const path = require('path');
    const http = require('http');

    const cleanPath = attach_path.trim().replace(/^[\/\\]+/, '');
    const cleanName = dms_file_name.trim().replace(/^[\/\\]+/, '');

    // 1. Check server disk path
    const baseDir = process.env.V1_MEDIA_BASE_DIR || '/home/tganb/dopams/media_cctnsv1';
    const fullDiskPath = path.join(baseDir, cleanPath, cleanName);

    const isPdf = cleanName.toLowerCase().endsWith('.pdf');
    const contentType = isPdf ? 'application/pdf' : 'image/jpeg';
    const disposition = download === '1' || download === 'true' ? 'attachment' : 'inline';

    if (fs.existsSync(fullDiskPath) && fs.statSync(fullDiskPath).size > 0) {
      res.setHeader('Content-Type', contentType);
      res.setHeader('Content-Disposition', `${disposition}; filename="${cleanName}"`);
      return fs.createReadStream(fullDiskPath).pipe(res);
    }

    // 2. Fetch directly from live Alfresco DMS endpoint
    const alfrescoBase = process.env.ALFRESCO_DOWNLOAD_API_URL || 'http://103.164.200.184/alfresco/download';
    const targetUrl = `${alfrescoBase}?path=${encodeURIComponent(cleanPath)}&name=${encodeURIComponent(cleanName)}`;

    const fetchReq = http.get(targetUrl, { timeout: 15000 }, (remoteRes) => {
      if (remoteRes.statusCode === 200) {
        res.setHeader('Content-Type', contentType);
        res.setHeader('Content-Disposition', `${disposition}; filename="${cleanName}"`);
        if (remoteRes.headers['content-length']) {
          res.setHeader('Content-Length', remoteRes.headers['content-length']);
        }
        remoteRes.pipe(res);
      } else {
        // Fallback: If blocked by FortiGuard/local firewall (HTTP 403) or not found, try proxying via dopams-182 server
        const fallbackServer = process.env.V1_FALLBACK_SERVER || 'http://192.168.103.182:5001';
        if (remoteRes.statusCode === 403 && !req.headers['x-forwarded-from-182']) {
          const fallbackUrl = `${fallbackServer}/api/v1/media/pdf?path=${encodeURIComponent(cleanPath)}&name=${encodeURIComponent(cleanName)}`;
          http.get(fallbackUrl, { timeout: 15000, headers: { 'x-forwarded-from-182': '1' } }, (fallbackRes) => {
            if (fallbackRes.statusCode === 200) {
              res.setHeader('Content-Type', contentType);
              res.setHeader('Content-Disposition', `${disposition}; filename="${cleanName}"`);
              return fallbackRes.pipe(res);
            }
            res.status(remoteRes.statusCode || 404).json({
              error: `Document not found on Alfresco DMS server (HTTP ${remoteRes.statusCode})`,
              path: cleanPath,
              name: cleanName,
            });
          }).on('error', () => {
            res.status(remoteRes.statusCode || 404).json({
              error: `Document not found on Alfresco DMS server (HTTP ${remoteRes.statusCode})`,
              path: cleanPath,
              name: cleanName,
            });
          });
        } else {
          res.status(remoteRes.statusCode || 404).json({
            error: `Document not found on Alfresco DMS server (HTTP ${remoteRes.statusCode})`,
            path: cleanPath,
            name: cleanName,
          });
        }
      }
    });

    fetchReq.on('error', (err) => {
      console.error('Error proxying PDF from Alfresco:', err.message);
      res.status(502).json({ error: 'Failed to retrieve document from Alfresco service: ' + err.message });
    });

    fetchReq.on('timeout', () => {
      fetchReq.destroy();
      res.status(504).json({ error: 'Alfresco download request timed out' });
    });
  } catch (err) {
    console.error('PDF Stream error:', err);
    res.status(500).json({ error: err.message });
  }
});

// GET /api/v1/media/stream - Stream V1 PDF document or return metadata
router.get('/media/stream', async (req, res) => {
  try {
    const { path: attach_path, name: dms_file_name, fir_reg_num, stream } = req.query;

    if (!attach_path || !dms_file_name) {
      return res.status(400).json({ error: 'Missing path or name query parameter' });
    }

    if (stream === 'true' || stream === '1') {
      return res.redirect(`/api/v1/media/pdf?path=${encodeURIComponent(attach_path)}&name=${encodeURIComponent(dms_file_name)}`);
    }

    const cleanPath = attach_path.trim().replace(/^[\/\\]+/, '');
    const cleanName = dms_file_name.trim().replace(/^[\/\\]+/, '');
    const baseDir = process.env.V1_MEDIA_BASE_DIR || '/home/tganb/dopams/media_cctnsv1';
    const fullDiskPath = `${baseDir}/${cleanPath}/${cleanName}`;

    // 1. Check if media status is in cctns.cctns_media_files
    let mediaRecord = null;
    try {
      const mediaDbRes = await queryV1(
        'SELECT * FROM cctns.cctns_media_files WHERE attach_path = $1 AND dms_file_name = $2 LIMIT 1',
        [cleanPath, cleanName]
      );
      if (mediaDbRes.rows.length > 0) {
        mediaRecord = mediaDbRes.rows[0];
      }
    } catch (e) {
      // Table might not exist yet
    }

    // 2. Return file info and live direct stream URL
    res.json({
      entity_type: 'FIR',
      fir_reg_num: fir_reg_num || 'N/A',
      attach_path: cleanPath,
      dms_file_name: cleanName,
      server_storage_path: fullDiskPath,
      storage_server: 'dopams-new (192.168.103.106)',
      stream_url: `/api/v1/media/pdf?path=${encodeURIComponent(cleanPath)}&name=${encodeURIComponent(cleanName)}`,
      status: mediaRecord?.status || 'CONFIGURED_ON_SERVER',
      downloaded_at: mediaRecord?.downloaded_at || null,
      file_size: mediaRecord?.file_size_bytes || null,
      mime_type: cleanName.endsWith('.pdf') ? 'application/pdf' : 'image/jpeg',
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
