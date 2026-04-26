import { useCallback, useEffect, useMemo, useState } from 'react';
import AppLayout from '@/components/layout/AppLayout';
import DispatchQrScanner from '@/components/dispatch/DispatchQrScanner';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { requestsApi } from '@/services/api';
import { useToast } from '@/hooks/use-toast';
import {
  buildTransferConfirmPayload,
  mapDeliveryConfirmationError,
  validateTransferConfirmInput,
} from '@/utils/deliveryConfirmation';
import { Camera, Loader2, QrCode, RefreshCcw, ShieldCheck } from 'lucide-react';

const DISPATCH_SCAN_REQUEST_CONTEXT_KEY = 'dispatch-scan:request-id';

const ScanDispatchQRCode = () => {
  const { toast } = useToast();
  const requestedRequestId = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return (params.get('requestId') || '').trim();
  }, []);

  const [requestContextId, setRequestContextId] = useState(() => {
    if (requestedRequestId) {
      return requestedRequestId;
    }

    try {
      return (window.sessionStorage.getItem(DISPATCH_SCAN_REQUEST_CONTEXT_KEY) || '').trim();
    } catch {
      return '';
    }
  });

  const [scanSession, setScanSession] = useState(0);
  const [scannerActive, setScannerActive] = useState(true);
  const [scannerRunning, setScannerRunning] = useState(false);
  const [scannerError, setScannerError] = useState<string | null>(null);
  const [scannedValue, setScannedValue] = useState('');
  const [completionConfirmOpen, setCompletionConfirmOpen] = useState(false);
  const [pendingCompletion, setPendingCompletion] = useState<
    { requestId: string; qrPayload: string; quantityReceived: number } | null
  >(null);
  const [verifying, setVerifying] = useState(false);
  const [verificationLocked, setVerificationLocked] = useState(false);

  const effectiveRequestId = requestContextId.trim();

  const persistRequestContext = useCallback((requestId: string) => {
    const trimmed = requestId.trim();
    if (!trimmed) {
      return;
    }

    try {
      window.sessionStorage.setItem(DISPATCH_SCAN_REQUEST_CONTEXT_KEY, trimmed);
    } catch {
      // Ignore storage errors.
    }
  }, []);

  const clearPersistedRequestContext = useCallback(() => {
    try {
      window.sessionStorage.removeItem(DISPATCH_SCAN_REQUEST_CONTEXT_KEY);
    } catch {
      // Ignore storage errors.
    }
  }, []);

  const handleRequestContextInputChange = (value: string) => {
    setRequestContextId(value);
    const trimmed = value.trim();
    if (trimmed) {
      persistRequestContext(trimmed);
      return;
    }
    clearPersistedRequestContext();
  };

  const handleScan = useCallback((value: string) => {
    if (verifying || verificationLocked) {
      return;
    }

    if (typeof value !== 'string' || !value.trim()) return;

    setScannedValue(value);
    setScannerActive(false);
    setScannerError(null);
  }, [verificationLocked, verifying]);

  const handleScanAgain = () => {
    if (verifying || verificationLocked) {
      return;
    }

    setScannedValue('');
    setScannerError(null);
    setCompletionConfirmOpen(false);
    setPendingCompletion(null);
    setScannerActive(true);
    setScanSession((prev) => prev + 1);
  };

  const handleVerifyCode = () => {
    if (!scannedValue || verifying || verificationLocked) return;

    try {
      const requestId = effectiveRequestId;

      const validation = validateTransferConfirmInput(scannedValue, 1);

      if (validation.error) {
        throw new Error(validation.error);
      }

      if (!requestId) {
        throw new Error('Request context is missing. Open scanner from a request card to submit transfer confirmation.');
      }

      persistRequestContext(requestId);

      setPendingCompletion({
        requestId,
        qrPayload: scannedValue,
        quantityReceived: 1,
      });
      setCompletionConfirmOpen(true);
    } catch (error) {
      const mappedError = mapDeliveryConfirmationError(error);
      toast({
        title: mappedError.title,
        description: mappedError.description,
        variant: 'destructive',
      });
    }
  };

  const handleConfirmVerification = async () => {
    if (!pendingCompletion || verifying || verificationLocked) return;

    try {
      setVerifying(true);
      await requestsApi.transferConfirm(
        pendingCompletion.requestId,
        buildTransferConfirmPayload(
          pendingCompletion.qrPayload,
          pendingCompletion.quantityReceived,
        ),
      );

      setVerificationLocked(true);
      setCompletionConfirmOpen(false);
      toast({
        title: 'Delivery successfully confirmed',
        description: 'Workflow completed',
      });
    } catch (error) {
      const mappedError = mapDeliveryConfirmationError(error);
      toast({
        title: mappedError.title,
        description: mappedError.description,
        variant: 'destructive',
      });
    } finally {
      setVerifying(false);
    }
  };

  useEffect(() => {
    if (!requestedRequestId) {
      return;
    }
    persistRequestContext(requestedRequestId);
  }, [persistRequestContext, requestedRequestId]);

  return (
    <AppLayout title="Dispatch QR Scanner"
      // subtitle="Scan dispatch and delivery QR codes using your webcam"
    >
      <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Camera className="h-4 w-4" />
              Live Webcam Preview
            </CardTitle>
            <CardDescription>
              Align the QR code inside the frame. Scanner stops automatically after a successful read.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {scannerActive ? (
              <DispatchQrScanner
                key={scanSession}
                active={scannerActive}
                onScan={handleScan}
                onScannerStateChange={setScannerRunning}
                onScannerError={setScannerError}
              />
            ) : (
              <div className="flex min-h-[300px] items-center justify-center rounded-lg border border-dashed bg-muted/30 text-center">
                <div className="space-y-2 p-4">
                  <QrCode className="mx-auto h-8 w-8 text-muted-foreground" />
                  <p className="text-sm font-medium">Scanner paused</p>
                  <p className="text-xs text-muted-foreground">Use Scan Again to restart webcam scanning.</p>
                </div>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={scannerRunning ? 'default' : 'secondary'}>
                {scannerRunning ? 'Scanning in progress' : scannerActive ? 'Initializing camera' : 'Scanner stopped'}
              </Badge>
              <Button variant="outline" onClick={handleScanAgain} disabled={verifying || verificationLocked}>
                <RefreshCcw className="mr-2 h-4 w-4" />
                Scan Again
              </Button>
            </div>

            {scannerError ? (
              <Alert variant="destructive">
                <AlertTitle>Camera unavailable</AlertTitle>
                <AlertDescription>{scannerError}</AlertDescription>
              </Alert>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Scanned Result</CardTitle>
            <CardDescription>Scanned QR content is treated as opaque payload and forwarded unchanged.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">Request context (required)</p>
              <Input
                value={requestContextId}
                onChange={(event) => handleRequestContextInputChange(event.target.value)}
                placeholder="Request ID (for example: req-123)"
                disabled={verifying || verificationLocked}
              />
              {!effectiveRequestId ? (
                <p className="text-xs text-warning">
                  Request ID is required for transfer confirmation. Open scanner from a request card or paste request ID manually.
                </p>
              ) : null}
            </div>

            {!scannedValue ? (
              <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                No QR value scanned yet.
              </div>
            ) : (
              <>
                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground">Opaque QR Payload</p>
                  <Input readOnly value={scannedValue} />
                </div>

                <div className="space-y-3">
                  <Alert>
                    <ShieldCheck className="h-4 w-4" />
                    <AlertTitle>Verification ready</AlertTitle>
                    <AlertDescription>
                      Confirm to submit the scanned payload to backend transfer-confirm.
                    </AlertDescription>
                  </Alert>
                  <Button onClick={handleVerifyCode} disabled={verifying || verificationLocked}>
                    {verifying ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                    {verificationLocked ? 'Delivery already confirmed' : 'Review completion'}
                  </Button>
                  {verificationLocked ? (
                    <p className="text-xs text-muted-foreground">
                      Verification is locked for this scanned QR to prevent duplicate submissions.
                    </p>
                  ) : null}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <AlertDialog open={completionConfirmOpen} onOpenChange={setCompletionConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm delivery completion</AlertDialogTitle>
            <AlertDialogDescription>
              Submit this scanned qrPayload to transfer-confirm and let backend decide final workflow state.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2 rounded-md border bg-muted/40 p-3 text-xs">
            <p>
              <span className="font-medium">Request:</span>{' '}
              <span className="font-mono">{pendingCompletion?.requestId || 'N/A'}</span>
            </p>
            <p>
              <span className="font-medium">qrPayload:</span>{' '}
              <span className="break-all font-mono">{pendingCompletion?.qrPayload || 'N/A'}</span>
            </p>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={verifying}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmVerification} disabled={verifying || verificationLocked}>
              {verifying ? 'Confirming...' : 'Confirm delivery'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppLayout>
  );
};

export default ScanDispatchQRCode;