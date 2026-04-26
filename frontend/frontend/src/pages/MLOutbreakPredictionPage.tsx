import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
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
import { mlApi } from '@/services/api';
import { useAuth } from '@/contexts/AuthContext';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/lib/utils';
import { AlertTriangle, Loader2, RefreshCcw, ShieldAlert, ShieldCheck } from 'lucide-react';

// --------------------------------------------------------------------------
// Types
// --------------------------------------------------------------------------

interface OutbreakItem {
  key: string;
  facilityId: string;
  predictionHorizonDays: number;
  outbreakProbability: number;
  /** Normalised to "low" | "medium" | "high" from the backend value. */
  riskLevel: string;
  /** Backend-provided description of the signal. */
  explanation: string;
  neighbors: Array<{ facilityId: string; distanceKm: number }>;
}

interface SuggestionRow {
  source: string;
  facilityId: string;
  distanceKm: number;
  availableQuantity: number;
}

type JobStatus = 'completed' | 'processing' | 'failed' | 'not_available' | null;

// --------------------------------------------------------------------------
// Constants
// --------------------------------------------------------------------------

const POLL_INTERVAL_MS = 5_000;
const MAX_POLL_ATTEMPTS = 24; // 2 minutes max

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

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

/** Converts a 0–1 float probability to an integer percentage. */
const toPct = (value: number): number => Math.round(Math.min(Math.max(value, 0), 1) * 100);

const normalizeRiskLevel = (value: unknown): string => {
  const raw = String(value ?? '').trim().toLowerCase();
  if (raw === 'high' || raw === 'medium' || raw === 'low') return raw;
  return 'unknown';
};

/**
 * A human-readable label for a risk level, answering "What is the signal?".
 * The backend doesn't return a disease name; this label uses risk_level to
 * communicate severity in plain terms.
 */
const riskSignalLabel = (riskLevel: string): string => {
  if (riskLevel === 'high') return 'High-Risk Outbreak Signal';
  if (riskLevel === 'medium') return 'Elevated Outbreak Risk';
  if (riskLevel === 'low') return 'Low-Level Monitoring Signal';
  return 'Outbreak Risk Signal';
};

/**
 * Secondary interpretation line to help users gauge urgency without ML
 * knowledge.
 */
const probabilityInterpretation = (riskLevel: string, pct: number): string => {
  if (riskLevel === 'high') return `${pct}% — Immediate attention recommended`;
  if (riskLevel === 'medium') return `${pct}% — Monitor closely`;
  return `${pct}% — Within normal range`;
};

const formatDateTime = (value: string | null | undefined): string => {
  if (!value) return 'Not available';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString();
};

// --------------------------------------------------------------------------
// Per-risk styling
// --------------------------------------------------------------------------

type RiskLevel = 'high' | 'medium' | 'low' | 'unknown';

const riskBadgeVariant = (
  riskLevel: string,
): 'destructive' | 'secondary' | 'default' | 'outline' => {
  if (riskLevel === 'high') return 'destructive';
  if (riskLevel === 'medium') return 'secondary';
  if (riskLevel === 'low') return 'default';
  return 'outline';
};

const riskRowClass = (riskLevel: string): string => {
  if (riskLevel === 'high') return 'bg-red-500/10 dark:bg-red-500/10';
  if (riskLevel === 'medium') return 'bg-amber-500/8 dark:bg-amber-500/10';
  return '';
};

const riskBarClass = (riskLevel: string): string => {
  if (riskLevel === 'high') return 'bg-red-500';
  if (riskLevel === 'medium') return 'bg-amber-500';
  return 'bg-emerald-500';
};

const riskProbabilityTextClass = (riskLevel: string): string => {
  if (riskLevel === 'high') return 'text-red-600 dark:text-red-400';
  if (riskLevel === 'medium') return 'text-amber-600 dark:text-amber-400';
  return 'text-emerald-600 dark:text-emerald-400';
};

// --------------------------------------------------------------------------
// Data extraction
// --------------------------------------------------------------------------

const extractOutbreakData = (
  payload: unknown,
): {
  status: JobStatus;
  isStale: boolean;
  hasPartialFailures: boolean;
  completedAt: string;
  outbreakItems: OutbreakItem[];
} => {
  const root = asRecord(payload);
  const data = asRecord(root.data);
  const status = (data.status as JobStatus) ?? null;
  const isStale = Boolean(data.is_stale);
  const hasPartialFailures = Boolean(data.has_partial_failures);
  const completedAt = String(data.completed_at ?? '');
  const items = asArray(data.items);

  const outbreakItems: OutbreakItem[] = items.map((item, index) => {
    const row = asRecord(item);
    const facilityId = String(row.facility_id ?? `facility-${index}`).trim();
    const neighbors = asArray(row.neighbors).map((n) => {
      const neighbor = asRecord(n);
      return {
        facilityId: String(neighbor.facility_id ?? '').trim(),
        distanceKm: toNumber(neighbor.distance_km ?? 0),
      };
    });

    return {
      key: `${facilityId}-${index}`,
      facilityId,
      predictionHorizonDays: toNumber(row.prediction_horizon_days ?? 7),
      outbreakProbability: toNumber(row.outbreak_probability),
      riskLevel: normalizeRiskLevel(row.risk_level),
      explanation: String(row.explanation ?? '').trim() || 'Outbreak risk signal',
      neighbors,
    };
  });

  return { status, isStale, hasPartialFailures, completedAt, outbreakItems };
};

const extractSuggestions = (payload: unknown): SuggestionRow[] => {
  const root = asRecord(payload);
  const data = asRecord(root.data);
  const list = asArray(data.items);

  return list
    .map((item) => {
      const row = asRecord(item);
      return {
        source: String(row.source ?? '').trim(),
        facilityId: String(row.facility_id ?? '').trim(),
        distanceKm: toNumber(row.distance_km ?? 0),
        availableQuantity: toNumber(row.available_quantity ?? 0),
      };
    })
    .filter((row) => row.facilityId);
};

// --------------------------------------------------------------------------
// Sub-components
// --------------------------------------------------------------------------

/** Card shown for each high-risk item at the top of the page. */
const HighRiskAlertCard = ({ item }: { item: OutbreakItem }) => {
  const pct = toPct(item.outbreakProbability);
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4 dark:border-red-800 dark:bg-red-950/20">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 shrink-0 text-red-600 dark:text-red-400" />
          <span className="font-semibold text-red-700 dark:text-red-300">
            {riskSignalLabel(item.riskLevel)}
          </span>
        </div>
        <Badge variant="destructive" className="shrink-0 uppercase tracking-wide text-[10px]">
          High Risk
        </Badge>
      </div>

      <div className="mt-3 flex items-center gap-3">
        <span className="text-3xl font-bold text-red-600 dark:text-red-400">{pct}%</span>
        <span className="text-sm text-red-600/80 dark:text-red-400/80">outbreak probability</span>
      </div>

      {/* Probability bar */}
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-red-200 dark:bg-red-900">
        <div
          className="h-full rounded-full bg-red-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>

      <p className="mt-3 text-sm text-red-700 dark:text-red-300">{item.explanation}</p>

      <p className="mt-2 text-xs text-red-600/60 dark:text-red-400/60">
        Facility {item.facilityId}
        {item.predictionHorizonDays > 0 && ` · ${item.predictionHorizonDays}-day forecast horizon`}
        {item.neighbors.length > 0 && ` · ${item.neighbors.length} neighboring facilit${item.neighbors.length === 1 ? 'y' : 'ies'} monitored`}
      </p>
    </div>
  );
};

// --------------------------------------------------------------------------
// Page component
// --------------------------------------------------------------------------

const MLOutbreakPredictionPage = () => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { toast } = useToast();

  const facilityId = user?.hospital_id || '';
  const facilityName = user?.hospital_name || 'Your facility';

  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const [outbreakItems, setOutbreakItems] = useState<OutbreakItem[]>([]);
  const [suggestions, setSuggestions] = useState<SuggestionRow[]>([]);
  const [completedAt, setCompletedAt] = useState('');
  const [isStale, setIsStale] = useState(false);
  const [hasPartialFailures, setHasPartialFailures] = useState(false);
  const [jobStatus, setJobStatus] = useState<JobStatus>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollCountRef = useRef(0);
  const inFlightRef = useRef(false);

  // Derived stats

  const highRiskItems = useMemo(
    () => outbreakItems.filter((item) => item.riskLevel === 'high'),
    [outbreakItems],
  );

  const averageRisk = useMemo(() => {
    if (outbreakItems.length === 0) return 0;
    return Math.round(
      outbreakItems.reduce((sum, item) => sum + toPct(item.outbreakProbability), 0) /
        outbreakItems.length,
    );
  }, [outbreakItems]);

  const currentStatus = useMemo(() => {
    if (highRiskItems.length > 0) return 'High attention required';
    if (outbreakItems.some((item) => item.riskLevel === 'medium')) return 'Monitoring advised';
    if (outbreakItems.length > 0) return 'Stable';
    return 'No recent signal';
  }, [highRiskItems, outbreakItems]);

  // --------------------------------------------------------------------------
  // Polling
  // --------------------------------------------------------------------------

  const stopPolling = () => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    pollCountRef.current = 0;
    inFlightRef.current = false;
  };

  const applyOutbreakPayload = (payload: unknown): JobStatus => {
    const {
      status,
      isStale: stale,
      hasPartialFailures: partial,
      completedAt: at,
      outbreakItems: items,
    } = extractOutbreakData(payload);
    setJobStatus(status);
    setIsStale(stale);
    setHasPartialFailures(partial);
    setCompletedAt(at);
    setOutbreakItems(items);
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
        const result = await mlApi.getLatestOutbreak(id);
        const status = applyOutbreakPayload(result);
        if (status !== 'processing') {
          stopPolling();
          if (status === 'completed') {
            toast({
              title: 'Outbreak assessment updated',
              description: 'Latest risk data is now available.',
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

  // --------------------------------------------------------------------------
  // Data loading
  // --------------------------------------------------------------------------

  const loadPageData = async () => {
    if (!facilityId) return;
    stopPolling();

    try {
      setLoading(true);

      const [outbreakResult, suggestionsResult] = await Promise.allSettled([
        mlApi.getLatestOutbreak(facilityId),
        mlApi.getRequestSuggestions(facilityId),
      ]);

      if (outbreakResult.status === 'fulfilled') {
        applyOutbreakPayload(outbreakResult.value);
      } else {
        setOutbreakItems([]);
      }

      if (suggestionsResult.status === 'fulfilled') {
        setSuggestions(extractSuggestions(suggestionsResult.value));
      } else {
        setSuggestions([]);
      }

      const hasFailure = [outbreakResult, suggestionsResult].some(
        (result) => result.status === 'rejected',
      );
      if (hasFailure) {
        toast({
          title: 'Some data could not be loaded',
          description: 'Available outbreak insights are shown below.',
          variant: 'destructive',
        });
      }
    } catch (error) {
      toast({
        title: 'Unable to load outbreak data',
        description: error instanceof Error ? error.message : 'Please try again in a moment.',
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  };

  const triggerRefresh = async () => {
    if (!facilityId) return;

    try {
      setRefreshing(true);

      const result = await mlApi.refresh(facilityId, {
        job_type: 'outbreak',
        prediction_horizon_days: 7,
      });
      const root = asRecord(result);

      if (root.success === false) {
        const errorObj = asRecord(root.error);
        if (String(errorObj.code) === 'active_job_exists') {
          toast({
            title: 'Already updating',
            description: 'An outbreak update is already running. Results will appear automatically.',
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
        title: 'Outbreak update triggered',
        description: 'New risk assessment is being computed. This page will refresh automatically.',
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

  // --------------------------------------------------------------------------
  // Render
  // --------------------------------------------------------------------------

  if (!facilityId) {
    return (
      <AppLayout title="Outbreak Prediction">
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
    <AppLayout title="Outbreak Prediction">
      <div className="space-y-6">
        {/* Stale data warning */}
        {isStale && (
          <Alert>
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Results may be outdated</AlertTitle>
            <AlertDescription>
              The outbreak data is more than 12 hours old. Trigger an update to get the latest risk
              assessment.
            </AlertDescription>
          </Alert>
        )}

        {/* Control card */}
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
                  <ShieldAlert className="mr-2 h-4 w-4" />
                )}
                {jobStatus === 'processing' ? 'Updating…' : 'Trigger Update'}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Summary stat cards */}
        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardContent className="flex items-center gap-3 p-6">
              <div className="rounded-lg bg-red-500/10 p-3">
                <ShieldAlert className="h-5 w-5 text-red-500" />
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Current status</p>
                <p className="text-lg font-semibold leading-tight">{currentStatus}</p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="flex items-center gap-3 p-6">
              <div className="rounded-lg bg-amber-500/10 p-3">
                <AlertTriangle className="h-5 w-5 text-amber-500" />
              </div>
              <div>
                <p className="text-2xl font-semibold">{highRiskItems.length}</p>
                <p className="text-xs text-muted-foreground">High-risk signals</p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="flex items-center gap-3 p-6">
              <div className="rounded-lg bg-sky-500/10 p-3">
                <RefreshCcw className="h-5 w-5 text-sky-600" />
              </div>
              <div>
                <p className="text-2xl font-semibold">{averageRisk}%</p>
                <p className="text-xs text-muted-foreground">Average outbreak probability</p>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* High-risk alert cards — only shown when high-risk items exist */}
        {highRiskItems.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base text-red-600 dark:text-red-400">
                <AlertTriangle className="h-4 w-4" />
                Active High-Risk Signals ({highRiskItems.length})
              </CardTitle>
              <CardDescription>
                These facilities require immediate attention. Review the signal details and consider
                taking action.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-3 sm:grid-cols-2">
                {highRiskItems.map((item) => (
                  <HighRiskAlertCard key={item.key} item={item} />
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Full assessment table */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Outbreak Assessment</CardTitle>
            <CardDescription>
              Risk signals from the latest outbreak detection run. Each row answers: what is
              happening, how likely it is, and whether action is needed.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="flex h-56 items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
              </div>
            ) : outbreakItems.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-10 text-center">
                <ShieldCheck className="h-10 w-10 text-muted-foreground/40" />
                <p className="text-sm text-muted-foreground">
                  {jobStatus === 'processing'
                    ? 'Risk assessment is being computed…'
                    : 'No outbreak data available. Results will appear after the next update cycle.'}
                </p>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="min-w-[220px]">Outbreak Signal</TableHead>
                    <TableHead className="min-w-[140px]">Probability</TableHead>
                    <TableHead>Severity</TableHead>
                    <TableHead className="min-w-[200px]">Details</TableHead>
                    <TableHead>Facility</TableHead>
                    <TableHead>Neighbors</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {outbreakItems.map((item) => {
                    const pct = toPct(item.outbreakProbability);
                    return (
                      <TableRow key={item.key} className={riskRowClass(item.riskLevel)}>
                        {/* What is happening */}
                        <TableCell>
                          <p className={cn('font-medium text-sm', riskProbabilityTextClass(item.riskLevel))}>
                            {riskSignalLabel(item.riskLevel)}
                          </p>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            {item.predictionHorizonDays}-day forecast horizon
                          </p>
                        </TableCell>

                        {/* How likely */}
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                              <div
                                className={cn('h-full rounded-full transition-all', riskBarClass(item.riskLevel))}
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                            <span className={cn('text-sm font-semibold tabular-nums', riskProbabilityTextClass(item.riskLevel))}>
                              {pct}%
                            </span>
                          </div>
                          <p className="mt-0.5 text-xs text-muted-foreground">
                            {probabilityInterpretation(item.riskLevel, pct)}
                          </p>
                        </TableCell>

                        {/* Should I care */}
                        <TableCell>
                          <Badge variant={riskBadgeVariant(item.riskLevel)} className="capitalize">
                            {item.riskLevel === 'high'
                              ? 'High Risk'
                              : item.riskLevel === 'medium'
                                ? 'Moderate'
                                : item.riskLevel === 'low'
                                  ? 'Low Risk'
                                  : item.riskLevel}
                          </Badge>
                        </TableCell>

                        {/* Signal details */}
                        <TableCell>
                          <p className="text-sm text-muted-foreground">{item.explanation}</p>
                        </TableCell>

                        {/* Facility */}
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {item.facilityId}
                        </TableCell>

                        {/* Neighbors */}
                        <TableCell className="text-sm">
                          {item.neighbors.length > 0 ? (
                            <span>{item.neighbors.length} nearby</span>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {/* Resource request suggestions */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Resource Request Suggestions</CardTitle>
            <CardDescription>
              Facilities that may have resources available based on forecast and outbreak data.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {suggestions.length === 0 ? (
              <p className="text-sm text-muted-foreground">No resource suggestions available.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Source</TableHead>
                    <TableHead>Facility ID</TableHead>
                    <TableHead>Distance (km)</TableHead>
                    <TableHead>Available Qty</TableHead>
                    <TableHead>Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {suggestions.map((row, index) => (
                    <TableRow key={`${row.facilityId}-${row.source}-${index}`}>
                      <TableCell>
                        <Badge variant="outline">{row.source || 'unknown'}</Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{row.facilityId}</TableCell>
                      <TableCell>{row.distanceKm}</TableCell>
                      <TableCell>{row.availableQuantity}</TableCell>
                      <TableCell>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            navigate(
                              `/sharing/requests/outgoing?supplying_hospital=${encodeURIComponent(row.facilityId)}&quantity_requested=${encodeURIComponent(String(row.availableQuantity))}`,
                            )
                          }
                        >
                          Create Request
                        </Button>
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

export default MLOutbreakPredictionPage;
