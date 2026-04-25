"""Base DRF permission classes for HRSP."""
from rest_framework.permissions import BasePermission, IsAuthenticated

from common.permissions.runtime import (
    USER_CONTEXT_HEALTHCARE,
    USER_CONTEXT_PLATFORM,
    has_any_permission,
    has_required_context,
    is_platform_operator,
    user_hospital_id,
)

ROLE_SUPER_ADMIN = "SUPER_ADMIN"
ROLE_PLATFORM_ADMIN = "PLATFORM_ADMIN"
ROLE_HEALTHCARE_ADMIN = "HEALTHCARE_ADMIN"
ROLE_PHARMACIST = "PHARMACIST"
ROLE_INVENTORY_MANAGER = "INVENTORY_MANAGER"
ROLE_DOCTOR = "DOCTOR"
ROLE_LOGISTICS_STAFF = "LOGISTICS_STAFF"
ROLE_HOSPITAL_STAFF = "STAFF"
ROLE_ML_ENGINEER = "ML_ENGINEER"
ROLE_ML_ADMIN = "ML_ADMIN"

# Namespaced permission codes for dual-scope RBAC
PERMISSION_PLATFORM_AUDIT_VIEW = "platform:audit.view"
PERMISSION_PLATFORM_HOSPITAL_MANAGE = "platform:hospital.manage"
PERMISSION_PLATFORM_HOSPITAL_REVIEW = "platform:hospital.review"
PERMISSION_PLATFORM_HOSPITAL_UPDATE_REVIEW = "platform:hospital.update.review"
PERMISSION_PLATFORM_HOSPITAL_OFFBOARDING_REVIEW = "platform:hospital.offboarding.review"
PERMISSION_PLATFORM_ROLE_MANAGE = "platform:role.manage"
PERMISSION_PLATFORM_ROLE_ASSIGN = "platform:role.assign"
PERMISSION_HOSPITAL_ROLE_MANAGE = "hospital:role.manage"
PERMISSION_HOSPITAL_ROLE_ASSIGN = "hospital:user_role.assign"
PERMISSION_HOSPITAL_HOSPITAL_UPDATE = "hospital:hospital.update"
PERMISSION_HOSPITAL_UPDATE_REQUEST_VIEW = "hospital:update_request.view"
PERMISSION_HOSPITAL_UPDATE_REQUEST_SUBMIT = "hospital:update_request.submit"
PERMISSION_HOSPITAL_OFFBOARDING_REQUEST = "hospital:offboarding.request"
PERMISSION_HOSPITAL_STAFF_MANAGE = "hospital:staff.manage"
PERMISSION_HOSPITAL_STAFF_SUPERVISE = "hospital:staff.supervise"
PERMISSION_HOSPITAL_CATALOG_VIEW = "hospital:catalog.view"
PERMISSION_HOSPITAL_CATALOG_MANAGE = "hospital:catalog.manage"
PERMISSION_HOSPITAL_INVENTORY_VIEW = "hospital:inventory.view"
PERMISSION_HOSPITAL_INVENTORY_EDIT = "hospital:inventory.edit"
PERMISSION_HOSPITAL_INVENTORY_MANAGE = "hospital:inventory.manage"
PERMISSION_HOSPITAL_INVENTORY_SUPERVISE = "hospital:inventory.supervise"
PERMISSION_HOSPITAL_INVENTORY_IMPORT = "hospital:inventory.import"
PERMISSION_HOSPITAL_RESOURCE_SHARE_VIEW = "hospital:resource_share.view"
PERMISSION_HOSPITAL_RESOURCE_SHARE_MANAGE = "hospital:resource_share.manage"
PERMISSION_HOSPITAL_RESOURCE_SHARE_SUPERVISE = "hospital:resource_share.supervise"
PERMISSION_HOSPITAL_ANALYTICS_VIEW = "hospital:analytics.view"
PERMISSION_HOSPITAL_REQUEST_VIEW = "hospital:request.view"
PERMISSION_HOSPITAL_REQUEST_CREATE = "hospital:request.create"
PERMISSION_HOSPITAL_REQUEST_APPROVE = "hospital:request.approve"
PERMISSION_HOSPITAL_REQUEST_DISPATCH = "hospital:request.dispatch"
PERMISSION_HOSPITAL_REQUEST_RETURN_VERIFY = "hospital:request.return.verify"
PERMISSION_HOSPITAL_REQUEST_RESERVE = "hospital:request.reserve"
PERMISSION_HOSPITAL_REQUEST_TRANSFER_CONFIRM = "hospital:request.transfer.confirm"
PERMISSION_HOSPITAL_REQUEST_DELIVERY_CONFIRM = "hospital:request.delivery.confirm"
PERMISSION_HOSPITAL_REQUEST_EXPIRE = "hospital:request.expire"
PERMISSION_HOSPITAL_PAYMENT_VIEW = "hospital:payment.view"
PERMISSION_HOSPITAL_PAYMENT_INITIATE = "hospital:payment.initiate"
PERMISSION_HOSPITAL_PAYMENT_CONFIRM = "hospital:payment.confirm"
PERMISSION_HOSPITAL_PAYMENT_REFUND_INITIATE = "hospital:payment.refund.initiate"
PERMISSION_HOSPITAL_PAYMENT_REFUND_CONFIRM = "hospital:payment.refund.confirm"
PERMISSION_HOSPITAL_PAYMENT_REPORT_VIEW = "hospital:payment.report.view"
PERMISSION_HOSPITAL_PAYMENT_RECONCILE_MANAGE = "hospital:payment.reconcile.manage"
PERMISSION_HOSPITAL_TRANSPORT_VIEW = "hospital:transport.view"
PERMISSION_HOSPITAL_TRANSPORT_CREATE = "hospital:transport.create"
PERMISSION_HOSPITAL_TRANSPORT_UPDATE = "hospital:transport.update"
PERMISSION_HOSPITAL_TRANSPORT_ASSIGN = "hospital:transport.assign"
PERMISSION_HOSPITAL_TRANSPORT_TRACK = "hospital:transport.track"
PERMISSION_HOSPITAL_COMMUNICATION_MANAGE = "hospital:communication.manage"
PERMISSION_HOSPITAL_BROADCAST_MANAGE = "hospital:broadcast.manage"
PERMISSION_COMMUNICATION_CHAT_VIEW = "communication:chat.view"
PERMISSION_COMMUNICATION_CONVERSATION_VIEW = "communication:conversation.view"
PERMISSION_COMMUNICATION_TEMPLATE_MANAGE = "communication:template.manage"
PERMISSION_COMMUNICATION_BROADCAST_VIEW = "communication:broadcast.view"
PERMISSION_COMMUNICATION_BROADCAST_SEND = "communication:broadcast.send"
PERMISSION_COMMUNICATION_BROADCAST_MANAGE = "communication:broadcast.manage"
PERMISSION_COMMUNICATION_BROADCAST_RESPONSE_VIEW = "communication:broadcast.response.view"
PERMISSION_COMMUNICATION_NOTIFICATION_VIEW = "communication:notification.view"
PERMISSION_EFFECTIVE_PERMISSION_VIEW_V2 = "auth:permission.effective.view"
PERMISSION_REPORTS_ANALYTICS_VIEW = "reports:analytics.view"
PERMISSION_REPORTS_PAYMENT_VIEW = "reports:payment.view"
PERMISSION_PLATFORM_ANALYTICS_VIEW = "platform:analytics.view"
PERMISSION_ML_DATASET_REVIEW = "ml:dataset.review"
PERMISSION_ML_TRAINING_MANAGE = "ml:training.manage"
PERMISSION_ML_MODEL_VERSION_MANAGE = "ml:model_version.manage"
PERMISSION_ML_MODEL_VERSION_ACTIVATE = "ml:model_version.activate"


def _user_has_role(user, *roles) -> bool:
    if not user or not user.is_authenticated:
        return False
    return bool(hasattr(user, "has_role") and user.has_role(*roles))


def _user_has_permission(user, *permission_codes) -> bool:
    return has_any_permission(user, permission_codes)


def _user_has_role_or_permission(user, *, roles=(), permissions=()) -> bool:
    return has_any_permission(
        user,
        permissions,
        allow_role_fallback=True,
        legacy_roles=roles,
    )


def _user_hospital_id(user):
    return user_hospital_id(user)


class DomainContextPermission(BasePermission):
    """Require a specific user domain context before evaluating finer permissions."""

    required_context = None
    require_healthcare_context_id = False
    message = "Your account context is not allowed to access this endpoint."

    def has_permission(self, request, view):
        return has_required_context(
            request.user,
            self.required_context,
            require_healthcare_context_id=self.require_healthcare_context_id,
            request_path=getattr(request, "path", ""),
        )


class RequireHealthcareContext(DomainContextPermission):
    required_context = USER_CONTEXT_HEALTHCARE
    require_healthcare_context_id = True
    message = "Healthcare context is required for this endpoint."


class RequirePlatformContext(DomainContextPermission):
    required_context = USER_CONTEXT_PLATFORM
    message = "Platform context is required for this endpoint."


class EnforceDomainContextMixin:
    """Inject a context permission into DRF views/actions in a reusable way."""

    context_permission_class = None

    def get_permissions(self):
        permissions = super().get_permissions()
        permission_class = getattr(self, "context_permission_class", None)
        if permission_class is None:
            return permissions

        if any(isinstance(permission, permission_class) for permission in permissions):
            return permissions

        insertion_index = 0
        for idx, permission in enumerate(permissions):
            if isinstance(permission, IsAuthenticated):
                insertion_index = idx + 1
                break

        permissions.insert(insertion_index, permission_class())
        return permissions


class PermissionCodePermission(BasePermission):
    """Permission-first authorization with temporary role fallback."""

    required_permissions = ()
    legacy_roles = ()
    allow_legacy_role_fallback = True
    include_platform_admin_roles = True
    hospital_scoped = False
    required_context = None
    require_healthcare_context_id = False

    def get_hospital_id(self, request, view):
        if not self.hospital_scoped:
            return None
        return _user_hospital_id(request.user)

    def _resolved_legacy_roles(self):
        roles = tuple(self.legacy_roles or ())
        if not self.include_platform_admin_roles:
            return roles
        if ROLE_SUPER_ADMIN not in roles:
            roles = (*roles, ROLE_SUPER_ADMIN)
        if ROLE_PLATFORM_ADMIN not in roles:
            roles = (*roles, ROLE_PLATFORM_ADMIN)
        return roles

    def has_permission(self, request, view):
        if self.required_context and not has_required_context(
            request.user,
            self.required_context,
            require_healthcare_context_id=self.require_healthcare_context_id,
            request_path=getattr(request, "path", ""),
        ):
            return False

        permission_codes = tuple(self.required_permissions or ())
        if not permission_codes:
            return False
        return has_any_permission(
            request.user,
            permission_codes,
            hospital_id=self.get_hospital_id(request, view),
            allow_role_fallback=self.allow_legacy_role_fallback,
            legacy_roles=self._resolved_legacy_roles(),
        )


class IsSuperAdmin(PermissionCodePermission):
    """Legacy compatibility wrapper for platform administrators."""

    required_permissions = (
        PERMISSION_PLATFORM_HOSPITAL_MANAGE,
        PERMISSION_PLATFORM_HOSPITAL_REVIEW,
        PERMISSION_PLATFORM_ROLE_MANAGE,
        PERMISSION_PLATFORM_AUDIT_VIEW,
    )
    legacy_roles = (ROLE_SUPER_ADMIN, ROLE_PLATFORM_ADMIN)
    include_platform_admin_roles = False
    required_context = USER_CONTEXT_PLATFORM


class IsHospitalAdmin(PermissionCodePermission):
    """Legacy compatibility wrapper for hospital administrators."""

    required_permissions = (
        PERMISSION_HOSPITAL_HOSPITAL_UPDATE,
        PERMISSION_HOSPITAL_STAFF_MANAGE,
        PERMISSION_HOSPITAL_STAFF_SUPERVISE,
        PERMISSION_HOSPITAL_ROLE_MANAGE,
        PERMISSION_HOSPITAL_REQUEST_APPROVE,
        PERMISSION_HOSPITAL_INVENTORY_SUPERVISE,
    )
    legacy_roles = (ROLE_HEALTHCARE_ADMIN,)
    required_context = USER_CONTEXT_HEALTHCARE
    require_healthcare_context_id = True


class CanSubmitHospitalUpdateRequest(PermissionCodePermission):
    required_permissions = (PERMISSION_HOSPITAL_UPDATE_REQUEST_SUBMIT,)
    legacy_roles = (ROLE_HEALTHCARE_ADMIN,)
    allow_legacy_role_fallback = False
    hospital_scoped = True
    required_context = USER_CONTEXT_HEALTHCARE
    require_healthcare_context_id = True


class CanViewHospitalUpdateRequests(PermissionCodePermission):
    required_permissions = (
        PERMISSION_HOSPITAL_UPDATE_REQUEST_VIEW,
        PERMISSION_HOSPITAL_UPDATE_REQUEST_SUBMIT,
    )
    legacy_roles = (ROLE_HEALTHCARE_ADMIN,)
    allow_legacy_role_fallback = False
    hospital_scoped = True
    required_context = USER_CONTEXT_HEALTHCARE
    require_healthcare_context_id = True


class CanRequestHospitalOffboarding(PermissionCodePermission):
    required_permissions = (PERMISSION_HOSPITAL_OFFBOARDING_REQUEST,)
    legacy_roles = (ROLE_HEALTHCARE_ADMIN,)
    allow_legacy_role_fallback = False
    hospital_scoped = True
    required_context = USER_CONTEXT_HEALTHCARE
    require_healthcare_context_id = True


class CanReviewHospitalUpdateRequests(PermissionCodePermission):
    required_permissions = (
        PERMISSION_PLATFORM_HOSPITAL_UPDATE_REVIEW,
        PERMISSION_PLATFORM_HOSPITAL_REVIEW,
    )
    legacy_roles = (ROLE_SUPER_ADMIN, ROLE_PLATFORM_ADMIN)
    allow_legacy_role_fallback = False
    include_platform_admin_roles = False
    required_context = USER_CONTEXT_PLATFORM


class CanReviewHospitalOffboardingRequests(PermissionCodePermission):
    required_permissions = (
        PERMISSION_PLATFORM_HOSPITAL_OFFBOARDING_REVIEW,
        PERMISSION_PLATFORM_HOSPITAL_REVIEW,
    )
    legacy_roles = (ROLE_SUPER_ADMIN, ROLE_PLATFORM_ADMIN)
    allow_legacy_role_fallback = False
    include_platform_admin_roles = False
    required_context = USER_CONTEXT_PLATFORM


class IsPharmacist(PermissionCodePermission):
    required_permissions = (
        PERMISSION_HOSPITAL_CATALOG_MANAGE,
        PERMISSION_HOSPITAL_INVENTORY_EDIT,
        PERMISSION_HOSPITAL_INVENTORY_MANAGE,
    )
    legacy_roles = (ROLE_PHARMACIST, ROLE_INVENTORY_MANAGER, ROLE_HEALTHCARE_ADMIN)
    required_context = USER_CONTEXT_HEALTHCARE
    require_healthcare_context_id = True


class IsLogisticsStaff(PermissionCodePermission):
    required_permissions = (
        PERMISSION_HOSPITAL_REQUEST_DISPATCH,
        PERMISSION_HOSPITAL_REQUEST_RETURN_VERIFY,
        PERMISSION_HOSPITAL_TRANSPORT_ASSIGN,
        PERMISSION_HOSPITAL_TRANSPORT_UPDATE,
        PERMISSION_HOSPITAL_TRANSPORT_TRACK,
    )
    legacy_roles = (ROLE_LOGISTICS_STAFF, ROLE_HEALTHCARE_ADMIN)
    required_context = USER_CONTEXT_HEALTHCARE
    require_healthcare_context_id = True


class IsSameHospital(BasePermission):
    """
    Object-level permission: the requesting user must belong to the same hospital
    as the object being accessed. Views must call get_object() for this to run.
    """

    def has_object_permission(self, request, view, obj):
        user_hospital = _user_hospital_id(request.user)
        if not user_hospital:
            return False
        if is_platform_operator(request.user, allow_role_fallback=True):
            return True
        # Determine the hospital_id on the target object
        obj_hospital = getattr(obj, "hospital_id", None)
        if obj_hospital is None:
            obj_hospital = getattr(obj, "hospital", None)
            if obj_hospital is not None:
                obj_hospital = getattr(obj_hospital, "id", obj_hospital)
        return str(user_hospital) == str(obj_hospital)


class IsVerifiedHospital(BasePermission):
    """Allow access only to healthcare-context users whose hospital is verified."""

    def has_permission(self, request, view):
        if not has_required_context(
            request.user,
            USER_CONTEXT_HEALTHCARE,
            require_healthcare_context_id=True,
            request_path=getattr(request, "path", ""),
        ):
            return False
        try:
            return request.user.staff.hospital.verified_status == "verified"
        except AttributeError:
            return False


class RoleOrPermissionPermission(BasePermission):
    """Allow access when user has any required role or permission code."""

    required_roles = ()
    required_permissions = ()
    include_super_admin = True
    required_context = None
    require_healthcare_context_id = False

    def has_permission(self, request, view):
        if self.required_context and not has_required_context(
            request.user,
            self.required_context,
            require_healthcare_context_id=self.require_healthcare_context_id,
            request_path=getattr(request, "path", ""),
        ):
            return False

        roles = tuple(self.required_roles or ())
        if self.include_super_admin and ROLE_SUPER_ADMIN not in roles:
            roles = (*roles, ROLE_SUPER_ADMIN)

        permissions = tuple(self.required_permissions or ())
        if not permissions:
            return _user_has_role(request.user, *roles)

        return has_any_permission(
            request.user,
            permissions,
            allow_role_fallback=True,
            legacy_roles=roles,
        )


class CanManageUserRoles(RoleOrPermissionPermission):
    required_roles = (ROLE_HEALTHCARE_ADMIN,)
    required_permissions = (PERMISSION_HOSPITAL_ROLE_ASSIGN, PERMISSION_HOSPITAL_ROLE_MANAGE)


class CanManageRolePermissions(RoleOrPermissionPermission):
    required_roles = (ROLE_HEALTHCARE_ADMIN,)
    required_permissions = (
        PERMISSION_HOSPITAL_ROLE_MANAGE,
        PERMISSION_PLATFORM_ROLE_MANAGE,
    )


class CanViewUserEffectivePermissions(RoleOrPermissionPermission):
    required_roles = (ROLE_HEALTHCARE_ADMIN,)
    required_permissions = (PERMISSION_EFFECTIVE_PERMISSION_VIEW_V2,)


class CanManagePlatformRoles(RoleOrPermissionPermission):
    required_roles = (ROLE_SUPER_ADMIN, ROLE_PLATFORM_ADMIN)
    required_permissions = (PERMISSION_PLATFORM_ROLE_MANAGE, PERMISSION_PLATFORM_ROLE_ASSIGN)
    required_context = USER_CONTEXT_PLATFORM


class CanAssignPlatformRoles(RoleOrPermissionPermission):
    required_roles = (ROLE_SUPER_ADMIN, ROLE_PLATFORM_ADMIN)
    required_permissions = (PERMISSION_PLATFORM_ROLE_ASSIGN, PERMISSION_PLATFORM_ROLE_MANAGE)
    required_context = USER_CONTEXT_PLATFORM


class CanManageHospitalRoles(RoleOrPermissionPermission):
    required_roles = (ROLE_HEALTHCARE_ADMIN, ROLE_PLATFORM_ADMIN)
    required_permissions = (PERMISSION_HOSPITAL_ROLE_MANAGE,)


class CanAssignHospitalRoles(RoleOrPermissionPermission):
    required_roles = (ROLE_HEALTHCARE_ADMIN, ROLE_PLATFORM_ADMIN)
    required_permissions = (PERMISSION_HOSPITAL_ROLE_ASSIGN, PERMISSION_HOSPITAL_ROLE_MANAGE)


class CanManageHospitalCommunication(RoleOrPermissionPermission):
    """Permission-first communication guard with temporary legacy role fallback."""

    required_roles = (
        ROLE_HEALTHCARE_ADMIN,
        ROLE_HOSPITAL_STAFF,
        ROLE_PHARMACIST,
        ROLE_LOGISTICS_STAFF,
    )
    required_permissions = (
        PERMISSION_HOSPITAL_COMMUNICATION_MANAGE,
        PERMISSION_COMMUNICATION_CHAT_VIEW,
        PERMISSION_COMMUNICATION_CONVERSATION_VIEW,
    )
    required_context = USER_CONTEXT_HEALTHCARE
    require_healthcare_context_id = True


class CanManageHospitalBroadcasts(RoleOrPermissionPermission):
    required_roles = (ROLE_HEALTHCARE_ADMIN, ROLE_PLATFORM_ADMIN)
    required_permissions = (
        PERMISSION_HOSPITAL_BROADCAST_MANAGE,
        PERMISSION_COMMUNICATION_BROADCAST_SEND,
        PERMISSION_COMMUNICATION_BROADCAST_MANAGE,
    )


class CanManageMLTrainingLifecycle(RoleOrPermissionPermission):
    required_roles = (
        ROLE_ML_ENGINEER,
        ROLE_ML_ADMIN,
    )
    required_permissions = (
        PERMISSION_ML_DATASET_REVIEW,
        PERMISSION_ML_TRAINING_MANAGE,
        PERMISSION_ML_MODEL_VERSION_MANAGE,
        "ml:job.manage",
        "ml:schedule.manage",
    )


class CanManageMLModelVersions(RoleOrPermissionPermission):
    required_roles = (
        ROLE_ML_ADMIN,
    )
    required_permissions = (
        PERMISSION_ML_MODEL_VERSION_MANAGE,
        PERMISSION_ML_MODEL_VERSION_ACTIVATE,
    )


class CanManageMLOperations(RoleOrPermissionPermission):
    required_roles = (
        ROLE_ML_ENGINEER,
        ROLE_ML_ADMIN,
    )
    required_permissions = (
        "ml:job.manage",
        "ml:schedule.manage",
        "ml:facility.settings.manage",
    )


class CanViewMLJobs(RoleOrPermissionPermission):
    required_roles = (
        ROLE_ML_ENGINEER,
        ROLE_ML_ADMIN,
    )
    required_permissions = (
        "ml:job.view",
        "ml:job.manage",
    )


class CanViewMLForecast(RoleOrPermissionPermission):
    required_roles = (
        ROLE_ML_ENGINEER,
        ROLE_ML_ADMIN,
    )
    required_permissions = (
        "ml:forecast.view",
        "ml:job.view",
        "ml:job.manage",
    )


class CanViewMLOutbreak(RoleOrPermissionPermission):
    required_roles = (
        ROLE_ML_ENGINEER,
        ROLE_ML_ADMIN,
    )
    required_permissions = (
        "ml:outbreak.view",
        "ml:job.view",
        "ml:job.manage",
    )


class CanViewMLSuggestions(RoleOrPermissionPermission):
    required_roles = (
        ROLE_ML_ENGINEER,
        ROLE_ML_ADMIN,
    )
    required_permissions = (
        "ml:suggestion.view",
        "ml:job.view",
        "ml:job.manage",
    )


# ---------------------------------------------------------------------------
# Client-facing ML permissions — open to all authenticated hospital staff.
# These govern the "frontend-safe" facility insight endpoints that return
# already-computed results with no raw ML internals exposed.
# ---------------------------------------------------------------------------

class CanViewFacilityForecast(RoleOrPermissionPermission):
    """Hospital staff reading their own facility's latest ML demand forecast."""
    required_roles = (
        ROLE_HEALTHCARE_ADMIN,
        ROLE_PHARMACIST,
        ROLE_INVENTORY_MANAGER,
        ROLE_DOCTOR,
        ROLE_LOGISTICS_STAFF,
        ROLE_HOSPITAL_STAFF,
        ROLE_ML_ENGINEER,
        ROLE_ML_ADMIN,
    )
    required_permissions = (
        PERMISSION_HOSPITAL_ANALYTICS_VIEW,
        PERMISSION_HOSPITAL_INVENTORY_VIEW,
        "ml:forecast.view",
    )


class CanViewFacilityOutbreak(RoleOrPermissionPermission):
    """Hospital staff reading their own facility's latest ML outbreak alert."""
    required_roles = (
        ROLE_HEALTHCARE_ADMIN,
        ROLE_PHARMACIST,
        ROLE_INVENTORY_MANAGER,
        ROLE_DOCTOR,
        ROLE_LOGISTICS_STAFF,
        ROLE_HOSPITAL_STAFF,
        ROLE_ML_ENGINEER,
        ROLE_ML_ADMIN,
    )
    required_permissions = (
        PERMISSION_HOSPITAL_ANALYTICS_VIEW,
        PERMISSION_HOSPITAL_INVENTORY_VIEW,
        "ml:outbreak.view",
    )


class CanViewFacilitySuggestions(RoleOrPermissionPermission):
    """Hospital staff reading ML-derived resource request suggestions for their facility."""
    required_roles = (
        ROLE_HEALTHCARE_ADMIN,
        ROLE_PHARMACIST,
        ROLE_INVENTORY_MANAGER,
        ROLE_LOGISTICS_STAFF,
        ROLE_HOSPITAL_STAFF,
        ROLE_ML_ENGINEER,
        ROLE_ML_ADMIN,
    )
    required_permissions = (
        PERMISSION_HOSPITAL_ANALYTICS_VIEW,
        PERMISSION_HOSPITAL_REQUEST_VIEW,
        PERMISSION_HOSPITAL_INVENTORY_VIEW,
        "ml:suggestion.view",
    )


class CanTriggerFacilityRefresh(RoleOrPermissionPermission):
    """Permission to queue an on-demand ML inference refresh for a facility.

    Intentionally permission-based with no role fallback, in line with the
    project's scope-first RBAC policy.  Users must hold at least one of the
    listed permission codes (e.g. granted via their hospital role assignment).
    SUPER_ADMIN is still included via include_super_admin=True.
    """
    required_roles = ()
    required_permissions = (
        PERMISSION_HOSPITAL_ANALYTICS_VIEW,
        PERMISSION_HOSPITAL_INVENTORY_MANAGE,
        "ml:job.manage",
    )


class CanViewHospitalAnalytics(RoleOrPermissionPermission):
    required_roles = (
        ROLE_HEALTHCARE_ADMIN,
        ROLE_PLATFORM_ADMIN,
    )
    required_permissions = (
        PERMISSION_HOSPITAL_ANALYTICS_VIEW,
        PERMISSION_REPORTS_ANALYTICS_VIEW,
        PERMISSION_PLATFORM_ANALYTICS_VIEW,
    )


class CanViewAuditLogs(RoleOrPermissionPermission):
    required_roles = (
        ROLE_SUPER_ADMIN,
        ROLE_PLATFORM_ADMIN,
    )
    required_permissions = (PERMISSION_PLATFORM_AUDIT_VIEW,)
    required_context = USER_CONTEXT_PLATFORM
