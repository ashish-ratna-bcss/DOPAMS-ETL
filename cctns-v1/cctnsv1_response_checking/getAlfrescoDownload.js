// ==============================================================================
// 5. Alfresco Document Download API - GET Method
// Uses environment variable: ALFRESCO_DOWNLOAD_API_URL, MEDIA_BASE_DIR
// ==============================================================================

const fs = require('fs');
const path = require('path');

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
// Configuration from environment
// ------------------------------------------------------------------------------
const ALFRESCO_DOWNLOAD_API_URL = process.env.ALFRESCO_DOWNLOAD_API_URL;
const MEDIA_BASE_DIR = process.env.MEDIA_BASE_DIR;

/**
 * Downloads a document/file using HTTP GET method from ALFRESCO_DOWNLOAD_API_URL
 * and saves it into the structured folder path on disk.
 *
 * Folder structure on disk: <baseDir>/<path>/<name>
 *
 * @param {Object} params - Parameters for the download request.
 * @param {string} params.path - Document path in Alfresco (e.g. 'FIR/2032/2032014/2021/01/311220202032014996')
 * @param {string} params.name - File name with extension (e.g. '2021-01-01_01.05.35.181.0.80140522.pdf')
 * @param {string} [params.baseDir] - Base root directory (defaults to MEDIA_BASE_DIR env var)
 * @param {string} [params.outputPath] - Optional explicit output file path override
 * @param {boolean} [params.overwrite=false] - If true, re-downloads even if file already exists
 * @param {Object} [customHeaders={}] - Optional custom request headers
 * @returns {Promise<{
 *   ok: boolean,
 *   status: number,
 *   statusText: string,
 *   contentType: string,
 *   contentLength: number,
 *   filename: string,
 *   data?: Buffer,
 *   savedPath?: string,
 *   isCached?: boolean
 * }>}
 */
async function getAlfrescoDownload(params = {}, customHeaders = {}) {
  const endpoint = process.env.ALFRESCO_DOWNLOAD_API_URL;

  if (!endpoint) {
    throw new Error('ALFRESCO_DOWNLOAD_API_URL is not set. Please add ALFRESCO_DOWNLOAD_API_URL to your .env file.');
  }

  const {
    path: docPath,
    name: docName,
    baseDir = process.env.MEDIA_BASE_DIR,
    outputPath,
    overwrite = false,
    ...restParams
  } = params;

  // Determine local destination file path
  let targetFilePath = outputPath;
  if (!targetFilePath && baseDir && docPath && docName) {
    targetFilePath = path.join(baseDir, docPath, docName);
  }

  // If file already exists and overwrite is false, skip re-download
  if (targetFilePath && !overwrite && fs.existsSync(targetFilePath)) {
    const stats = fs.statSync(targetFilePath);
    if (stats.size > 0) {
      return {
        ok: true,
        status: 200,
        statusText: 'Cached',
        contentType: 'application/pdf',
        contentLength: stats.size,
        filename: docName || path.basename(targetFilePath),
        savedPath: path.resolve(targetFilePath),
        isCached: true,
      };
    }
  }

  const url = new URL(endpoint);

  if (docPath) {
    url.searchParams.append('path', docPath);
  }
  if (docName) {
    url.searchParams.append('name', docName);
  }

  // Append any extra query parameters dynamically
  Object.entries(restParams).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.append(key, String(value));
    }
  });

  const headers = {
    ...customHeaders,
  };

  if (process.env.AUTH_TOKEN) {
    headers['Authorization'] = `Bearer ${process.env.AUTH_TOKEN}`;
  } else if (process.env.API_KEY) {
    headers['x-api-key'] = process.env.API_KEY;
  }

  console.log(`\n[ALFRESCO_DOWNLOAD_API_URL] Sending GET request to: ${url.toString()}`);

  try {
    const response = await fetch(url.toString(), {
      method: 'GET',
      headers,
    });

    const contentType = response.headers.get('content-type') || '';
    const contentLength = parseInt(response.headers.get('content-length') || '0', 10);
    const contentDisposition = response.headers.get('content-disposition') || '';

    // Extract filename from header or fallback to param
    let filename = docName || 'downloaded_file';
    if (contentDisposition) {
      const match = contentDisposition.match(/filename=["']?([^"';]+)["']?/i);
      if (match && match[1]) {
        filename = match[1].trim();
      }
    }

    if (!response.ok) {
      let errorBody = '';
      try {
        errorBody = await response.text();
      } catch (_) {}
      throw new Error(`HTTP Error ${response.status} (${response.statusText}): ${errorBody}`);
    }

    const arrayBuffer = await response.arrayBuffer();
    const buffer = Buffer.from(arrayBuffer);

    const result = {
      ok: response.ok,
      status: response.status,
      statusText: response.statusText,
      contentType,
      contentLength: buffer.length || contentLength,
      filename,
      data: buffer,
      isCached: false,
    };

    if (targetFilePath) {
      fs.mkdirSync(path.dirname(targetFilePath), { recursive: true });
      fs.writeFileSync(targetFilePath, buffer);
      result.savedPath = path.resolve(targetFilePath);
    }

    return result;
  } catch (error) {
    console.error('[ALFRESCO_DOWNLOAD_API_URL] GET Request failed:', error.message);
    throw error;
  }
}

// ------------------------------------------------------------------------------
// Run independently: node getAlfrescoDownload.js <path> <name> [baseDirOrOutputPath]
// ------------------------------------------------------------------------------
if (require.main === module) {
  (async () => {
    try {
      const cliArgs = process.argv.slice(2);
      const docPath = cliArgs[0];
      const docName = cliArgs[1];
      const customPath = cliArgs[2];

      if (!docPath || !docName) {
        console.error('Usage: node getAlfrescoDownload.js <path> <name> [baseDir]');
        console.error('Example: node getAlfrescoDownload.js "FIR/2032/2032014/2021/01/311220202032014996" "2021-01-01_01.05.35.181.0.80140522.pdf"');
        process.exit(1);
      }

      console.log('Fetching document from Alfresco (GET)...');
      console.log(`Path: ${docPath}`);
      console.log(`Name: ${docName}`);

      const result = await getAlfrescoDownload({
        path: docPath,
        name: docName,
        baseDir: customPath || process.env.MEDIA_BASE_DIR || __dirname,
      });

      console.log('\n Successfully processed document:');
      console.table([
        {
          Status: `${result.status} ${result.statusText}`,
          'Content-Type': result.contentType,
          'File Size (Bytes)': result.contentLength,
          Filename: result.filename,
          'Cached (Skipped Download)': result.isCached ? 'YES' : 'NO',
          'Saved Path': result.savedPath || 'Memory buffer only',
        },
      ]);
    } catch (err) {
      console.error('Execution finished with error:', err.message);
      process.exit(1);
    }
  })();
}

module.exports = {
  getAlfrescoDownload,
  downloadAlfrescoDocument: getAlfrescoDownload,
  ALFRESCO_DOWNLOAD_API_URL,
  MEDIA_BASE_DIR,
};
