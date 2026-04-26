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
import { useAuth } from '@/contexts/AuthContext';
import { useToast } from '@/hooks/use-toast';
import { RESOURCE_SHARES_UPDATED_EVENT } from '@/constants/events';
import { hasAnyPermission } from '@/lib/rbac';
import { REFUND_STATUS } from '@/types/refund';
import ReturnVerificationDialog from '@/components/request/ReturnVerificationDialog';

interface RequestRow {
  id: string;
  supplyingHospitalId: string;
  resource: string;
  supplierHospital: string;
  requesterHospital: string;
  requestedAt: string;
  quantityRequested: number;
  quantityApproved: number | null;
  quantityTransferred: number | null;
  priceSnapshot: number | null;
  totalPrice: number | null;
  status: string;
  paymentStatus: string;
  neededBy: string | null;
  priority: string;
  notes: string;
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
  supplyingHospitalId: String(item.supplying_hospital || item.supplying_hospital_id || ''),
  resource: item.catalog_item_name || item.resource_name || 'Unknown resource',
  supplierHospital: item.supplying_hospital_name || '',
  requesterHospital: item.requesting_hospital_name || '',
  requestedAt: String(item.created_at || item.requested_at || item.requestedAt || item.submitted_at || item.updated_at || ''),
  quantityRequested: Number(item.quantity_requested ?? 0),
  quantityApproved: normalizeNumber(item.quantity_approved),
  quantityTransferred: normalizeNumber(item.quantity_transferred),
  priceSnapshot: normalizeNumber(item.price_snapshot),
  totalPrice: normalizeNumber(item.total_price),
  status: String(item.workflow_state || item.status || 'PENDING'),
  paymentStatus: String(item.payment_status || 'unpaid'),
  neededBy: item.needed_by || null,
  priority: String(item.priority || 'normal'),
  notes: String(item.notes || ''),
  cancellationReason: String(item.cancellation_reason || item.cancel_reason || ''),
});

const currency = (value: number | null) => (value === null ? '-' : value.toLocaleString());

const PERM_VERIFY_RETURN = 'share.request.approve';

interface VerifyReturnActionProps {
  requestId: string;
  quantityTransferred: number | null;
}

const VerifyReturnAction = ({ requestId, quantityTransferred }: VerifyReturnActionProps) => {
  const { user } = useAuth();
  const [showDialog, setShowDialog] = useState(false);

  const canVerify = hasAnyPermission(user, [PERM_VERIFY_RETURN]);

  const { data: raw } = useQuery({
    queryKey: ['refund-status', requestId],
    queryFn: () => requestsApi.getRefundStatus(requestId),
    retry: 1,
    enabled: canVerify,
  });

  if (!canVerify) return null;

  const root = raw as Record<string, unknown> | null | undefined;
  const data = root?.data ?? raw;
  const refundStatus = data && typeof data === 'object'
    ? (data as Record<string, unknown>).refund_status as string | undefined
    : undefined;

  if (refundStatus !== REFUND_STATUS.PENDING_RETURN_VERIFICATION) return null;

  return (
    <div className="mt-2">
      {!showDialog ? (
        <Button size="sm" variant="outline" onClick={() => setShowDialog(true)}>
          Verify Return
        </Button>
      ) : (
        <ReturnVerificationDialog
          requestId={requestId}
          quantityTransferred={quantityTransferred}
          onSuccess={() => setShowDialog(false)}
          onClose={() => setShowDialog(false)}
        />
      )}
    </div>
  );
};

const IncomingRequests = () => {
  const { user } = useAuth();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [approvedQty, setApprovedQty] = useState<Record<string, string>>({});
  const [rejectReason, setRejectReason] = useState<Record<string, string>>({});
  const [confirmingRequest, setConfirmingRequest] = useState<RequestRow | null>(null);

  const hospitalId = user?.hospital_id || '';

  const requestsQuery = useQuery({
    queryKey: ['incoming-requests'],
    queryFn: async () => {
      const res: unknown = await requestsApi.getAll();
      const raw = res?.data?.results ?? res?.data ?? res?.results ?? (Array.isArray(res) ? res : []);
      return (Array.isArray(raw) ? raw : []).map(mapRequest);
    },
  });

  const incomingRequests = useMemo(() => requestsQuery.data || [], [requestsQuery.data]);

  const scopedIncoming = useMemo(() => {
    return incomingRequests
      .filter((item) => !hospitalId || item.supplyingHospitalId === hospitalId)
      .sort((a, b) => {
        const timestampDiff = toEpochMillis(b.requestedAt) - toEpochMillis(a.requestedAt);
        if (timestampDiff !== 0) {
          return timestampDiff;
        }
        return b.id.localeCompare(a.id);
      });
  }, [incomingRequests, hospitalId]);

  const refreshShareDependentViews = useCallback(() => {
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ['incoming-requests'] }),
      queryClient.invalidateQueries({ queryKey: ['outgoing-requests'] }),
      queryClient.invalidateQueries({ queryKey: ['shared-resources-list'] }),
      queryClient.invalidateQueries({ queryKey: ['inventory-list'] }),
      queryClient.invalidateQueries({ queryKey: ['retail-inventory'] }),
    ]);
    window.dispatchEvent(new Event(RESOURCE_SHARES_UPDATED_EVENT));
  }, [queryClient]);

  const approveMutation = useMutation({
    mutationFn: async ({ id, quantityApproved }: { id: string; quantityApproved: number }) => {
      return requestsApi.approve(id, { decision: 'approved', quantity_approved: quantityApproved });
    },
    onSuccess: () => {
      toast({ title: 'Request approved' });
      setConfirmingRequest(null);
      refreshShareDependentViews();
    },
    onError: (error: unknown) => {
      toast({ title: 'Approval failed', description: error?.message || 'Please try again.', variant: 'destructive' });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: async ({ id, reason }: { id: string; reason: string }) => {
      return requestsApi.approve(id, { decision: 'rejected', reason });
    },
    onSuccess: () => {
      toast({ title: 'Request rejected' });
      refreshShareDependentViews();
    },
    onError: (error: unknown) => {
      toast({ title: 'Rejection failed', description: error?.message || 'Please try again.', variant: 'destructive' });
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

  return (
    <AppLayout title="Incoming Requests"
      // subtitle="Review and process requests from other hospitals"
    >
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>Incoming Resource Requests</CardTitle>
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
                    <TableHead>Requester</TableHead>
                    <TableHead>Requested Qty</TableHead>
                    <TableHead>Price Snapshot</TableHead>
                    <TableHead>Total Price</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Payment</TableHead>
                    <TableHead>Needed By</TableHead>
                    <TableHead>Priority</TableHead>
                    <TableHead>Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {scopedIncoming.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={10} className="text-center text-muted-foreground">
                        No incoming requests.
                      </TableCell>
                    </TableRow>
                  ) : (
                    scopedIncoming.map((item) => {
                      const isPending = item.status.toUpperCase() === 'PENDING';
                      const qtyValue = approvedQty[item.id] ?? String(item.quantityRequested);
                      return (
                        <TableRow key={item.id}>
                          <TableCell className="font-medium">{item.resource}</TableCell>
                          <TableCell>{item.requesterHospital || '-'}</TableCell>
                          <TableCell>{item.quantityRequested}</TableCell>
                          <TableCell>{currency(item.priceSnapshot)}</TableCell>
                          <TableCell>{currency(item.totalPrice)}</TableCell>
                          <TableCell>
                            <Badge variant={getStatusVariant(item.status)}>{item.status}</Badge>
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline">{item.paymentStatus}</Badge>
                          </TableCell>
                          <TableCell>{item.neededBy ? new Date(item.neededBy).toLocaleDateString() : '-'}</TableCell>
                          <TableCell className="capitalize">{item.priority}</TableCell>
                          <TableCell className="space-y-2 min-w-56">
                            {isPending ? (
                              <>
                                <Label htmlFor={`qty-${item.id}`} className="text-xs">Approved quantity</Label>
                                <Input
                                  id={`qty-${item.id}`}
                                  type="number"
                                  min={1}
                                  max={item.quantityRequested}
                                  value={qtyValue}
                                  onChange={(event) => setApprovedQty((prev) => ({ ...prev, [item.id]: event.target.value }))}
                                />
                                <Button
                                  size="sm"
                                  onClick={() => setConfirmingRequest(item)}
                                  disabled={approveMutation.isPending}
                                >
                                  Approve
                                </Button>
                                <Textarea
                                  placeholder="Rejection reason"
                                  rows={2}
                                  value={rejectReason[item.id] || ''}
                                  onChange={(event) => setRejectReason((prev) => ({ ...prev, [item.id]: event.target.value }))}
                                />
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => rejectMutation.mutate({ id: item.id, reason: rejectReason[item.id] || 'Rejected by supplier' })}
                                  disabled={rejectMutation.isPending}
                                >
                                  Reject
                                </Button>
                              </>
                            ) : (
                              <span className="text-xs text-muted-foreground">No action available</span>
                            )}
                            <VerifyReturnAction
                              requestId={item.id}
                              quantityTransferred={item.quantityTransferred}
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

        {confirmingRequest && (
          <Card>
            <CardHeader>
              <CardTitle>Confirm Approval</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p><span className="font-medium">Requested quantity:</span> {confirmingRequest.quantityRequested}</p>
              <p><span className="font-medium">Price snapshot:</span> {currency(confirmingRequest.priceSnapshot)}</p>
              <p><span className="font-medium">Total cost:</span> {currency(confirmingRequest.totalPrice)}</p>
              <div className="flex gap-2 pt-2">
                <Button
                  onClick={() => {
                    const parsedQty = Number(approvedQty[confirmingRequest.id] ?? confirmingRequest.quantityRequested);
                    approveMutation.mutate({
                      id: confirmingRequest.id,
                      quantityApproved: Number.isFinite(parsedQty) && parsedQty > 0 ? parsedQty : confirmingRequest.quantityRequested,
                    });
                  }}
                  disabled={approveMutation.isPending}
                >
                  {approveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                  Confirm approval
                </Button>
                <Button variant="outline" onClick={() => setConfirmingRequest(null)}>Cancel</Button>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </AppLayout>
  );
};

export default IncomingRequests;
