// filepath: frontend/src/services/api.js
import axios from 'axios';

const api = axios.create({
  baseURL: 'http://localhost:8000',
  timeout: 900000, // 15 minutes for AI processing
});

// Add request interceptor for debugging
api.interceptors.request.use((config) => {
  console.log('API Request:', config.method?.toUpperCase(), config.url);
  return config;
});

// Add response interceptor for error handling
api.interceptors.response.use(
  (response) => {
    console.log('API Response:', response.status, response.config.url);
    return response;
  },
  (error) => {
    console.error('API Error:', error.response?.status, error.response?.data || error.message);
    return Promise.reject(error);
  }
);

// Evaluation API functions
export const evaluateAnswers = async (formData, onUploadProgress) => {
  return api.post('/evaluate', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    onUploadProgress,
    timeout: 900000, // 15 minutes
  });
};

export const evaluateBatch = async (formData, onUploadProgress) => {
  return api.post('/evaluate-batch', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    onUploadProgress,
    timeout: 1800000, // 30 minutes for batch processing
  });
};

export const healthCheck = async () => {
  // Short health timeout so UI doesn't hang
  return api.get('/health', { timeout: 5000 });
};

export const initializeModel = async () => {
  return api.post('/initialize-model');
};

export const evaluateDiagram = (formData, onProgress) => {
  return api.post('/evaluate-diagram', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    timeout: 300000, // 5 minutes
    onUploadProgress: onProgress,
  });
};

export const evaluateDiagramBatch = (formData, onProgress) => {
  return api.post('/evaluate-diagram-batch', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    timeout: 600000, // 10 minutes for batch
    onUploadProgress: onProgress,
  });
};

export default api;