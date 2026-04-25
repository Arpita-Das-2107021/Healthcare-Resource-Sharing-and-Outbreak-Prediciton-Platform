import {
  analyticsApi,
  broadcastsApi,
  conversationsApi,
  hospitalsApi,
  integrationsApi,
  inventoryModuleApi,
  pharmacyCsvApi,
  offboardingApi,
  hospitalUpdateRequestsApi,
  invitationsApi,
  salesApi,
  shipmentsApi,
  requestsApi,
} from '@/services/api';

describe('API contract wrappers', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: async () => JSON.stringify({ success: true, data: {} }),
    } as Response);
  });

  it('uses hospital-scoped offboarding submit endpoint', async () => {
    await hospitalsApi.submitOffboardingRequest('hospital-1', 'Need to offboard');

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/hospitals/hospital-1/offboarding-request/',
      expect.objectContaining({
        method: 'POST',
      })
    );
  });

  it('uses super-admin direct offboarding endpoint', async () => {
    await hospitalsApi.adminOffboard('hospital-1', {
      reason: 'Direct offboarding by SUPER_ADMIN.',
      admin_notes: 'Administrative action',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/hospitals/hospital-1/admin-offboard/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          reason: 'Direct offboarding by SUPER_ADMIN.',
          admin_notes: 'Administrative action',
        }),
      })
    );
  });

  it('uses admin offboarding listing endpoint', async () => {
    await offboardingApi.listAdminRequests({ status: 'pending' });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/admin/hospital-offboarding-requests/?status=pending',
      expect.any(Object)
    );
  });

  it('uses admin offboarding review email endpoint', async () => {
    await offboardingApi.sendReviewEmail('off-1', {
      recipient_email: 'ops@city.test',
      subject: 'Offboarding review required',
      message: 'Please address pending clarifications before approval.',
    });

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/admin/hospital-offboarding-requests/off-1/send-review-email/'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          recipient_email: 'ops@city.test',
          subject: 'Offboarding review required',
          message: 'Please address pending clarifications before approval.',
        }),
      })
    );
  });

  it('uses direct hospital offboarding review email endpoint', async () => {
    await hospitalsApi.sendOffboardingReviewEmail('hospital-1', {
      recipient_email: 'ops@city.test',
      subject: 'Direct offboarding review notice',
      message: 'Please review this direct offboarding notice.',
    });

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/hospitals/hospital-1/admin-offboard/send-review-email/'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          recipient_email: 'ops@city.test',
          subject: 'Direct offboarding review notice',
          message: 'Please review this direct offboarding notice.',
        }),
      })
    );
  });

  it('uses hospital update request reject payload', async () => {
    await hospitalUpdateRequestsApi.reject('req-1', 'Verification failed');

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/admin/hospital-update-requests/req-1/reject/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ rejection_reason: 'Verification failed' }),
      })
    );
  });

  it('uses hospital update request review email endpoint', async () => {
    await hospitalUpdateRequestsApi.sendReviewEmail('req-1', {
      recipient_email: 'ops@city.test',
      subject: 'Update review required',
      message: 'Please revise and resubmit your request.',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/admin/hospital-update-requests/req-1/send-review-email/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          recipient_email: 'ops@city.test',
          subject: 'Update review required',
          message: 'Please revise and resubmit your request.',
        }),
      })
    );
  });

  it('uses broadcast close endpoint', async () => {
    await broadcastsApi.close('broadcast-1');

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/broadcasts/broadcast-1/close/',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('uses broadcast unread count endpoint', async () => {
    await broadcastsApi.getUnreadCount();

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/broadcasts/unread-count/',
      expect.any(Object)
    );
  });

  it('falls back to emergency broadcast create endpoint when primary create routes fail', async () => {
    vi.spyOn(global, 'fetch').mockRestore();
    const fetchMock = vi.spyOn(global, 'fetch');

    fetchMock
      .mockRejectedValueOnce(new Error('broadcast create endpoint unavailable'))
      .mockRejectedValueOnce(new Error('broadcast create payload rejected'))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: async () => JSON.stringify({ success: true, data: { id: 'broadcast-22' } }),
      } as Response);

    await broadcastsApi.create({
      title: 'Emergency oxygen support',
      message: 'Need 8 cylinders within 30 minutes',
      scope: 'all',
      priority: 'urgent',
      allow_response: true,
      send_email: true,
      notify_recipients: true,
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/api/v1/broadcasts/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          title: 'Emergency oxygen support',
          message: 'Need 8 cylinders within 30 minutes',
          scope: 'all',
          priority: 'urgent',
          allow_response: true,
          send_email: true,
          notify_recipients: true,
        }),
      })
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/api/v1/broadcasts/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          title: 'Emergency oxygen support',
          message: 'Need 8 cylinders within 30 minutes',
          scope: 'all',
          priority: 'urgent',
          allow_response: true,
        }),
      })
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/api/v1/emergency-broadcasts/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          title: 'Emergency oxygen support',
          message: 'Need 8 cylinders within 30 minutes',
          scope: 'all',
          priority: 'urgent',
          allow_response: true,
          send_email: true,
          notify_recipients: true,
        }),
      })
    );
  });

  it('retries broadcast create without location when backend rejects location payload', async () => {
    vi.spyOn(global, 'fetch').mockRestore();
    const fetchMock = vi.spyOn(global, 'fetch');

    fetchMock
      .mockRejectedValueOnce(new Error('location schema mismatch'))
      .mockRejectedValueOnce(new Error('location still rejected'))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: async () => JSON.stringify({ success: true, data: { id: 'broadcast-44' } }),
      } as Response);

    await broadcastsApi.create({
      title: 'Localized emergency alert',
      message: 'Use nearby support hubs only.',
      scope: 'all',
      priority: 'urgent',
      allow_response: true,
      send_email: true,
      notify_recipients: true,
      location: {
        lat: 23.810331,
        lng: 90.412521,
        address: 'Dhaka, Bangladesh',
      },
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/api/v1/broadcasts/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          title: 'Localized emergency alert',
          message: 'Use nearby support hubs only.',
          scope: 'all',
          priority: 'urgent',
          allow_response: true,
          send_email: true,
          notify_recipients: true,
          location: {
            lat: 23.810331,
            lng: 90.412521,
            address: 'Dhaka, Bangladesh',
          },
        }),
      })
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'http://localhost:8000/api/v1/broadcasts/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          title: 'Localized emergency alert',
          message: 'Use nearby support hubs only.',
          scope: 'all',
          priority: 'urgent',
          allow_response: true,
        }),
      })
    );
  });

  it('uses chat global unread count endpoint', async () => {
    await conversationsApi.getGlobalUnreadCount();

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/chat/unread-count/',
      expect.any(Object)
    );
  });

  it('uses preferred conversation message send endpoint with normalized body payload', async () => {
    await conversationsApi.sendMessage('conversation-3', {
      body: '  Need rapid response  ',
      mentions: ['user-2'],
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/conversations/conversation-3/messages/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ body: 'Need rapid response', mentions: ['user-2'] }),
      })
    );
  });

  it('falls back to chat-scoped send endpoint when preferred message endpoint fails', async () => {
    vi.spyOn(global, 'fetch').mockRestore();
    const fetchMock = vi.spyOn(global, 'fetch');
    fetchMock
      .mockRejectedValueOnce(new Error('conversation message endpoint unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: async () => JSON.stringify({ success: true, data: {} }),
      } as Response);

    await conversationsApi.sendMessage('conversation-4', { content: 'Fallback delivery' });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/api/v1/conversations/conversation-4/messages/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ body: 'Fallback delivery' }),
      })
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/api/v1/chat/conversations/conversation-4/messages/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ body: 'Fallback delivery' }),
      })
    );
  });

  it('uses conversation read endpoint with preferred last_read_message_id payload', async () => {
    await conversationsApi.markRead('conversation-1', { last_read_message_id: 'message-9' });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/conversations/conversation-1/read/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ last_read_message_id: 'message-9' }),
      })
    );
  });

  it('falls back to chat scoped read endpoint when communication read endpoint fails', async () => {
    vi.spyOn(global, 'fetch').mockRestore();
    const fetchMock = vi.spyOn(global, 'fetch');
    fetchMock
      .mockRejectedValueOnce(new Error('communications read endpoint unavailable'))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: async () => JSON.stringify({ success: true, data: {} }),
      } as Response);

    await conversationsApi.markRead('conversation-2', { last_read_message_id: 'message-10' });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/api/v1/conversations/conversation-2/read/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ last_read_message_id: 'message-10' }),
      })
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/api/v1/chat/conversations/conversation-2/read/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ last_read_message_id: 'message-10' }),
      })
    );
  });

  it('supports markRead without payload using empty object body', async () => {
    await conversationsApi.markRead('conversation-5');

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/conversations/conversation-5/read/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({}),
      })
    );
  });

  it('uses broadcast read endpoint', async () => {
    await broadcastsApi.markRead('broadcast-2');

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/broadcasts/broadcast-2/read/',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('uses broadcast response endpoint with payload', async () => {
    await broadcastsApi.respond('broadcast-3', { response: 'We can supply 10 units.' });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/broadcasts/broadcast-3/respond/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ response: 'We can supply 10 units.' }),
      })
    );
  });

  it('uses resource request create endpoint with expected payload shape', async () => {
    await requestsApi.create({
      supplying_hospital: 'hospital-1',
      catalog_item: 'catalog-1',
      quantity_requested: 2,
      priority: 'normal',
      notes: 'Need urgent support',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/requests/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          supplying_hospital: 'hospital-1',
          catalog_item: 'catalog-1',
          quantity_requested: 2,
          priority: 'normal',
          notes: 'Need urgent support',
        }),
      })
    );
  });

  it('aggregates all request pages when page is not explicitly provided', async () => {
    vi.spyOn(global, 'fetch').mockRestore();
    const fetchMock = vi.spyOn(global, 'fetch');

    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: async () =>
          JSON.stringify({
            data: [{ id: 'request-1' }],
            meta: { page: 1, limit: 1, total: 2, total_pages: 2 },
          }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: async () =>
          JSON.stringify({
            data: [{ id: 'request-2' }],
            meta: { page: 2, limit: 1, total: 2, total_pages: 2 },
          }),
      } as Response);

    const response = await requestsApi.getAll();

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://localhost:8000/api/v1/requests/',
      expect.any(Object)
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8000/api/v1/requests/?page=2',
      expect.any(Object)
    );
    expect(response).toEqual(
      expect.objectContaining({
        data: [{ id: 'request-1' }, { id: 'request-2' }],
      })
    );
  });

  it('keeps request list single-page when explicit page query is provided', async () => {
    vi.spyOn(global, 'fetch').mockRestore();
    const fetchMock = vi.spyOn(global, 'fetch');

    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: async () =>
        JSON.stringify({
          data: [{ id: 'request-1' }],
          meta: { page: 1, limit: 20, total: 40, total_pages: 2 },
        }),
    } as Response);

    await requestsApi.getAll({ page: '1' });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/requests/?page=1',
      expect.any(Object)
    );
  });

  it('uses internal sales list endpoint with query params', async () => {
    await salesApi.getAll({ page: '2', page_size: '25', search: 'Amoxicillin' });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/sales/records/?page=2&page_size=25&search=Amoxicillin',
      expect.any(Object)
    );
  });

  it('uses internal sales create endpoint and returns response status', async () => {
    await salesApi.create(
      {
        quantity_sold: 4,
        medicine_name: 'Paracetamol',
      },
      'sales-idem-1'
    );

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/sales/records/',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Idempotency-Key': 'sales-idem-1' }),
        body: JSON.stringify({
          quantity_sold: 4,
          medicine_name: 'Paracetamol',
        }),
      })
    );
  });

  it('uses internal sales detail endpoint', async () => {
    await salesApi.getById('sale-1');

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/sales/records/sale-1/',
      expect.any(Object)
    );
  });

  it('uses request approve endpoint', async () => {
    await requestsApi.approve('request-1', {
      decision: 'approved',
      quantity_approved: 2,
      waive_payment: false,
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/requests/request-1/approve/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          decision: 'approved',
          quantity_approved: 2,
          waive_payment: false,
        }),
      })
    );
  });

  it('uses invitation revoke delete endpoint', async () => {
    await invitationsApi.revoke('inv-1');

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/invitations/inv-1/',
      expect.objectContaining({ method: 'DELETE' })
    );
  });

  it('uses request reserve endpoint with idempotency key', async () => {
    await requestsApi.reserve('request-2', { requested_quantity: 5 }, 'idem-key-1');

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/requests/request-2/reserve/',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Idempotency-Key': 'idem-key-1' }),
      })
    );
  });

  it('dispatch mutation posts directly to dispatch endpoint', async () => {
    await requestsApi.dispatch('request-terminal', { notes: 'Attempt dispatch' });

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/requests/request-terminal/dispatch/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ notes: 'Attempt dispatch' }),
      })
    );
  });

  it('shipment tracking mutation posts directly to tracking endpoint', async () => {
    await shipmentsApi.addTracking('shipment-terminal', {
      status: 'in_transit',
      location: 'Hub A',
    });

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/shipments/shipment-terminal/tracking/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          status: 'in_transit',
          location: 'Hub A',
        }),
      })
    );
  });

  it('uses request transfer confirm endpoint with qrPayload payload', async () => {
    await requestsApi.transferConfirm(
      'request-3',
      {
        qrPayload: 'opaque-qr-payload',
        quantity_received: 1,
      },
      'idem-key-2'
    );

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/requests/request-3/transfer-confirm/',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Idempotency-Key': 'idem-key-2' }),
        body: JSON.stringify({
          qrPayload: 'opaque-qr-payload',
          quantity_received: 1,
        }),
      })
    );
  });

  it('supports sender-stage transfer confirm with qrPayload only', async () => {
    await requestsApi.transferConfirm(
      'request-3a',
      {
        qrPayload: 'opaque-qr-payload-only',
        quantity_received: 2,
      },
      'idem-key-sender-only'
    );

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/requests/request-3a/transfer-confirm/',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Idempotency-Key': 'idem-key-sender-only' }),
        body: JSON.stringify({
          qrPayload: 'opaque-qr-payload-only',
          quantity_received: 2,
        }),
      })
    );
  });

  it('does not expose or call legacy confirm-delivery endpoint', async () => {
    expect((requestsApi as Record<string, unknown>).confirmDelivery).toBeUndefined();

    await requestsApi.transferConfirm('request-legacy-guard', {
      qrPayload: 'opaque-legacy-guard',
      quantity_received: 1,
    });

    const calledLegacyEndpoint = (fetch as ReturnType<typeof vi.fn>).mock.calls.some((call) => {
      const url = String(call[0] || '');
      return url.includes('/api/v1/requests/confirm-delivery/');
    });

    expect(calledLegacyEndpoint).toBe(false);
  });

  it('uses legacy confirm payment endpoint', async () => {
    await requestsApi.confirmPayment('request-4', {
      payment_status: 'paid',
      payment_note: 'Manual settlement',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/requests/request-4/confirm-payment/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ payment_status: 'paid', payment_note: 'Manual settlement' }),
      })
    );
  });

  it('uses workflow-v2 gateway confirm endpoint without payment_id when omitted', async () => {
    await requestsApi.confirmGatewayPayment('request-4', {
      payment_status: 'SUCCESS',
      provider_transaction_id: 'trx-4',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/requests/request-4/payments/confirm/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ payment_status: 'SUCCESS', provider_transaction_id: 'trx-4' }),
      })
    );
  });

  it('uses refunds initiate endpoint', async () => {
    await requestsApi.initiateRefund('request-5', { reason: 'Delivery failed' });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/requests/request-5/refunds/initiate/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ reason: 'Delivery failed' }),
      })
    );
  });

  it('uses return initiation alias endpoint', async () => {
    await requestsApi.initiateReturn('request-6', { reason: 'Shipment condition issue' });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/requests/request-6/refunds/initiate/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          cause: 'REQUESTER_CANCELLATION',
          cause_note: 'Shipment condition issue',
        }),
      })
    );
  });

  it('maps handover confirmation to shipment tracking endpoint', async () => {
    await shipmentsApi.confirmHandover('shipment-9', {
      receiver_name: 'Receiver',
      receiver_position: 'Pharmacist',
      notes: 'Received in good condition',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/shipments/shipment-9/tracking/',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('loads platform analytics from the platform summary endpoint', async () => {
    await analyticsApi.get();

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/analytics/platform-summary/',
      expect.any(Object)
    );
  });

  it('uses integration sync endpoint', async () => {
    await integrationsApi.sync('integration-7');

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/integrations/integration-7/sync/',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('sends language in inventory validate import form-data', async () => {
    const file = new File(['name,quantity\nItem,3'], 'inventory.csv', { type: 'text/csv' });

    await inventoryModuleApi.validateImport(
      file,
      {
        mode: 'MERGE',
        language: 'bn',
      },
      'idem-import-1'
    );

    const [, request] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    const body = request.body as FormData;

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/inventory-module/imports/validate/',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Idempotency-Key': 'idem-import-1' }),
      })
    );
    expect(body.get('file')).toBe(file);
    expect(body.get('mode')).toBe('MERGE');
    expect(body.get('language')).toBe('bn');
    expect(body.get('idempotency_key')).toBe('idem-import-1');
  });

  it('mirrors generated idempotency key to validate header and form-data', async () => {
    const file = new File(['name,quantity\nItem,3'], 'inventory.csv', { type: 'text/csv' });

    await inventoryModuleApi.validateImport(file, {
      mode: 'MERGE',
      language: 'en',
    });

    const [, request] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    const body = request.body as FormData;
    const headers = request.headers as Record<string, string>;
    const resolvedKey = body.get('idempotency_key');

    expect(typeof resolvedKey).toBe('string');
    expect(resolvedKey).toBeTruthy();
    if (typeof resolvedKey === 'string') {
      expect(headers['Idempotency-Key']).toBe(resolvedKey);
    }
  });

  it('sends commit idempotency key in both header and payload', async () => {
    const file = new File(['name,quantity\nItem,3'], 'inventory.csv', { type: 'text/csv' });

    await inventoryModuleApi.commitImport(
      file,
      {
        mode: 'MERGE',
      },
      'idem-commit-1'
    );

    const [, request] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    const body = request.body as FormData;
    const headers = request.headers as Record<string, string>;

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/inventory-module/imports/commit/',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Idempotency-Key': 'idem-commit-1' }),
      })
    );
    expect(headers['Idempotency-Key']).toBe('idem-commit-1');
    expect(body.get('file')).toBe(file);
    expect(body.get('idempotency_key')).toBe('idem-commit-1');
    expect(body.get('mode')).toBe('MERGE');
  });

  it('uses csv chat endpoint with file_id, query, and language only', async () => {
    await inventoryModuleApi.chatImportErrors({
      file_id: 'file-123',
      query: 'Explain missing required columns.',
      language: 'en',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/csv/chat',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          file_id: 'file-123',
          query: 'Explain missing required columns.',
          language: 'en',
        }),
      })
    );
  });

  it('uses inventory csv create session endpoint', async () => {
    await inventoryModuleApi.createChatSession({
      file_id: 'file-123',
      language: 'bn',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/csv/sessions/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          file_id: 'file-123',
          language: 'bn',
        }),
      })
    );
  });

  it('uses inventory csv session message endpoint', async () => {
    await inventoryModuleApi.sendChatSessionMessage('session-1', {
      query: 'Explain row errors',
      language: 'en',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/csv/sessions/session-1/messages/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          query: 'Explain row errors',
          language: 'en',
        }),
      })
    );
  });

  it('uses inventory csv session history endpoint with paging params', async () => {
    await inventoryModuleApi.getChatSessionMessages('session-1', {
      page: '2',
      page_size: '50',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/csv/sessions/session-1/messages/?page=2&page_size=50',
      expect.any(Object)
    );
  });

  it('uses pharmacy dataset validate endpoint and carries language/idempotency', async () => {
    const file = new File(['id,total\n1,100'], 'sales.csv', { type: 'text/csv' });

    await pharmacyCsvApi.validateImport(
      'sales',
      file,
      {
        language: 'bn',
      },
      'pharmacy-validate-1'
    );

    const [, request] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    const body = request.body as FormData;

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/pharmacy-csv/sales/imports/validate/',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Idempotency-Key': 'pharmacy-validate-1' }),
      })
    );
    expect(body.get('file')).toBe(file);
    expect(body.get('language')).toBe('bn');
    expect(body.get('idempotency_key')).toBe('pharmacy-validate-1');
  });

  it('uses pharmacy dataset commit endpoint', async () => {
    const file = new File(['id,moved\n1,5'], 'movement.csv', { type: 'text/csv' });

    await pharmacyCsvApi.commitImport('movement', file, undefined, 'pharmacy-commit-1');

    const [, request] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    const body = request.body as FormData;

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/pharmacy-csv/movements/imports/commit/',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Idempotency-Key': 'pharmacy-commit-1' }),
      })
    );
    expect(body.get('file')).toBe(file);
    expect(body.get('idempotency_key')).toBe('pharmacy-commit-1');
  });

  it('uses pharmacy csv create session endpoint', async () => {
    await pharmacyCsvApi.createChatSession({
      file_id: 'pharmacy-file-1',
      language: 'en',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/pharmacy-csv/chat/sessions/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          file_id: 'pharmacy-file-1',
          language: 'en',
        }),
      })
    );
  });

  it('uses pharmacy csv session message endpoint', async () => {
    await pharmacyCsvApi.sendChatSessionMessage('pharmacy-session-1', {
      query: 'Explain sales errors',
      language: 'en',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/pharmacy-csv/chat/sessions/pharmacy-session-1/messages/',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          query: 'Explain sales errors',
          language: 'en',
        }),
      })
    );
  });

  it('uses pharmacy csv session history endpoint with paging params', async () => {
    await pharmacyCsvApi.getChatSessionMessages('pharmacy-session-1', {
      page: '1',
      page_size: '100',
    });

    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/pharmacy-csv/chat/sessions/pharmacy-session-1/messages/?page=1&page_size=100',
      expect.any(Object)
    );
  });
});
