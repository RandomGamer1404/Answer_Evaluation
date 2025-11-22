// filepath: frontend/src/components/ProtectedRoute.jsx
import { Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

const ProtectedRoute = ({ children, adminOnly }) => {
  const { token, user } = useAuth();
  if (!token) return <Navigate to="/login" />;
  if (adminOnly && !user?.isAdmin) return <Navigate to="/dashboard" />;
  return children;
};

export default ProtectedRoute;