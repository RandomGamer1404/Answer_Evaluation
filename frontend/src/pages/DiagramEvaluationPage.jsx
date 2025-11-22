import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import ImageUpload from '../components/ImageUpload';
import ProgressBar from '../components/ProgressBar';
import { evaluateDiagram, evaluateDiagramBatch, healthCheck } from '../services/api';

const DiagramEvaluationPage = () => {
  const [answerKey, setAnswerKey] = useState(null);
  const [studentDiagrams, setStudentDiagrams] = useState([]);
  const [evaluationMode, setEvaluationMode] = useState('single');
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState('idle');
  const [statusMessage, setStatusMessage] = useState('');
  const [error, setError] = useState('');

  const navigate = useNavigate();

  const handleSingleStudentSelect = (file) => {
    setStudentDiagrams(file ? [file] : []);
  };

  const handleBatchStudentSelect = (files) => {
    setStudentDiagrams(files || []);
  };

  const validateForm = () => {
    if (!answerKey) {
      setError('Please upload an answer key diagram');
      return false;
    }
    if (studentDiagrams.length === 0) {
      setError('Please upload at least one student diagram');
      return false;
    }
    
    // Validate file sizes
    const maxSize = 10 * 1024 * 1024; // 10MB
    if (answerKey.size > maxSize) {
      setError('Answer key image is too large (max 10MB)');
      return false;
    }
    
    for (let diagram of studentDiagrams) {
      if (diagram.size > maxSize) {
        setError(`Student diagram "${diagram.name}" is too large (max 10MB)`);
        return false;
      }
    }
    
    setError('');
    return true;
  };

  const simulateProgress = (startProgress, endProgress, duration) => {
    return new Promise((resolve) => {
      const steps = Math.floor(duration / 1000);
      const increment = (endProgress - startProgress) / steps;
      let currentProgress = startProgress;
      
      const interval = setInterval(() => {
        currentProgress += increment;
        setProgress(Math.min(currentProgress, endProgress));
        
        if (currentProgress >= endProgress) {
          clearInterval(interval);
          resolve();
        }
      }, 1000);
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    if (!validateForm()) return;

    setIsLoading(true);
    setProgress(0);
    setStatus('uploading');
    setStatusMessage('Checking backend status...');

    try {
      // Check backend health
      await healthCheck();
      
      setStatusMessage('Uploading images...');
      setProgress(5);

      const formData = new FormData();
      formData.append('answer_key', answerKey);
      
      let response;

      if (evaluationMode === 'single') {
        formData.append('student_diagram', studentDiagrams[0]);
        
        await simulateProgress(5, 20, 3000);
        setStatusMessage('Images uploaded. Initializing AI vision model...');
        
        setStatus('processing');
        await simulateProgress(20, 40, 5000);
        setStatusMessage('AI vision model loaded. Analyzing diagrams...');
        
        await simulateProgress(40, 70, 8000);
        setStatusMessage('Comparing diagrams with AI...');
        
        response = evaluateDiagram(formData, () => {});
        
        await simulateProgress(70, 95, 10000);
        setStatusMessage('AI analysis in progress...');
        
        response = await response;
        
      } else {
        studentDiagrams.forEach(file => {
          formData.append('student_diagrams', file);
        });
        
        await simulateProgress(5, 15, 3000);
        setStatusMessage('Images uploaded. Initializing AI vision model...');
        
        setStatus('processing');
        await simulateProgress(15, 30, 5000);
        setStatusMessage('AI vision model loaded. Processing batch diagrams...');
        
        await simulateProgress(30, 50, 10000);
        setStatusMessage('Batch diagram analysis in progress...');
        
        response = evaluateDiagramBatch(formData, () => {});
        
        await simulateProgress(50, 85, 15000);
        setStatusMessage('AI comparing diagrams in batch...');
        
        response = await response;
      }

      await simulateProgress(95, 100, 1000);
      setProgress(100);
      setStatus('completed');
      setStatusMessage('Evaluation completed successfully!');

      // Navigate to results with the response data
      navigate('/results', { 
        state: { 
          results: response.data,
          type: 'diagram'
        } 
      });

    } catch (error) {
      console.error('Evaluation failed:', error);
      let errorMessage = 'An unexpected error occurred during evaluation.';
      
      if (error.code === 'ECONNABORTED') {
        errorMessage = 'Request timed out. The AI model may be taking longer to process. Please try with fewer diagrams or smaller files.';
      } else if (error.response?.data?.detail) {
        errorMessage = error.response.data.detail;
      } else if (error.code === 'ECONNREFUSED') {
        errorMessage = 'Cannot connect to backend server. Please make sure the server is running.';
      } else if (error.message) {
        errorMessage = error.message;
      }
      
      setStatusMessage(`Error: ${errorMessage}`);
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  const resetForm = () => {
    setAnswerKey(null);
    setStudentDiagrams([]);
    setProgress(0);
    setStatus('idle');
    setStatusMessage('');
    setError('');
    setIsLoading(false);
  };

  return (
    <div className="max-w-4xl mx-auto">
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-8">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">Diagram Evaluation</h1>
          <p className="text-gray-400">
            Upload answer key and student diagram images for AI-powered visual comparison and evaluation
          </p>
          <div className="mt-4 p-4 bg-blue-900/50 border border-blue-700 rounded-lg">
            <p className="text-blue-200 text-sm">
              <strong>Note:</strong> AI diagram analysis may take 2-10 minutes depending on image complexity and number of diagrams. 
              Please ensure images are clear and well-labeled for best results.
            </p>
          </div>
        </div>

        {error && (
          <div className="mb-6 p-4 bg-red-900/50 border border-red-700 rounded-lg">
            <p className="text-red-200">{error}</p>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Evaluation Mode */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-3">
              Evaluation Mode
            </label>
            <div className="space-y-2">
              <label className="flex items-center">
                <input
                  type="radio"
                  value="single"
                  checked={evaluationMode === 'single'}
                  onChange={(e) => setEvaluationMode(e.target.value)}
                  className="mr-2 text-blue-600"
                  disabled={isLoading}
                />
                <span className="text-gray-300">Single Diagram</span>
              </label>
              <label className="flex items-center">
                <input
                  type="radio"
                  value="batch"
                  checked={evaluationMode === 'batch'}
                  onChange={(e) => setEvaluationMode(e.target.value)}
                  className="mr-2 text-blue-600"
                  disabled={isLoading}
                />
                <span className="text-gray-300">Batch Evaluation (Multiple Diagrams)</span>
              </label>
            </div>
          </div>

          {/* Answer Key Upload */}
          <ImageUpload
            onFileSelect={setAnswerKey}
            label="Answer Key Diagram"
            required
            selectedFiles={answerKey ? [answerKey] : []}
          />

          {/* Student Diagrams Upload */}
          <ImageUpload
            onFileSelect={evaluationMode === 'single' ? handleSingleStudentSelect : handleBatchStudentSelect}
            multiple={evaluationMode === 'batch'}
            label={evaluationMode === 'single' ? 'Student Diagram' : 'Student Diagrams (Multiple)'}
            required
            selectedFiles={studentDiagrams}
          />

          {/* Progress Bar */}
          {isLoading && (
            <ProgressBar 
              progress={progress} 
              status={status} 
              message={statusMessage}
            />
          )}

          {/* Action Buttons */}
          <div className="flex gap-4">
            <button
              type="submit"
              disabled={isLoading}
              className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed text-white font-medium py-3 px-6 rounded-lg transition-colors"
            >
              {isLoading ? 'Evaluating...' : 'Start Evaluation'}
            </button>
            
            <button
              type="button"
              onClick={resetForm}
              disabled={isLoading}
              className="bg-gray-600 hover:bg-gray-700 disabled:bg-gray-500 disabled:cursor-not-allowed text-white font-medium py-3 px-6 rounded-lg transition-colors"
            >
              Reset
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default DiagramEvaluationPage;