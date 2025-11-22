import { useState, useRef } from 'react';

const ImageUpload = ({ onFileSelect, multiple = false, label, required = false, selectedFiles = [] }) => {
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef(null);

  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    
    const files = Array.from(e.dataTransfer.files).filter(file => 
      file.type.startsWith('image/')
    );
    
    if (files.length > 0) {
      if (multiple) {
        onFileSelect(files);
      } else {
        onFileSelect(files[0]);
      }
    }
  };

  const handleFileSelect = (e) => {
    const files = Array.from(e.target.files);
    if (files.length > 0) {
      if (multiple) {
        onFileSelect(files);
      } else {
        onFileSelect(files[0]);
      }
    }
  };

  const handleClick = () => {
    fileInputRef.current?.click();
  };

  const removeFile = (index) => {
    if (multiple) {
      const newFiles = selectedFiles.filter((_, i) => i !== index);
      onFileSelect(newFiles);
    } else {
      onFileSelect(null);
    }
  };

  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium text-gray-300">
        {label} {required && <span className="text-red-400">*</span>}
      </label>
      
      <div
        className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors ${
          isDragging
            ? 'border-blue-400 bg-blue-50/10'
            : 'border-gray-600 hover:border-gray-500'
        }`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={handleClick}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple={multiple}
          onChange={handleFileSelect}
          className="hidden"
        />
        
        <div className="text-gray-400">
          <svg className="w-12 h-12 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
          <p className="text-lg font-medium">
            {multiple ? 'Drop images here or click to select' : 'Drop image here or click to select'}
          </p>
          <p className="text-sm text-gray-500 mt-1">
            Supports: JPEG, PNG, GIF (Max 10MB each)
          </p>
        </div>
      </div>

      {/* Selected files display */}
      {selectedFiles.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm text-gray-300">
            Selected {multiple ? 'files' : 'file'}:
          </p>
          <div className="space-y-1">
            {(multiple ? selectedFiles : [selectedFiles[0]]).map((file, index) => (
              <div key={index} className="flex items-center justify-between bg-gray-700 p-2 rounded">
                <span className="text-sm text-gray-300 truncate">{file?.name || 'Selected file'}</span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    removeFile(index);
                  }}
                  className="text-red-400 hover:text-red-300 ml-2"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default ImageUpload;