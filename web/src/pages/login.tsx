/** 登录页：用户名 + 密码换取 JWT，成功后由 App 守卫切换到主界面。 */

import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";

export function LoginPage(): JSX.Element {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError("请输入用户名和密码");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await login(username.trim(), password);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "登录失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  };

  return <div className="login-page">
    <form className="login-card" onSubmit={(event) => void submit(event)}>
      <div className="login-brand">
        <span className="brand-mark">智</span>
        <div><strong>智维先知</strong><small>可靠性中枢 / v2.1</small></div>
      </div>
      <h1>登录控制台</h1>
      <p className="login-sub">CNC 预测性维护多智能体平台 · JWT 认证</p>
      {error && <div className="login-error">{error}</div>}
      <div className="login-field">
        <label htmlFor="login-username">用户名</label>
        <input id="login-username" value={username} autoFocus autoComplete="username"
          onChange={(event) => setUsername(event.target.value)} placeholder="admin" />
      </div>
      <div className="login-field">
        <label htmlFor="login-password">密码</label>
        <input id="login-password" type="password" value={password} autoComplete="current-password"
          onChange={(event) => setPassword(event.target.value)} placeholder="••••••••" />
      </div>
      <button className="button primary" type="submit" disabled={busy}>{busy ? "正在登录…" : "登 录"}</button>
      <p className="login-hint">
        角色权限：viewer 只读 · operator 可触发诊断 / 审批 / 建单 · admin 另可推送报告与管理账号。<br />
        初始管理员账号见部署目录 .env 的 ADMIN_USERNAME / ADMIN_PASSWORD。
      </p>
    </form>
  </div>;
}
