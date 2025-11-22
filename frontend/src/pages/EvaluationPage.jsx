import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import FileUpload from '../components/FileUpload';
import ProgressBar from '../components/ProgressBar';
import { evaluateAnswers, evaluateBatch, healthCheck, initializeModel } from '../services/api';

const EvaluationPage = () => {
  const [answerKey, setAnswerKey] = useState(null);
  const [studentAnswers, setStudentAnswers] = useState([]);
  const [maxQuestions, setMaxQuestions] = useState(10);
  const [useOcrForStudent, setUseOcrForStudent] = useState(true);
  const [useOcrForAnswerKey, setUseOcrForAnswerKey] = useState(false);
  const [evaluationMode, setEvaluationMode] = useState('single'); // 'single' or 'batch'
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState('idle');
  const [statusMessage, setStatusMessage] = useState('');
  const [error, setError] = useState('');
  
  const navigate = useNavigate();

  const handleSingleStudentSelect = (file) => {
    setStudentAnswers(file ? [file] : []);
  };

  const handleBatchStudentSelect = (files) => {
    setStudentAnswers(files || []);
  };

  const validateForm = () => {
    if (!answerKey) {
      setError('Please upload an answer key PDF');
      return false;
    }
    if (studentAnswers.length === 0) {
      setError('Please upload at least one student answer PDF');
      return false;
    }
    if (maxQuestions < 1 || maxQuestions > 20) {
      setError('Number of questions must be between 1 and 20');
      return false;
    }
    setError('');
    return true;
  };

  const simulateProgress = (startProgress, endProgress, duration) => {
    return new Promise((resolve) => {
      const steps = Math.floor(duration / 1000); // 1 step per second
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
      // Check backend health first
      await healthCheck();
      
      setStatusMessage('Uploading files...');
      setProgress(5);

      const formData = new FormData();
      formData.append('answer_key', answerKey);
      formData.append('max_questions', maxQuestions.toString());
      formData.append('use_ocr_for_student', useOcrForStudent.toString());
      formData.append('use_ocr_for_answer_key', useOcrForAnswerKey.toString());

      let response;

      if (evaluationMode === 'single') {
        formData.append('student_answer', studentAnswers[0]);
        
        // Upload progress simulation
        await simulateProgress(5, 20, 3000);
        setStatusMessage('Files uploaded. Initializing AI model...');
        
        // Model initialization progress
        setStatus('processing');
        await simulateProgress(20, 40, 5000);
        setStatusMessage('AI model loaded. Processing with OCR...');
        
        // OCR progress
        await simulateProgress(40, 60, 8000);
        setStatusMessage('OCR complete. Starting AI evaluation...');
        
        // Start actual API call
        response = evaluateAnswers(formData, () => {});
        
        // Evaluation progress simulation
        await simulateProgress(60, 90, 10000);
        setStatusMessage('AI evaluation in progress...');
        
        // Wait for actual response
        response = await response;
        
      } else {
        studentAnswers.forEach(file => {
          formData.append('student_answers', file);
        });
        
        // Similar progress for batch but longer
        await simulateProgress(5, 15, 3000);
        setStatusMessage('Files uploaded. Initializing AI model...');
        
        setStatus('processing');
        await simulateProgress(15, 30, 5000);
        setStatusMessage('AI model loaded. Processing batch with OCR...');
        
        await simulateProgress(30, 50, 10000);
        setStatusMessage('OCR complete. Starting batch AI evaluation...');
        
        response = evaluateBatch(formData, () => {});
        
        await simulateProgress(50, 85, 15000);
        setStatusMessage('Batch AI evaluation in progress...');
        
        response = await response;
      }

      setProgress(100);
      setStatus('completed');
      setStatusMessage('Evaluation completed successfully!');

      // Navigate to results page with data
      setTimeout(() => {
        navigate('/results', { 
          state: { 
            results: response.data,
            metadata: {
              evaluationMode,
              answerKeyName: answerKey.name,
              studentCount: studentAnswers.length
            }
          } 
        });
      }, 1500);

    } catch (error) {
      console.error('Evaluation error:', error);
      setStatus('error');
      setProgress(0);
      
      let errorMessage = 'An unexpected error occurred.';
      
      if (error.code === 'ECONNABORTED') {
        errorMessage = 'Request timed out. The AI model may be taking longer to process. Please try with fewer questions or a smaller file.';
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
    setStudentAnswers([]);
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
          <h1 className="text-3xl font-bold text-white mb-2">Answer Evaluation</h1>
          <p className="text-gray-400">
            Upload answer key and student answer PDFs for AI-powered evaluation
          </p>
          <div className="mt-4 p-4 bg-yellow-900/50 border border-yellow-700 rounded-lg">
            <p className="text-yellow-200 text-sm">
              <strong>Note:</strong> AI evaluation may take 5-15 minutes depending on document size and number of questions. 
              Please be patient during processing.
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
            <div className="flex space-x-4">
              <label className="flex items-center">
                <input
                  type="radio"
                  value="single"
                  checked={evaluationMode === 'single'}
                  onChange={(e) => setEvaluationMode(e.target.value)}
                  className="mr-2 text-blue-600"
                  disabled={isLoading}
                />
                <span className="text-gray-300">Single Student</span>
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
                <span className="text-gray-300">Batch Evaluation (Longer processing)</span>
              </label>
            </div>
          </div>

          {/* Answer Key Upload */}
          <FileUpload
            onFileSelect={setAnswerKey}
            label="Answer Key PDF"
            required
          />

          {/* Student Answers Upload */}
          <FileUpload
            onFileSelect={evaluationMode === 'single' ? handleSingleStudentSelect : handleBatchStudentSelect}
            multiple={evaluationMode === 'batch'}
            label={evaluationMode === 'single' ? 'Student Answer PDF' : 'Student Answer PDFs (Multiple)'}
            required
          />

          {/* Configuration */}
          <div className="grid md:grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Number of Questions
              </label>
              <input
                type="number"
                min="1"
                max="20"
                value={maxQuestions}
                onChange={(e) => setMaxQuestions(parseInt(e.target.value) || 1)}
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                disabled={isLoading}
              />
              <p className="text-xs text-gray-500 mt-1">Lower number = faster processing</p>
            </div>

            <div className="space-y-3">
              <label className="block text-sm font-medium text-gray-300">
                OCR Settings
              </label>
              <label className="flex items-center">
                <input
                  type="checkbox"
                  checked={useOcrForStudent}
                  onChange={(e) => setUseOcrForStudent(e.target.checked)}
                  className="mr-2 text-blue-600"
                  disabled={isLoading}
                />
                <span className="text-gray-300">Use OCR for student answers</span>
              </label>
              <label className="flex items-center">
                <input
                  type="checkbox"
                  checked={useOcrForAnswerKey}
                  onChange={(e) => setUseOcrForAnswerKey(e.target.checked)}
                  className="mr-2 text-blue-600"
                  disabled={isLoading}
                />
                <span className="text-gray-300">Use OCR for answer key</span>
              </label>
            </div>
          </div>

          {/* Progress Bar */}
          {isLoading && (
            <ProgressBar 
              progress={progress} 
              status={status} 
              message={statusMessage} 
            />
          )}

          {/* Submit Button */}
          <div className="flex space-x-4">
            <button
              type="submit"
              disabled={isLoading || !answerKey || studentAnswers.length === 0}
              className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed text-white font-medium py-3 px-6 rounded-lg transition-colors"
            >
              {isLoading ? 'Evaluating...' : 'Start Evaluation'}
            </button>
            
            {(isLoading || status === 'completed' || status === 'error') && (
              <button
                type="button"
                onClick={resetForm}
                className="px-6 py-3 bg-gray-600 hover:bg-gray-700 text-white font-medium rounded-lg transition-colors"
              >
                Reset
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
};

export default EvaluationPage;