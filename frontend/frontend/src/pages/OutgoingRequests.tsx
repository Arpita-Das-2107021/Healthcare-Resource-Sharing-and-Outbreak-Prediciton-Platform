import { useCallback, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import AppLayout from '@/components/layout/AppLayout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { AlertTriangle, Loader2 } from 'lucide-react';
import { requestsApi } from '@/services/api';
import { useToast } from '@/hooks/use-toast';
import { useAuth } from '@/contexts/AuthContext';
import { RESOURCE_SHARES_UPDATED_EVENT } from '@/constants/events';
import RefundActionButtons from '@/components/request/RefundActionButtons';

interface RequestRow {
  id: string;
  requestingHospitalId: string;
  resource: string;
  supplierHospital: string;
  requestedAt: string;
  quantityRequested: number;
  priceSnapshot: number | null;
  totalPrice: number | null;
  status: string;
  paymentStatus: string;
  paymentNote: string;
  neededBy: string | null;
  priority: string;
  cancellationReason: string;
}

const normalizeNumber = (value: unknown): number | null => {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const toEpochMillis = (value: string): number => {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const mapRequest = (item: unknown): RequestRow => ({
  id: String(item.id || ''),
  requestingHospitalId: String(item.requesting_hospital || item.requesting_hospital_id || ''),
  resource: item.catalog_item_name || item.resource_name || 'Unknown resource',
  supplierHospital: item.supplying_hospital_name || '',
  requestedAt: String(item.created_at || item.requested_at || item.requestedAt || item.submitted_at || item.updated_at || ''),
  quantityRequested: Number(item.quantity_requested ?? 0),
  priceSnapshot: normalizeNumber(item.price_snapshot),
  totalPrice: normalizeNumber(item.total_price),
  status: String(item.workflow_state || item.status || 'PENDING'),
  paymentStatus: String(item.payment_status || 'unpaid'),
  paymentNote: String(item.payment_note || ''),
  neededBy: item.needed_by || null,
  priority: String(item.priority || 'normal'),
  cancellationReason: String(item.cancellation_reason || item.cancel_reason || ''),
});

const currency = (value: number | null) => (value === null ? '-' : value.toLocaleString());

const OutgoingRequests = () => {
  const { user } = useAuth();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [paymentNote, setPaymentNote] = useState<Record<string, string>>({});
  const [cancelReason, setCancelReason] = useState<Record<string, string>>({});

  const requestsQuery = useQuery({
    queryKey: ['outgoing-requests'],
    queryFn: async () => {
      const res: unknown = await requestsApi.getAll();
      const raw = res?.data?.results ?? res?.data ?? res?.results ?? (Array.isArray(res) ? res : []);
      return (Array.isArray(raw) ? raw : []).map(mapRequest);
    },
  });

  const outgoingRequests = useMemo(() => {
    const hospitalId = user?.hospital_id || '';
    return (requestsQuery.data || [])
      .filter((item) => !hospitalId || item.requestingHospitalId === hospitalId)
      .sort((a, b) => {
        const timestampDiff = toEpochMillis(b.requestedAt) - toEpochMillis(a.requestedAt);
        if (timestampDiff !== 0) {
          return timestampDiff;
        }
        return b.id.localeCompare(a.id);
      });
  }, [requestsQuery.data, user?.hospital_id]);

  const refreshShareDependentViews = useCallback(() => {
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ['outgoing-requests'] }),
      queryClient.invalidateQueries({ queryKey: ['incoming-requests'] }),
      queryClient.invalidateQueries({ queryKey: ['shared-resources-list'] }),
      queryClient.invalidateQueries({ queryKey: ['inventory-list'] }),
      queryClient.invalidateQueries({ queryKey: ['retail-inventory'] }),
    ]);
    window.dispatchEvent(new Event(RESOURCE_SHARES_UPDATED_EVENT));
  }, [queryClient]);

  const cancelMutation = useMutation({
    mutationFn: async ({ id, status, reason }: { id: string; status: string; reason: string }) => {
      const normalized = status.toUpperCase();

      const trimmedReason = reason.trim();
      if (['IN_TRANSIT', 'DISPATCHED'].includes(normalized) && !trimmedReason) {
        throw new Error('Cancellation reason is required once dispatch has started.');
      }

      return requestsApi.cancelRequest(id, trimmedReason ? { reason: trimmedReason } : {});
    },
    onSuccess: () => {
      toast({ title: 'Request cancellation processed' });
      refreshShareDependentViews();
    },
    onError: (error: unknown) => {
      toast({ title: 'Cancellation failed', description: error?.message || 'Please try again.', variant: 'destructive' });
    },
  });

  const markPaidMutation = useMutation({
    mutationFn: async ({ id, note }: { id: string; note: string }) => {
      return requestsApi.confirmPayment(id, {
        payment_status: 'PAYMENT_COMPLETED',
        payment_note: note || undefined,
      });
    },
    onSuccess: () => {
      toast({ title: 'Payment updated' });
      queryClient.invalidateQueries({ queryKey: ['outgoing-requests'] });
    },
    onError: (error: unknown) => {
      toast({ title: 'Payment update failed', description: error?.message || 'Please verify permissions.', variant: 'destructive' });
    },
  });

  const getStatusVariant = (status: string): 'default' | 'destructive' | 'secondary' | 'outline' => {
    const normalized = status.toUpperCase();
    if (normalized === 'PENDING') return 'secondary';
    if (['APPROVED', 'RESERVED', 'PAYMENT_PENDING', 'PAYMENT_COMPLETED', 'IN_TRANSIT', 'COMPLETED'].includes(normalized)) {
      return 'default';
    }
    if (['FAILED', 'CANCELLED', 'EXPIRED'].includes(normalized)) return 'destructive';
    return 'outline';
  };

  const getPaymentVariant = (status: string): 'default' | 'secondary' | 'outline' => {
    const normalized = status.toLowerCase();
    if (normalized === 'paid') return 'default';
    if (normalized === 'pending_manual_verification') return 'secondary';
    return 'outline';
  };

  return (
    <AppLayout title="Outgoing Requests"
      // subtitle="Track requests sent to supplier hospitals"
    >
      <Card>
        <CardHeader>
          <CardTitle>Outgoing Resource Requests</CardTitle>
        </CardHeader>
        <CardContent>
          {requestsQuery.isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading requests...
            </div>
          ) : requestsQuery.isError ? (
            <div className="text-destructive flex items-center gap-2">
              <AlertTriangle className="h-4 w-4" /> Failed to load requests.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Resource</TableHead>
                  <TableHead>Supplier Hospital</TableHead>
                  <TableHead>Quantity</TableHead>
                  <TableHead>Price Snapshot</TableHead>
                  <TableHead>Total Price</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Payment</TableHead>
                  <TableHead>Needed By</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Actions</TableHead>
                  <TableHead>Refund</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {outgoingRequests.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={11} className="text-center text-muted-foreground">
                      No outgoing requests.
                    </TableCell>
                  </TableRow>
                ) : (
                  outgoingRequests.map((item) => {
                    const terminalStatuses = ['COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED'];
                    const canCancel = !terminalStatuses.includes(item.status.toUpperCase());
                    const canMarkPaid = item.paymentStatus.toUpperCase() !== 'PAYMENT_COMPLETED';
                    return (
                      <TableRow key={item.id}>
                        <TableCell className="font-medium">{item.resource}</TableCell>
                        <TableCell>{item.supplierHospital || '-'}</TableCell>
                        <TableCell>{item.quantityRequested}</TableCell>
                        <TableCell>{currency(item.priceSnapshot)}</TableCell>
                        <TableCell>{currency(item.totalPrice)}</TableCell>
                        <TableCell>
                          <Badge variant={getStatusVariant(item.status)}>{item.status}</Badge>
                        </TableCell>
                        <TableCell>
                          <Badge variant={getPaymentVariant(item.paymentStatus)}>{item.paymentStatus}</Badge>
                        </TableCell>
                        <TableCell>{item.neededBy ? new Date(item.neededBy).toLocaleDateString() : '-'}</TableCell>
                        <TableCell className="capitalize">{item.priority}</TableCell>
                        <TableCell className="space-y-2 min-w-60">
                          {item.cancellationReason ? (
                            <p className="text-xs text-muted-foreground">Cancellation: {item.cancellationReason}</p>
                          ) : null}

                          <Label htmlFor={`pay-note-${item.id}`} className="text-xs">Payment note</Label>
                          <Input
                            id={`pay-note-${item.id}`}
                            placeholder="Paid via hospital billing system (Invoice #1234)"
                            value={paymentNote[item.id] ?? item.paymentNote}
                            onChange={(event) => setPaymentNote((prev) => ({ ...prev, [item.id]: event.target.value }))}
                          />

                          {canCancel ? (
                            <>
                              <Label htmlFor={`cancel-reason-${item.id}`} className="text-xs">Cancellation / return reason</Label>
                              <Textarea
                                id={`cancel-reason-${item.id}`}
                                rows={2}
                                placeholder={['DISPATCHED', 'IN_TRANSIT'].includes(item.status.toUpperCase())
                                  ? 'Required after dispatch (e.g., damaged package, wrong item)'
                                  : 'Optional reason'}
                                value={cancelReason[item.id] || ''}
                                onChange={(event) => setCancelReason((prev) => ({ ...prev, [item.id]: event.target.value }))}
                              />
                            </>
                          ) : null}

                          <div className="flex gap-2">
                            <Button
                              size="sm"
                              onClick={() => markPaidMutation.mutate({ id: item.id, note: paymentNote[item.id] ?? item.paymentNote })}
                              disabled={!canMarkPaid || markPaidMutation.isPending}
                            >
                              Mark Paid
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => cancelMutation.mutate({
                                id: item.id,
                                status: item.status,
                                reason: cancelReason[item.id] || '',
                              })}
                              disabled={!canCancel || cancelMutation.isPending}
                            >
                              Cancel
                            </Button>
                          </div>
                        </TableCell>
                        <TableCell className="min-w-52 align-top">
                          <RefundActionButtons
                            requestId={item.id}
                            requestSummary={{
                              resource: item.resource,
                              supplierHospital: item.supplierHospital,
                              totalPrice: item.totalPrice,
                            }}
                          />
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </AppLayout>
  );
};

export default OutgoingRequests;
