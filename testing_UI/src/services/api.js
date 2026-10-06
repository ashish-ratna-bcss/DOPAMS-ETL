const API_BASE = process.env.REACT_APP_API_BASE || 'http://localhost:5001/api';

async function request(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;
  try {
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP error! Status: ${response.status}`);
    }

    return await response.json();
  } catch (err) {
    console.error(`API Error on ${endpoint}:`, err);
    throw err;
  }
}

export const api = {
  // Health
  getHealth: () => request('/health'),

  // V1 API
  getV1Firs: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/v1/firs?${query}`);
  },
  getV1FirDetail: (fir_reg_num) => request(`/v1/fir/${encodeURIComponent(fir_reg_num)}`),
  getV1Districts: () => request('/v1/districts'),
  getV1MediaPdfUrl: (attach_path, dms_file_name, download = false) => {
    const host = window.location.hostname || 'localhost';
    const base = process.env.REACT_APP_API_BASE || `http://${host}:5001/api`;
    const dl = download ? '&download=1' : '';
    return `${base}/v1/media/pdf?path=${encodeURIComponent(attach_path)}&name=${encodeURIComponent(dms_file_name)}${dl}`;
  },

  // V2 API
  getV2Crimes: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/v2/crimes?${query}`);
  },
  getV2CrimeDetail: (crime_id) => request(`/v2/crime/${encodeURIComponent(crime_id)}`),
  getV2Hierarchy: () => request('/v2/hierarchy'),

  // Media
  getMediaInfo: (file_id) => request(`/media/info/${encodeURIComponent(file_id)}`),
  getMediaStreamUrl: (file_id) => {
    const host = window.location.hostname || 'localhost';
    const base = process.env.REACT_APP_API_BASE || `http://${host}:5001/api`;
    return `${base}/media/stream/${encodeURIComponent(file_id)}`;
  },

  // Cross Compare
  getCompare: (query) => request(`/compare/${encodeURIComponent(query)}`),
};

export default api;
