import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { chartPalette, onThemeChange } from "./theme";
import type { Reading } from "./types";

interface TrendChartProps { readings: Reading[]; metrics: string[]; }

const metricNames: Record<string, string> = {
  op_temperature: "主轴温度 (℃)", op_amperage: "电流 (A)", op_voltage: "电压 (V)", rotor_speed: "转速 (RPM)",
  vibration_rms: "振动 RMS (mm/s)", motor_power: "电机功率 (kW)", cutting_force: "切削力 (N)", feed_force: "进给力 (N)",
  spindle_load: "主轴负载 (%)", coolant_temp: "冷却液温 (℃)", coolant_pressure: "冷却液压 (MPa)", oil_pressure: "油压 (MPa)",
  noise_level: "噪声 (dB)", tool_wear: "刀具磨损", surface_roughness: "表面粗糙度", position_error: "位置误差 (mm)",
  power_factor: "功率因数", harmonic_distortion: "谐波失真 (%)", bearing_temp: "轴承温度 (℃)", motor_vibration: "电机振动 (mm/s)",
  ambient_temp: "环境温度 (℃)", humidity: "湿度 (%)"
};

export function TrendChart({ readings, metrics }: TrendChartProps): JSX.Element {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = container.current;
    if (!element) return undefined;
    const render = (target: HTMLDivElement): echarts.ECharts => {
      const palette = chartPalette();
      const chart = echarts.init(target, undefined, { renderer: "svg" });
      chart.setOption({
        animation: false,
        grid: { left: 46, right: 24, top: 36, bottom: 40 },
        tooltip: { trigger: "axis" },
        legend: { data: metrics.map((metric) => metricNames[metric] ?? metric), textStyle: { color: palette.muted, fontSize: 11 } },
        xAxis: { type: "category", data: readings.map((item) => item.timestamp.slice(5, 16)), axisLabel: { color: palette.muted, fontSize: 10 }, axisLine: { lineStyle: { color: palette.border } } },
        yAxis: { type: "value", scale: true, axisLabel: { color: palette.muted, fontSize: 10 }, splitLine: { lineStyle: { color: palette.splitLine } } },
        series: metrics.map((metric, index) => ({ name: metricNames[metric] ?? metric, type: "line", showSymbol: false, smooth: false, lineStyle: { width: 1.5 }, data: readings.map((item) => item.metrics[metric] ?? null), color: palette.series[index % palette.series.length] }))
      });
      return chart;
    };
    let chart = render(element);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element);
    // 主题切换后按新配色整体重绘
    const off = onThemeChange(() => { chart.dispose(); chart = render(element); chart.resize(); });
    return () => { off(); observer.disconnect(); chart.dispose(); };
  }, [metrics, readings]);

  return <div className="trend-chart" ref={container} aria-label="设备传感器趋势图" />;
}
