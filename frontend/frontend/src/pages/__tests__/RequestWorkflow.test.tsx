import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import RequestWorkflow from '@/pages/RequestWorkflow';

const getAll = vi.fn();
const getById = vi.fn();
const approve = vi.fn();
const reserve = vi.fn();
const dispatch = vi.fn();
const initiatePayment = vi.fn();
const cancelRequest = vi.fn();
const initiateReturn = vi.fn();
const verifyReturn = vi.fn();
const transferConfirm = vi.fn();
const update = vi.fn();
const getShipments = vi.fn();
const addTracking = vi.fn();
const confirmHandover = vi.fn();
const getStaff = vi.fn();
const mockToast = vi.fn();

const WORKFLOW_TEST_PERMISSIONS = [
  'hospital:request.approve',
  'hospital:request.dispatch',
  'hospital:request.transfer.confirm',
  'hospital:request.return.verify',
  'hospital:transport.track',
  'hospital:transport.update',
];

let mockUser: Record<string, unknown> = {
  hospital_id: 'hospital-owner-1',
  effective_permissions: WORKFLOW_TEST_PERMISSIONS,
};

vi.mock('@/components/layout/AppLayout', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: mockUser,
  }),
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mockToast }),
}));

vi.mock('@/services/api', () => ({
  requestsApi: {
    getAll: (...args: unknown[]) => getAll(...args),
    getById: (...args: unknown[]) => getById(...args),
    getByIdFresh: (...args: unknown[]) => getById(...args),
    approve: (...args: unknown[]) => approve(...args),
    reserve: (...args: unknown[]) => reserve(...args),
    cancelRequest: (...args: unknown[]) => cancelRequest(...args),
    dispatch: (...args: unknown[]) => dispatch(...args),
    initiatePayment: (...args: unknown[]) => initiatePayment(...args),
    initiateReturn: (...args: unknown[]) => initiateReturn(...args),
    verifyReturn: (...args: unknown[]) => verifyReturn(...args),
    transferConfirm: (...args: unknown[]) => transferConfirm(...args),
    update: (...args: unknown[]) => update(...args),
  },
  shipmentsApi: {
    getAll: (...args: unknown[]) => getShipments(...args),
    addTracking: (...args: unknown[]) => addTracking(...args),
    confirmHandover: (...args: unknown[]) => confirmHandover(...args),
  },
  staffApi: {
    getAll: (...args: unknown[]) => getStaff(...args),
  },
}));

const incomingBase = {
  id: 'req-incoming',
  catalog_item_name: 'Ventilator',
  catalog_item: 'cat-1',
  requesting_hospital: 'hospital-requester-1',
  requesting_hospital_name: 'City Hospital',
  supplying_hospital: 'hospital-owner-1',
  supplying_hospital_name: 'Owner Hospital',
  quantity_requested: 3,
  priority: 'urgent',
  created_at: '2026-03-10T10:00:00Z',
};

const outgoingBase = {
  id: 'req-outgoing',
  catalog_item_name: 'Blood Bag',
  catalog_item: 'cat-2',
  requesting_hospital: 'hospital-owner-1',
  requesting_hospital_name: 'Owner Hospital',
  supplying_hospital: 'hospital-supplier-2',
  supplying_hospital_name: 'Supplier Hospital',
  quantity_requested: 2,
  priority: 'normal',
  created_at: '2026-03-10T11:00:00Z',
};

describe('RequestWorkflow', () => {
  let windowOpenSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, '', '/sharing/requests');
    windowOpenSpy = vi.spyOn(window, 'open').mockReturnValue({} as Window);

    mockUser = {
      hospital_id: 'hospital-owner-1',
      effective_permissions: WORKFLOW_TEST_PERMISSIONS,
    };

    getAll.mockResolvedValue({
      data: [{ ...incomingBase, status: 'pending', payment_required: true }],
    });
    getShipments.mockResolvedValue({ data: [] });
    getStaff.mockResolvedValue({
      data: [
        {
          id: 'staff-1',
          full_name: 'Rider One',
          phone: '01700000000',
        },
      ],
    });
    getById.mockResolvedValue({ data: { ...outgoingBase, status: 'PAYMENT_PENDING', payment_required: true } });
    approve.mockResolvedValue({ success: true });
    reserve.mockResolvedValue({ data: { payment_required: true, workflow_state: 'RESERVED' } });
    dispatch.mockResolvedValue({ success: true });
    initiatePayment.mockResolvedValue({ data: { payment_id: 'pay-1' } });
    cancelRequest.mockResolvedValue({ success: true });
    initiateReturn.mockResolvedValue({ data: { workflow_state: 'RETURNING', shipment_status: 'returning', return_token: 'RET-123' } });
    verifyReturn.mockResolvedValue({ success: true });
    transferConfirm.mockResolvedValue({ success: true });
    update.mockResolvedValue({ success: true });
    addTracking.mockResolvedValue({ success: true });
    confirmHandover.mockResolvedValue({ success: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    windowOpenSpy.mockRestore();
  });

  it('shows classic request workflow layout and request list', async () => {
    render(<RequestWorkflow />);

    expect(screen.queryByText('Incoming Requests by Shared Resource')).not.toBeInTheDocument();
    expect(await screen.findByText('Sorting and Filters')).toBeInTheDocument();
    expect(await screen.findAllByText('Ventilator')).not.toHaveLength(0);
  });

  it('uses compact date header and removes request number label', async () => {
    render(<RequestWorkflow />);

    await screen.findByText('Ventilator');
    expect(screen.queryByText(/request\s*#/i)).not.toBeInTheDocument();
  });

  it('keeps outgoing requests newest-first when API only returns requested_at timestamps', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...outgoingBase,
          id: 'req-old',
          catalog_item_name: 'Older Plasma Pack',
          created_at: undefined,
          requested_at: '2026-03-10T09:00:00Z',
        },
        {
          ...outgoingBase,
          id: 'req-new',
          catalog_item_name: 'New Plasma Pack',
          created_at: undefined,
          requested_at: '2026-03-10T12:00:00Z',
        },
      ],
    });

    const { container } = render(<RequestWorkflow view="outgoing" />);

    await screen.findByText('New Plasma Pack');
    await screen.findByText('Older Plasma Pack');

    const content = container.textContent || '';
    expect(content.indexOf('New Plasma Pack')).toBeLessThan(content.indexOf('Older Plasma Pack'));
  });

  it('sends waive_payment in supplier approval payload when selected', async () => {
    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByLabelText(/Waive payment for requester on approval/i));
    await userEvent.click(await screen.findByRole('button', { name: /approve request/i }));

    await waitFor(() => {
      expect(approve).toHaveBeenCalledWith(
        'req-incoming',
        expect.objectContaining({ decision: 'approved', quantity_approved: 3, waive_payment: true })
      );
    });
  });

  it('initiates SSLCommerz redirect with idempotency-safe payment initiation', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        { ...outgoingBase, id: 'req-pay', status: 'PAYMENT_PENDING', payment_required: true },
      ],
    });

    getById
      .mockResolvedValueOnce({ data: { ...outgoingBase, id: 'req-pay', status: 'PAYMENT_PENDING', payment_required: true } })
      .mockResolvedValueOnce({ data: { ...outgoingBase, id: 'req-pay', status: 'PAYMENT_PENDING', payment_required: true, latest_payment_id: 'pay-123' } });

    initiatePayment.mockResolvedValueOnce({
      data: {
        payment_id: 'pay-123',
        gateway_redirect_url: 'https://sandbox.sslcommerz.test/pay-123',
      },
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    await userEvent.click(await screen.findByRole('button', { name: /proceed to sslcommerz/i }));

    await waitFor(() => {
      expect(initiatePayment).toHaveBeenCalledWith(
        'req-pay',
        expect.objectContaining({
          gateway: 'sslcommerz',
          return_url: expect.stringContaining('payment_request_id=req-pay'),
          cancel_url: expect.stringContaining('payment_request_id=req-pay'),
        }),
        expect.any(String)
      );
      expect(windowOpenSpy).toHaveBeenCalledWith('https://sandbox.sslcommerz.test/pay-123', '_self');
    });
  });

  it('treats empty gateway redirect as zero-price auto-settlement and refreshes request state', async () => {
    getAll.mockResolvedValueOnce({
      data: [{ ...outgoingBase, id: 'req-zero', status: 'PAYMENT_PENDING', payment_required: true }],
    });

    getById
      .mockResolvedValueOnce({
        data: {
          ...outgoingBase,
          id: 'req-zero',
          status: 'PAYMENT_PENDING',
          payment_required: true,
        },
      })
      .mockResolvedValueOnce({
        data: {
          ...outgoingBase,
          id: 'req-zero',
          status: 'PAYMENT_COMPLETED',
          payment_required: true,
          latest_payment_transaction_status: 'SUCCESS',
          latest_payment_id: 'pay-zero',
        },
      });

    initiatePayment.mockResolvedValueOnce({
      data: {
        payment_id: 'pay-zero',
        gateway_redirect_url: '',
      },
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    await userEvent.click(await screen.findByRole('button', { name: /proceed to sslcommerz/i }));

    await waitFor(() => {
      expect(initiatePayment).toHaveBeenCalledWith(
        'req-zero',
        expect.objectContaining({ gateway: 'sslcommerz' }),
        expect.any(String)
      );
      expect(windowOpenSpy).not.toHaveBeenCalled();
      expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'Payment auto-settled' }));
    });
  });

  it('captures dispatch delivery_qr from dispatch response for in-session QR usage', async () => {
    getAll
      .mockResolvedValueOnce({
        data: [{ ...incomingBase, id: 'req-dispatch-flow', status: 'RESERVED', payment_required: false }],
      })
      .mockResolvedValue({
        data: [{ ...incomingBase, id: 'req-dispatch-flow', status: 'IN_TRANSIT', payment_required: false }],
      });

    getById.mockResolvedValue({
      data: {
        ...incomingBase,
        id: 'req-dispatch-flow',
        status: 'RESERVED',
        payment_required: false,
      },
    });

    dispatch.mockResolvedValueOnce({
      data: {
        id: 'dispatch-record-1',
        request: 'req-dispatch-flow',
        shipment: 'shipment-dispatch-flow',
        delivery_qr: {
          qrPayload: 'OPAQUE-DISPATCH-FROM-API',
          expiresAt: '2026-03-10T14:00:00Z',
        },
      },
    });

    render(<RequestWorkflow />);

    await userEvent.type(await screen.findByPlaceholderText(/vehicle number\/details/i), 'Van-11');
    await userEvent.click(await screen.findByRole('button', { name: /assign delivery personnel and dispatch/i }));

    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledWith(
        'req-dispatch-flow',
        expect.objectContaining({
          notes: expect.stringContaining('Delivery personnel: Rider One'),
        })
      );
      expect(dispatch.mock.calls[0][1]).toEqual(expect.objectContaining({ notes: expect.stringContaining('Vehicle: Van-11') }));
      expect(screen.getByText('Dispatch QR')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /download qr code/i })).toBeInTheDocument();
      expect(screen.queryByText('OPAQUE-DISPATCH-FROM-API')).not.toBeInTheDocument();
    });
  });

  it('keeps dispatch qr visible when dispatch response includes token-only delivery_qr', async () => {
    getAll
      .mockResolvedValueOnce({
        data: [{ ...incomingBase, id: 'req-dispatch-token-only', status: 'RESERVED', payment_required: false }],
      })
      .mockResolvedValue({
        data: [{ ...incomingBase, id: 'req-dispatch-token-only', status: 'IN_TRANSIT', payment_required: false }],
      });

    getById.mockResolvedValue({
      data: {
        ...incomingBase,
        id: 'req-dispatch-token-only',
        status: 'RESERVED',
        payment_required: false,
      },
    });

    dispatch.mockResolvedValueOnce({
      data: {
        id: 'dispatch-record-token-only',
        request: 'req-dispatch-token-only',
        shipment: 'shipment-token-only',
        delivery_qr: {
          token: 'OPAQUE-TOKEN-ONLY-123',
          expiresAt: '2026-03-10T14:15:00Z',
        },
      },
    });

    render(<RequestWorkflow />);

    await userEvent.type(await screen.findByPlaceholderText(/vehicle number\/details/i), 'Van-12');
    await userEvent.click(await screen.findByRole('button', { name: /assign delivery personnel and dispatch/i }));

    await waitFor(() => {
      expect(dispatch).toHaveBeenCalledWith(
        'req-dispatch-token-only',
        expect.objectContaining({
          notes: expect.stringContaining('Vehicle: Van-12'),
        })
      );
      expect(screen.getByText('Dispatch QR')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /download qr code/i })).toBeInTheDocument();
      expect(screen.queryByText('OPAQUE-TOKEN-ONLY-123')).not.toBeInTheDocument();
    });
  });

  it('prints dispatch QR with preserved wrapper and scanner-friendly print size', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...incomingBase,
          id: 'req-print-qr',
          status: 'IN_TRANSIT',
          payment_required: false,
          dispatch_qr_payload: 'OPAQUE-PRINT-QR',
        },
      ],
    });

    const writeSpy = vi.fn();
    const printWindowStub = {
      document: {
        write: writeSpy,
        close: vi.fn(),
      },
      focus: vi.fn(),
      print: vi.fn(),
      close: vi.fn(),
      addEventListener: vi.fn(),
    } as unknown as Window;

    windowOpenSpy.mockReturnValueOnce(printWindowStub);

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('button', { name: /print qr code/i }));

    expect(windowOpenSpy).toHaveBeenCalled();
    expect(writeSpy).toHaveBeenCalledWith(expect.stringContaining('qr-print-target'));
    expect(writeSpy).toHaveBeenCalledWith(expect.stringContaining('width: 420px'));
  });

  it('renders dispatch qr from token-only shipment fields while workflow is active', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...incomingBase,
          id: 'req-qr-bundle',
          status: 'IN_TRANSIT',
          payment_required: true,
          latest_shipment: {
            id: 'shipment-qr-bundle',
            tokens: {
              dispatch_token: 'BUNDLE-DISPATCH-123',
              receive_token: 'BUNDLE-RECEIVE-123',
            },
          },
        },
      ],
    });

    render(<RequestWorkflow />);

    expect(await screen.findByText('Dispatch QR')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download qr code/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /confirm sender handover/i })).not.toBeInTheDocument();
    expect(screen.queryByText('BUNDLE-DISPATCH-123')).not.toBeInTheDocument();
  });

  it('ignores backend-provided hidden receiver-token fields for completion UI', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...incomingBase,
          id: 'req-qr-image',
          status: 'IN_TRANSIT',
          payment_required: true,
          dispatch_qr_code_url: 'https://example.test/qr/req-qr-image.svg',
          receiver_token: 'RECEIVER-ONLY-123',
        },
      ],
    });

    render(<RequestWorkflow />);

    expect(await screen.findByText('Dispatch QR')).toBeInTheDocument();
    expect(screen.queryByText(/receiver-only-123/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/receive token/i)).not.toBeInTheDocument();
  });

  it('ignores receiver-token-only variant fields and keeps receiver confirmation minimal UI', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...outgoingBase,
          id: 'req-receiver-qr',
          status: 'IN_TRANSIT',
          payment_required: false,
          completion_stage: 'SENDER_CONFIRMED',
          latest_shipment: {
            id: 'shipment-receiver-qr',
            tokens: {
              receiver_qr_token: 'RECEIVER-QR-777',
            },
          },
        },
      ],
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));

    expect(await screen.findByText('Receiver confirmation')).toBeInTheDocument();
    expect(screen.queryByText('RECEIVER-QR-777')).not.toBeInTheDocument();
  });

  it('handles return flow with polling refresh until callback-updated completion is visible', async () => {
    window.history.replaceState(
      {},
      '',
      '/sharing/requests?payment_request_id=req-return&status=success'
    );

    getAll.mockResolvedValueOnce({
      data: [{ ...outgoingBase, id: 'req-return', status: 'PAYMENT_PENDING', payment_required: true }],
    });

    let refreshCalls = 0;
    getById.mockImplementation(async () => {
      refreshCalls += 1;
      if (refreshCalls === 1) {
        return {
          data: {
            ...outgoingBase,
            id: 'req-return',
            status: 'PAYMENT_PENDING',
            payment_required: true,
            latest_payment_transaction_status: 'PENDING',
          },
        };
      }
      return {
        data: {
          ...outgoingBase,
          id: 'req-return',
          status: 'PAYMENT_COMPLETED',
          payment_required: true,
          latest_payment_transaction_status: 'SUCCESS',
        },
      };
    });

    render(<RequestWorkflow />);

    await waitFor(() => {
      expect(getById).toHaveBeenCalledWith('req-return');
      expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'Checking payment status' }));
    }, { timeout: 2500 });

    await waitFor(() => {
      expect(getById.mock.calls.length).toBeGreaterThanOrEqual(2);
    }, { timeout: 9000 });
  }, 15000);

  it('cancels a pre-dispatch request using POST cancel endpoint', async () => {
    getAll.mockResolvedValueOnce({
      data: [{ ...outgoingBase, id: 'req-cancel', status: 'RESERVED', payment_required: true }],
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    await userEvent.click(await screen.findByRole('button', { name: /^cancel request$/i }));
    await userEvent.type(await screen.findByPlaceholderText(/optional cancellation reason/i), 'Inventory no longer needed');
    await userEvent.click(await screen.findByRole('button', { name: /confirm cancellation/i }));

    await waitFor(() => {
      expect(cancelRequest).toHaveBeenCalledWith('req-cancel', { reason: 'Inventory no longer needed' });
    });
  });

  it('initiates return for dispatched request with required reason and shows return context', async () => {
    getAll.mockResolvedValueOnce({
      data: [{ ...outgoingBase, id: 'req-return-init', status: 'IN_TRANSIT', payment_required: true }],
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    await userEvent.type(await screen.findByPlaceholderText(/reason for initiating return/i), 'Shipment condition issue');
    await userEvent.click(await screen.findByRole('button', { name: /initiate return/i }));

    await waitFor(() => {
      expect(initiateReturn).toHaveBeenCalledWith('req-return-init', { reason: 'Shipment condition issue' });
      expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'Return started' }));
    });
  });

  it('verifies return using return token in sender/logistics context', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...incomingBase,
          id: 'req-return-verify',
          status: 'RETURNING',
          payment_required: true,
          return_token: 'RET-VERIFY-1',
          shipment_status: 'returning',
        },
      ],
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('button', { name: /verify return/i }));

    await waitFor(() => {
      expect(verifyReturn).toHaveBeenCalledWith(
        'req-return-verify',
        expect.objectContaining({ return_token: expect.stringContaining('RET-VERIFY-1') })
      );
    });
  });

  it('shows error when return initiation reason is missing', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        { ...outgoingBase, id: 'req-return-missing-reason', status: 'IN_TRANSIT', payment_required: true },
      ],
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    await userEvent.click(await screen.findByRole('button', { name: /initiate return/i }));

    await waitFor(() => {
      expect(initiateReturn).not.toHaveBeenCalled();
      expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'Reason required' }));
    });
  });

  it('shows forbidden-role style error when return verification is rejected', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...incomingBase,
          id: 'req-forbidden-verify',
          status: 'RETURNING',
          payment_required: true,
          return_token: 'RET-FORBIDDEN-1',
          shipment_status: 'returning',
        },
      ],
    });
    verifyReturn.mockRejectedValueOnce(new Error('403 Forbidden: not permitted to verify return'));

    render(<RequestWorkflow />);
    await userEvent.click(await screen.findByRole('button', { name: /verify return/i }));

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'Return verification failed' }));
    });
  });

  it('renders failed payment state and failure context for callback failure outcomes', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...incomingBase,
          id: 'req-failed',
          status: 'FAILED',
          payment_required: true,
          failed_reason: 'SSLCommerz validation failed',
          latest_payment_transaction_status: 'FAILED',
        },
      ],
    });

    render(<RequestWorkflow />);

    const requestTitle = await screen.findByRole('heading', { name: 'Ventilator' });
    if (!screen.queryByText(/Payment failure context/i)) {
      await userEvent.click(requestTitle);
    }

    expect(screen.queryByText(/payment state: failed/i)).not.toBeInTheDocument();
    expect(await screen.findByText(/Payment failure context/i)).toBeInTheDocument();
    expect(screen.getByText(/SSLCommerz validation failed/i)).toBeInTheDocument();
  });

  it('does not render sender-side transfer confirmation action in in-transit sender panel', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...incomingBase,
          id: 'req-sender-stage-removed',
          status: 'IN_TRANSIT',
          payment_required: false,
          completion_stage: 'NOT_STARTED',
        },
      ],
    });

    render(<RequestWorkflow />);

    await screen.findByRole('heading', { name: 'Ventilator' });
    expect(screen.queryByText(/sender confirmation/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /confirm sender handover/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/shipment update/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /mark shipment progress/i })).not.toBeInTheDocument();
    expect(transferConfirm).not.toHaveBeenCalled();
  });

  it('hides dispatch qr payload clutter in completed state panels', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...incomingBase,
          id: 'req-completed-clean',
          status: 'COMPLETED',
          payment_required: false,
          completion_stage: 'RECEIVER_CONFIRMED',
          dispatch_qr_payload: 'SHOULD-NOT-RENDER',
        },
      ],
    });

    render(<RequestWorkflow />);

    expect((await screen.findAllByText('Completed')).length).toBeGreaterThan(0);
    expect(screen.queryByText(/dispatch qr payload/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /download qr code/i })).not.toBeInTheDocument();
  });

  it('submits receiver-stage transfer confirmation and reaches completed state', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...outgoingBase,
          id: 'req-receiver-stage',
          status: 'IN_TRANSIT',
          payment_required: false,
          completion_stage: 'SENDER_CONFIRMED',
        },
      ],
    });

    getById.mockResolvedValueOnce({
      data: {
        ...outgoingBase,
        id: 'req-receiver-stage',
        status: 'COMPLETED',
        payment_required: false,
        completion_stage: 'RECEIVER_CONFIRMED',
        sender_confirmed_at: '2026-03-10T12:10:00Z',
        receiver_confirmed_at: '2026-03-10T12:20:00Z',
      },
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    const receiverTokenInput = await screen.findByPlaceholderText(/scan or paste opaque qrpayload/i);
    await userEvent.click(receiverTokenInput);
    await userEvent.paste('OPAQUE-RECEIVER-1');
    await userEvent.click(await screen.findByRole('button', { name: /confirm receiver handover/i }));

    await waitFor(() => {
      expect(transferConfirm).toHaveBeenCalledWith(
        'req-receiver-stage',
        expect.objectContaining({
          qrPayload: 'OPAQUE-RECEIVER-1',
          quantity_received: expect.any(Number),
        }),
        expect.any(String)
      );
      expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'Delivery successfully confirmed' }));
    });
  });

  it('submits scanned payload unchanged when receiver scans JSON containing extra fields', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...outgoingBase,
          id: 'req-receiver-no-receive-token',
          status: 'IN_TRANSIT',
          payment_required: false,
          completion_stage: 'NOT_STARTED',
        },
      ],
    });

    getById.mockResolvedValueOnce({
      data: {
        ...outgoingBase,
        id: 'req-receiver-no-receive-token',
        status: 'COMPLETED',
        payment_required: false,
        completion_stage: 'RECEIVER_CONFIRMED',
      },
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    const receiverTokenInput = await screen.findByPlaceholderText(/scan or paste opaque qrpayload/i);
    await userEvent.click(receiverTokenInput);
    await userEvent.paste('{"requestId":"req-receiver-no-receive-token","dispatch_token":"DISPATCH-ONLY-77","receive_token":"LEGACY-IGNORE"}');
    await userEvent.click(await screen.findByRole('button', { name: /confirm receiver handover/i }));

    await waitFor(() => {
      expect(transferConfirm).toHaveBeenCalledWith(
        'req-receiver-no-receive-token',
        {
          qrPayload: '{"requestId":"req-receiver-no-receive-token","dispatch_token":"DISPATCH-ONLY-77","receive_token":"LEGACY-IGNORE"}',
          quantity_received: expect.any(Number),
        },
        expect.any(String)
      );
      const submittedPayload = transferConfirm.mock.calls[0][1] as Record<string, unknown>;
      expect(submittedPayload.receive_token).toBeUndefined();
      expect(submittedPayload.dispatch_token).toBeUndefined();
    });
  });

  it('disables receiver completion when transfer-confirm permission is missing', async () => {
    mockUser = {
      hospital_id: 'hospital-owner-1',
      effective_permissions: WORKFLOW_TEST_PERMISSIONS.filter((code) => code !== 'hospital:request.transfer.confirm'),
    };

    getAll.mockResolvedValueOnce({
      data: [{ ...outgoingBase, id: 'req-missing-permission', status: 'IN_TRANSIT', payment_required: false }],
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    await userEvent.type(await screen.findByPlaceholderText(/scan or paste opaque qrpayload/i), 'OPAQUE-NO-PERM-1');

    const receiverButton = await screen.findByRole('button', { name: /confirm receiver handover/i });
    expect(receiverButton).toBeDisabled();
    expect(screen.getByText(/missing permission: hospital:request.transfer.confirm/i)).toBeInTheDocument();
  });

  it('disables receiver completion when user context is not healthcare', async () => {
    mockUser = {
      hospital_id: 'hospital-owner-1',
      context: 'PLATFORM',
      effective_permissions: WORKFLOW_TEST_PERMISSIONS,
    };

    getAll.mockResolvedValueOnce({
      data: [{ ...outgoingBase, id: 'req-context-mismatch', status: 'IN_TRANSIT', payment_required: false }],
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    await userEvent.type(await screen.findByPlaceholderText(/scan or paste opaque qrpayload/i), 'OPAQUE-CTX-1');

    const receiverButton = await screen.findByRole('button', { name: /confirm receiver handover/i });
    expect(receiverButton).toBeDisabled();
    expect(screen.getByText(/healthcare context is required for receiver confirmation/i)).toBeInTheDocument();
  });

  it('unlocks receiver confirmation when backend sends sender_confirmed boolean without completion stage text', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...outgoingBase,
          id: 'req-receiver-bool-flag',
          status: 'IN_TRANSIT',
          payment_required: false,
          sender_confirmed: true,
        },
      ],
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));

    await userEvent.type(
      await screen.findByPlaceholderText(/scan or paste opaque qrpayload/i),
      'OPAQUE-BOOL-1',
    );

    const receiverButton = await screen.findByRole('button', { name: /confirm receiver handover/i });
    expect(receiverButton).toBeEnabled();
    expect(screen.queryByText(/awaiting supplier sender confirmation/i)).not.toBeInTheDocument();
  });

  it('hides sender confirmation and shipment tracking actions after terminal completion stage', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...incomingBase,
          id: 'req-terminal-lock',
          status: 'IN_TRANSIT',
          payment_required: false,
          dispatch_qr_payload: 'OPAQUE-LOCK-1',
          completion_stage: 'RECEIVER_CONFIRMED',
        },
      ],
    });

    render(<RequestWorkflow />);

    expect(screen.queryByRole('button', { name: /confirm sender handover/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/sender confirmation/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/shipment update/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /mark shipment progress/i })).not.toBeInTheDocument();

    expect(transferConfirm).not.toHaveBeenCalled();
    expect(addTracking).not.toHaveBeenCalled();
  });

  it('locks receiver completion action after terminal completion stage', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...outgoingBase,
          id: 'req-receiver-locked',
          status: 'IN_TRANSIT',
          payment_required: false,
          dispatch_qr_payload: 'OPAQUE-RECEIVER-LOCK',
          completion_stage: 'RECEIVER_CONFIRMED',
        },
      ],
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    const receiverButton = await screen.findByRole('button', { name: /delivery already confirmed/i });
    expect(receiverButton).toBeDisabled();
    expect(transferConfirm).not.toHaveBeenCalled();
  });

  it('does not render transfer confirmation actions for returning workflows', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...outgoingBase,
          id: 'req-returning-no-completion',
          status: 'RETURNING',
          payment_required: false,
          completion_stage: 'SENDER_CONFIRMED',
          dispatch_qr_payload: 'OPAQUE-RETURNING',
        },
      ],
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    expect(screen.queryByRole('button', { name: /confirm receiver handover/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /confirm sender handover/i })).not.toBeInTheDocument();
    expect(transferConfirm).not.toHaveBeenCalled();
  });

  it('reuses the same idempotency key when retrying receiver confirmation after a transient failure', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...outgoingBase,
          id: 'req-idempotency-retry-receiver',
          status: 'IN_TRANSIT',
          payment_required: false,
          completion_stage: 'NOT_STARTED',
        },
      ],
    });

    transferConfirm
      .mockRejectedValueOnce(new Error('temporary upstream failure'))
      .mockResolvedValueOnce({ success: true });

    getById.mockResolvedValueOnce({
      data: {
        ...outgoingBase,
        id: 'req-idempotency-retry-receiver',
        status: 'COMPLETED',
        payment_required: false,
        completion_stage: 'RECEIVER_CONFIRMED',
      },
    });

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    await userEvent.type(await screen.findByPlaceholderText(/scan or paste opaque qrpayload/i), 'OPAQUE-RETRY-1');
    const receiverButton = await screen.findByRole('button', { name: /confirm receiver handover/i });
    await userEvent.click(receiverButton);

    await waitFor(() => {
      expect(transferConfirm).toHaveBeenCalledTimes(1);
      expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'Receiver confirmation failed' }));
    });

    await userEvent.click(await screen.findByRole('button', { name: /confirm receiver handover/i }));

    await waitFor(() => {
      expect(transferConfirm).toHaveBeenCalledTimes(2);
    });

    const firstIdempotencyKey = transferConfirm.mock.calls[0][2];
    const secondIdempotencyKey = transferConfirm.mock.calls[1][2];

    expect(firstIdempotencyKey).toBeTruthy();
    expect(secondIdempotencyKey).toBe(firstIdempotencyKey);
  });

  it('shows qr-expired error messaging when receiver transfer confirmation payload is expired', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...outgoingBase,
          id: 'req-token-expired-receiver',
          status: 'IN_TRANSIT',
          payment_required: false,
          completion_stage: 'NOT_STARTED',
        },
      ],
    });

    transferConfirm.mockRejectedValueOnce(new Error('QR code has expired.'));

    render(<RequestWorkflow />);

    await userEvent.click(await screen.findByRole('tab', { name: /outgoing requests/i }));
    await userEvent.type(await screen.findByPlaceholderText(/scan or paste opaque qrpayload/i), 'OPAQUE-EXPIRED-1');
    await userEvent.click(await screen.findByRole('button', { name: /confirm receiver handover/i }));

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ title: 'Expired QR code' }));
    });
  });

  it('supports waived-payment path by skipping payment action in requester view and allowing dispatch', async () => {
    getAll.mockResolvedValueOnce({
      data: [
        {
          ...incomingBase,
          id: 'req-waived-incoming',
          status: 'RESERVED',
          payment_required: false,
          payment_note: 'Supplier waived payment due emergency transfer',
        },
        {
          ...outgoingBase,
          id: 'req-waived-outgoing',
          status: 'RESERVED',
          payment_required: false,
          payment_note: 'Supplier waived payment due emergency transfer',
        },
      ],
    });

    render(<RequestWorkflow />);

    const incomingTitle = await screen.findByRole('heading', { name: 'Ventilator' });
    if (!screen.queryByRole('button', { name: /assign delivery personnel and dispatch/i })) {
      await userEvent.click(incomingTitle);
    }

    expect(await screen.findByRole('button', { name: /assign delivery personnel and dispatch/i })).toBeEnabled();
    expect(screen.queryByText(/Payment waived/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /proceed to sslcommerz/i })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /outgoing requests/i }));
    const outgoingTitle = await screen.findByRole('heading', { name: 'Blood Bag' });
    if (!screen.queryByText(/Supplier waived payment/i)) {
      await userEvent.click(outgoingTitle);
    }
    expect(screen.getByText(/Supplier waived payment/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /proceed to sslcommerz/i })).not.toBeInTheDocument();
  });
});
