/** 主题（浅色 / 暗色）与图表配色的单一来源。
 *
 * 主题通过 <html data-theme="..."> 切换，CSS 变量见 styles.css；
 * ECharts 等 JS 侧配色从这里读取计算后的变量值，保证与 CSS 同步。
 */

export type ThemeName = "light" | "dark";

const STORAGE_KEY = "zhiwei-theme";

function current(): ThemeName {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

/** 读取当前主题（默认浅色）。 */
export function getTheme(): ThemeName {
  return current();
}

/** 切换主题并持久化；返回新主题。 */
export function toggleTheme(): ThemeName {
  const next: ThemeName = current() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem(STORAGE_KEY, next); } catch { /* 隐私模式忽略 */ }
  window.dispatchEvent(new CustomEvent<ThemeName>("themechange", { detail: next }));
  return next;
}

/** 订阅主题变化（ECharts 重绘用）。 */
export function onThemeChange(listener: (theme: ThemeName) => void): () => void {
  const handler = (event: Event): void => listener((event as CustomEvent<ThemeName>).detail);
  window.addEventListener("themechange", handler);
  return () => window.removeEventListener("themechange", handler);
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** 图表调色板：随主题切换（趋势图多序列 + 迷你图点缀色）。 */
export function chartPalette(): { series: string[]; accent: string; text: string; muted: string; border: string; splitLine: string } {
  const dark = current() === "dark";
  return {
    series: dark
      ? ["#a3e635", "#38bdf8", "#fbbf24", "#f87171", "#c084fc", "#2dd4bf", "#fb923c", "#a1a1aa", "#4ade80", "#f472b6", "#60a5fa", "#facc15"]
      : ["#18181b", "#a16207", "#b91c1c", "#2563eb", "#4d7c0f", "#7c3aed", "#0891b2", "#db2777", "#475569", "#d97706", "#059669", "#dc2626"],
    accent: cssVar("--accent") || (dark ? "#a3e635" : "#4d7c0f"),
    text: cssVar("--foreground") || (dark ? "#fafafa" : "#18181b"),
    muted: cssVar("--muted-foreground") || (dark ? "#a1a1aa" : "#71717a"),
    border: cssVar("--border") || (dark ? "#27272a" : "#e4e4e7"),
    splitLine: cssVar("--border") || (dark ? "#27272a" : "#f4f4f5")
  };
}
