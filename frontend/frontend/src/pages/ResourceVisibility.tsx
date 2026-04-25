import AppLayout from '@/components/layout/AppLayout';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { inventoryApi } from '@/services/api';
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Search, Eye, EyeOff, AlertTriangle, CheckCircle, Loader2, Save, Check, ChevronsUpDown } from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import { useAuth } from '@/contexts/AuthContext';
import { Navigate } from 'react-router-dom';
import { hasAnyPermission } from '@/lib/rbac';
import { RESOURCE_SHARES_UPDATED_EVENT } from '@/constants/events';
import { cn } from '@/lib/utils';

type VisibilityResourceType = 'drugs' | 'blood' | 'organs' | 'equipment';

type VisibilityItem = {
  id: string;
  inventoryId: string;
  catalogItemId: string;
  name: string;
  type: VisibilityResourceType;
  totalQuantity: number;
  sharedQuantity: number;
  costPerUnit: number | null;
  shareRecordId?: string;
};

type InventoryTypeLookup = {
  byInventoryId: Map<string, VisibilityResourceType>;
  byCatalogItemId: Map<string, VisibilityResourceType>;
  costByInventoryId: Map<string, number>;
  costByCatalogItemId: Map<string, number>;
};

type UnknownRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is UnknownRecord => typeof value === 'object' && value !== null;

const getErrorMessage = (error: unknown): string =>
  error instanceof Error && error.message ? error.message : 'Please try again.';

const toNullableNumber = (value: unknown): number | null => {
  if (value === null || value === undefined || value === '') {
    return null;
  }

  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const formatCurrency = (value: number | null): string => {
  if (value === null) {
    return '-';
  }

  return `\u09f3${value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;
};

const normalizeType = (value: string): VisibilityResourceType | null => {
  const v = (value || '').toLowerCase().trim();
  if (!v) return null;

  if (['drug', 'drugs', 'medication', 'medicine'].includes(v)) return 'drugs';
  if (['blood', 'blood_product', 'blood-products'].includes(v)) return 'blood';
  if (['organ', 'organs'].includes(v)) return 'organs';
  if (['equipment', 'device', 'devices', 'tool', 'tools'].includes(v)) return 'equipment';

  return null;
};

const buildInventoryTypeLookup = (items: unknown[]): InventoryTypeLookup => {
  const byInventoryId = new Map<string, VisibilityResourceType>();
  const byCatalogItemId = new Map<string, VisibilityResourceType>();
  const costByInventoryId = new Map<string, number>();
  const costByCatalogItemId = new Map<string, number>();

  items.forEach((item) => {
    const row = isRecord(item) ? item : {};
    const inventoryId = String(row.id || row.inventory_id || '').trim();
    const catalogItemId = String(row.catalog_item || row.catalog_item_id || '').trim();
    const rawType = String(
      row.resource_type_name ||
        row.resource_type ||
        row.catalog_item_resource_type_name ||
        row.catalog_item_type ||
        row.type ||
        ''
    );
    const unitCost = toNullableNumber(
      row.price_per_unit ??
        row.unit_price ??
        row.current_unit_price ??
        row.price_snapshot
    );

    const normalizedType = normalizeType(rawType);
    if (!normalizedType) {
      return;
    }

    if (inventoryId && !byInventoryId.has(inventoryId)) {
      byInventoryId.set(inventoryId, normalizedType);
    }

    if (catalogItemId && !byCatalogItemId.has(catalogItemId)) {
      byCatalogItemId.set(catalogItemId, normalizedType);
    }

    if (unitCost !== null) {
      if (inventoryId && !costByInventoryId.has(inventoryId)) {
        costByInventoryId.set(inventoryId, unitCost);
      }

      if (catalogItemId && !costByCatalogItemId.has(catalogItemId)) {
        costByCatalogItemId.set(catalogItemId, unitCost);
      }
    }
  });

  return { byInventoryId, byCatalogItemId, costByInventoryId, costByCatalogItemId };
};

const resolveVisibilityType = (
  source: UnknownRecord,
  inventoryTypeLookup?: InventoryTypeLookup
): VisibilityResourceType => {
  const details = isRecord(source.catalog_item_details) ? source.catalog_item_details : {};
  const catalogItemObj = isRecord(source.catalog_item_obj) ? source.catalog_item_obj : {};

  const payloadType =
    normalizeType(String(source.resource_type_name || '')) ||
    normalizeType(String(source.resource_type || '')) ||
    normalizeType(String(source.catalog_item_resource_type_name || '')) ||
    normalizeType(String(source.catalog_item_type || '')) ||
    normalizeType(String(source.type || '')) ||
    normalizeType(String(details.resource_type_name || '')) ||
    normalizeType(String(details.resource_type || '')) ||
    normalizeType(String(details.category || '')) ||
    normalizeType(String(catalogItemObj.resource_type_name || '')) ||
    normalizeType(String(catalogItemObj.resource_type || '')) ||
    normalizeType(String(catalogItemObj.type || ''));

  if (payloadType) {
    return payloadType;
  }

  const inventoryId = String(source.inventory_id || source.id || '').trim();
  const catalogItemId = String(source.catalog_item || source.catalog_item_id || source.catalog_item_uuid || '').trim();

  const inventoryMatchedType = inventoryId ? inventoryTypeLookup?.byInventoryId.get(inventoryId) : null;
  if (inventoryMatchedType) {
    return inventoryMatchedType;
  }

  const catalogMatchedType = catalogItemId ? inventoryTypeLookup?.byCatalogItemId.get(catalogItemId) : null;
  if (catalogMatchedType) {
    return catalogMatchedType;
  }

  return 'equipment';
};

const getTypeLabel = (type: VisibilityResourceType): string => {
  if (type === 'drugs') return 'Medicine';
  if (type === 'blood') return 'Blood';
  if (type === 'organs') return 'Organs';
  return 'Equipment';
};

const extractList = (res: unknown): unknown[] => {
  if (Array.isArray(res)) return res;
  if (!isRecord(res)) return [];

  const data = res.data;
  if (Array.isArray(data)) return data;
  if (isRecord(data) && Array.isArray(data.results)) return data.results;
  if (Array.isArray(res.results)) return res.results;
  return [];
};

const mapVisibilityItem = (item: unknown, inventoryTypeLookup?: InventoryTypeLookup): VisibilityItem => {
  const source: UnknownRecord = isRecord(item) ? item : {};
  const details = isRecord(source.catalog_item_details) ? source.catalog_item_details : null;
  const catalogItemObj = isRecord(source.catalog_item_obj) ? source.catalog_item_obj : null;

  const totalQty = Number(
    source.total_quantity ??
      source.quantity_available ??
      source.total_inventory ??
      source.quantity ??
      0
  );

  const sharedQty = Number(
    source.shared_quantity ??
      source.quantity_shared ??
      source.quantity_offered ??
      0
  );

  const inventoryId = String(source.inventory_id || source.id || '');
  const catalogItemId = String(source.catalog_item || source.catalog_item_id || source.catalog_item_uuid || '');
  const resolvedName =
    source.product_name ||
    source.catalog_item_name ||
    source.resource_name ||
    source.name ||
    details?.name ||
    catalogItemObj?.name ||
    'Unknown item';

  const shareRecordId = source.share_record_id || source.resource_share_id || source.share_id;
  const inventoryMatchedCost = inventoryId ? inventoryTypeLookup?.costByInventoryId.get(inventoryId) : null;
  const catalogMatchedCost = catalogItemId ? inventoryTypeLookup?.costByCatalogItemId.get(catalogItemId) : null;
  const costPerUnit = toNullableNumber(
    source.price_per_unit ??
      source.unit_price ??
      source.current_unit_price ??
      source.price_snapshot ??
      source.cost_per_unit ??
      source.costPerUnit ??
      details?.price_per_unit ??
      details?.unit_price ??
      catalogItemObj?.price_per_unit ??
      catalogItemObj?.unit_price ??
      inventoryMatchedCost ??
      catalogMatchedCost
  );

  return {
    id: String(source.id || inventoryId || catalogItemId || crypto.randomUUID()),
    inventoryId,
    catalogItemId,
    name: String(resolvedName),
    type: resolveVisibilityType(source, inventoryTypeLookup),
    totalQuantity: Number.isFinite(totalQty) ? totalQty : 0,
    sharedQuantity: Number.isFinite(sharedQty) ? sharedQty : 0,
    costPerUnit,
    shareRecordId: shareRecordId != null ? String(shareRecordId) : undefined,
  };
};

const formatOptionLabel = (item: VisibilityItem): string =>
  `${item.name} (${getTypeLabel(item.type)}) - Shared ${item.sharedQuantity}/${item.totalQuantity}`;

const ResourceVisibility = () => {
  const { user } = useAuth();
  const { toast } = useToast();
  const canAccess = hasAnyPermission(user, ['hospital:resource_share.visibility.view', 'hospital:resource_share.manage']);
  const [items, setItems] = useState<VisibilityItem[]>([]);
  const [draftSharedQty, setDraftSharedQty] = useState<Record<string, string>>({});
  const [savingIds, setSavingIds] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [type, setType] = useState('all');
  const [visibilityFilter, setVisibilityFilter] = useState<'all' | 'shared' | 'non-shared'>('all');
  const [selectedItemId, setSelectedItemId] = useState('');
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [selectorQuery, setSelectorQuery] = useState('');

  const loadVisibility = useCallback(async () => {
    try {
      setLoading(true);
      const [visibilityRes, inventoryRes] = await Promise.all([
        inventoryApi.getShareVisibility(),
        inventoryApi.getAll().catch(() => null),
      ]);

      const rawVisibility = extractList(visibilityRes);
      const rawInventory = extractList(inventoryRes);
      const inventoryTypeLookup = buildInventoryTypeLookup(rawInventory);
      const mapped = rawVisibility.map((item) => mapVisibilityItem(item, inventoryTypeLookup));
      setItems(mapped);

      const drafts: Record<string, string> = {};
      mapped.forEach((entry) => {
        drafts[entry.id] = String(entry.sharedQuantity);
      });
      setDraftSharedQty(drafts);
    } catch (err: unknown) {
      toast({
        title: 'Failed to load visibility data',
        description: getErrorMessage(err),
        variant: 'destructive',
      });
      setItems([]);
      setDraftSharedQty({});
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (!canAccess) {
      return;
    }

    void loadVisibility();
  }, [canAccess, loadVisibility]);

  useEffect(() => {
    if (!canAccess) {
      return;
    }

    const handleResourceSharesUpdated = () => {
      void loadVisibility();
    };

    window.addEventListener(RESOURCE_SHARES_UPDATED_EVENT, handleResourceSharesUpdated);
    window.addEventListener('focus', handleResourceSharesUpdated);

    return () => {
      window.removeEventListener(RESOURCE_SHARES_UPDATED_EVENT, handleResourceSharesUpdated);
      window.removeEventListener('focus', handleResourceSharesUpdated);
    };
  }, [canAccess, loadVisibility]);

  const filtered = useMemo(
    () =>
      items.filter((item) => {
        const matchesSearch = item.name.toLowerCase().includes(search.toLowerCase());
        const matchesType = type === 'all' || item.type === type;
        const matchesVisibility =
          visibilityFilter === 'all' ||
          (visibilityFilter === 'shared' ? item.sharedQuantity > 0 : item.sharedQuantity <= 0);

        return matchesSearch && matchesType && matchesVisibility;
      }),
    [items, search, type, visibilityFilter]
  );

  const selectorOptions = useMemo(() => {
    const query = selectorQuery.trim().toLowerCase();
    if (!query) {
      return filtered;
    }

    return filtered.filter((item) => {
      const nameMatch = item.name.toLowerCase().includes(query);
      const typeMatch = item.type.toLowerCase().includes(query);
      const quantityMatch = String(item.totalQuantity).includes(query) || String(item.sharedQuantity).includes(query);
      return nameMatch || typeMatch || quantityMatch;
    });
  }, [filtered, selectorQuery]);

  const selectedItem = useMemo(
    () => filtered.find((item) => item.id === selectedItemId) || null,
    [filtered, selectedItemId]
  );

  useEffect(() => {
    if (filtered.length === 0) {
      if (selectedItemId) {
        setSelectedItemId('');
      }
      return;
    }

    if (!filtered.some((item) => item.id === selectedItemId)) {
      setSelectedItemId(filtered[0].id);
    }
  }, [filtered, selectedItemId]);

  const visibleCount = items.filter((i) => i.sharedQuantity > 0).length;
  const hiddenCount = items.filter((i) => i.sharedQuantity <= 0).length;

  const saveSharedQuantity = async (item: VisibilityItem, overrideQuantity?: number) => {
    const rawValue = overrideQuantity ?? Number(draftSharedQty[item.id] ?? item.sharedQuantity);
    const sharedQuantity = Math.max(0, Number(rawValue));

    if (!Number.isFinite(sharedQuantity)) {
      toast({ title: 'Invalid quantity', description: 'Please enter a valid number.', variant: 'destructive' });
      return;
    }

    if (sharedQuantity > item.totalQuantity) {
      toast({
        title: 'Quantity exceeds inventory',
        description: `Shared quantity cannot exceed total inventory (${item.totalQuantity}).`,
        variant: 'destructive',
      });
      return;
    }

    setSavingIds((prev) => ({ ...prev, [item.id]: true }));
    try {
      const response = await inventoryApi.updateShareVisibility({
        inventory_id: item.inventoryId,
        catalog_item: item.catalogItemId,
        shared_quantity: sharedQuantity,
        share_record_id: item.shareRecordId,
      });

      const responseRecord = isRecord(response) ? response : null;
      const updatedPayload = responseRecord?.data ?? response;
      const updatedRecord: UnknownRecord = isRecord(updatedPayload) ? updatedPayload : {};
      const mappedUpdated = mapVisibilityItem({
        ...item,
        ...updatedRecord,
        shared_quantity: updatedRecord.shared_quantity ?? sharedQuantity,
      });

      const nextItem: VisibilityItem = {
        ...mappedUpdated,
        id: item.id,
        inventoryId: mappedUpdated.inventoryId || item.inventoryId,
        catalogItemId: mappedUpdated.catalogItemId || item.catalogItemId,
        shareRecordId: mappedUpdated.shareRecordId ?? item.shareRecordId,
      };

      setItems((prev) => prev.map((entry) => (entry.id === item.id ? { ...entry, ...nextItem } : entry)));
      setDraftSharedQty((prev) => ({ ...prev, [item.id]: String(sharedQuantity) }));

      toast({
        title: 'Share quantity updated',
        description: `${item.name} shared quantity set to ${sharedQuantity}.`,
      });
      window.dispatchEvent(new Event(RESOURCE_SHARES_UPDATED_EVENT));
    } catch (err: unknown) {
      toast({
        title: 'Failed to update share quantity',
        description: getErrorMessage(err),
        variant: 'destructive',
      });
    } finally {
      setSavingIds((prev) => ({ ...prev, [item.id]: false }));
    }
  };

  const selectedDraftValue = selectedItem ? draftSharedQty[selectedItem.id] ?? String(selectedItem.sharedQuantity) : '';

  const updateSelectedDraft = (value: string) => {
    if (!selectedItem) {
      return;
    }

    setDraftSharedQty((prev) => ({ ...prev, [selectedItem.id]: value }));
  };

  const handleSaveSelected = async () => {
    if (!selectedItem) {
      return;
    }

    await saveSharedQuantity(selectedItem);
  };

  const handleSetHidden = async () => {
    if (!selectedItem) {
      return;
    }

    await saveSharedQuantity(selectedItem, 0);
  };

  const handleUseMax = async () => {
    if (!selectedItem) {
      return;
    }

    await saveSharedQuantity(selectedItem, selectedItem.totalQuantity);
  };

  const isSelectedItemSaving = selectedItem ? Boolean(savingIds[selectedItem.id]) : false;

  // API only allows hospital admin scope for inventory share visibility.
  if (!canAccess) {
    return <Navigate to="/dashboard" replace />;
  }

  return (
    <AppLayout
      title="Resource Visibility"
      // subtitle="Control which resources are shared with other hospitals"
    >
      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="h-8 w-8 animate-spin" /><span className="ml-2">Loading resources...</span>
        </div>
      ) : (
      <div className="space-y-6">
        {/* Summary Cards */}
        <div className="grid gap-4 sm:grid-cols-3">
          <Card>
            <CardContent className="p-4">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                  <Eye className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <p className="text-2xl font-bold">{visibleCount}</p>
                  <p className="text-sm text-muted-foreground">Visible Resources</p>
                </div>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted">
                  <EyeOff className="h-5 w-5 text-muted-foreground" />
                </div>
                <div>
                  <p className="text-2xl font-bold">{hiddenCount}</p>
                  <p className="text-sm text-muted-foreground">Hidden Resources</p>
                </div>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-success/10">
                  <CheckCircle className="h-5 w-5 text-success" />
                </div>
                <div>
                  <p className="text-2xl font-bold">{items.length}</p>
                  <p className="text-sm text-muted-foreground">Total Resources</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Info Banner */}
        {/* <Card className="border-info/20 bg-info/5">
          <CardContent className="p-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-info mt-0.5" />
              <div>
                <h4 className="font-medium">Visibility And Share Management</h4>
                <p className="text-sm text-muted-foreground mt-1">
                  Set shared quantity above <strong>0</strong> to add a resource to shared offers and update it later here.
                  Set it to <strong>0</strong> to remove it from partner visibility.
                </p>
              </div>
            </div>
          </CardContent>
        </Card> */}

        {/* Filters */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          <div className="flex gap-2 flex-wrap">
            <div className="relative w-64">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input 
                placeholder="Search resources..." 
                value={search} 
                onChange={(e) => setSearch(e.target.value)} 
                className="pl-9" 
              />
            </div>
            <Tabs value={type} onValueChange={setType}>
              <TabsList>
                <TabsTrigger value="all">All</TabsTrigger>
                <TabsTrigger value="drugs">Medicine</TabsTrigger>
                <TabsTrigger value="blood">Blood</TabsTrigger>
                <TabsTrigger value="organs">Organs</TabsTrigger>
                <TabsTrigger value="equipment">Equipment</TabsTrigger>
              </TabsList>
            </Tabs>
            <Tabs value={visibilityFilter} onValueChange={(value) => setVisibilityFilter(value as typeof visibilityFilter)}>
              <TabsList>
                <TabsTrigger value="all">All Resources</TabsTrigger>
                <TabsTrigger value="shared">Shared</TabsTrigger>
                <TabsTrigger value="non-shared">Non Shared</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </div>

        {/* Visibility Form */}
        <Card className="border-border/70 shadow-sm">
          <CardContent className="p-5 sm:p-6">
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,0.75fr)]">
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="visibility-resource-selector">Resource or medicine</Label>
                  <Popover open={selectorOpen} onOpenChange={setSelectorOpen}>
                    <PopoverTrigger asChild>
                      <Button
                        id="visibility-resource-selector"
                        type="button"
                        variant="outline"
                        role="combobox"
                        aria-expanded={selectorOpen}
                        className={cn('w-full justify-between font-normal', !selectedItem && 'text-muted-foreground')}
                      >
                        {selectedItem
                          ? selectedItem.name
                          : filtered.length > 0
                            ? 'Select a resource'
                            : 'No resources for current filters'}
                        <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
                      <Command shouldFilter={false}>
                        <CommandInput
                          placeholder="Search in filtered resources..."
                          value={selectorQuery}
                          onValueChange={setSelectorQuery}
                        />
                        <CommandList>
                          <CommandEmpty>No matching resource found.</CommandEmpty>
                          <CommandGroup>
                            {selectorOptions.map((item) => (
                              <CommandItem
                                key={item.id}
                                value={`${item.name} ${item.type} ${item.totalQuantity} ${item.sharedQuantity}`}
                                onSelect={() => {
                                  setSelectedItemId(item.id);
                                  setSelectorQuery('');
                                  setSelectorOpen(false);
                                }}
                              >
                                <Check
                                  className={cn(
                                    'mr-2 h-4 w-4 shrink-0',
                                    selectedItemId === item.id ? 'opacity-100' : 'opacity-0'
                                  )}
                                />
                                <span className="truncate">{formatOptionLabel(item)}</span>
                              </CommandItem>
                            ))}
                          </CommandGroup>
                        </CommandList>
                      </Command>
                    </PopoverContent>
                  </Popover>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="shared-quantity-input">Shared quantity</Label>
                  <Input
                    id="shared-quantity-input"
                    type="number"
                    min={0}
                    max={selectedItem?.totalQuantity ?? 0}
                    value={selectedDraftValue}
                    onChange={(e) => updateSelectedDraft(e.target.value)}
                    disabled={!selectedItem}
                  />
                  <p className="text-xs text-muted-foreground">
                    {selectedItem
                      ? `Set 0 to hide this resource. Maximum allowed: ${selectedItem.totalQuantity}.`
                      : 'Choose a resource first to manage visibility.'}
                  </p>
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button
                    onClick={handleSaveSelected}
                    disabled={!selectedItem || isSelectedItemSaving}
                  >
                    {isSelectedItemSaving ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Save className="mr-2 h-4 w-4" />
                    )}
                    Save Share Settings
                  </Button>
                  <Button
                    variant="outline"
                    type="button"
                    onClick={handleSetHidden}
                    disabled={!selectedItem || isSelectedItemSaving}
                  >
                    Set Hidden
                  </Button>
                  <Button
                    variant="outline"
                    type="button"
                    onClick={handleUseMax}
                    disabled={!selectedItem || isSelectedItemSaving}
                  >
                    Use Max
                  </Button>
                </div>
              </div>

              <div className="rounded-xl border border-border/70 bg-muted/20 p-4">
                <p className="text-sm font-medium">Selection Snapshot</p>
                {selectedItem ? (
                  <div className="mt-4 space-y-3 text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-muted-foreground">Resource</span>
                      <span className="text-right font-medium">{selectedItem.name}</span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-muted-foreground">Type</span>
                      <Badge variant="secondary" className="capitalize">
                        {getTypeLabel(selectedItem.type)}
                      </Badge>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-muted-foreground">Total inventory</span>
                      <span className="font-medium">{selectedItem.totalQuantity}</span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-muted-foreground">Shared now</span>
                      <span className="font-medium">{selectedItem.sharedQuantity}</span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-muted-foreground">Cost per unit</span>
                      <span className="font-medium">{formatCurrency(selectedItem.costPerUnit)}</span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-muted-foreground">Visibility</span>
                      <Badge variant={selectedItem.sharedQuantity > 0 ? 'outline' : 'secondary'}>
                        {selectedItem.sharedQuantity > 0 ? 'Visible' : 'Hidden'}
                      </Badge>
                    </div>
                  </div>
                ) : (
                  <p className="mt-4 text-sm text-muted-foreground">No item matches the current filters.</p>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4 sm:p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="font-medium">Filtered Resources</h3>
                <p className="text-sm text-muted-foreground">Pick an item to load it into the form above.</p>
              </div>
              <Badge variant="outline">{filtered.length} matches</Badge>
            </div>

            {filtered.length === 0 ? (
              <div className="py-10 text-center">
                <EyeOff className="mx-auto h-10 w-10 text-muted-foreground/50" />
                <h4 className="mt-3 text-base font-medium">No resources found</h4>
                <p className="text-sm text-muted-foreground">Try adjusting your search or filters.</p>
              </div>
            ) : (
              <div className="mt-4 overflow-x-auto rounded-lg border border-border/60">
                <table className="w-full min-w-[760px] text-sm">
                  <thead className="bg-muted/40 text-muted-foreground">
                    <tr>
                      <th className="px-4 py-3 text-left font-medium">Resource</th>
                      <th className="px-4 py-3 text-left font-medium">Type</th>
                      <th className="px-4 py-3 text-left font-medium">Total</th>
                      <th className="px-4 py-3 text-left font-medium">Shared</th>
                      <th className="px-4 py-3 text-left font-medium">Cost</th>
                      <th className="px-4 py-3 text-left font-medium">Visibility</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((item) => {
                      const selected = selectedItemId === item.id;
                      return (
                        <tr
                          key={item.id}
                          tabIndex={0}
                          onClick={() => setSelectedItemId(item.id)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault();
                              setSelectedItemId(item.id);
                            }
                          }}
                          className={cn(
                            'cursor-pointer border-t border-border/50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50',
                            selected ? 'bg-primary/5' : 'hover:bg-muted/30'
                          )}
                        >
                          <td className="px-4 py-3 font-medium">
                            <div className="flex items-center gap-2">
                              <span>{item.name}</span>
                              {selected ? <Badge variant="outline">Selected</Badge> : null}
                            </div>
                          </td>
                          <td className="px-4 py-3 tracking-wide text-xs text-muted-foreground">{getTypeLabel(item.type)}</td>
                          <td className="px-4 py-3">{item.totalQuantity}</td>
                          <td className="px-4 py-3">{item.sharedQuantity}</td>
                          <td className="px-4 py-3">{formatCurrency(item.costPerUnit)}</td>
                          <td className="px-4 py-3">
                            <Badge variant={item.sharedQuantity > 0 ? 'outline' : 'secondary'}>
                              {item.sharedQuantity > 0 ? 'Visible' : 'Hidden'}
                            </Badge>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
      )}
    </AppLayout>
  );
};

export default ResourceVisibility;
