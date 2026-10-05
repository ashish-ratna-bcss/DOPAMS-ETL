// ==============================================================================
// Media Synchronization CLI / Helper (Node.js)
// ==============================================================================

const fs = require('fs');
const path = require('path');
const { getAlfrescoDownload } = require('./getAlfrescoDownload');

// Load environment
if (typeof process.loadEnvFile === 'function') {
  try {
    process.loadEnvFile();
  } catch (err) {}
} else {
  try {
    require('dotenv').config();
  } catch (err) {}
}

const MEDIA_BASE_DIR = process.env.MEDIA_BASE_DIR || path.join(__dirname, 'media_downloads');

/**
 * Downloads a list of media attachment records concurrently.
 *
 * @param {Array<{path: string, name: string}>} items
 * @param {number} [concurrency=5]
 */
async function syncMediaBatch(items = [], concurrency = 5) {
  console.log(`\n=== Starting Media Batch Sync (${items.length} items) ===`);
  console.log(`Base Media Directory: ${MEDIA_BASE_DIR}`);
  console.log(`Concurrency: ${concurrency}\n`);

  const results = {
    total: items.length,
    downloaded: 0,
    cached: 0,
    failed: 0,
  };

  let index = 0;
  const workers = Array(Math.min(concurrency, items.length || 1))
    .fill(null)
    .map(async () => {
      while (index < items.length) {
        const currentIndex = index++;
        const item = items[currentIndex];

        try {
          const res = await getAlfrescoDownload({
            path: item.path,
            name: item.name,
            baseDir: MEDIA_BASE_DIR,
          });

          if (res.isCached) {
            results.cached++;
            console.log(`[${currentIndex + 1}/${items.length}]  CACHED: ${item.name}`);
          } else {
            results.downloaded++;
            console.log(`[${currentIndex + 1}/${items.length}]  DOWNLOADED: ${item.name} (${res.contentLength} bytes)`);
          }
        } catch (err) {
          results.failed++;
          console.error(`[${currentIndex + 1}/${items.length}] ❌ FAILED: ${item.name} - ${err.message}`);
        }
      }
    });

  await Promise.all(workers);

  console.log('\n=== Media Batch Sync Completed ===');
  console.table([results]);
  return results;
}

// ------------------------------------------------------------------------------
// Run independently: node syncMedia.js [optional_json_file_or_single_path]
// ------------------------------------------------------------------------------
if (require.main === module) {
  (async () => {
    const args = process.argv.slice(2);

    let itemsToProcess = [];

    if (args.length >= 2) {
      itemsToProcess = [{ path: args[0], name: args[1] }];
    } else if (args.length === 1 && fs.existsSync(args[0])) {
      const content = JSON.parse(fs.readFileSync(args[0], 'utf-8'));
      const rawRecords = content.data || (Array.isArray(content) ? content : [content]);
      itemsToProcess = rawRecords
        .filter((r) => (r.ATTACH_PATH || r.attach_path) && (r.DMS_FILE_NAME || r.dms_file_name))
        .map((r) => ({
          path: r.ATTACH_PATH || r.attach_path,
          name: r.DMS_FILE_NAME || r.dms_file_name,
        }));
    } else {
      // Default sample
      itemsToProcess = [
        {
          path: 'FIR/2032/2032014/2021/01/311220202032014996',
          name: '2021-01-01_01.05.35.181.0.80140522.pdf',
        },
      ];
    }

    await syncMediaBatch(itemsToProcess);
  })();
}

module.exports = {
  syncMediaBatch,
};
