import type { EngineMetrics, MetricsRange } from "@/schemas/metrics";
import { mockDelay } from "./mock-delay";

const SERIES_30D: EngineMetrics["series"] = [
  { label: "01/05", mrr: 82.1, baseline: 80 },
  { label: "05/05", mrr: 84.0, baseline: 80.3 },
  { label: "08/05", mrr: 86.4, baseline: 80.6, round: "Round #106" },
  { label: "12/05", mrr: 88.3, baseline: 80.9 },
  { label: "15/05", mrr: 89.8, baseline: 81.2, round: "Round #107" },
  { label: "19/05", mrr: 90.6, baseline: 81.5 },
  { label: "22/05", mrr: 93.1, baseline: 81.8, round: "Round #108" },
  { label: "26/05", mrr: 94.4, baseline: 82.1 },
  { label: "29/05", mrr: 95.6, baseline: 82.4, round: "Round #109" },
  { label: "Today", mrr: 96.4, baseline: 82.7 },
];

export const metricsService = {
  async getEngineMetrics(range: MetricsRange): Promise<EngineMetrics> {
    await mockDelay();
    const series =
      range === "30d"
        ? SERIES_30D
        : SERIES_30D.map((p) => ({ ...p, mrr: p.mrr - (range === "all" ? 6 : 3) + p.mrr / 40 }));
    return {
      mrr: 96.4,
      precisionAt1: 91.2,
      cycle: 108,
      signals: 342,
      positiveRate: 88,
      threshold: 50,
      lastOptimizationHours: 4,
      bufferGathered: 42,
      hoursRemaining: 1.2,
      series,
    };
  },
  async triggerOptimization(): Promise<{ startedAt: string }> {
    await mockDelay(600);
    return { startedAt: new Date().toISOString() };
  },
};
