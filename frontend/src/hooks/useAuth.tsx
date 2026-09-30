import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { authApi } from '../api/auth';
import { tokenStore } from '../api/client';
import type { User } from '../types/template';

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<void>;
  logout: () => void;
  isAtLeast: (role: 'contractor' | 'reviewer' | 'admin') => boolean;
}

const RANK = { contractor: 0, reviewer: 1, admin: 2 } as const;

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!tokenStore.get()) {
      setLoading(false);
      return;
    }
    authApi
      .me()
      .then(setUser)
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    setUser(await authApi.login(email, password));
  }, []);

  const register = useCallback(async (email: string, password: string, fullName: string) => {
    setUser(await authApi.register(email, password, fullName));
  }, []);

  const logout = useCallback(() => {
    authApi.logout();
    setUser(null);
  }, []);

  const isAtLeast = useCallback(
    (role: 'contractor' | 'reviewer' | 'admin') =>
      user ? RANK[user.role] >= RANK[role] : false,
    [user],
  );

  const value = useMemo(
    () => ({ user, loading, login, register, logout, isAtLeast }),
    [user, loading, login, register, logout, isAtLeast],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside an AuthProvider');
  return context;
}
