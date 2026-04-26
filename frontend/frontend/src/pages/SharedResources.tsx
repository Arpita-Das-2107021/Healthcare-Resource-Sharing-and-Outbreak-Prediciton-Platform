import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import AppLayout from '@/components/layout/AppLayout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { AlertTriangle, Building2, Clock3, Loader2 } from 'lucide-react';
import { requestsApi, resourceSharesApi } from '@/services/api';
import { useAuth } from '@/contexts/AuthContext';
import { useToast } from '@/hooks/use-toast';
import { type ResourceWithVisibility } from '@/types/healthcare';
import { ResourceCard } from '@/components/ResourceCard';
import { ResourceRequestForm } from '@/components/ResourceRequestForm';
import { RESOURCE_SHARES_UPDATED_EVENT } from '@/constants/events';
import ResourceConnectionGraph, {
  type ResourceConnectionEdge,
  type ResourceConnectionNode,
} from '@/components/resource/ResourceConnectionGraph';

interface ShareRow {
  id: string;
  hospitalId: string;
  hospitalName: string;
  catalogItemId: string;
  resourceName: string;
  resourceType: 'drugs' | 'blood' | 'organs' | 'equipment';
  resourceTypeName: string;
  sharedQuantity: number;
  quantityOffered: number;
  committedQuantity: number;
  pricePerUnit: number | null;
  validUntil: string | null;
  status: string;
  notes: string;
  updatedAt: string;
}

interface RequestRow {
  id: string;
  resourceKey: string;
  catalogItemId: string;
  resourceShareId: string;
  resourceName: string;
  requestingHospitalId: string;
  requestingHospitalName: string;
  supplyingHospitalId: string;
  quantityRequested: number;
  status: string;
  requestedAt: string;
}

interface MySharedResourceInsight {
  resourceKey: string;
  shareId: string;
  resourceName: string;
  resourceTypeName: string;
  sharedQuantity: number;
  availableQuantity: number;
  committedQuantity: number;
  totalRequests: number;
  pendingCount: number;
  inProgressCount: number;
  closedCount: number;
  totalRequestedQuantity: number;
  latestRequestedAt: string;
  requestingHospitals: Set<string>;
}

const MEDICINE_IMAGE_PRIMARY = '/public/medicine.png';
const MEDICINE_IMAGE_FALLBACK = '/medicine.png';
const RESOURCE_IMAGE_FALLBACK = '/placeholder.svg';

const asRecord = (value: unknown): Record<string, unknown> => {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
};

const readString = (record: Record<string, unknown>, keys: string[], fallback = ''): string => {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }

  return fallback;
};

const readIdFromUnknown = (value: unknown): string => {
  if (typeof value === 'string' || typeof value === 'number') {
    return String(value).trim();
  }

  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return readString(value as Record<string, unknown>, ['id', 'uuid', 'pk']);
  }

  return '';
};

const normalizeStatus = (value: string): string => (value || '').toLowerCase().replace(/[-\s]/g, '_');

const isPendingForSupplierAction = (status: string): boolean => {
  const normalized = normalizeStatus(status);
  return normalized === 'pending' || normalized === 'requested' || normalized === 'new';
};

const isClosedStatus = (status: string): boolean => {
  const normalized = normalizeStatus(status);
  return [
    'completed',
    'fulfilled',
    'received',
    'closed',
    'cancelled',
    'canceled',
    'rejected',
    'failed',
    'expired',
  ].includes(normalized);
};

const isTerminalShareStatus = (status: string): boolean => {
  const normalized = normalizeStatus(status);
  return ['closed', 'inactive', 'expired', 'cancelled', 'canceled', 'rejected', 'failed', 'deleted'].includes(normalized);
};

const normalizeNumber = (value: unknown): number | null => {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const readNonNegativeNumber = (...values: unknown[]): number | null => {
  for (const value of values) {
    const parsed = normalizeNumber(value);
    if (parsed !== null) {
      return parsed < 0 ? 0 : parsed;
    }
  }
  return null;
};

const resolveShareQuantities = (
  item: Record<string, unknown>
): { sharedQuantity: number; availableQuantity: number; committedQuantity: number } => {
  const shareVisibility =
    item.share_visibility && typeof item.share_visibility === 'object'
      ? (item.share_visibility as Record<string, unknown>)
      : {};

  const configuredSharedQuantity = readNonNegativeNumber(
    item.configured_quantity_offered,
    item.shared_quantity,
    item.total_shared_quantity,
    shareVisibility.shared_quantity,
    shareVisibility.configured_quantity_offered,
    item.quantity_shared
  );

  // For discoverability endpoints, quantity_offered mirrors available_share_quantity.
  const availableQuantity = readNonNegativeNumber(
    item.available_share_quantity,
    item.available_shared_quantity,
    item.quantity_offered,
    shareVisibility.available_share_quantity,
    shareVisibility.available_shared_quantity,
    shareVisibility.quantity_offered,
    item.remaining_shared_quantity,
    item.remaining_share_quantity,
    item.quantity_available_for_share,
    item.quantity_remaining_for_share
  );

  const reservedQuantity = readNonNegativeNumber(
    item.reserved_quantity,
    item.quantity_reserved,
    shareVisibility.reserved_quantity,
    shareVisibility.quantity_reserved
  );

  const transferredQuantity = readNonNegativeNumber(
    item.transferred_quantity,
    item.quantity_transferred,
    shareVisibility.transferred_quantity,
    shareVisibility.quantity_transferred
  );

  const committedQuantity = readNonNegativeNumber(
    item.committed_quantity,
    shareVisibility.committed_quantity
  );

  const derivedCommittedQuantity =
    committedQuantity ??
    (reservedQuantity !== null || transferredQuantity !== null
      ? (reservedQuantity ?? 0) + (transferredQuantity ?? 0)
      : null);

  const resolvedSharedQuantity =
    configuredSharedQuantity ??
    (availableQuantity !== null && derivedCommittedQuantity !== null
      ? availableQuantity + derivedCommittedQuantity
      : availableQuantity ?? 0);

  const resolvedAvailableQuantity =
    availableQuantity ??
    (derivedCommittedQuantity !== null
      ? Math.max(0, resolvedSharedQuantity - derivedCommittedQuantity)
      : resolvedSharedQuantity);

  const resolvedCommittedQuantity =
    derivedCommittedQuantity ?? Math.max(0, resolvedSharedQuantity - resolvedAvailableQuantity);

  return {
    sharedQuantity: resolvedSharedQuantity,
    availableQuantity: resolvedAvailableQuantity,
    committedQuantity: resolvedCommittedQuantity,
  };
};

const normalizeType = (value: unknown): ShareRow['resourceType'] => {
  const normalized = String(value || '').toLowerCase();
  if (['drug', 'drugs', 'medication', 'medicine'].includes(normalized)) return 'drugs';
  if (['blood', 'blood_product', 'blood-products'].includes(normalized)) return 'blood';
  if (['organ', 'organs'].includes(normalized)) return 'organs';
  return 'equipment';
};

const resolveResourceCardImage = (value: unknown): string => {
  const normalized = String(value || '').toLowerCase();
  if (['drug', 'drugs', 'medication', 'medicine'].includes(normalized)) {
    return MEDICINE_IMAGE_PRIMARY;
  }

  return RESOURCE_IMAGE_FALLBACK;
};

const mapShare = (value: unknown): ShareRow => {
  const item = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>;
  const hospital = asRecord(item.hospital);
  const quantityMetrics = resolveShareQuantities(item);

  const hospitalId =
    readIdFromUnknown(item.hospital) ||
    readIdFromUnknown(item.hospital_id) ||
    readIdFromUnknown(item.offering_hospital) ||
    readIdFromUnknown(item.offering_hospital_id);

  const catalogItemId =
    readIdFromUnknown(item.catalog_item) ||
    readIdFromUnknown(item.catalog_item_id);

  return {
    id: String(item.id || item.share_id || item.resource_share_id || item.share_record_id || ''),
    hospitalId,
    hospitalName: readString(item, ['hospital_name', 'offering_hospital_name'], readString(hospital, ['name'])),
    catalogItemId,
    resourceName: String(item.catalog_item_name || item.resource_name || item.product_name || 'Resource'),
    resourceType: normalizeType(item.resource_type_name || item.resource_type || item.catalog_item_resource_type_name || item.catalog_item_type || item.type),
    resourceTypeName: String(item.resource_type_name || item.catalog_item_resource_type_name || 'General'),
    sharedQuantity: quantityMetrics.sharedQuantity,
    quantityOffered: quantityMetrics.availableQuantity,
    committedQuantity: quantityMetrics.committedQuantity,
    pricePerUnit: normalizeNumber(item.price_snapshot ?? item.price_per_unit ?? item.unit_price),
    validUntil: item.valid_until ? String(item.valid_until) : null,
    status: String(item.status || 'active'),
    notes: String(item.notes || ''),
    updatedAt: String(item.updated_at || item.created_at || ''),
  };
};

const extractShares = (response: unknown): ShareRow[] => {
  const root = (response && typeof response === 'object' ? response : {}) as Record<string, unknown>;
  const data = (root.data && typeof root.data === 'object' ? root.data : {}) as Record<string, unknown>;
  const raw =
    (Array.isArray(data.results) && data.results) ||
    (Array.isArray(root.data) && root.data) ||
    (Array.isArray(root.results) && root.results) ||
    (Array.isArray(response) ? response : []);

  return (Array.isArray(raw) ? raw : []).map(mapShare);
};

const mapRequest = (value: unknown): RequestRow => {
  const item = asRecord(value);
  const resource = asRecord(item.resource);
  const hospital = asRecord(item.hospital);

  const catalogItemId =
    readString(item, ['catalog_item', 'catalog_item_id']) ||
    readIdFromUnknown(item.catalog_item);
  const resourceShareId =
    readString(item, ['resource_share_id', 'resource_share', 'share_id', 'share_record_id']) ||
    readIdFromUnknown(item.resource_share);
  const resourceName = readString(item, ['catalog_item_name', 'resource_name'], readString(resource, ['name'], 'Resource'));
  const resourceKey =
    catalogItemId ||
    resourceShareId ||
    resourceName ||
    String(item.id || '');

  return {
    id: String(item.id || ''),
    resourceKey,
    catalogItemId,
    resourceShareId,
    resourceName,
    requestingHospitalId:
      readIdFromUnknown(item.requesting_hospital) ||
      readIdFromUnknown(item.requesting_hospital_id) ||
      readIdFromUnknown(item.hospital) ||
      readIdFromUnknown(item.hospital_id),
    requestingHospitalName: readString(
      item,
      ['requesting_hospital_name'],
      readString(hospital, ['name'], readString(item, ['hospital_name'], 'Unknown Hospital'))
    ),
    supplyingHospitalId:
      readIdFromUnknown(item.supplying_hospital) ||
      readIdFromUnknown(item.supplying_hospital_id),
    quantityRequested: Math.max(0, normalizeNumber(item.quantity_requested ?? item.quantity) ?? 0),
    status: String(item.workflow_state || item.status || 'pending'),
    requestedAt: readString(item, ['created_at', 'requested_at']),
  };
};

const extractRequests = (response: unknown): RequestRow[] => {
  const root = asRecord(response);
  const data = asRecord(root.data);

  const raw =
    (Array.isArray(data.results) && data.results) ||
    (Array.isArray(root.data) && root.data) ||
    (Array.isArray(root.results) && root.results) ||
    (Array.isArray(response) ? response : []);

  return (Array.isArray(raw) ? raw : []).map(mapRequest);
};

const formatTimestamp = (value: string): string => {
  if (!value) return 'Unknown time';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Unknown time';
  return parsed.toLocaleString();
};

const toCardResource = (share: ShareRow): ResourceWithVisibility => {
  const availability = share.quantityOffered > 5 ? 'available' : share.quantityOffered > 0 ? 'limited' : 'unavailable';

  return {
    id: share.id,
    name: share.resourceName,
    type: share.resourceType,
    resourceTypeName: share.resourceTypeName,
    hospital: share.hospitalName || 'Partner hospital',
    quantity: share.quantityOffered,
    availability,
    isEmergency: false,
    region: '',
    lastUpdated: share.updatedAt || new Date().toISOString(),
    isVisibleToOthers: true,
    requestCount: 0,
    image: resolveResourceCardImage(share.resourceTypeName || share.resourceType),
    description: share.notes || undefined,
    hospitalId: share.hospitalId,
    catalogItemId: share.catalogItemId,
  };
};

const toResourceRouteState = (share: ShareRow): { share: Record<string, unknown> } => ({
  share: {
    id: share.id,
    hospital: share.hospitalId,
    hospital_name: share.hospitalName,
    catalog_item: share.catalogItemId,
    catalog_item_name: share.resourceName,
    resource_type_name: share.resourceTypeName,
    quantity_offered: share.quantityOffered,
    status: share.status,
    notes: share.notes,
    valid_until: share.validUntil,
    updated_at: share.updatedAt,
  },
});

const SharedResources = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const myHospitalId = String(user?.hospital_id || '').trim();
  const { toast } = useToast();
  const normalizedPath = location.pathname.replace(/\/+$/, '');
  const isMySharedResourcesRoute = normalizedPath === '/sharing/my-resources';

  const [hospitalFilter, setHospitalFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState<'all' | 'drugs' | 'blood' | 'organs' | 'equipment'>('all');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [selectedResource, setSelectedResource] = useState<ResourceWithVisibility | null>(null);
  const [requestModalOpen, setRequestModalOpen] = useState(false);
  const [manualSummaryViewEnabled, setManualSummaryViewEnabled] = useState(false);
  const showIncomingSummary = isMySharedResourcesRoute || manualSummaryViewEnabled;

  const sharesQuery = useQuery({
    queryKey: ['shared-resources-list'],
    queryFn: async () => {
      const response: unknown = await resourceSharesApi.getAll();
      return extractShares(response);
    },
  });

  const incomingRequestsQuery = useQuery({
    queryKey: ['shared-resources-incoming-requests', myHospitalId],
    enabled: showIncomingSummary && Boolean(myHospitalId),
    queryFn: async () => {
      const response: unknown = await requestsApi.getAll();
      return extractRequests(response);
    },
  });

  const refetchSharedResources = sharesQuery.refetch;

  useEffect(() => {
    const handleResourceSharesUpdated = () => {
      void refetchSharedResources();
    };

    window.addEventListener(RESOURCE_SHARES_UPDATED_EVENT, handleResourceSharesUpdated);
    return () => {
      window.removeEventListener(RESOURCE_SHARES_UPDATED_EVENT, handleResourceSharesUpdated);
    };
  }, [refetchSharedResources]);

  const discoverableShares = useMemo(() => {
    return (sharesQuery.data || []).filter((item) => {
      if (isTerminalShareStatus(item.status) || item.quantityOffered <= 0) return false;

      const isMyHospitalShare = Boolean(myHospitalId) && item.hospitalId === myHospitalId;
      if (isMyHospitalShare) return false;
      if (hospitalFilter !== 'all' && item.hospitalId !== hospitalFilter) return false;
      if (typeFilter !== 'all' && item.resourceType !== typeFilter) return false;
      if (categoryFilter !== 'all' && item.resourceTypeName !== categoryFilter) return false;

      if (!item.validUntil) return true;
      const validUntilEpoch = new Date(item.validUntil).getTime();
      return Number.isNaN(validUntilEpoch) || validUntilEpoch > Date.now();
    });
  }, [sharesQuery.data, myHospitalId, hospitalFilter, typeFilter, categoryFilter]);

  const discoverResources = useMemo(
    () => discoverableShares.map(toCardResource),
    [discoverableShares]
  );

  const hospitalsForFilter = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of sharesQuery.data || []) {
      if (item.hospitalId && item.hospitalId !== myHospitalId) {
        map.set(item.hospitalId, item.hospitalName || item.hospitalId);
      }
    }
    return Array.from(map.entries()).map(([id, name]) => ({ id, name }));
  }, [sharesQuery.data, myHospitalId]);

  const categoriesForFilter = useMemo(() => {
    const items = sharesQuery.data || [];
    return Array.from(new Set(items.map((item) => item.resourceTypeName))).sort();
  }, [sharesQuery.data]);

  const shareById = useMemo(() => {
    const map = new Map<string, ShareRow>();
    for (const item of sharesQuery.data || []) {
      if (item.id) {
        map.set(item.id, item);
      }
    }
    return map;
  }, [sharesQuery.data]);

  const myShareById = useMemo(() => {
    const map = new Map<string, ShareRow>();
    for (const item of sharesQuery.data || []) {
      if (item.id && item.hospitalId === myHospitalId) {
        map.set(item.id, item);
      }
    }
    return map;
  }, [sharesQuery.data, myHospitalId]);

  const myShareByCatalogItem = useMemo(() => {
    const map = new Map<string, ShareRow>();
    for (const item of sharesQuery.data || []) {
      if (item.catalogItemId && item.hospitalId === myHospitalId && !map.has(item.catalogItemId)) {
        map.set(item.catalogItemId, item);
      }
    }
    return map;
  }, [sharesQuery.data, myHospitalId]);

  const myShareByName = useMemo(() => {
    const map = new Map<string, ShareRow>();
    for (const item of sharesQuery.data || []) {
      const normalizedName = item.resourceName.trim().toLowerCase();
      if (normalizedName && item.hospitalId === myHospitalId && !map.has(normalizedName)) {
        map.set(normalizedName, item);
      }
    }
    return map;
  }, [sharesQuery.data, myHospitalId]);

  const mySharedResources = useMemo(() => {
    if (!myHospitalId) return [];

    return (sharesQuery.data || [])
      .filter((item) => item.hospitalId === myHospitalId && item.status.toLowerCase() === 'active')
      .sort((a, b) => {
        if (b.sharedQuantity !== a.sharedQuantity) {
          return b.sharedQuantity - a.sharedQuantity;
        }
        return a.resourceName.localeCompare(b.resourceName);
      });
  }, [sharesQuery.data, myHospitalId]);

  const mySharedInsights = useMemo(() => {
    if (!myHospitalId) return [];

    const summaryByShareId = new Map<string, MySharedResourceInsight>();

    const buildBaseSummary = (share: ShareRow): MySharedResourceInsight => ({
      resourceKey: share.id || share.catalogItemId || share.resourceName.toLowerCase(),
      shareId: share.id,
      resourceName: share.resourceName || 'Resource',
      resourceTypeName: share.resourceTypeName || 'General',
      sharedQuantity: share.sharedQuantity,
      availableQuantity: share.quantityOffered,
      committedQuantity: share.committedQuantity,
      totalRequests: 0,
      pendingCount: 0,
      inProgressCount: 0,
      closedCount: 0,
      totalRequestedQuantity: 0,
      latestRequestedAt: '',
      requestingHospitals: new Set<string>(),
    });

    mySharedResources.forEach((share) => {
      if (!share.id) {
        return;
      }
      summaryByShareId.set(share.id, buildBaseSummary(share));
    });

    (incomingRequestsQuery.data || [])
      .filter((request) => request.supplyingHospitalId === myHospitalId)
      .forEach((request) => {
        const normalizedName = request.resourceName.trim().toLowerCase();
        const matchedShare =
          (request.resourceShareId && myShareById.get(request.resourceShareId)) ||
          (request.catalogItemId && myShareByCatalogItem.get(request.catalogItemId)) ||
          (normalizedName && myShareByName.get(normalizedName)) ||
          null;

        if (!matchedShare?.id) {
          return;
        }

        const entry = summaryByShareId.get(matchedShare.id) || buildBaseSummary(matchedShare);

        entry.totalRequests += 1;
        entry.totalRequestedQuantity += request.quantityRequested;
        entry.requestingHospitals.add(request.requestingHospitalName || 'Unknown Hospital');

        if (isPendingForSupplierAction(request.status)) {
          entry.pendingCount += 1;
        } else if (isClosedStatus(request.status)) {
          entry.closedCount += 1;
        } else {
          entry.inProgressCount += 1;
        }

        const latestKnown = new Date(entry.latestRequestedAt).getTime();
        const candidate = new Date(request.requestedAt).getTime();
        if (!Number.isNaN(candidate) && (Number.isNaN(latestKnown) || candidate > latestKnown)) {
          entry.latestRequestedAt = request.requestedAt;
        }

        summaryByShareId.set(matchedShare.id, entry);
      });

    return Array.from(summaryByShareId.values()).sort((a, b) => {
      if (b.pendingCount !== a.pendingCount) {
        return b.pendingCount - a.pendingCount;
      }
      if (b.totalRequests !== a.totalRequests) {
        return b.totalRequests - a.totalRequests;
      }
      if (b.sharedQuantity !== a.sharedQuantity) {
        return b.sharedQuantity - a.sharedQuantity;
      }
      return a.resourceName.localeCompare(b.resourceName);
    });
  }, [
    incomingRequestsQuery.data,
    myHospitalId,
    myShareByCatalogItem,
    myShareById,
    myShareByName,
    mySharedResources,
  ]);

  const mySharedTotals = useMemo(
    () =>
      mySharedInsights.reduce(
        (acc, entry) => {
          acc.resourceCount += 1;
          acc.totalSharedQuantity += entry.sharedQuantity;
          acc.totalAvailableQuantity += entry.availableQuantity;
          acc.totalPending += entry.pendingCount;
          return acc;
        },
        {
          resourceCount: 0,
          totalSharedQuantity: 0,
          totalAvailableQuantity: 0,
          totalPending: 0,
        }
      ),
    [mySharedInsights]
  );

  const resourceConnectionGraph = useMemo(() => {
    const rows = sharesQuery.data || [];
    const requests = incomingRequestsQuery.data || [];

    if (rows.length === 0) {
      return { nodes: [] as ResourceConnectionNode[], edges: [] as ResourceConnectionEdge[] };
    }

    const graphNodes: ResourceConnectionNode[] = [];
    const graphEdges: ResourceConnectionEdge[] = [];
    const seenNodeIds = new Set<string>();
    const seenEdgeIds = new Set<string>();

    const addNode = (id: string, label: string, type: ResourceConnectionNode['type']) => {
      if (!id || seenNodeIds.has(id)) return;
      seenNodeIds.add(id);
      graphNodes.push({ id, label: label || 'Resource', type });
    };

    const addEdge = (
      id: string,
      source: string,
      target: string,
      type: ResourceConnectionEdge['type'],
    ) => {
      if (!source || !target || source === target || seenEdgeIds.has(id)) return;
      seenEdgeIds.add(id);
      graphEdges.push({ id, source, target, type });
    };

    const limitedShares = rows.slice(0, 28);
    const shareNodeByShareId = new Map<string, string>();
    const shareByCatalogItem = new Map<string, ShareRow>();
    const shareByName = new Map<string, ShareRow>();

    limitedShares.forEach((share) => {
      const nodeId = `share:${share.id}`;
      shareNodeByShareId.set(share.id, nodeId);
      if (share.catalogItemId && !shareByCatalogItem.has(share.catalogItemId)) {
        shareByCatalogItem.set(share.catalogItemId, share);
      }

      const normalizedName = share.resourceName.trim().toLowerCase();
      if (normalizedName && !shareByName.has(normalizedName)) {
        shareByName.set(normalizedName, share);
      }

      addNode(nodeId, share.resourceName, 'shared');
    });

    const sharesByType = new Map<string, ShareRow[]>();
    limitedShares.forEach((share) => {
      const group = sharesByType.get(share.resourceType) || [];
      group.push(share);
      sharesByType.set(share.resourceType, group);
    });

    sharesByType.forEach((group) => {
      const sorted = [...group].sort((a, b) => a.resourceName.localeCompare(b.resourceName));
      for (let index = 0; index < sorted.length - 1; index += 1) {
        const current = sorted[index];
        const next = sorted[index + 1];
        const source = shareNodeByShareId.get(current.id);
        const target = shareNodeByShareId.get(next.id);
        if (!source || !target) continue;
        addEdge(`shared:${current.id}:${next.id}`, source, target, 'shared');
      }
    });

    const resolveShareForRequest = (request: RequestRow): ShareRow | null => {
      if (request.resourceShareId && shareNodeByShareId.has(request.resourceShareId)) {
        return limitedShares.find((share) => share.id === request.resourceShareId) || null;
      }

      if (request.catalogItemId && shareByCatalogItem.has(request.catalogItemId)) {
        return shareByCatalogItem.get(request.catalogItemId) || null;
      }

      const normalizedName = request.resourceName.trim().toLowerCase();
      if (normalizedName && shareByName.has(normalizedName)) {
        return shareByName.get(normalizedName) || null;
      }

      return null;
    };

    requests.slice(0, 140).forEach((request) => {
      const matchedShare = resolveShareForRequest(request);
      if (!matchedShare) return;

      const shareNodeId = shareNodeByShareId.get(matchedShare.id);
      if (!shareNodeId) return;

      if (request.supplyingHospitalId && myHospitalId && request.supplyingHospitalId === myHospitalId) {
        const incomingNodeId = `incoming:${request.requestingHospitalId || request.requestingHospitalName || request.id}`;
        addNode(incomingNodeId, request.requestingHospitalName || 'Requester', 'incoming');
        addEdge(`incoming:${request.id}`, incomingNodeId, shareNodeId, 'incoming');
      }

      if (request.requestingHospitalId && myHospitalId && request.requestingHospitalId === myHospitalId) {
        const outgoingNodeId = `outgoing:${matchedShare.hospitalId || request.supplyingHospitalId || request.id}`;
        addNode(outgoingNodeId, matchedShare.hospitalName || 'Partner Facility', 'outgoing');
        addEdge(`outgoing:${request.id}`, shareNodeId, outgoingNodeId, 'outgoing');
      }
    });

    return { nodes: graphNodes, edges: graphEdges };
  }, [sharesQuery.data, incomingRequestsQuery.data, myHospitalId]);

  return (
    <AppLayout title="Shared Resources"
      // subtitle="Discover partner offers. Manage your own shared inventory in Visibility Control."
    >
      <div className="space-y-4">
        {/* <Card className="border-info/20 bg-info/5">
          <CardContent className="flex flex-col gap-3 p-4 md:flex-row md:items-center md:justify-between">
            <div>
              <h3 className="font-medium">Manage share offers in Visibility Control</h3>
              <p className="text-sm text-muted-foreground">
                Add and update your hospital shared resources from the Visibility Control page.
              </p>
            </div>
            <Button asChild variant="outline">
              <Link to="/sharing/visibility">Open Visibility Control</Link>
            </Button>
          </CardContent>
        </Card> */}

        <Card>
          <CardHeader>
            <CardTitle>{isMySharedResourcesRoute ? 'My Shared Resources' : 'Available from Partner HealthCares'}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {!isMySharedResourcesRoute && (
              <div className="grid gap-3 md:grid-cols-4">
                <Select value={hospitalFilter} onValueChange={setHospitalFilter}>
                  <SelectTrigger>
                    <SelectValue placeholder="All Hospitals" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Partner Hospitals</SelectItem>
                    {hospitalsForFilter.map((hospital) => (
                      <SelectItem key={hospital.id} value={hospital.id}>{hospital.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Select value={typeFilter} onValueChange={(value) => setTypeFilter(value as typeof typeFilter)}>
                  <SelectTrigger>
                    <SelectValue placeholder="All Types" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Types</SelectItem>
                    <SelectItem value="drugs">Drugs</SelectItem>
                    <SelectItem value="blood">Blood</SelectItem>
                    <SelectItem value="organs">Organs</SelectItem>
                    <SelectItem value="equipment">Equipment</SelectItem>
                  </SelectContent>
                </Select>

                <Select value={categoryFilter} onValueChange={setCategoryFilter}>
                  <SelectTrigger>
                    <SelectValue placeholder="All Categories" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Categories</SelectItem>
                    {categoriesForFilter.map((category) => (
                      <SelectItem key={category} value={category}>{category}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                {/* <div className="flex items-center gap-2">
                  <Button
                    className="w-full"
                    variant={showIncomingSummary ? 'default' : 'outline'}
                    onClick={() => setManualSummaryViewEnabled((previous) => !previous)}
                    type="button"
                  >
                    {showIncomingSummary ? 'Hide My Shared Resources' : 'View My Shared Resources'}
                  </Button>
                </div> */}
              </div>
            )}

            {showIncomingSummary && (
              <div className="rounded-xl border border-border/70 bg-gradient-to-br from-primary/10 via-background to-accent/30 p-4 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wide text-primary">My Shared Resources</p>
                    <h3 className="text-lg font-semibold">Live Share And Request Overview</h3>
                    <p className="text-sm text-muted-foreground">
                      Live quantity and request insights for resources your hospital is currently sharing.
                    </p>
                  </div>
                  <Badge variant="secondary">Owner view</Badge>
                </div>

                {incomingRequestsQuery.isLoading ? (
                  <div className="mt-4 flex items-center text-sm text-muted-foreground">
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Loading incoming request insights...
                  </div>
                ) : incomingRequestsQuery.isError ? (
                  <div className="mt-4 flex items-center gap-2 text-sm text-destructive">
                    <AlertTriangle className="h-4 w-4" />
                    Failed to load incoming request insights.
                  </div>
                ) : mySharedInsights.length === 0 ? (
                  <p className="mt-4 text-sm text-muted-foreground">
                    You do not have any active shared resources yet.
                  </p>
                ) : (
                  <>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                      <div className="rounded-lg border border-border/60 bg-card/90 p-3">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground">Resources Shared</p>
                        <p className="mt-1 text-xl font-semibold">{mySharedTotals.resourceCount}</p>
                      </div>
                      <div className="rounded-lg border border-border/60 bg-card/90 p-3">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground">Total Shared Qty</p>
                        <p className="mt-1 text-xl font-semibold">{mySharedTotals.totalSharedQuantity}</p>
                      </div>
                      <div className="rounded-lg border border-border/60 bg-card/90 p-3">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground">Available To Share</p>
                        <p className="mt-1 text-xl font-semibold">{mySharedTotals.totalAvailableQuantity}</p>
                      </div>
                      <div className="rounded-lg border border-border/60 bg-card/90 p-3">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground">Pending Requests</p>
                        <p className="mt-1 text-xl font-semibold">{mySharedTotals.totalPending}</p>
                      </div>
                    </div>

                    <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {mySharedInsights.map((entry) => {
                      const linkedShare = entry.shareId ? myShareById.get(entry.shareId) : undefined;
                      const requesters = Array.from(entry.requestingHospitals);
                      const previewNames = requesters.slice(0, 3).join(', ');
                      const extraRequesterCount = Math.max(0, requesters.length - 3);
                      const utilizedQuantity = Math.max(0, entry.sharedQuantity - entry.availableQuantity);
                      const utilizationPercent =
                        entry.sharedQuantity > 0
                          ? Math.min(100, Math.round((utilizedQuantity / entry.sharedQuantity) * 100))
                          : 0;

                      return (
                        <div
                          key={entry.resourceKey}
                          className="rounded-lg border border-border/70 bg-card/95 p-4 shadow-sm transition-colors hover:border-primary/40"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              {entry.shareId ? (
                                <Link
                                  to={`/resource/${encodeURIComponent(entry.shareId)}`}
                                  state={linkedShare ? toResourceRouteState(linkedShare) : undefined}
                                  className="font-semibold text-primary transition-colors hover:text-primary/90 hover:underline"
                                >
                                  {entry.resourceName}
                                </Link>
                              ) : (
                                <h4 className="font-semibold">{entry.resourceName}</h4>
                              )}
                              <p className="mt-1 text-xs text-muted-foreground">{entry.resourceTypeName}</p>
                            </div>
                            <Badge variant={entry.pendingCount > 0 ? 'destructive' : 'secondary'}>
                              Pending: {entry.pendingCount}
                            </Badge>
                          </div>

                          <div className="mt-3 h-24 overflow-hidden rounded-md border border-border/60 bg-muted/20">
                            <img
                              src={resolveResourceCardImage(entry.resourceTypeName)}
                              alt={`${entry.resourceName} preview`}
                              className="h-full w-full object-cover"
                              onError={(event) => {
                                const target = event.currentTarget;
                                if (target.src.includes('/public/medicine.png')) {
                                  target.src = MEDICINE_IMAGE_FALLBACK;
                                  return;
                                }

                                if (!target.src.includes('/placeholder.svg')) {
                                  target.src = RESOURCE_IMAGE_FALLBACK;
                                }
                              }}
                            />
                          </div>

                          <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
                            <div className="rounded-md border border-border/50 bg-muted/40 p-2">
                              <p className="text-muted-foreground">Shared Qty</p>
                              <p className="text-lg font-semibold">{entry.sharedQuantity}</p>
                            </div>
                            <div className="rounded-md border border-border/50 bg-muted/40 p-2">
                              <p className="text-muted-foreground">Available</p>
                              <p className="text-lg font-semibold">{entry.availableQuantity}</p>
                            </div>
                            <div className="rounded-md border border-border/50 bg-muted/40 p-2">
                              <p className="text-muted-foreground">Incoming</p>
                              <p className="text-lg font-semibold">{entry.totalRequests}</p>
                            </div>
                            <div className="rounded-md border border-border/50 bg-muted/40 p-2">
                              <p className="text-muted-foreground">Requested Qty</p>
                              <p className="text-lg font-semibold">{entry.totalRequestedQuantity}</p>
                            </div>
                            <div className="rounded-md border border-border/50 bg-muted/40 p-2">
                              <p className="text-muted-foreground">Committed Qty</p>
                              <p className="text-lg font-semibold">{entry.committedQuantity}</p>
                            </div>
                            <div className="rounded-md border border-border/50 bg-muted/40 p-2">
                              <p className="text-muted-foreground">In Progress</p>
                              <p className="text-lg font-semibold">{entry.inProgressCount}</p>
                            </div>
                          </div>

                          <div className="mt-3">
                            <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
                              <span>Shared quantity used</span>
                              <span>{utilizedQuantity}/{entry.sharedQuantity || 0} ({utilizationPercent}%)</span>
                            </div>
                            <div className="h-2 overflow-hidden rounded-full bg-muted">
                              <div
                                className="h-full rounded-full bg-primary"
                                style={{ width: `${utilizationPercent}%` }}
                              />
                            </div>
                          </div>

                          <div className="mt-3 rounded-md border border-border/40 bg-muted/30 px-2 py-1.5 text-xs text-muted-foreground">
                            Closed requests: <span className="font-medium text-foreground">{entry.closedCount}</span>
                          </div>

                          {entry.totalRequests > 0 ? (
                            <>
                              <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
                                <Clock3 className="h-3.5 w-3.5" />
                                Latest request: {formatTimestamp(entry.latestRequestedAt)}
                              </div>
                              <div className="mt-2 flex items-start gap-2 text-xs text-muted-foreground">
                                <Building2 className="mt-0.5 h-3.5 w-3.5" />
                                <span>
                                  Requester hospitals: {previewNames || 'Unknown Hospital'}
                                  {extraRequesterCount > 0 ? ` +${extraRequesterCount} more` : ''}
                                </span>
                              </div>
                            </>
                          ) : (
                            <p className="mt-3 text-xs text-muted-foreground">
                              No incoming requests yet for this shared resource.
                            </p>
                          )}
                        </div>
                      );
                    })}
                    </div>
                  </>
                )}

                {/* Graph section is intentionally disabled for now and can be re-enabled when needed.
                <div className="mt-6">
                  <h4 className="text-sm font-semibold text-foreground">Connection Highlights</h4>
                  <p className="text-xs text-muted-foreground">
                    Clicking a node merges incoming, outgoing, and shared relationships into one unified highlighted state.
                  </p>
                  <ResourceConnectionGraph
                    className="mt-3"
                    nodes={resourceConnectionGraph.nodes}
                    edges={resourceConnectionGraph.edges}
                    emptyMessage="No relationship graph could be built from the currently loaded shared resource data."
                  />
                </div>
                */}
              </div>
            )}

            {!showIncomingSummary && (sharesQuery.isLoading ? (
              <div className="flex items-center py-8"><Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading shared resources...</div>
            ) : sharesQuery.isError ? (
              <div className="text-destructive flex items-center gap-2"><AlertTriangle className="h-4 w-4" /> Failed to load shares.</div>
            ) : discoverResources.length === 0 ? (
              <p className="text-sm text-muted-foreground">No active shared resources match your filters.</p>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {discoverResources.map((resource) => {
                  const linkedShare = shareById.get(resource.id);

                  return (
                    <ResourceCard
                      key={resource.id}
                      resource={resource}
                      resourceRouteState={linkedShare ? toResourceRouteState(linkedShare) : { resource }}
                      onClick={(value) => {
                        if (!value.id) {
                          toast({
                            title: 'Resource details unavailable',
                            description: 'This share does not have a valid identifier yet.',
                            variant: 'destructive',
                          });
                          return;
                        }

                        const cardShare = shareById.get(value.id);

                        navigate(`/resource/${encodeURIComponent(value.id)}`, {
                          state: cardShare ? toResourceRouteState(cardShare) : { resource: value },
                        });
                      }}
                      onRequest={(value) => {
                        setSelectedResource(value);
                        setRequestModalOpen(true);
                      }}
                    />
                  );
                })}
              </div>
            ))}
          </CardContent>
        </Card>

        <ResourceRequestForm
          resource={selectedResource}
          isOpen={requestModalOpen}
          onClose={() => {
            setRequestModalOpen(false);
            setSelectedResource(null);
          }}
        />
      </div>
    </AppLayout>
  );
};

export default SharedResources;
