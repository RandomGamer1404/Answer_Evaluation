import { useLocation, Link, useNavigate } from 'react-router-dom';
import { useState } from 'react';

const ResultsPage = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const results = location.state?.results;
  const type = location.state?.type || 'text';  // 'diagram' for diagram flow

  if (!results) {
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-8">
        <h2 className="text-xl font-bold text-white mb-4">Error</h2>
        <p className="text-red-200">Invalid results data structure</p>
        <pre className="text-xs text-gray-400 mt-4 overflow-auto">
          {JSON.stringify(results, null, 2)}
        </pre>
      </div>
    );
  }

  // Diagram results renderer
  if (type === 'diagram' && results.status === 'success' && results.results?.overall_score !== undefined) {
    const r = results.results;
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-3xl font-bold text-white mb-2">Diagram Evaluation Results</h1>
            <p className="text-gray-400">
              Engine: {results.metadata?.evaluation_engine || 'Qwen VL'}
            </p>
          </div>
          <Link
            to="/diagram-evaluate"
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors"
          >
            New Diagram Evaluation
          </Link>
        </div>

        <div className="grid md:grid-cols-3 gap-4">
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-6 text-center">
            <div className="text-2xl font-bold text-blue-400">{Math.round((r.overall_score || 0) * 100)}%</div>
            <div className="text-sm text-gray-400">Overall Score</div>
          </div>
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-6 text-center">
            <div className="text-2xl font-bold text-yellow-400">{results.results?.letter_grade || 'F'}</div>
            <div className="text-sm text-gray-400">Letter Grade</div>
          </div>
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-6 text-center">
            <div className="text-2xl font-bold text-green-400">{results.metadata?.student_diagram_filename || 'Student'}</div>
            <div className="text-sm text-gray-400">Student Diagram</div>
          </div>
        </div>

        <div className="grid md:grid-cols-3 gap-4">
          {[
            { label: 'Accuracy', val: r.accuracy_score },
            { label: 'Labeling', val: r.labeling_score },
            { label: 'Structure', val: r.structure_score },
            { label: 'Completeness', val: r.completeness_score },
            { label: 'Clarity', val: r.clarity_score },
          ].map((s, i) => (
            <div key={i} className="bg-gray-800 rounded-lg border border-gray-700 p-4">
              <div className="text-sm text-gray-400">{s.label}</div>
              <div className="text-xl font-bold text-white">{Math.round((s.val || 0) * 100)}%</div>
            </div>
          ))}
        </div>

        <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
          <h3 className="text-lg font-semibold text-white mb-3">Detailed Feedback</h3>
          <p className="text-gray-300 whitespace-pre-wrap">{r.detailed_feedback || 'No feedback provided.'}</p>
        </div>

        <div className="grid md:grid-cols-3 gap-4">
          {['correct_elements', 'missing_elements', 'incorrect_elements'].map((k) => (
            <div key={k} className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <h4 className="font-medium text-white mb-2">
                {k.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}
              </h4>
              <ul className="list-disc list-inside text-gray-300 space-y-1">
                {(r[k] || []).length ? r[k].map((it, idx) => <li key={idx}>{it}</li>) : <li>None</li>}
              </ul>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const renderSingleStudentResults = () => {
    // Handle both direct results and nested results structure
    const summary = results.summary || results.results?.summary;
    const detailedResults = results.detailed_results || results.results?.detailed_results;

    if (!summary) {
      return (
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-8">
          <h2 className="text-xl font-bold text-white mb-4">Error</h2>
          <p className="text-red-200">Invalid results data structure</p>
          <pre className="text-xs text-gray-400 mt-4 overflow-auto">
            {JSON.stringify(results, null, 2)}
          </pre>
        </div>
      );
    }

    return (
      <>
        {/* Summary Card */}
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-6 mb-6">
          <h2 className="text-xl font-bold text-white mb-4">Evaluation Summary</h2>
          <div className="grid md:grid-cols-4 gap-4">
            <div className="text-center">
              <div className="text-2xl font-bold text-blue-400">
                {summary.total_questions_evaluated || 0}
              </div>
              <div className="text-sm text-gray-400">Questions Evaluated</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-green-400">
                {summary.total_marks_obtained?.toFixed(1) || 0}/{summary.total_marks_possible || 0}
              </div>
              <div className="text-sm text-gray-400">Marks Obtained</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-yellow-400">
                {summary.overall_percentage?.toFixed(1) || 0}%
              </div>
              <div className="text-sm text-gray-400">Overall Percentage</div>
            </div>
            <div className="text-center">
              <div className={`text-2xl font-bold ${getGradeColor(summary.letter_grade || 'F')}`}>
                {summary.letter_grade || 'F'}
              </div>
              <div className="text-sm text-gray-400">Letter Grade</div>
            </div>
          </div>
        </div>

        {/* Detailed Results */}
        <div className="space-y-4">
          <h2 className="text-xl font-bold text-white">Detailed Question Analysis</h2>
          {detailedResults && Array.isArray(detailedResults) ? (
            detailedResults.map((result, index) => (
              <QuestionCard 
                key={index} 
                result={result} 
                isExpanded={expandedQuestions.has(result.question_number)}
                onToggle={() => toggleQuestion(result.question_number)}
              />
            ))
          ) : (
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <p className="text-gray-400">No detailed results available</p>
            </div>
          )}
        </div>
      </>
    );
  };

  const renderBatchResults = () => {
    const batchResults = results.batch_results;
    const meta = results.metadata;

    return (
      <>
        {/* Batch Summary */}
        <div className="bg-gray-800 rounded-lg border border-gray-700 p-6 mb-6">
          <h2 className="text-xl font-bold text-white mb-4">Batch Evaluation Summary</h2>
          <div className="grid md:grid-cols-3 gap-4">
            <div className="text-center">
              <div className="text-2xl font-bold text-blue-400">{meta?.total_students || 0}</div>
              <div className="text-sm text-gray-400">Total Students</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-green-400">{meta?.successful_evaluations || 0}</div>
              <div className="text-sm text-gray-400">Successful Evaluations</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-red-400">
                {(meta?.total_students || 0) - (meta?.successful_evaluations || 0)}
              </div>
              <div className="text-sm text-gray-400">Failed Evaluations</div>
            </div>
          </div>
        </div>

        {/* Individual Student Results */}
        <div className="space-y-6">
          <h2 className="text-xl font-bold text-white">Individual Student Results</h2>
          {batchResults && Array.isArray(batchResults) ? (
            batchResults.map((studentResult, index) => (
              <div key={index} className="bg-gray-800 rounded-lg border border-gray-700 p-6">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-lg font-semibold text-white">
                    {studentResult.student_filename}
                  </h3>
                  <span className={`px-3 py-1 rounded-full text-sm font-medium ${
                    studentResult.status === 'success' 
                      ? 'bg-green-900 text-green-200' 
                      : 'bg-red-900 text-red-200'
                  }`}>
                    {studentResult.status}
                  </span>
                </div>

                {studentResult.status === 'success' && studentResult.results ? (
                  <div className="grid md:grid-cols-4 gap-4">
                    <div className="text-center">
                      <div className="text-lg font-bold text-green-400">
                        {studentResult.results.summary?.total_marks_obtained?.toFixed(1) || 0}/
                        {studentResult.results.summary?.total_marks_possible || 0}
                      </div>
                      <div className="text-sm text-gray-400">Marks</div>
                    </div>
                    <div className="text-center">
                      <div className="text-lg font-bold text-yellow-400">
                        {studentResult.results.summary?.overall_percentage?.toFixed(1) || 0}%
                      </div>
                      <div className="text-sm text-gray-400">Percentage</div>
                    </div>
                    <div className="text-center">
                      <div className={`text-lg font-bold ${getGradeColor(studentResult.results.summary?.letter_grade || 'F')}`}>
                        {studentResult.results.summary?.letter_grade || 'F'}
                      </div>
                      <div className="text-sm text-gray-400">Grade</div>
                    </div>
                    <div className="text-center">
                      <button
                        onClick={() => navigate('/results', { 
                          state: { 
                            results: studentResult.results, 
                            metadata: { ...metadata, singleStudent: studentResult.student_filename }
                          } 
                        })}
                        className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-medium transition-colors"
                      >
                        View Details
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="text-red-200">
                    <p className="font-medium">Error:</p>
                    <p className="text-sm">{studentResult.error || 'Unknown error occurred'}</p>
                  </div>
                )}
              </div>
            ))
          ) : (
            <div className="bg-gray-800 rounded-lg border border-gray-700 p-6">
              <p className="text-gray-400">No batch results available</p>
            </div>
          )}
        </div>
      </>
    );
  };

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white mb-2">Evaluation Results</h1>
            <p className="text-gray-400">
              {metadata?.evaluationMode === 'batch' ? 'Batch evaluation results' : 'Single student evaluation results'}
              {metadata?.singleStudent && ` for ${metadata.singleStudent}`}
            </p>
          </div>
          <Link
            to="/evaluate"
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition-colors"
          >
            New Evaluation
          </Link>
        </div>
      </div>

      {metadata?.evaluationMode === 'batch' && !metadata?.singleStudent ? 
        renderBatchResults() : 
        renderSingleStudentResults()
      }
    </div>
  );
};

const QuestionCard = ({ result, isExpanded, onToggle }) => {
  if (!result) {
    return (
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <p className="text-gray-400">Invalid question data</p>
      </div>
    );
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700">
      <div 
        className="p-4 cursor-pointer hover:bg-gray-750 transition-colors"
        onClick={onToggle}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <h3 className="text-lg font-semibold text-white">
              Question {result.question_number || 'N/A'}
            </h3>
            <div className="text-sm text-gray-400">
              {result.marks_obtained?.toFixed(1) || 0}/{result.total_marks || 0} marks
            </div>
            <div className="text-sm text-blue-400">
              {result.total_marks > 0 ? ((result.marks_obtained / result.total_marks) * 100).toFixed(1) : 0}%
            </div>
          </div>
          <svg 
            className={`w-5 h-5 text-gray-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
            fill="none" 
            stroke="currentColor" 
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </div>

      {isExpanded && (
        <div className="border-t border-gray-700 p-4 space-y-4">
          {/* Question Text */}
          <div>
            <h4 className="font-medium text-white mb-2">Question:</h4>
            <p className="text-gray-300 text-sm">{result.question || 'No question text available'}</p>
          </div>

          {/* Student Answer */}
          <div>
            <h4 className="font-medium text-white mb-2">Student Answer:</h4>
            <p className="text-gray-300 text-sm whitespace-pre-wrap">{result.student_answer || 'No answer provided'}</p>
          </div>

          {/* Point Evaluations */}
          {result.point_evaluations && result.point_evaluations.length > 0 && (
            <div>
              <h4 className="font-medium text-white mb-2">Point-wise Evaluation:</h4>
              <div className="space-y-2">
                {result.point_evaluations.map((pe, index) => (
                  <div key={index} className="bg-gray-700 rounded p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium text-gray-300">
                        {pe.expert_point_text ? 
                          (pe.expert_point_text.length > 100 ? 
                            pe.expert_point_text.substring(0, 100) + '...' : 
                            pe.expert_point_text
                          ) : 
                          'Point text not available'
                        }
                      </span>
                      <span className="text-sm font-bold text-blue-400">
                        {((pe.score || 0) * 100).toFixed(0)}%
                      </span>
                    </div>
                    {pe.feedback_on_point && (
                      <p className="text-xs text-gray-400">{pe.feedback_on_point}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Overall Scores */}
          <div>
            <h4 className="font-medium text-white mb-2">Overall Assessment:</h4>
            <div className="grid md:grid-cols-3 gap-4">
              <div className="text-center">
                <div className="text-lg font-bold text-blue-400">
                  {((result.overall_semantic_accuracy || 0) * 100).toFixed(0)}%
                </div>
                <div className="text-xs text-gray-400">Semantic Accuracy</div>
              </div>
              <div className="text-center">
                <div className="text-lg font-bold text-green-400">
                  {((result.overall_factual_correctness || 0) * 100).toFixed(0)}%
                </div>
                <div className="text-xs text-gray-400">Factual Correctness</div>
              </div>
              <div className="text-center">
                <div className="text-lg font-bold text-purple-400">
                  {((result.overall_understanding_depth || 0) * 100).toFixed(0)}%
                </div>
                <div className="text-xs text-gray-400">Understanding Depth</div>
              </div>
            </div>
          </div>

          {/* Feedback */}
          {result.feedback && (
            <div>
              <h4 className="font-medium text-white mb-2">Feedback:</h4>
              <p className="text-gray-300 text-sm whitespace-pre-wrap">{result.feedback}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default ResultsPage;