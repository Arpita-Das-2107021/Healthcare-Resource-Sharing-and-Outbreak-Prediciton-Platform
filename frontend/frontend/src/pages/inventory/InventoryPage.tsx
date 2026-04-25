import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import AppLayout from '@/components/layout/AppLayout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { usePermission } from '@/hooks/usePermission';
import { useToast } from '@/hooks/use-toast';
import { inventoryApi, inventoryModuleApi } from '@/services/api';
import { inventoryService, type InventoryItem } from '@/services/inventoryService';
import { RESOURCE_SHARES_UPDATED_EVENT } from '@/constants/events';

const INVENTORY_QUERY_KEY = ['retail-inventory'];

const INVENTORY_ADJUST_PERMISSION_CODES = [
  'hospital:inventory.edit',
  'hospital:inventory.manage',
  'inventory.edit',
  'inventory.manage',
  'inventory.batch.view',
  'inventory.cost.view',
];

const INVENTORY_QUICK_UPDATE_PERMISSION_CODES = ['hospital:inventory.import', 'inventory.import'];

type InventoryTransactionType = 'restock' | 'adjustment' | 'correction';

const asRecord = (value: unknown): Record<string, unknown> => {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }

  return {};
};

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

  return `৳${value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;
};

const discountBadgeLabel = (discount: unknown): string | null => {
  if (discount === null || discount === undefined) {
    return null;
  }

  if (typeof discount === 'number' && Number.isFinite(discount)) {
    return `Save ${discount}%`;
  }

  if (typeof discount === 'string') {
    const trimmed = discount.trim();
    return trimmed ? trimmed : null;
  }

  const row = asRecord(discount);
  const percent = toNullableNumber(
    row.percentage ?? row.percent ?? row.discount_percentage,
  );
  if (percent !== null) {
    return `Save ${percent}%`;
  }

  const amount = toNullableNumber(
    row.amount ?? row.flat_amount ?? row.discount_amount,
  );
  if (amount !== null) {
    return `৳${amount} OFF`;
  }

  const label = typeof row.label === 'string' ? row.label.trim() : '';
  if (label) {
    return label;
  }

  return null;
};

const InventoryPage = () => {
  const { can, canAny } = usePermission();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [editingItem, setEditingItem] = useState<InventoryItem | null>(null);
  const [quantityDelta, setQuantityDelta] = useState('');
  const [pricePerUnit, setPricePerUnit] = useState('');
  const [transactionType, setTransactionType] = useState<InventoryTransactionType>('restock');
  const [notes, setNotes] = useState('');

  const canViewInventory = can('inventory.view');
  const canCreateSale = canAny(['sale.create', 'sale.manage', 'sales.manage', 'hospital:sales.manage']);
  const canAdjustInventory = canAny(INVENTORY_ADJUST_PERMISSION_CODES);
  const canQuickUpdateInventory = canAny(INVENTORY_QUICK_UPDATE_PERMISSION_CODES);
  const canUpdateInventory = canAdjustInventory || canQuickUpdateInventory;

  const inventoryQuery = useQuery({
    queryKey: INVENTORY_QUERY_KEY,
    queryFn: () => inventoryService.list(),
    enabled: canViewInventory,
  });

  useEffect(() => {
    if (!canViewInventory) {
      return;
    }

    const refreshInventory = () => {
      void queryClient.invalidateQueries({ queryKey: INVENTORY_QUERY_KEY });
    };

    window.addEventListener(RESOURCE_SHARES_UPDATED_EVENT, refreshInventory);
    window.addEventListener('focus', refreshInventory);

    return () => {
      window.removeEventListener(RESOURCE_SHARES_UPDATED_EVENT, refreshInventory);
      window.removeEventListener('focus', refreshInventory);
    };
  }, [canViewInventory, queryClient]);

  const inventoryItems = useMemo(() => inventoryQuery.data || [], [inventoryQuery.data]);

  const rows = useMemo(() => {
    return inventoryItems.map((item: InventoryItem) => ({
      ...item,
      discountLabel: discountBadgeLabel(item.discount),
    }));
  }, [inventoryItems]);

  const updateInventoryMutation = useMutation({
    mutationFn: async (payload: {
      item: InventoryItem;
      quantityDelta: number;
      transactionType: InventoryTransactionType;
      notes: string;
      pricePerUnit: number | null;
    }) => {
      if (canQuickUpdateInventory && payload.pricePerUnit !== null) {
        return inventoryModuleApi.quickUpdate({
          inventory_id: payload.item.id,
          catalog_item: payload.item.catalog_item_id || undefined,
          quantity_delta: payload.quantityDelta,
          transaction_type: payload.transactionType,
          notes: payload.notes || 'Updated via inventory stock list',
          unit_price: payload.pricePerUnit,
          price_per_unit: payload.pricePerUnit,
        });
      }

      if (canAdjustInventory) {
        return inventoryApi.adjust(payload.item.id, {
          quantity_delta: payload.quantityDelta,
          transaction_type: payload.transactionType,
          notes: payload.notes || 'Updated via inventory stock list',
        });
      }

      if (canQuickUpdateInventory) {
        return inventoryModuleApi.quickUpdate({
          inventory_id: payload.item.id,
          catalog_item: payload.item.catalog_item_id || undefined,
          quantity_delta: payload.quantityDelta,
          transaction_type: payload.transactionType,
          notes: payload.notes || 'Updated via inventory stock list',
        });
      }

      throw new Error('You are not authorized to update inventory.');
    },
    onSuccess: () => {
      toast({
        title: 'Inventory updated',
        description: 'Stock update has been saved successfully.',
      });
      setEditingItem(null);
      setQuantityDelta('');
      setPricePerUnit('');
      setTransactionType('restock');
      setNotes('');
      queryClient.invalidateQueries({ queryKey: INVENTORY_QUERY_KEY });
    },
    onError: (error: unknown) => {
      toast({
        title: 'Inventory update failed',
        description: error instanceof Error ? error.message : 'Please try again.',
        variant: 'destructive',
      });
    },
  });

  const openUpdateDialog = (item: InventoryItem) => {
    setEditingItem(item);
    setQuantityDelta('');
    setPricePerUnit(item.price_per_unit !== null ? String(item.price_per_unit) : '');
    setTransactionType('restock');
    setNotes(`Stock update requested for ${item.catalog_item_name}`);
  };

  const handleSubmitUpdate = () => {
    if (!editingItem) {
      return;
    }

    const normalizedQuantityDelta = quantityDelta.trim();
    const normalizedPriceInput = pricePerUnit.trim();
    const hasQuantityUpdate = normalizedQuantityDelta.length > 0;
    const hasPriceUpdate = normalizedPriceInput.length > 0;

    if (!hasQuantityUpdate && !hasPriceUpdate) {
      toast({
        title: 'No changes provided',
        description: 'Enter a quantity change, a new price, or both.',
        variant: 'destructive',
      });
      return;
    }

    const parsedDelta = hasQuantityUpdate ? Number(normalizedQuantityDelta) : 0;
    if (hasQuantityUpdate && (!Number.isFinite(parsedDelta) || parsedDelta === 0)) {
      toast({
        title: 'Invalid quantity change',
        description: 'Enter a non-zero number (for example 10 or -3).',
        variant: 'destructive',
      });
      return;
    }

    const parsedPrice = hasPriceUpdate ? Number(normalizedPriceInput) : null;
    if (hasPriceUpdate && (!Number.isFinite(parsedPrice) || parsedPrice < 0)) {
      toast({
        title: 'Invalid price',
        description: 'Enter a valid non-negative unit price.',
        variant: 'destructive',
      });
      return;
    }

    if (hasPriceUpdate && !canQuickUpdateInventory) {
      toast({
        title: 'Price update unavailable',
        description: 'Updating price from stock list requires quick update access.',
        variant: 'destructive',
      });
      return;
    }

    updateInventoryMutation.mutate({
      item: editingItem,
      quantityDelta: parsedDelta,
      transactionType,
      notes: notes.trim(),
      pricePerUnit: parsedPrice,
    });
  };

  const buildResourceRouteState = (item: InventoryItem) => ({
    share: {
      id: item.id,
      catalog_item: item.catalog_item_id,
      catalog_item_name: item.catalog_item_name,
      resource_type_name: item.resource_type_name || 'drugs',
      hospital_name: item.hospital_name,
      quantity_offered: item.quantity_free,
      quantity_available: item.quantity_available,
      status: item.quantity_free > 0 ? 'active' : 'closed',
    },
  });

  if (!canViewInventory) {
    return <Navigate to="/dashboard" replace />;
  }

  return (
    <AppLayout title="Inventory">
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>Inventory Catalog</CardTitle>
          </CardHeader>
          <CardContent>
            {inventoryQuery.isLoading ? (
              <div className="flex items-center justify-center py-10 text-muted-foreground">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading inventory...
              </div>
            ) : inventoryQuery.isError ? (
              <div className="rounded border border-destructive/20 bg-destructive/5 p-3 text-sm text-destructive">
                Failed to load inventory.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Catalog Item</TableHead>
                    <TableHead>Quantity Free</TableHead>
                    <TableHead>Price Per Unit</TableHead>
                    <TableHead>Discount</TableHead>
                    <TableHead className="text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center text-muted-foreground">
                        No inventory items found.
                      </TableCell>
                    </TableRow>
                  ) : (
                    rows.map((item) => (
                      <TableRow key={item.id}>
                        <TableCell className="font-medium">
                          <Link
                            to={`/resource/${encodeURIComponent(item.id)}`}
                            state={buildResourceRouteState(item)}
                            className="cursor-pointer text-primary transition-colors hover:underline hover:text-primary/90"
                          >
                            {item.catalog_item_name}
                          </Link>
                        </TableCell>
                        <TableCell>{item.quantity_free}</TableCell>
                        <TableCell>{formatCurrency(item.price_per_unit)}</TableCell>
                        <TableCell>
                          {item.discountLabel ? (
                            <Badge variant="secondary">{item.discountLabel}</Badge>
                          ) : (
                            <span className="text-muted-foreground">-</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-2">
                            {canCreateSale ? (
                              item.quantity_free > 0 ? (
                                <Button asChild size="sm">
                                  <Link to={`/sales/create?inventory=${encodeURIComponent(item.id)}`}>Sell</Link>
                                </Button>
                              ) : (
                                <Button size="sm" disabled>
                                  Sell
                                </Button>
                              )
                            ) : null}

                            {canUpdateInventory ? (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => openUpdateDialog(item)}
                              >
                                Update Inventory
                              </Button>
                            ) : null}
                          </div>

                          {!canCreateSale && !canUpdateInventory ? (
                            <span className="text-xs text-muted-foreground">No action permission</span>
                          ) : null}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Dialog
          open={Boolean(editingItem)}
          onOpenChange={(open) => {
            if (!open && !updateInventoryMutation.isPending) {
              setEditingItem(null);
              setPricePerUnit('');
            }
          }}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Update Inventory</DialogTitle>
              <DialogDescription>
                {editingItem ? `Adjust stock for ${editingItem.catalog_item_name}.` : 'Adjust stock quantities.'}
              </DialogDescription>
            </DialogHeader>

            {editingItem ? (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-3 rounded-md border border-border/70 bg-muted/20 p-3 text-sm">
                  <div>
                    <p className="text-muted-foreground">Quantity available</p>
                    <p className="font-medium">{editingItem.quantity_available}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Quantity free</p>
                    <p className="font-medium">{editingItem.quantity_free}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Current price</p>
                    <p className="font-medium">{formatCurrency(editingItem.price_per_unit)}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Update mode</p>
                    <p className="font-medium">
                      {canAdjustInventory ? 'Inventory Adjust API' : 'Inventory Module Quick Update API'}
                    </p>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="inventory-quantity-delta">Quantity change</Label>
                  <Input
                    id="inventory-quantity-delta"
                    type="number"
                    value={quantityDelta}
                    onChange={(event) => setQuantityDelta(event.target.value)}
                    placeholder="Use + for restock, - for reduction"
                    disabled={updateInventoryMutation.isPending}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="inventory-unit-price">Unit price (optional)</Label>
                  <Input
                    id="inventory-unit-price"
                    type="number"
                    min={0}
                    step="0.01"
                    value={pricePerUnit}
                    onChange={(event) => setPricePerUnit(event.target.value)}
                    placeholder="Leave empty to keep current price"
                    disabled={updateInventoryMutation.isPending || !canQuickUpdateInventory}
                  />
                  {!canQuickUpdateInventory ? (
                    <p className="text-xs text-muted-foreground">
                      Price updates are disabled for your current role.
                    </p>
                  ) : null}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="inventory-transaction-type">Transaction type</Label>
                  <Select
                    value={transactionType}
                    onValueChange={(value) => setTransactionType(value as InventoryTransactionType)}
                    disabled={updateInventoryMutation.isPending}
                  >
                    <SelectTrigger id="inventory-transaction-type">
                      <SelectValue placeholder="Select transaction type" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="restock">Restock</SelectItem>
                      <SelectItem value="adjustment">Adjustment</SelectItem>
                      <SelectItem value="correction">Correction</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="inventory-update-notes">Notes</Label>
                  <Input
                    id="inventory-update-notes"
                    value={notes}
                    onChange={(event) => setNotes(event.target.value)}
                    placeholder="Add update notes"
                    disabled={updateInventoryMutation.isPending}
                  />
                </div>
              </div>
            ) : null}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setEditingItem(null)}
                disabled={updateInventoryMutation.isPending}
              >
                Cancel
              </Button>
              <Button type="button" onClick={handleSubmitUpdate} disabled={updateInventoryMutation.isPending || !editingItem}>
                {updateInventoryMutation.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Updating...
                  </>
                ) : (
                  'Save Update'
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </AppLayout>
  );
};

export default InventoryPage;
