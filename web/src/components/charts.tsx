/** 仪表盘迷你图（ECharts 封装）：Sparkline 折线 + MiniBar 柱状。
 *
 * 只用于总览统计卡的小型可视化，配色跟随主题（theme.ts），
 * 不引入第二个图表库。数据全部来自后端真实读数。
 */

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { chartPalette, onThemeChange } from "../theme";

interface MiniChartProps {
  /** 数值序列（真实数据，长度 1–24）。 */
  data: number[];
  /** 无障碍标签。 */
  label: string;
  /** 容器类名（默认统计卡内的 30px 迷你图）。 */
  className?: string;
}

function useMiniChart(build: (chart: echarts.ECharts) => void, deps: unknown[]): React.RefObject<HTMLDivElement> {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = container.current;
    if (!element) return undefined;
    const chart = echarts.init(element, undefined, { renderer: "svg" });
    build(chart);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element);
    const off = onThemeChange(() => { chart.dispose(); const next = echarts.init(element, undefined, { renderer: "svg" }); build(next); next.resize(); });
    return () => { off(); observer.disconnect(); chart.dispose(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return container;
}

/** 迷你折线：单序列趋势（如产线平均健康度）。 */
export function Sparkline({ data, label, className = "stat-spark" }: MiniChartProps): JSX.Element {
  const ref = useMiniChart((chart) => {
    const palette = chartPalette();
    chart.setOption({
      animation: false,
      grid: { left: 0, right: 0, top: 4, bottom: 0 },
      xAxis: { type: "category", show: false, data: data.map((_, index) => index) },
      yAxis: { type: "value", show: false, min: (value: { min: number }) => Math.min(0, value.min) },
      tooltip: { trigger: "axis", formatter: () => "" },
      series: [{ type: "line", data, showSymbol: false, smooth: true, lineStyle: { width: 1.5, color: palette.accent }, areaStyle: { color: palette.accent, opacity: 0.12 } }]
    });
  }, [data.join(",")]);
  return <div className={className} ref={ref} role="img" aria-label={label} />;
}

/** 迷你柱状：分类计数（如各产线高风险设备数）。 */
export function MiniBar({ data, label, className = "stat-spark" }: MiniChartProps): JSX.Element {
  const ref = useMiniChart((chart) => {
    const palette = chartPalette();
    chart.setOption({
      animation: false,
      grid: { left: 0, right: 0, top: 4, bottom: 0 },
      xAxis: { type: "category", show: false, data: data.map((_, index) => index) },
      yAxis: { type: "value", show: false, min: 0 },
      tooltip: { trigger: "axis", formatter: () => "" },
      series: [{ type: "bar", data, barWidth: "55%", itemStyle: { color: palette.accent, borderRadius: [2, 2, 0, 0] } }]
    });
  }, [data.join(",")]);
  return <div className={className} ref={ref} role="img" aria-label={label} />;
}
