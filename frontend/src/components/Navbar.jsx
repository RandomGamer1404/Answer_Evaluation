import { Link, useLocation } from 'react-router-dom';

const Navbar = () => {
  const location = useLocation();

  const isActive = (path) => {
    return location.pathname === path;
  };

  return (
    <nav className="bg-gray-800 border-b border-gray-700">
      <div className="container mx-auto px-4">
        <div className="flex justify-between items-center h-16">
          <Link to="/" className="flex items-center space-x-2">
            {/* Remove empty src to avoid reloading whole page */}
            <img src="/Untitled design-3.png" alt="AutoGrade" className="h-12 w-24" />
            
          </Link>
          
          <div className="flex space-x-4">
            <Link
              to="/"
              className={`px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                isActive('/') 
                  ? 'bg-blue-600 text-white' 
                  : 'text-gray-300 hover:bg-gray-700 hover:text-white'
              }`}
            >
              Home
            </Link>
            <Link
              to="/evaluate"
              className={`px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                isActive('/evaluate') 
                  ? 'bg-blue-600 text-white' 
                  : 'text-gray-300 hover:bg-gray-700 hover:text-white'
              }`}
            >
              Evaluate
            </Link>
            <Link
              to="/diagram-evaluate"
              className="text-gray-300 hover:text-white px-3 py-2 rounded-md text-sm font-medium transition-colors"
            >
              Diagram Evaluation
            </Link>
          </div>
        </div>
      </div>
    </nav>
  );
};

export default Navbar;