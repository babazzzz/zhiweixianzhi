/** 登录态上下文：JWT 存取、启动验活、401 广播登出、角色判定。
 *
 * 与后端 `src/api/security.py` 对应：
 * - token / user 持久化在 localStorage（zhiwei_token / zhiwei_user）；
 * - 启动时用 `/api/auth/me` 验活，失效即清理，避免过期 token 卡在界面；
 * - api.ts 拦截到 401 会派发 `zhiwei:unauthorized` 事件，这里负责登出；
 * - canWrite = operator / admin，canAdmin = admin，与后端 OperatorUp / AdminOnly 一致。
 */

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { apiBase } from "./api";

export type Role = "admin" | "operator" | "viewer";

export interface AuthUser {
  username: string;
  role: Role;
  display_name: string | null;
}

interface AuthState {
  user: AuthUser | null;
  /** 启动验活是否完成（未完成时不渲染主界面，避免闪登录页） */
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  /** 操作员及以上：触发诊断、扫描、审批、建单、写记忆 */
  canWrite: boolean;
  /** 仅管理员：推报告、删记忆、用户与循环管理 */
  canAdmin: boolean;
}

const TOKEN_KEY = "zhiwei_token";
const USER_KEY = "zhiwei_user";
const UNAUTHORIZED_EVENT = "zhiwei:unauthorized";

const AuthContext = createContext<AuthState | null>(null);

function readStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<AuthUser>;
    if (!parsed.username || !parsed.role) return null;
    return { username: String(parsed.username), role: parsed.role as Role, display_name: parsed.display_name ?? null };
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [user, setUser] = useState<AuthUser | null>(readStoredUser);
  const [ready, setReady] = useState(false);

  const clearSession = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setUser(null);
  }, []);

  // 启动验活：本地 token 向后端换当前用户，失效则清理
  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      setReady(true);
      return;
    }
    let cancelled = false;
    fetch(`${apiBase}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json() as Promise<AuthUser>;
      })
      .then((payload) => {
        if (cancelled) return;
        const next: AuthUser = { username: payload.username, role: payload.role, display_name: payload.display_name ?? null };
        localStorage.setItem(USER_KEY, JSON.stringify(next));
        setUser(next);
      })
      .catch(() => { if (!cancelled) clearSession(); })
      .finally(() => { if (!cancelled) setReady(true); });
    return () => { cancelled = true; };
  }, [clearSession]);

  // 401 广播 → 登出（token 过期 / 被顶下线时自动回登录页）
  useEffect(() => {
    window.addEventListener(UNAUTHORIZED_EVENT, clearSession);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, clearSession);
  }, [clearSession]);

  const login = useCallback(async (username: string, password: string) => {
    const response = await fetch(`${apiBase}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password })
    });
    if (!response.ok) {
      if (response.status === 401) throw new Error("用户名或密码错误");
      const detail = await response.text().catch(() => "");
      throw new Error(`登录失败（HTTP ${response.status}）${detail.slice(0, 120)}`);
    }
    const payload = await response.json() as { access_token: string; username: string; role: Role; display_name: string | null };
    localStorage.setItem(TOKEN_KEY, payload.access_token);
    const next: AuthUser = { username: payload.username, role: payload.role, display_name: payload.display_name ?? null };
    localStorage.setItem(USER_KEY, JSON.stringify(next));
    setUser(next);
  }, []);

  const value: AuthState = {
    user,
    ready,
    login,
    logout: clearSession,
    canWrite: user?.role === "admin" || user?.role === "operator",
    canAdmin: user?.role === "admin"
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return context;
}
