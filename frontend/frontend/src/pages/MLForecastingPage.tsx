import { useEffect, useMemo, useRef, useState } from 'react';
import AppLayout from '@/components/layout/AppLayout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { mlApi } from '@/services/api';
import { useAuth } from '@/contexts/AuthContext';
import { useToast } from '@/hooks/use-toast';
import { AlertTriangle, CheckCircle2, Loader2, RefreshCcw, TrendingUp } from 'lucide-react';

interface ForecastRow {
  key: string;
  resourceCatalogId: string;
  predictionHorizonDays: number;
  predictedDemand: number;
  shareableQuantity: number;
  restock: boolean;
  restockAmount: number;
  explanation: string;
  confidenceScore: number;
}

type JobStatus = 'completed' | 'processing' | 'failed' | 'not_available' | null;

const POLL_INTERVAL_MS = 5_000;
const MAX_POLL_ATTEMPTS = 24; // 2 minutes max

const asRecord = (value: unknown): Record<string, unknown> => {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
};

const asArray = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

const toNumber = (value: unknown): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const extractForecastData = (
  payload: unknown,
): {
  status: JobStatus;
  isStale: boolean;
  hasPartialFailures: boolean;
  completedAt: string;
  rows: ForecastRow[];
} => {
  const root = asRecord(payload);
  const data = asRecord(root.data);
  const status = (data.status as JobStatus) ?? null;
  const isStale = Boolean(data.is_stale);
  const hasPartialFailures = Boolean(data.has_partial_failures);
  const completedAt = String(data.completed_at ?? '');
  const items = asArray(data.items);

  const rows: ForecastRow[] = items.map((item, index) => {
    const row = asRecord(item);
    const resourceCatalogId = String(row.resource_catalog_id ?? `item-${index}`).trim();
    return {
      key: `${resourceCatalogId}-${index}`,
      resourceCatalogId,
      predictionHorizonDays: toNumber(row.prediction_horizon_days ?? 7),
      predictedDemand: toNumber(row.predicted_demand),
      shareableQuantity: toNumber(row.shareable_quantity),
      restock: Boolean(row.restock),
      restockAmount: toNumber(row.restock_amount),
      explanation: String(row.explanation ?? '').trim(),
      confidenceScore: toNumber(row.confidence_score),
    };
  });

  return { status, isStale, hasPartialFailures, completedAt, rows };
};

const formatDateTime = (value: string): string => {
  if (!value) return 'Not available';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
};

const MLForecastingPage = () => {
  const { user } = useAuth();
  const { toast } = useToast();

  const facilityId = user?.hospital_id || '';
  const facilityName = user?.hospital_name || 'Your facility';

  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const [rows, setRows] = useState<ForecastRow[]>([]);
  const [completedAt, setCompletedAt] = useState('');
  const [isStale, setIsStale] = useState(false);
  const [hasPartialFailures, setHasPartialFailures] = useState(false);
  const [jobStatus, setJobStatus] = useState<JobStatus>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollCountRef = useRef(0);
  const inFlightRef = useRef(false);

  const totalPredicted = useMemo(
    () => rows.reduce((sum, row) => sum + row.predictedDemand, 0),
    [rows],
  );

  const restockCount = useMemo(() => rows.filter((row) => row.restock).length, [rows]);

  const avgConfidence = useMemo(() => {
    if (rows.length === 0) return 0;
    const total = rows.reduce((sum, row) => sum + row.confidenceScore, 0);
    return Math.round((total / rows.length) * 100);
  }, [rows]);

  const stopPolling = () => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    pollCountRef.current = 0;
    inFlightRef.current = false;
  };

  const applyForecastPayload = (payload: unknown): JobStatus => {
    const {
      status,
      isStale: stale,
      hasPartialFailures: partial,
      completedAt: at,
      rows: extracted,
    } = extractForecastData(payload);
    setJobStatus(status);
    setIsStale(stale);
    setHasPartialFailures(partial);
    setCompletedAt(at);
    setRows(extracted);
    return status;
  };

  const startPolling = (id: string) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      if (inFlightRef.current) return;
      pollCountRef.current += 1;
      if (pollCountRef.current >= MAX_POLL_ATTEMPTS) {
        stopPolling();
        return;
      }
      inFlightRef.current = true;
      try {
        const result = await mlApi.getLatestForecast(id);
        const status = applyForecastPayload(result);
        if (status !== 'processing') {
          stopPolling();
          if (status === 'completed') {
            toast({
              title: 'Forecast updated',
              description: 'Latest predictions are now available.',
            });
          }
        }
      } catch {
        // silent — user sees results when the next successful poll lands
      } finally {
        inFlightRef.current = false;
      }
    }, POLL_INTERVAL_MS);
  };

  const loadPageData = async () => {
    if (!facilityId) return;
    stopPolling();

    try {
      setLoading(true);

      const result = await mlApi.getLatestForecast(facilityId);
      applyForecastPayload(result);
    } catch (error) {
      toast({
        title: 'Unable to load forecast',
        description: error instanceof Error ? error.message : 'Please try again in a moment.',
        variant: 'destructive',
      });
      setRows([]);
    } finally {
      setLoading(false);
    }
  };

  const triggerRefresh = async () => {
    if (!facilityId) return;

    try {
      setRefreshing(true);

      const result = await mlApi.refresh(facilityId, {
        job_type: 'forecast',
        prediction_horizon_days: 7,
      });
      const root = asRecord(result);

      if (root.success === false) {
        const errorObj = asRecord(root.error);
        if (String(errorObj.code) === 'active_job_exists') {
          toast({
            title: 'Already updating',
            description: 'A forecast update is already running. Results will appear automatically.',
          });
          setJobStatus('processing');
          startPolling(facilityId);
        } else {
          toast({
            title: 'Refresh failed',
            description: String(errorObj.message || 'Please try again later.'),
            variant: 'destructive',
          });
        }
        return;
      }

      toast({
        title: 'Forecast update triggered',
        description: 'New predictions are being computed. This page will refresh automatically.',
      });
      setJobStatus('processing');
      startPolling(facilityId);
    } catch (error) {
      toast({
        title: 'Refresh failed',
        description: error instanceof Error ? error.message : 'Please try again later.',
        variant: 'destructive',
      });
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void loadPageData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facilityId]);

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearInterval(pollRef.current);
    };
  }, []);

  if (!facilityId) {
    return (
      <AppLayout title="Forecasting">
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Facility context missing</AlertTitle>
          <AlertDescription>
            We could not detect your facility from the current account. Contact your administrator to
            continue.
          </AlertDescription>
        </Alert>
      </AppLayout>
    );
  }

  return (
    <AppLayout title="Forecasting">
      <div className="space-y-6">
        {isStale && (
          <Alert>
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Results may be outdated</AlertTitle>
            <AlertDescription>
              The forecast data is more than 24 hours old. Trigger an update to get the latest
              predictions.
            </AlertDescription>
          </Alert>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="text-base">{facilityName}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs text-muted-foreground">
                Last updated: {formatDateTime(completedAt)}
              </p>
              {isStale && <Badge variant="secondary">Stale</Badge>}
              {hasPartialFailures && <Badge variant="destructive">Partial data</Badge>}
              {jobStatus === 'processing' && (
                <Badge variant="outline" className="gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Updating…
                </Badge>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={loadPageData} disabled={loading}>
                {loading ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCcw className="mr-2 h-4 w-4" />
                )}
                Refresh
              </Button>
              <Button onClick={triggerRefresh} disabled={refreshing || jobStatus === 'processing'}>
                {refreshing ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <TrendingUp className="mr-2 h-4 w-4" />
                )}
                {jobStatus === 'processing' ? 'Updating…' : 'Trigger Update'}
              </Button>
            </div>
          </CardContent>
        </Card>

        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardContent className="flex items-center gap-3 p-6">
              <div className="rounded-lg bg-primary/10 p-3">
                <TrendingUp className="h-5 w-5 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-semibold">{Math.round(totalPredicted)}</p>
                <p className="text-xs text-muted-foreground">Projected total demand</p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="flex items-center gap-3 p-6">
              <div className="rounded-lg bg-amber-500/10 p-3">
                <AlertTriangle className="h-5 w-5 text-amber-500" />
              </div>
              <div>
                <p className="text-2xl font-semibold">{restockCount}</p>
                <p className="text-xs text-muted-foreground">Items needing restock</p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="flex items-center gap-3 p-6">
              <div className="rounded-lg bg-sky-500/10 p-3">
                <CheckCircle2 className="h-5 w-5 text-sky-600" />
              </div>
              <div>
                <p className="text-2xl font-semibold">{avgConfidence}%</p>
                <p className="text-xs text-muted-foreground">Average confidence</p>
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Demand Forecast</CardTitle>
            <CardDescription>
              Predicted demand and shareable quantities for the upcoming period.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="flex h-[320px] items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
              </div>
            ) : rows.length === 0 ? (
              <div className="flex h-[320px] items-center justify-center">
                <p className="text-sm text-muted-foreground">
                  {jobStatus === 'processing'
                    ? 'Predictions are being computed…'
                    : 'No forecast data available. Trigger an update to generate predictions.'}
                </p>
              </div>
            ) : (
              <div className="h-[320px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={rows.slice(0, 18)}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                    <XAxis dataKey="resourceCatalogId" tick={{ fontSize: 10 }} />
                    <YAxis />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: 'hsl(var(--card))',
                        border: '1px solid hsl(var(--border))',
                        borderRadius: '8px',
                      }}
                    />
                    <Area
                      type="monotone"
                      dataKey="predictedDemand"
                      stroke="hsl(var(--primary))"
                      fill="hsl(var(--primary) / 0.2)"
                      strokeWidth={2}
                      name="Predicted Demand"
                    />
                    <Area
                      type="monotone"
                      dataKey="shareableQuantity"
                      stroke="hsl(var(--chart-2))"
                      fill="transparent"
                      strokeWidth={2}
                      strokeDasharray="5 5"
                      name="Shareable Qty"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Forecast Details</CardTitle>
            <CardDescription>Per-resource predictions and restock recommendations.</CardDescription>
          </CardHeader>
          <CardContent>
            {rows.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {jobStatus === 'processing'
                  ? 'Predictions are being computed…'
                  : 'No forecast rows available.'}
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Resource</TableHead>
                    <TableHead>Horizon</TableHead>
                    <TableHead>Predicted</TableHead>
                    <TableHead>Shareable</TableHead>
                    <TableHead>Restock</TableHead>
                    <TableHead>Amount</TableHead>
                    <TableHead>Confidence</TableHead>
                    <TableHead>Explanation</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.key}>
                      <TableCell className="font-mono text-xs">{row.resourceCatalogId}</TableCell>
                      <TableCell>{row.predictionHorizonDays}d</TableCell>
                      <TableCell>{Math.round(row.predictedDemand)}</TableCell>
                      <TableCell>{Math.round(row.shareableQuantity)}</TableCell>
                      <TableCell>
                        <Badge variant={row.restock ? 'destructive' : 'default'}>
                          {row.restock ? 'Yes' : 'No'}
                        </Badge>
                      </TableCell>
                      <TableCell>{row.restock ? Math.round(row.restockAmount) : '—'}</TableCell>
                      <TableCell>{Math.round(row.confidenceScore * 100)}%</TableCell>
                      <TableCell className="max-w-[280px] truncate text-muted-foreground">
                        {row.explanation || '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
};

export default MLForecastingPage;
