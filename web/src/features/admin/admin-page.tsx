import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  Loader2,
  Clock,
  MessagesSquare,
  SearchCheck,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Area, AreaChart, CartesianGrid, Line, ReferenceDot, XAxis, YAxis } from "recharts";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Progress } from "@/components/ui/progress";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatNumber, formatPercent } from "@/lib/format";
import { metricsRangeSchema, type MetricsRange } from "@/schemas/metrics";
import { engineMetricsQueryOptions, useTriggerOptimization } from "./admin-queries";

const chartConfig = {
  mrr: { label: "Mean reciprocal rank (MRR)", color: "var(--chart-1)" },
  baseline: { label: "Baseline", color: "var(--outline-variant)" },
} satisfies ChartConfig;

export function AdminPage() {
  const [range, setRange] = useState<MetricsRange>("30d");
  const { data } = useQuery({
    ...engineMetricsQueryOptions(range),
    placeholderData: keepPreviousData,
  });
  const trigger = useTriggerOptimization();

  if (!data) return null;
  const bufferPct = Math.round((data.bufferGathered / data.threshold) * 100);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-8 px-8 py-10">
      <PageHeader
        title="Engine administration & learning"
        description="Technical overview of autonomous vector tuning, human feedback calibration, and retraining pipeline controls."
      />
      <div className="-mt-4 grid gap-6 md:grid-cols-3">
        <KpiCard label="Search accuracy (MRR)" icon={SearchCheck} value={formatPercent(data.mrr)}>
          Since cycle #{data.cycle} • p@1: {formatPercent(data.precisionAt1)}
        </KpiCard>
        <KpiCard
          label="Signals collected"
          icon={MessagesSquare}
          value={String(data.signals)}
          unit="feedbacks"
        >
          <span className="text-primary">{data.positiveRate} % positive</span> •{" "}
          {data.bufferGathered} / {data.threshold} threshold
        </KpiCard>
        <KpiCard
          label="Last optimization"
          icon={Clock}
          value={`${data.lastOptimizationHours}h`}
          unit="ago • took 3m 42s"
        >
          <span className="flex items-center gap-1 text-primary">
            <CheckCircle2 className="size-4" /> Zero regression
          </span>
        </KpiCard>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="rounded-2xl shadow-soft lg:col-span-2">
          <CardHeader className="flex-row items-start justify-between gap-4">
            <div className="flex flex-col gap-1">
              <p className="text-label-sm uppercase text-muted-foreground">Empirical convergence</p>
              <CardTitle className="text-headline-md">Search precision & ranking</CardTitle>
            </div>
            <ToggleGroup
              type="single"
              value={range}
              onValueChange={(v) => {
                const parsed = metricsRangeSchema.safeParse(v);
                if (parsed.success) setRange(parsed.data);
              }}
              variant="segmented"
              size="sm"
              aria-label="Time range"
              className="shrink-0 rounded-lg bg-surface-low p-1"
            >
              <ToggleGroupItem value="30d">Last 30 days</ToggleGroupItem>
              <ToggleGroupItem value="90d">90 days</ToggleGroupItem>
              <ToggleGroupItem value="all">All time</ToggleGroupItem>
            </ToggleGroup>
          </CardHeader>
          <CardContent>
            <div className="rounded-xl bg-surface-low p-4">
              <ChartContainer config={chartConfig} className="aspect-auto h-72 w-full">
                <AreaChart data={data.series} margin={{ top: 24, right: 12, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="mrrFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--color-mrr)" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="var(--color-mrr)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid vertical={false} strokeDasharray="3 3" />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} />
                  <YAxis
                    domain={[75, 100]}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(v: number) => formatNumber(v)}
                  />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <Line
                    dataKey="baseline"
                    stroke="var(--color-baseline)"
                    strokeDasharray="4 4"
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Area
                    dataKey="mrr"
                    type="monotone"
                    stroke="var(--color-mrr)"
                    strokeWidth={2.5}
                    fill="url(#mrrFill)"
                    isAnimationActive={false}
                  />
                  {data.series
                    .filter((p) => p.round)
                    .map((p) => (
                      <ReferenceDot
                        key={p.label}
                        x={p.label}
                        y={p.mrr}
                        r={5}
                        fill="var(--card)"
                        stroke="var(--color-mrr)"
                        strokeWidth={2}
                        label={{ value: p.round ?? "", position: "top", fontSize: 10 }}
                      />
                    ))}
                </AreaChart>
              </ChartContainer>
            </div>
          </CardContent>
        </Card>

        <Card className="flex flex-col rounded-2xl shadow-soft">
          <CardHeader>
            <p className="text-label-sm uppercase text-muted-foreground">Pipeline engine</p>
            <CardTitle className="text-headline-md">Optimization controller</CardTitle>
            <CardDescription>
              Autonomous calibrator triggers automatically when signal threshold reaches{" "}
              {data.threshold} events.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col gap-6">
            <div className="flex flex-col gap-3 rounded-xl bg-surface-low p-4">
              <div className="flex justify-between text-label-md text-foreground">
                <span>Auto-calibration buffer</span>
                <span className="text-primary">{bufferPct} %</span>
              </div>
              <Progress value={bufferPct} />
              <div className="flex justify-between text-label-md text-muted-foreground">
                <span>
                  {data.bufferGathered} / {data.threshold} signals gathered
                </span>
                <span>~{formatNumber(data.hoursRemaining)}h remaining</span>
              </div>
            </div>
            <Button
              size="xl"
              variant="premium"
              className="mt-auto w-full"
              disabled={trigger.isPending}
              onClick={() =>
                trigger.mutate(undefined, {
                  onSuccess: () => toast.success("Optimization round started"),
                })
              }
            >
              {trigger.isPending ? (
                <>
                  <Loader2 className="animate-spin" strokeWidth={2} />
                  Optimizing…
                </>
              ) : (
                <>
                  <Zap strokeWidth={2} />
                  Run optimization now
                </>
              )}
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

interface KpiCardProps {
  label: string;
  icon: LucideIcon;
  value: string;
  unit?: string;
  children: ReactNode;
}

function KpiCard({ label, icon: Icon, value, unit, children }: KpiCardProps) {
  return (
    <Card className="flex flex-col gap-5 rounded-2xl p-5 shadow-soft transition-shadow duration-200 hover:shadow-md">
      <div className="flex items-start justify-between">
        <p className="text-label-sm uppercase text-muted-foreground">{label}</p>
        <div className="flex size-8 items-center justify-center rounded-lg bg-surface-low text-primary">
          <Icon className="size-4" />
        </div>
      </div>
      <p className="flex items-baseline gap-2">
        <span className="text-headline-xl text-foreground">{value}</span>
        {unit ? <span className="text-label-md text-muted-foreground">{unit}</span> : null}
      </p>
      <div className="text-body-sm text-muted-foreground">{children}</div>
    </Card>
  );
}
