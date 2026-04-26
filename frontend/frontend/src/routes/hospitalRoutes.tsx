/**
 * Hospital Application Routes
 *
 * These routes belong to hospital users (Hospital Admin, Pharmacist,
 * Doctor, Coordinator, Logistics Staff, etc.).
 * They are intentionally separate from platform administration routes.
 *
 * Exported as a JSX Fragment so React Router v6 can process it
 * transparently when included inside <Routes> in App.tsx.
 */
import { Navigate, Route } from 'react-router-dom';
import ProtectedRoute from '@/components/auth/ProtectedRoute';
import Dashboard from '@/pages/Dashboard';
import InventoryPage from '@/pages/inventory/InventoryPage';
import InventoryCsvImportCenter from '@/pages/InventoryCsvImportCenter';
import InventoryImportJobDetail from '@/pages/InventoryImportJobDetail';
import SalesCreatePage from '@/pages/sales/SalesCreatePage';
import SalesHistoryPage from '@/pages/sales/SalesHistoryPage';
import SaleDetailPage from '@/pages/sales/SaleDetailPage';
import SharedResources from '@/pages/SharedResources';
import RequestWorkflow from '@/pages/RequestWorkflow';
import ScanDispatchQRCode from '@/pages/ScanDispatchQRCode';
import ResourceVisibility from '@/pages/ResourceVisibility';
import RequestTemplatesPage from '@/pages/RequestTemplatesPage';
import EmergencyBroadcastPage from '@/pages/EmergencyBroadcastPage';
import Hospitals from '@/pages/Hospitals';
import HospitalDetails from '@/pages/HospitalDetails';
import HospitalTrustProfile from '@/pages/HospitalTrustProfile';
import ResourceDetails from '@/pages/ResourceDetails';
import Messages from '@/pages/Messages';
import Reports from '@/pages/Reports';
import Catalog from '@/pages/Catalog';
import UserProfile from '@/pages/UserProfile';
import OffboardingRequest from '@/pages/hospital-admin/OffboardingRequest';
import FacilitySourceSetup from '@/pages/hospital-admin/FacilitySourceSetup';
import HospitalUpdateRequests from '@/pages/hospital-admin/HospitalUpdateRequests';
import HospitalStaffInvitations from '@/pages/hospital-admin/StaffInvitations';
import HospitalStaffManagement from '@/pages/hospital-admin/StaffManagement';
import HospitalStaffProfiles from '@/pages/hospital-admin/StaffProfiles';
import HospitalStaffProfileDetails from '@/pages/hospital-admin/StaffProfileDetails';
import HospitalRoleManagement from '@/pages/hospital-admin/RoleManagement';
// import PaymentCheckoutStatus from '@/pages/PaymentCheckoutStatus';
// import PaymentOperationsCenter from '@/pages/PaymentOperationsCenter';
// import PaymentReconciliationConsole from '@/pages/PaymentReconciliationConsole';
import MLForecastingPage from '@/pages/MLForecastingPage';
import MLOutbreakPredictionPage from '@/pages/MLOutbreakPredictionPage';
import MLOperations from '@/pages/MLOperations';
import MLInsightsDashboard from '@/pages/MLInsightsDashboard';

const HOSPITAL_ADMIN_ADMINISTRATION_PERMISSIONS = [
  'hospital:offboarding.request',
  'hospital:role.manage',
  'hospital:user_role.assign',
  'hospital:staff.manage',
  'hospital:invitation.manage',
  'hospital:hospital.update',
  'hospital:integration.manage',
];

const PLATFORM_ML_ALLOWED_ROLES = ['ML_ENGINEER', 'ML_ADMIN'];

const PLATFORM_ML_OPERATIONS_PERMISSIONS = [
  'ml:job.view',
  'ml:job.manage',
  'ml:dataset.review',
  'ml:training.manage',
  'ml:model_version.manage',
  'ml:model_version.activate',
];

const PLATFORM_ML_INSIGHTS_PERMISSIONS = [
  'ml:forecast.view',
  'ml:outbreak.view',
  'ml:suggestion.view',
  'ml:dataset.review',
  'ml:training.manage',
  'ml:model_version.manage',
  'ml:model_version.activate',
];

// ── Hospital application routes ───────────────────────────────────────────────
// Exported as a Fragment, NOT a component, so React Router v6 can see
// the Route elements directly when {HospitalRoutes} is placed in <Routes>.
const PERMISSIONS = {
  dashboard: [
    'dashboard:view',
    'hospital:inventory.view',
    'hospital:request.view',
    'hospital:hospital.view',
    'communication:chat.view',
    'reports:view',
  ],
  inventory: ['hospital:inventory.view'],
  forecasting: ['ml:forecast.view', 'hospital:inventory.view'],
  outbreak: ['ml:outbreak.view', 'hospital:inventory.view'],
  inventoryImports: ['hospital:inventory.import', 'hospital:inventory.view'],
  sales: ['hospital:sales.view'],
  hospitals: ['hospital:hospital.view', 'platform:hospital.view'],
  trustProfiles: ['hospital:hospital.view', 'hospital:hospital.update', 'hospital:inventory.view', 'platform:hospital.view'],
  trustPerformance: ['hospital:analytics.view', 'reports:analytics.view'],
  resourceDetails: ['hospital:inventory.view', 'hospital:resource_share.view'],
  sharing: ['hospital:resource_share.view'],
  requests: ['hospital:request.view'],
  dispatchScan: ['hospital:request.view'],
  visibility: ['hospital:resource_share.visibility.view', 'hospital:resource_share.manage'],
  templates: ['communication:template.view', 'hospital:request.create'],
  messages: ['communication:chat.view', 'communication:conversation.view', 'hospital:communication.view'],
  emergency: ['communication:broadcast.view', 'communication:broadcast.manage', 'hospital:broadcast.manage'],
  catalog: ['hospital:catalog.view'],
  paymentCheckout: ['hospital:payment.view', 'hospital:payment.initiate'],
  paymentOps: ['hospital:payment.view'],
  paymentReconcile: ['hospital:payment.reconcile.manage'],
  refundInitiate: ['hospital:payment.refund.initiate'],
  refundConfirm: ['hospital:payment.refund.confirm'],
  mlOperations: PLATFORM_ML_OPERATIONS_PERMISSIONS,
  mlInsights: PLATFORM_ML_INSIGHTS_PERMISSIONS,
  reports: ['reports:view', 'hospital:analytics.view', 'platform:audit.view'],
  hospitalRoleManagement: ['hospital:role.manage', 'hospital:user_role.assign'],
  hospitalStaffManagement: ['hospital:staff.manage', 'hospital:staff.supervise'],
  hospitalStaffProfiles: ['hospital:staff.view', 'hospital:staff.manage', 'hospital:staff.supervise'],
  hospitalStaffInvitations: ['hospital:invitation.view', 'hospital:invitation.manage', 'hospital:user_role.assign'],
  hospitalUpdateRequests: HOSPITAL_ADMIN_ADMINISTRATION_PERMISSIONS,
  hospitalFacilitySourceSetup: ['hospital:integration.manage', 'hospital:hospital.update'],
  profile: [
    'auth:permission.effective.view',
    'dashboard:view',
    'communication:notification.view',
    'communication:chat.view',
    'hospital:hospital.view',
    'platform:user.view',
  ],
  offboarding: HOSPITAL_ADMIN_ADMINISTRATION_PERMISSIONS,
};

const HospitalRoutes = (
  <>
    {/* Core dashboard */}
    <Route
      path="/dashboard"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.dashboard} requiredContext="HEALTHCARE">
          <Dashboard />
        </ProtectedRoute>
      }
    />

    {/* Inventory */}
    <Route
      path="/inventory"
      element={
        <ProtectedRoute requiredContext="HEALTHCARE">
          <InventoryPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="/inventory/forecasting"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.forecasting} requiredContext="HEALTHCARE">
          <MLForecastingPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="/inventory/outbreak-prediction"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.outbreak} requiredContext="HEALTHCARE">
          <MLOutbreakPredictionPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="/inventory/imports"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.inventoryImports} requiredContext="HEALTHCARE">
          <InventoryCsvImportCenter />
        </ProtectedRoute>
      }
    />
    <Route
      path="/inventory/imports/:jobId"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.inventoryImports} requiredContext="HEALTHCARE">
          <InventoryImportJobDetail />
        </ProtectedRoute>
      }
    />

    {/* Internal Sales */}
    <Route
      path="/sales"
      element={
        <ProtectedRoute requiredContext="HEALTHCARE">
          <Navigate to="/sales/history" replace />
        </ProtectedRoute>
      }
    />
    <Route
      path="/sales/create"
      element={
        <ProtectedRoute requiredContext="HEALTHCARE">
          <SalesCreatePage />
        </ProtectedRoute>
      }
    />
    <Route
      path="/sales/history"
      element={
        <ProtectedRoute requiredContext="HEALTHCARE">
          <SalesHistoryPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="/sales/:id"
      element={
        <ProtectedRoute requiredContext="HEALTHCARE">
          <SaleDetailPage />
        </ProtectedRoute>
      }
    />

    {/* Hospitals & trust */}
    <Route
      path="/hospitals"
      element={
        <ProtectedRoute requiredContext="HEALTHCARE">
          <Hospitals />
        </ProtectedRoute>
      }
    />
    <Route
      path="/hospital/:hospitalId"
      element={
        <ProtectedRoute requiredContext="HEALTHCARE">
          <HospitalDetails />
        </ProtectedRoute>
      }
    />
    <Route
      path="/hospital/:hospitalId/profile"
      element={
        <ProtectedRoute requiredContext="HEALTHCARE">
          <HospitalTrustProfile />
        </ProtectedRoute>
      }
    />
    <Route
      path="/hospital-profiles"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.trustProfiles} requiredContext="HEALTHCARE">
          <Hospitals />
        </ProtectedRoute>
      }
    />
    <Route
      path="/trust/profiles"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.trustProfiles} requiredContext="HEALTHCARE">
          <Navigate to="/hospital-profiles" replace />
        </ProtectedRoute>
      }
    />
    <Route
      path="/hospital-admin/performance-tracking"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.trustPerformance} requiredContext="HEALTHCARE">
          <HospitalTrustProfile />
        </ProtectedRoute>
      }
    />
    <Route
      path="/trust/performance"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.trustPerformance} requiredContext="HEALTHCARE">
          <Navigate to="/hospital-admin/performance-tracking" replace />
        </ProtectedRoute>
      }
    />
    {/* Resources */}
    <Route
      path="/resource/:resourceId"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.resourceDetails} requiredContext="HEALTHCARE">
          <ResourceDetails />
        </ProtectedRoute>
      }
    />

    {/* Resource sharing */}
    <Route
      path="/sharing"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.sharing} requiredContext="HEALTHCARE">
          <SharedResources />
        </ProtectedRoute>
      }
    />
    <Route
      path="/sharing/my-resources"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.sharing} requiredContext="HEALTHCARE">
          <SharedResources />
        </ProtectedRoute>
      }
    />
    <Route
      path="/sharing/requests"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.requests} requiredContext="HEALTHCARE">
          <RequestWorkflow view="incoming" />
        </ProtectedRoute>
      }
    />
    <Route
      path="/sharing/requests/incoming"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.requests} requiredContext="HEALTHCARE">
          <RequestWorkflow view="incoming" />
        </ProtectedRoute>
      }
    />
    <Route
      path="/sharing/requests/outgoing"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.requests} requiredContext="HEALTHCARE">
          <RequestWorkflow view="outgoing" />
        </ProtectedRoute>
      }
    />
    <Route
      path="/dispatch/scan"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.dispatchScan} requiredContext="HEALTHCARE">
          <ScanDispatchQRCode />
        </ProtectedRoute>
      }
    />
    <Route
      path="/sharing/visibility"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.visibility} requiredContext="HEALTHCARE">
          <ResourceVisibility />
        </ProtectedRoute>
      }
    />
    <Route
      path="/sharing/templates"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.templates} requiredContext="HEALTHCARE">
          <RequestTemplatesPage />
        </ProtectedRoute>
      }
    />

    {/* Communication */}
    <Route
      path="/messages"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.messages} requiredContext="HEALTHCARE">
          <Messages />
        </ProtectedRoute>
      }
    />
    <Route
      path="/communication/emergency"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.emergency}>
          <EmergencyBroadcastPage />
        </ProtectedRoute>
      }
    />

    {/* Catalog & reporting */}
    <Route
      path="/catalog"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.catalog} requiredContext="HEALTHCARE">
          <Catalog />
        </ProtectedRoute>
      }
    />
    {/* <Route
      path="/payments/checkout"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.paymentCheckout} requiredContext="HEALTHCARE">
          <PaymentCheckoutStatus />
        </ProtectedRoute>
      }
    />
    <Route
      path="/payments/operations"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.paymentOps} requiredContext="HEALTHCARE">
          <PaymentOperationsCenter />
        </ProtectedRoute>
      }
    />
    <Route
      path="/payments/reconciliation"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.paymentReconcile} requiredContext="HEALTHCARE">
          <PaymentReconciliationConsole />
        </ProtectedRoute>
      }
    /> */}
    <Route
      path="/ml/operations"
      element={
        <ProtectedRoute
          requiredPermissions={PERMISSIONS.mlOperations}
          requiredContext="PLATFORM"
          requiredPlatformRoles={PLATFORM_ML_ALLOWED_ROLES}
        >
          <MLOperations />
        </ProtectedRoute>
      }
    />
    <Route
      path="/ml/insights"
      element={
        <ProtectedRoute
          requiredPermissions={PERMISSIONS.mlInsights}
          requiredContext="PLATFORM"
          requiredPlatformRoles={PLATFORM_ML_ALLOWED_ROLES}
        >
          <MLInsightsDashboard />
        </ProtectedRoute>
      }
    />
    <Route
      path="/reports"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.reports}>
          <Reports />
        </ProtectedRoute>
      }
    />

    {/* User profile */}
    <Route
      path="/profile"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.profile}>
          <UserProfile />
        </ProtectedRoute>
      }
    />

    <Route
      path="/hospital-admin/offboarding-request"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.offboarding} requiredContext="HEALTHCARE">
          <OffboardingRequest />
        </ProtectedRoute>
      }
    />

    <Route
      path="/hospital-admin/hospital-update-requests"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.hospitalUpdateRequests} requiredContext="HEALTHCARE">
          <HospitalUpdateRequests />
        </ProtectedRoute>
      }
    />

    {/* Hospital admin operations (separate from platform /admin/* routes) */}
    <Route
      path="/hospital-admin/roles"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.hospitalRoleManagement} requiredContext="HEALTHCARE">
          <HospitalRoleManagement />
        </ProtectedRoute>
      }
    />
    <Route
      path="/hospital-admin/staff"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.hospitalStaffManagement} requiredContext="HEALTHCARE">
          <HospitalStaffManagement />
        </ProtectedRoute>
      }
    />
    <Route
      path="/hospital-admin/staff-profiles"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.hospitalStaffProfiles} requiredContext="HEALTHCARE">
          <HospitalStaffProfiles />
        </ProtectedRoute>
      }
    />
    <Route
      path="/hospital-admin/staff-profiles/:staffId"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.hospitalStaffProfiles} requiredContext="HEALTHCARE">
          <HospitalStaffProfileDetails />
        </ProtectedRoute>
      }
    />
    <Route
      path="/hospital-admin/staff-invitations"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.hospitalStaffInvitations} requiredContext="HEALTHCARE">
          <HospitalStaffInvitations />
        </ProtectedRoute>
      }
    />
    <Route
      path="/hospital-admin/facility-source-setup"
      element={
        <ProtectedRoute requiredPermissions={PERMISSIONS.hospitalFacilitySourceSetup} requiredContext="HEALTHCARE">
          <FacilitySourceSetup />
        </ProtectedRoute>
      }
    />
  </>
);

export default HospitalRoutes;

