import type { User } from '../types/template';
import { api, tokenStore } from './client';

interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export const authApi = {
  async login(email: string, password: string): Promise<User> {
    const result = await api.post<TokenResponse>('/api/auth/login', { email, password });
    tokenStore.set(result.access_token);
    return result.user;
  },

  async register(email: string, password: string, fullName: string): Promise<User> {
    const result = await api.post<TokenResponse>('/api/auth/register', {
      email,
      password,
      full_name: fullName,
    });
    tokenStore.set(result.access_token);
    return result.user;
  },

  me: () => api.get<User>('/api/auth/me'),

  logout: () => tokenStore.clear(),
};
