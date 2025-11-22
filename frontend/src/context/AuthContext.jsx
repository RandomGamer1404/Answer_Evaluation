// filepath: frontend/src/context/AuthContext.jsx
import { createContext, useState, useContext, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../services/api';

const AuthContext = createContext();

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(localStorage.getItem('token'));
  const navigate = useNavigate();

  const fetchUser = async () => {
    try {
      // Assuming you have a /api/auth/me endpoint to get current user
      const { data } = await api.get('/auth/me');
      setUser(data);
    } catch (error) {
      console.error("Failed to fetch user", error);
      logout(); // Log out if token is invalid
    }
  };

  useEffect(() => {
    if (token && !user) {
      api.defaults.headers.common['Authorization'] = `Bearer ${token}`;
      fetchUser();
    }
  }, [token]);

  const login = async (email, password) => {
    const { data } = await api.post('/auth/login', { email, password });
    localStorage.setItem('token', data.token);
    setToken(data.token);
    setUser(data);
    if (data.isAdmin) {
      navigate('/admin'); // Redirect admin users to admin portal
    } else {
      navigate('/dashboard');
    }
  };

  const register = async (email, password, country, city) => {
    const { data } = await api.post('/auth/register', { email, password, country, city });
    localStorage.setItem('token', data.token);
    setToken(data.token);
    setUser(data); // Fix: The user data is at the root of the response
    navigate('/dashboard');
  };

  const logout = () => {
    localStorage.removeItem('token');
    setToken(null);
    setUser(null);
    delete api.defaults.headers.common['Authorization'];
    navigate('/login');
  };

  return (
    <AuthContext.Provider value={{ user, token, login, register, logout, fetchUser }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);

// Assuming this is part of your backend code, e.g., in a controller file
