import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AppLayout from '@/components/layout/AppLayout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { mlApi } from '@/services/api';
import { useToast } from '@/hooks/use-toast';
import { Loader2 } from 'lucide-react';

interface SuggestionRow {
  source: string;
  facility_id: string;
  distance_km: number;
  available_quantity: number;
}

const asRecord = (value: unknown): Record<string, unknown> => {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  return {};
};

const asArray = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

const toNumber = (value: unknown): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const prettyJson = (value: unknown): string => {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const MLInsightsDashboard = () => {
  const navigate = useNavigate();
  const { toast } = useToast();

  const [facilityId, setFacilityId] = useState('');
  const [loading, setLoading] = useState(false);

  const [forecastPayload, setForecastPayload] = useState<unknown>(null);
  const [outbreakPayload, setOutbreakPayload] = useState<unknown>(null);
  const [suggestions, setSuggestions] = useState<SuggestionRow[]>([]);
  const [partialFailure, setPartialFailure] = useState(false);

  const loadInsights = async () => {
    if (!facilityId.trim()) {
      toast({ title: 'Facility id required', description: 'Enter a facility id first.', variant: 'destructive' });
      return;
    }

    try {
      setLoading(true);
      setPartialFailure(false);

      const [forecastRes, outbreakRes, suggestionsRes] = await Promise.allSettled([
        mlApi.getLatestForecast(facilityId.trim()),
        mlApi.getLatestOutbreak(facilityId.trim()),
        mlApi.getRequestSuggestions(facilityId.trim()),
      ]);

      if (forecastRes.status === 'fulfilled') {
        setForecastPayload(forecastRes.value);
      }
      if (outbreakRes.status === 'fulfilled') {
        setOutbreakPayload(outbreakRes.value);
      }
      if (suggestionsRes.status === 'fulfilled') {
        const root = asRecord(suggestionsRes.value);
        const data = asRecord(root.data);
        const list = asArray(data.items);
        setSuggestions(
          list
            .map((item) => {
              const row = asRecord(item);
              return {
                source: String(row.source ?? '').trim(),
                facility_id: String(row.facility_id ?? '').trim(),
                distance_km: toNumber(row.distance_km ?? 0),
                available_quantity: toNumber(row.available_quantity ?? 0),
              };
            })
            .filter((row) => row.facility_id),
        );
      } else {
        setSuggestions([]);
      }

      const hasFailure = [forecastRes, outbreakRes, suggestionsRes].some((result) => result.status === 'rejected');
      setPartialFailure(hasFailure);

      if (hasFailure) {
        toast({
          title: 'Partial insight response',
          description: 'Some ML endpoints failed. Available data is still shown.',
          variant: 'destructive',
        });
      }
    } catch (error) {
      toast({
        title: 'Unable to load insights',
        description: error instanceof Error ? error.message : 'ML insight query failed.',
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <AppLayout title="ML Insights Dashboard"
      // subtitle="View latest forecast, outbreak risk, and request suggestions"
    >
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Facility Context</CardTitle>
            <CardDescription>Load latest ML outputs by facility id.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 md:flex-row md:items-end">
            <div className="flex-1 space-y-2">
              <Label htmlFor="facility-id">Facility ID</Label>
              <Input id="facility-id" value={facilityId} onChange={(event) => setFacilityId(event.target.value)} placeholder="facility-uuid" />
            </div>
            <Button onClick={loadInsights} disabled={loading}>
              {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Load Insights
            </Button>
            {partialFailure ? <Badge variant="destructive">Partial Failure</Badge> : null}
          </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Latest Forecast</CardTitle>
            </CardHeader>
            <CardContent>
              {forecastPayload ? (
                <pre className="max-h-72 overflow-auto rounded-md border bg-muted p-3 text-xs">
                  {prettyJson(forecastPayload)}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground">No forecast payload loaded.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Latest Outbreak</CardTitle>
            </CardHeader>
            <CardContent>
              {outbreakPayload ? (
                <pre className="max-h-72 overflow-auto rounded-md border bg-muted p-3 text-xs">
                  {prettyJson(outbreakPayload)}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground">No outbreak payload loaded.</p>
              )}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Request Suggestions</CardTitle>
          </CardHeader>
          <CardContent>
            {suggestions.length === 0 ? (
              <p className="text-sm text-muted-foreground">No suggestion rows available.</p>
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
                    <TableRow key={`${row.facility_id}-${row.source}-${index}`}>
                      <TableCell className="text-xs text-muted-foreground">{row.source || '—'}</TableCell>
                      <TableCell className="font-mono text-xs">{row.facility_id}</TableCell>
                      <TableCell>{row.distance_km}</TableCell>
                      <TableCell>{row.available_quantity}</TableCell>
                      <TableCell>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            navigate(
                              `/sharing/requests/outgoing?supplying_hospital=${encodeURIComponent(row.facility_id)}&quantity_requested=${encodeURIComponent(String(row.available_quantity))}`
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

export default MLInsightsDashboard;
