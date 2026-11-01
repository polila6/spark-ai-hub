"use client";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const AX = { stroke: "rgb(var(--subtle))", fontSize: 11, tickLine: false, axisLine: false } as const;
const TT = { contentStyle: { background: "rgb(var(--elevated))", border: "1px solid rgb(var(--border))", borderRadius: 10, fontSize: 12, color: "rgb(var(--fg))" } };
export const SERIES = ["rgb(var(--accent))", "rgb(var(--info))", "rgb(var(--warn))", "rgb(var(--danger))", "#a855f7", "#14b8a6"];

export function Spark({ data, dataKey, color = SERIES[0], height = 48, domain }: { data: any[]; dataKey: string; color?: string; height?: number; domain?: [number, number] }) {
  const id = `g-${dataKey}-${color.length}`;
  return (
    <div style={{ height }} aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          {domain && <YAxis hide domain={domain} />}
          <Area type="monotone" dataKey={dataKey} stroke={color} strokeWidth={1.6} fill={`url(#${id})`} isAnimationActive={false} connectNulls />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function TimeChart({ data, lines, height = 220, unit, xKey = "label" }: { data: any[]; lines: { key: string; name: string; color?: string }[]; height?: number; unit?: string; xKey?: string }) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
          <CartesianGrid stroke="rgb(var(--border))" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey={xKey} {...AX} minTickGap={40} />
          <YAxis {...AX} unit={unit} width={48} />
          <Tooltip {...TT} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {lines.map((l, i) => <Line key={l.key} type="monotone" dataKey={l.key} name={l.name} stroke={l.color ?? SERIES[i % SERIES.length]} dot={false} strokeWidth={1.8} isAnimationActive={false} connectNulls />)}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Bars({ data, bars, xKey, height = 240, unit }: { data: any[]; bars: { key: string; name: string; color?: string }[]; xKey: string; height?: number; unit?: string }) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
          <CartesianGrid stroke="rgb(var(--border))" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey={xKey} {...AX} interval={0} tick={{ fontSize: 10 }} />
          <YAxis {...AX} unit={unit} width={52} />
          <Tooltip {...TT} cursor={{ fill: "rgb(var(--elevated))" }} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {bars.map((b, i) => <Bar key={b.key} dataKey={b.key} name={b.name} fill={b.color ?? SERIES[i % SERIES.length]} radius={[6, 6, 0, 0]} />)}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
