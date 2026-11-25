const express = require('express');
const cors = require('cors');
const path = require('path');
const dotenv = require('dotenv');

dotenv.config({ path: path.join(__dirname, '..', '.env') });

const app = express();
const PORT = process.env.PORT || 5001;

// Middlewares
app.use(cors());
app.use(express.json());

// Routes
const healthRoutes = require('./routes/health');
const v1Routes = require('./routes/v1');
const v2Routes = require('./routes/v2');
const mediaRoutes = require('./routes/media');
const compareRoutes = require('./routes/compare');

app.use('/api/health', healthRoutes);
app.use('/api/v1', v1Routes);
app.use('/api/v2', v2Routes);
app.use('/api/media', mediaRoutes);
app.use('/api/compare', compareRoutes);

// Root test route
app.get('/api', (req, res) => {
  res.json({
    message: 'CCTNS V1 & V2 Testing API Bridge is running',
    endpoints: {
      health: '/api/health',
      v1_firs: '/api/v1/firs',
      v2_crimes: '/api/v2/crimes',
      media: '/api/media/info/:file_id',
      compare: '/api/compare/:query',
    },
  });
});

// Serve static frontend build if present (for single-port production server deployment)
const fs = require('fs');
const buildPath = path.join(__dirname, '..', 'build');
if (fs.existsSync(buildPath)) {
  app.use(express.static(buildPath));
  app.use((req, res, next) => {
    if (req.method === 'GET' && !req.path.startsWith('/api')) {
      return res.sendFile(path.join(buildPath, 'index.html'));
    }
    next();
  });
}

app.listen(PORT, '0.0.0.0', () => {
  console.log(`🚀 CCTNS API Bridge running on http://0.0.0.0:${PORT}`);
});


