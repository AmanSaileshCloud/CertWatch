import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import type { CheckRecord } from "../../types";
import { formatClock, formatDateTime, formatMs } from "../../lib/format";

type Props = {
  /** Checks oldest first, as returned by api.getSeries. */
  checks: CheckRecord[];
  hours: number;
};

const HEIGHT = 220;
const MARGIN = { top: 14, right: 14, bottom: 28, left: 52 };
const DOWN_STRIP = 6;
const Y_TICKS = 4;
const X_TICKS = 6;

interface Point {
  time: number;
  check: CheckRecord;
}

/** Track an element's content width with ResizeObserver. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

/** Round up to 1/2/5 × 10ⁿ so axis labels are friendly. */
function niceCeil(value: number): number {
  const exp = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 5, 10].find((m) => m * exp >= value) ?? 10;
  return step * exp;
}

/** Consecutive down checks → [start, end] time spans (end = next check, or start). */
function downSpans(points: Point[]): [number, number][] {
  const spans: [number, number][] = [];
  let start: number | null = null;
  points.forEach((p, i) => {
    if (!p.check.is_up && start === null) start = p.time;
    const next = points[i + 1];
    if (start !== null && (!next || next.check.is_up)) {
      spans.push([start, next ? next.time : p.time]);
      start = null;
    }
  });
  return spans;
}

/** SVG path for up checks, broken into separate segments at every down check. */
function linePath(points: Point[], x: (t: number) => number, y: (ms: number) => number): string {
  let path = "";
  let penDown = false;
  for (const p of points) {
    if (!p.check.is_up || p.check.response_ms === null) {
      penDown = false;
      continue;
    }
    path += `${penDown ? "L" : "M"}${x(p.time).toFixed(1)},${y(p.check.response_ms).toFixed(1)}`;
    penDown = true;
  }
  return path;
}

function nearestIndex(points: Point[], time: number): number {
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].time < time) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(points[lo - 1].time - time) < Math.abs(points[lo].time - time)) return lo - 1;
  return lo;
}

function summarize(points: Point[], hours: number): string {
  const up = points.filter((p) => p.check.is_up && p.check.response_ms !== null);
  const down = points.length - points.filter((p) => p.check.is_up).length;
  if (up.length === 0) return `Response time chart, last ${hours} hours: no successful checks; ${down} down checks.`;
  const ms = up.map((p) => p.check.response_ms as number);
  const avg = ms.reduce((a, b) => a + b, 0) / ms.length;
  return (
    `Response time chart, last ${hours} hours: ${points.length} checks, ` +
    `average ${Math.round(avg)} ms, min ${Math.min(...ms)} ms, max ${Math.max(...ms)} ms, ${down} down checks.`
  );
}

export function ResponseTimeChart({ checks, hours }: Props) {
  const { ref, width } = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);

  const points = useMemo<Point[]>(
    () => checks.map((check) => ({ time: new Date(check.checked_at).getTime(), check })),
    [checks],
  );
  // Fix "now" per data load so the x axis doesn't drift between renders.
  const end = useMemo(() => Date.now(), [checks]); // eslint-disable-line react-hooks/exhaustive-deps
  const start = end - hours * 3_600_000;

  const maxMs = Math.max(0, ...points.map((p) => p.check.response_ms ?? 0));
  const yMax = niceCeil(Math.max(100, maxMs * 1.1));
  const plotW = Math.max(0, width - MARGIN.left - MARGIN.right);
  const plotH = HEIGHT - MARGIN.top - MARGIN.bottom;
  const x = (t: number) => MARGIN.left + ((t - start) / (end - start)) * plotW;
  const y = (ms: number) => MARGIN.top + plotH - DOWN_STRIP - (ms / yMax) * (plotH - DOWN_STRIP);
  const baseline = MARGIN.top + plotH;

  const yTicks = Array.from({ length: Y_TICKS + 1 }, (_, i) => (yMax / Y_TICKS) * i);
  const xTicks = Array.from({ length: X_TICKS + 1 }, (_, i) => start + ((end - start) / X_TICKS) * i);

  function onMove(e: PointerEvent<SVGRectElement>) {
    if (points.length === 0) return;
    const box = e.currentTarget.getBoundingClientRect();
    const time = start + ((e.clientX - box.left) / box.width) * (end - start);
    setHover(nearestIndex(points, time));
  }

  const hovered = hover !== null ? points[hover] : null;

  return (
    <div className="rtchart" ref={ref}>
      {points.length === 0 ? (
        <p className="rtchart__empty">No checks in the last {hours} hours yet.</p>
      ) : (
        width > 0 && (
          <svg width={width} height={HEIGHT} role="img" aria-label={summarize(points, hours)} className="rtchart__svg">
            {yTicks.map((t) => (
              <g key={t}>
                <line className="rtchart__grid" x1={MARGIN.left} x2={width - MARGIN.right} y1={y(t)} y2={y(t)} />
                <text className="rtchart__label" x={MARGIN.left - 8} y={y(t)} dy="0.32em" textAnchor="end">
                  {formatMs(t)}
                </text>
              </g>
            ))}
            {xTicks.map((t, i) => (
              <text
                key={t}
                className="rtchart__label"
                x={x(t)}
                y={HEIGHT - 8}
                textAnchor={i === 0 ? "start" : i === X_TICKS ? "end" : "middle"}
              >
                {formatClock(t)}
              </text>
            ))}

            {downSpans(points).map(([from, to]) => (
              <g key={from}>
                <rect className="rtchart__downband" x={x(from)} y={MARGIN.top} width={Math.max(3, x(to) - x(from))} height={plotH} />
                <rect className="rtchart__downmark" x={x(from)} y={baseline - DOWN_STRIP} width={Math.max(3, x(to) - x(from))} height={DOWN_STRIP} rx={2} />
              </g>
            ))}

            <line className="rtchart__axis" x1={MARGIN.left} x2={width - MARGIN.right} y1={baseline} y2={baseline} />
            <path className="rtchart__line" d={linePath(points, x, y)} />

            {hovered && (
              <g className="rtchart__hover" aria-hidden="true">
                <line x1={x(hovered.time)} x2={x(hovered.time)} y1={MARGIN.top} y2={baseline} />
                <circle
                  cx={x(hovered.time)}
                  cy={hovered.check.is_up && hovered.check.response_ms !== null ? y(hovered.check.response_ms) : baseline - DOWN_STRIP / 2}
                  r={4.5}
                  className={hovered.check.is_up ? "is-up" : "is-down"}
                />
              </g>
            )}

            <rect
              className="rtchart__hit"
              x={MARGIN.left}
              y={MARGIN.top}
              width={plotW}
              height={plotH}
              onPointerMove={onMove}
              onPointerLeave={() => setHover(null)}
            />
          </svg>
        )
      )}

      {hovered && (
        <div
          className="rtchart__tip"
          style={{
            left: Math.min(Math.max(x(hovered.time), 90), width - 90),
            top: MARGIN.top,
          }}
          aria-hidden="true"
        >
          <span className="rtchart__tip-time">{formatDateTime(hovered.check.checked_at)}</span>
          {hovered.check.is_up ? (
            <strong>{formatMs(hovered.check.response_ms)}</strong>
          ) : (
            <strong className="rtchart__tip-down">
              Down{hovered.check.status_code ? ` · ${hovered.check.status_code}` : ""}
              {hovered.check.error ? ` — ${hovered.check.error}` : ""}
            </strong>
          )}
        </div>
      )}

      <div className="rtchart__legend" aria-hidden="true">
        <span><i className="rtchart__key rtchart__key--line" /> Response time</span>
        <span><i className="rtchart__key rtchart__key--down" /> Down</span>
      </div>
    </div>
  );
}
