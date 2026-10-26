import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react";
import { api, setToken, getToken, setUnauthorizedHandler, setVerifyToken } from "./api";

export type User = {
  id: string; username: string; display_name: string; utype: string; locale: string; roles: string[];
  scope: { type: string; value: string | null } | null; institution: { id: string; name: string; state: string; region: string; slug: string } | null;
  student_id: string | null; permissions: string[]; demo: boolean;
};
type Ctx = { user: User | null; loading: boolean; setLocale: (l: "en" | "hi") => Promise<void>; login: (u: string, p: string) => Promise<User>; logout: () => Promise<void>; can: (p: string) => boolean };
const AuthCtx = createContext<Ctx>(null as any);
export const useAuth = () => useContext(AuthCtx);

export const homeFor = (u: User) => u.utype === "INSTITUTION" ? "/institution" : u.utype === "AICTE" ? "/aicte" : u.utype === "VERIFIER" ? "/verifier" : "/student";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(!!getToken());

  const clear = useCallback(() => { setToken(null); setVerifyToken(null); setUser(null); }, []);
  useEffect(() => { setUnauthorizedHandler(clear); }, [clear]);
  useEffect(() => {
    if (!getToken()) return;
    api<User>("/me").then(setUser).catch(clear).finally(() => setLoading(false));
  }, [clear]);

  const login = async (username: string, password: string) => {
    const r = await api<{ access_token: string; verify_token?: string; user: User }>("/auth/login", { body: { username, password } });
    setToken(r.access_token); setVerifyToken(r.verify_token || null); setUser(r.user);
    return r.user;
  };
  const logout = async () => { try { await api("/auth/logout", { method: "POST", body: {} }); } catch { /* already gone */ } clear(); };
  const setLocale = async (l: "en" | "hi") => { await api("/me/locale", { method: "PATCH", body: { locale: l } }); setUser((u) => (u ? { ...u, locale: l } : u)); };
  const can = (p: string) => !!user?.permissions.includes(p);
  return <AuthCtx.Provider value={{ user, loading, setLocale, login, logout, can }}>{children}</AuthCtx.Provider>;
}
