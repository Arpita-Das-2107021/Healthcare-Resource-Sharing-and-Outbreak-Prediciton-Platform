import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import ResourceVisibility from '@/pages/ResourceVisibility';

const mockGetShareVisibility = vi.fn();
const mockGetAllInventory = vi.fn();
const mockUpdateShareVisibility = vi.fn();
const mockToast = vi.fn();

vi.mock('@/components/layout/AppLayout', () => ({
  default: ({ children, title }: { children: ReactNode; title: string }) => (
    <div>
      <h1>{title}</h1>
      {children}
    </div>
  ),
}));

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: {
      role: 'HOSPITAL_ADMIN',
      hospital_id: 'hospital-1',
      effective_permissions: ['hospital:resource_share.visibility.view', 'hospital:resource_share.manage'],
    },
  }),
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mockToast }),
}));

vi.mock('@/lib/rbac', () => ({
  hasAnyPermission: () => true,
}));

vi.mock('@/services/api', () => ({
  inventoryApi: {
    getShareVisibility: (...args: unknown[]) => mockGetShareVisibility(...args),
    getAll: (...args: unknown[]) => mockGetAllInventory(...args),
    updateShareVisibility: (...args: unknown[]) => mockUpdateShareVisibility(...args),
  },
}));

const renderResourceVisibility = () =>
  render(
    <MemoryRouter>
      <ResourceVisibility />
    </MemoryRouter>
  );

describe('ResourceVisibility', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetAllInventory.mockResolvedValue({ data: [] });
  });

  it('renders inventory options including entries with shared quantity 0', async () => {
    mockGetAllInventory.mockResolvedValue({
      data: [
        {
          id: 'inv-1',
          catalog_item: 'cat-1',
          catalog_item_name: 'Ventilator',
          resource_type: 'equipment',
          price_per_unit: 250,
        },
        {
          id: 'inv-2',
          catalog_item: 'cat-2',
          catalog_item_name: 'Oxygen Cylinder',
          resource_type: 'equipment',
          price_per_unit: null,
        },
      ],
    });

    mockGetShareVisibility.mockResolvedValue({
      data: [
        {
          id: 'inv-1',
          inventory_id: 'inv-1',
          catalog_item: 'cat-1',
          catalog_item_name: 'Ventilator',
          total_quantity: 5,
          shared_quantity: 2,
          share_record_id: 'share-1',
          resource_type: 'equipment',
        },
        {
          id: 'inv-2',
          inventory_id: 'inv-2',
          catalog_item: 'cat-2',
          catalog_item_name: 'Oxygen Cylinder',
          total_quantity: 40,
          shared_quantity: 0,
          share_record_id: null,
          resource_type: 'equipment',
        },
      ],
    });

    renderResourceVisibility();

    const table = await screen.findByRole('table');
    expect(within(table).getByText('Cost')).toBeInTheDocument();
    expect(within(table).getByText('Ventilator')).toBeInTheDocument();
    expect(within(table).getByText('Oxygen Cylinder')).toBeInTheDocument();

    const ventilatorRow = within(table).getByText('Ventilator').closest('tr');
    expect(ventilatorRow).not.toBeNull();
    expect(within(ventilatorRow as HTMLTableRowElement).getByText('৳250')).toBeInTheDocument();

    const oxygenRow = within(table).getByText('Oxygen Cylinder').closest('tr');
    expect(oxygenRow).not.toBeNull();
    expect(within(oxygenRow as HTMLTableRowElement).getByText('-')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /Non Shared/i }));

    expect(screen.getByLabelText(/shared quantity/i)).toHaveValue(0);
  });

  it('filters resources by all, shared, and non-shared visibility', async () => {
    mockGetShareVisibility.mockResolvedValue({
      data: [
        {
          id: 'inv-1',
          inventory_id: 'inv-1',
          catalog_item: 'cat-1',
          catalog_item_name: 'Ventilator',
          total_quantity: 5,
          shared_quantity: 2,
          share_record_id: 'share-1',
          resource_type: 'equipment',
        },
        {
          id: 'inv-2',
          inventory_id: 'inv-2',
          catalog_item: 'cat-2',
          catalog_item_name: 'Oxygen Cylinder',
          total_quantity: 40,
          shared_quantity: 0,
          share_record_id: null,
          resource_type: 'equipment',
        },
      ],
    });

    renderResourceVisibility();

    await screen.findByRole('table');
    expect(screen.getByText(/2 matches/i)).toBeInTheDocument();

    let table = screen.getByRole('table');
    expect(within(table).getByText('Ventilator')).toBeInTheDocument();
    expect(within(table).getByText('Oxygen Cylinder')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /^Shared$/i }));
    expect(screen.getByText(/1 matches/i)).toBeInTheDocument();
    table = screen.getByRole('table');
    expect(within(table).getByText('Ventilator')).toBeInTheDocument();
    expect(within(table).queryByText('Oxygen Cylinder')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /Non Shared/i }));
    expect(screen.getByText(/1 matches/i)).toBeInTheDocument();
    table = screen.getByRole('table');
    expect(within(table).getByText('Oxygen Cylinder')).toBeInTheDocument();
    expect(within(table).queryByText('Ventilator')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('tab', { name: /All Resources/i }));
    expect(screen.getByText(/2 matches/i)).toBeInTheDocument();
    table = screen.getByRole('table');
    expect(within(table).getByText('Ventilator')).toBeInTheDocument();
    expect(within(table).getByText('Oxygen Cylinder')).toBeInTheDocument();
  });

  it('saves updated shared quantity through inventory share-visibility API from form controls', async () => {
    mockGetShareVisibility.mockResolvedValue({
      data: [
        {
          id: 'inv-1',
          inventory_id: 'inv-1',
          catalog_item: 'cat-1',
          catalog_item_name: 'Ventilator',
          total_quantity: 5,
          shared_quantity: 2,
          share_record_id: 'share-1',
          resource_type: 'equipment',
        },
      ],
    });

    mockUpdateShareVisibility.mockResolvedValue({
      data: {
        id: 'inv-1',
        inventory_id: 'inv-1',
        catalog_item: 'cat-1',
        catalog_item_name: 'Ventilator',
        total_quantity: 5,
        shared_quantity: 3,
        share_record_id: 'share-1',
        resource_type: 'equipment',
      },
    });

    renderResourceVisibility();

    const table = await screen.findByRole('table');
    await userEvent.click(within(table).getByText('Ventilator'));

    const input = screen.getByLabelText(/shared quantity/i);
    await waitFor(() => expect(input).toBeEnabled());
    await userEvent.clear(input);
    await userEvent.type(input, '3');

    await userEvent.click(screen.getByRole('button', { name: /save share settings/i }));

    await waitFor(() => {
      expect(mockUpdateShareVisibility).toHaveBeenCalledWith({
        inventory_id: 'inv-1',
        catalog_item: 'cat-1',
        shared_quantity: 3,
        share_record_id: 'share-1',
      });
    });
  });

  it('applies Set Hidden and Use Max as immediate updates', async () => {
    mockGetShareVisibility.mockResolvedValue({
      data: [
        {
          id: 'inv-1',
          inventory_id: 'inv-1',
          catalog_item: 'cat-1',
          catalog_item_name: 'Ventilator',
          total_quantity: 5,
          shared_quantity: 2,
          share_record_id: 'share-1',
          resource_type: 'equipment',
        },
      ],
    });

    mockUpdateShareVisibility
      .mockResolvedValueOnce({
        data: {
          id: 'inv-1',
          inventory_id: 'inv-1',
          catalog_item: 'cat-1',
          catalog_item_name: 'Ventilator',
          total_quantity: 5,
          shared_quantity: 0,
          share_record_id: 'share-1',
          resource_type: 'equipment',
        },
      })
      .mockResolvedValueOnce({
        data: {
          id: 'inv-1',
          inventory_id: 'inv-1',
          catalog_item: 'cat-1',
          catalog_item_name: 'Ventilator',
          total_quantity: 5,
          shared_quantity: 5,
          share_record_id: 'share-1',
          resource_type: 'equipment',
        },
      });

    renderResourceVisibility();

    await screen.findByText('Ventilator');

    await userEvent.click(screen.getByRole('button', { name: /set hidden/i }));

    await waitFor(() => {
      expect(mockUpdateShareVisibility).toHaveBeenCalledWith({
        inventory_id: 'inv-1',
        catalog_item: 'cat-1',
        shared_quantity: 0,
        share_record_id: 'share-1',
      });
    });

    await userEvent.click(screen.getByRole('button', { name: /use max/i }));

    await waitFor(() => {
      expect(mockUpdateShareVisibility).toHaveBeenCalledWith({
        inventory_id: 'inv-1',
        catalog_item: 'cat-1',
        shared_quantity: 5,
        share_record_id: 'share-1',
      });
    });
  });
});
