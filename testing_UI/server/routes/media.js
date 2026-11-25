const express = require('express');
const router = express.Router();
const fs = require('fs');
const path = require('path');
const http = require('http');
const { queryV2 } = require('../db');

// Helper to sanitize remote file URL
function getRemoteFileUrl(media) {
  const v2Host = process.env.V2_MEDIA_HOST || '192.168.103.106:8080';

  if (media.file_url && media.file_url.trim()) {
    return media.file_url.replace(/localhost:8080/g, v2Host).replace(/127\.0\.0\.1:8080/g, v2Host);
  }

  if (media.file_path && media.file_path.trim()) {
    const cleanPath = media.file_path.trim().replace(/^\/+/, '');
    return `http://${v2Host}/files/${cleanPath}`;
  }

  if (media.media_url && media.media_url.trim()) {
    return media.media_url.replace(/localhost:8080/g, v2Host).replace(/127\.0\.0\.1:8080/g, v2Host);
  }

  return null;
}

// GET /api/media/info/:file_id - Get media metadata
router.get('/info/:file_id', async (req, res) => {
  try {
    const { file_id } = req.params;
    const result = await queryV2(
      'SELECT * FROM file_media_bookkeeping WHERE file_id::text = $1 OR id::text = $1 OR parent_id = $1 LIMIT 1',
      [file_id]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Media record not found in CCTNS V2' });
    }

    const media = result.rows[0];
    const remoteUrl = getRemoteFileUrl(media);

    res.json({
      ...media,
      resolved_remote_url: remoteUrl,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /api/media/stream/:file_id - Stream media content directly
router.get('/stream/:file_id', async (req, res) => {
  try {
    const { file_id } = req.params;
    const { download } = req.query;

    const result = await queryV2(
      'SELECT * FROM file_media_bookkeeping WHERE file_id::text = $1 OR id::text = $1 OR parent_id = $1 LIMIT 1',
      [file_id]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'File record not found in media database' });
    }

    const media = result.rows[0];
    const fileName = media.media_name || media.file_name || (media.file_path ? path.basename(media.file_path) : 'file');
    const ext = path.extname(fileName).toLowerCase().replace('.', '') || (media.file_url?.includes('.docx') ? 'docx' : (media.file_url?.includes('.pdf') ? 'pdf' : 'jpg'));

    const mimeMap = {
      pdf: 'application/pdf',
      docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      doc: 'application/msword',
      xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      xls: 'application/vnd.ms-excel',
      jpg: 'image/jpeg',
      jpeg: 'image/jpeg',
      png: 'image/png',
      webp: 'image/webp',
      gif: 'image/gif',
      zip: 'application/zip',
      txt: 'text/plain',
    };

    const contentType = mimeMap[ext] || (media.source_field === 'FIR_COPY' ? 'application/pdf' : 'application/octet-stream');
    const disposition = download === '1' || download === 'true' || ext === 'docx' || ext === 'doc' ? 'attachment' : 'inline';
    const cleanFilename = fileName.endsWith(`.${ext}`) ? fileName : `${fileName}.${ext}`;

    // 1. Check local physical disk first (e.g. /mnt/shared-etl-files on dopams-182)
    const baseDir = process.env.V2_MEDIA_BASE_DIR || '/mnt/shared-etl-files';
    const cleanRelPath = (media.file_path || '').trim().replace(/^\/+/, '');
    
    let localDiskPath = path.join(baseDir, cleanRelPath);
    if (!fs.existsSync(localDiskPath) || fs.statSync(localDiskPath).isDirectory()) {
      if (fs.existsSync(`${localDiskPath}.${ext}`)) {
        localDiskPath = `${localDiskPath}.${ext}`;
      } else if (fs.existsSync(`${localDiskPath}.docx`)) {
        localDiskPath = `${localDiskPath}.docx`;
      } else if (fs.existsSync(`${localDiskPath}.pdf`)) {
        localDiskPath = `${localDiskPath}.pdf`;
      }
    }

    if (fs.existsSync(localDiskPath) && !fs.statSync(localDiskPath).isDirectory() && fs.statSync(localDiskPath).size > 0) {
      res.setHeader('Content-Type', contentType);
      res.setHeader('Content-Disposition', `${disposition}; filename="${cleanFilename}"`);
      res.setHeader('X-Media-Source', 'LOCAL_DISK');
      res.setHeader('X-Media-Origin-Path', localDiskPath);
      return fs.createReadStream(localDiskPath).pipe(res);
    }

    // 2. Fetch directly from remote V2 file server on dopams-new
    let remoteUrl = getRemoteFileUrl(media);

    if (!remoteUrl) {
      return res.status(404).json({
        error: 'No file attachment URL available for this record in CCTNS V2',
        mediaInfo: media,
      });
    }

    // Direct proxy stream
    const proxyReq = http.get(remoteUrl, { timeout: 15000 }, (remoteRes) => {
      if (remoteRes.statusCode === 200) {
        res.setHeader('Content-Type', contentType);
        res.setHeader('Content-Disposition', `${disposition}; filename="${cleanFilename}"`);
        res.setHeader('X-Media-Source', 'REMOTE_STORAGE');
        res.setHeader('X-Media-Origin-Path', remoteUrl);
        if (remoteRes.headers['content-length']) {
          res.setHeader('Content-Length', remoteRes.headers['content-length']);
        }
        remoteRes.pipe(res);
      } else {
        // Fallback: If URL doesn't have extension, try with original extension appended
        const fallbackExt = ext ? `.${ext}` : '.docx';
        if (!remoteUrl.endsWith(fallbackExt)) {
          const fallbackUrl = `${remoteUrl}${fallbackExt}`;
          http.get(fallbackUrl, { timeout: 15000 }, (fallbackRes) => {
            if (fallbackRes.statusCode === 200) {
              res.setHeader('Content-Type', contentType);
              res.setHeader('Content-Disposition', `${disposition}; filename="${cleanFilename}"`);
              return fallbackRes.pipe(res);
            }
            res.status(remoteRes.statusCode || 404).json({
              error: `Media file not found on V2 file server (HTTP ${remoteRes.statusCode})`,
              url: remoteUrl,
            });
          }).on('error', (err) => {
            res.status(502).json({ error: 'Failed to retrieve media: ' + err.message });
          });
        } else {
          res.status(remoteRes.statusCode || 404).json({
            error: `Media file not found on V2 file server (HTTP ${remoteRes.statusCode})`,
            url: remoteUrl,
          });
        }
      }
    });

    proxyReq.on('error', (err) => {
      console.error('Error fetching V2 media:', err.message);
      res.status(502).json({ error: 'Failed to fetch media file from V2 storage: ' + err.message });
    });

    proxyReq.on('timeout', () => {
      proxyReq.destroy();
      res.status(504).json({ error: 'V2 Media download request timed out' });
    });

  } catch (err) {
    console.error('V2 Media Stream error:', err);
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
