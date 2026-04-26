"""URL routes for ML orchestration APIs.

Route groups:

  INTERNAL ML PIPELINE  — ML engineers / background workers only
      model1/predict/, model2/predict/, jobs/*, schedules/*, facilities/*/settings/
      jobs/*/results/*, training/*, model-versions/*, models/active/, callbacks/*

  CLIENT-FACING FACILITY INSIGHTS  — authenticated hospital staff
      facilities/me/latest-forecast/          (resolves facility from user context)
      facilities/<id>/latest-forecast/
      facilities/me/latest-outbreak/
      facilities/<id>/latest-outbreak/
      facilities/me/request-suggestions/
      facilities/<id>/request-suggestions/
      facilities/me/refresh/
      facilities/<id>/refresh/

See views.py for full per-view documentation.
"""
from django.urls import path

from .views import (
    # Internal pipeline
    ActiveModelConfigView,
    FacilitySettingsPatchView,
    MLForecastResultView,
    MLJobCancelView,
    MLJobCollectionView,
    MLJobDetailView,
    MLJobEventsView,
    MLJobRetryView,
    MLModelVersionActivateView,
    MLModelVersionApproveView,
    MLModelVersionCollectionView,
    MLModelVersionDeactivateView,
    MLModelVersionDetailView,
    MLModelVersionReviewView,
    MLModelVersionRollbackView,
    MLOutbreakResultView,
    MLScheduleActivateView,
    MLScheduleCollectionView,
    MLScheduleDeactivateView,
    MLScheduleDetailView,
    MLTrainingDatasetApproveView,
    MLTrainingDatasetCollectionView,
    MLTrainingDatasetDetailView,
    MLTrainingDatasetGenerateView,
    MLTrainingDatasetRejectView,
    MLTrainingJobCollectionView,
    MLTrainingJobDetailView,
    Model1PredictView,
    Model2PredictView,
    ServerBCallbackView,
    ServerBTrainingCallbackView,
    # Client-facing facility insights
    FacilityLatestForecastView,
    FacilityLatestOutbreakView,
    FacilityRefreshView,
    FacilityRequestSuggestionsView,
)

# ---------------------------------------------------------------------------
# INTERNAL ML PIPELINE  — INTERNAL ONLY – NOT FOR FRONTEND USE – SUBJECT TO CHANGE
# Requires ML_ENGINEER or ML_ADMIN role / ml:job.manage permission.
# ---------------------------------------------------------------------------
_internal = [
    # Direct JSON inference (raw feature rows required)
    path("model1/predict/", Model1PredictView.as_view(), name="ml-model1-predict"),      # INTERNAL ONLY
    path("model2/predict/", Model2PredictView.as_view(), name="ml-model2-predict"),      # INTERNAL ONLY

    # Job lifecycle
    path("jobs/", MLJobCollectionView.as_view(), name="ml-jobs"),                                                    # INTERNAL ONLY
    path("jobs/<uuid:job_id>/", MLJobDetailView.as_view(), name="ml-job-detail"),                                    # INTERNAL ONLY
    path("jobs/<uuid:job_id>/retry/", MLJobRetryView.as_view(), name="ml-job-retry"),                                # INTERNAL ONLY
    path("jobs/<uuid:job_id>/cancel/", MLJobCancelView.as_view(), name="ml-job-cancel"),                             # INTERNAL ONLY
    path("jobs/<uuid:job_id>/events/", MLJobEventsView.as_view(), name="ml-job-events"),                             # INTERNAL ONLY
    path("jobs/<uuid:job_id>/results/forecast/", MLForecastResultView.as_view(), name="ml-forecast-results"),        # INTERNAL ONLY
    path("jobs/<uuid:job_id>/results/outbreak/", MLOutbreakResultView.as_view(), name="ml-outbreak-results"),        # INTERNAL ONLY

    # Schedules
    path("schedules/", MLScheduleCollectionView.as_view(), name="ml-schedules"),                                                      # INTERNAL ONLY
    path("schedules/<uuid:schedule_id>/", MLScheduleDetailView.as_view(), name="ml-schedule-detail"),                                 # INTERNAL ONLY
    path("schedules/<uuid:schedule_id>/activate/", MLScheduleActivateView.as_view(), name="ml-schedule-activate"),                    # INTERNAL ONLY
    path("schedules/<uuid:schedule_id>/deactivate/", MLScheduleDeactivateView.as_view(), name="ml-schedule-deactivate"),              # INTERNAL ONLY

    # Facility ML settings (ML admin only)
    path("facilities/<uuid:facility_id>/settings/", FacilitySettingsPatchView.as_view(), name="ml-facility-settings-patch"),          # INTERNAL ONLY

    # Training lifecycle
    path("training/datasets/generate/", MLTrainingDatasetGenerateView.as_view(), name="ml-training-dataset-generate"),               # INTERNAL ONLY
    path("training/datasets/", MLTrainingDatasetCollectionView.as_view(), name="ml-training-dataset-list"),                           # INTERNAL ONLY
    path("training/datasets/<uuid:dataset_id>/", MLTrainingDatasetDetailView.as_view(), name="ml-training-dataset-detail"),           # INTERNAL ONLY
    path("training/datasets/<uuid:dataset_id>/approve/", MLTrainingDatasetApproveView.as_view(), name="ml-training-dataset-approve"), # INTERNAL ONLY
    path("training/datasets/<uuid:dataset_id>/reject/", MLTrainingDatasetRejectView.as_view(), name="ml-training-dataset-reject"),    # INTERNAL ONLY
    path("training/jobs/", MLTrainingJobCollectionView.as_view(), name="ml-training-job-list-create"),                                # INTERNAL ONLY
    path("training/jobs/<uuid:training_job_id>/", MLTrainingJobDetailView.as_view(), name="ml-training-job-detail"),                  # INTERNAL ONLY

    # Model version management
    path("model-versions/", MLModelVersionCollectionView.as_view(), name="ml-model-version-list"),                                              # INTERNAL ONLY
    path("model-versions/<uuid:version_id>/", MLModelVersionDetailView.as_view(), name="ml-model-version-detail"),                              # INTERNAL ONLY
    path("model-versions/<uuid:version_id>/review/", MLModelVersionReviewView.as_view(), name="ml-model-version-review"),                       # INTERNAL ONLY
    path("model-versions/<uuid:version_id>/approve/", MLModelVersionApproveView.as_view(), name="ml-model-version-approve"),                    # INTERNAL ONLY
    path("model-versions/<uuid:version_id>/activate/", MLModelVersionActivateView.as_view(), name="ml-model-version-activate"),                 # INTERNAL ONLY
    path("model-versions/<uuid:version_id>/deactivate/", MLModelVersionDeactivateView.as_view(), name="ml-model-version-deactivate"),           # INTERNAL ONLY
    path("model-versions/<uuid:version_id>/rollback/", MLModelVersionRollbackView.as_view(), name="ml-model-version-rollback"),                 # INTERNAL ONLY
    path("models/active/", ActiveModelConfigView.as_view(), name="ml-active-model-configs"),                                                    # INTERNAL ONLY

    # Server B callbacks (HMAC-verified, no user auth)
    path("callbacks/server-b/", ServerBCallbackView.as_view(), name="ml-server-b-callback"),                                          # INTERNAL ONLY
    path("callbacks/server-b", ServerBCallbackView.as_view(), name="ml-server-b-callback-no-slash"),                                  # INTERNAL ONLY
    path("training/callbacks/server-b/", ServerBTrainingCallbackView.as_view(), name="ml-training-server-b-callback"),                # INTERNAL ONLY
    path("training/callbacks/server-b", ServerBTrainingCallbackView.as_view(), name="ml-training-server-b-callback-no-slash"),        # INTERNAL ONLY
]

# ---------------------------------------------------------------------------
# CLIENT-FACING FACILITY INSIGHTS  — all authenticated hospital staff
# No raw ML features, no scheduled_time, no model_version, no job polling.
#
# "me" routes derive facility from request.user; must come before UUID routes
# so Django does not try to match the literal "me" against the UUID converter.
# ---------------------------------------------------------------------------
_client = [
    # latest-forecast
    path("facilities/me/latest-forecast/", FacilityLatestForecastView.as_view(), name="ml-facility-me-latest-forecast"),
    path("facilities/<uuid:facility_id>/latest-forecast/", FacilityLatestForecastView.as_view(), name="ml-facility-latest-forecast"),

    # latest-outbreak
    path("facilities/me/latest-outbreak/", FacilityLatestOutbreakView.as_view(), name="ml-facility-me-latest-outbreak"),
    path("facilities/<uuid:facility_id>/latest-outbreak/", FacilityLatestOutbreakView.as_view(), name="ml-facility-latest-outbreak"),

    # request-suggestions
    path("facilities/me/request-suggestions/", FacilityRequestSuggestionsView.as_view(), name="ml-facility-me-request-suggestions"),
    path("facilities/<uuid:facility_id>/request-suggestions/", FacilityRequestSuggestionsView.as_view(), name="ml-facility-request-suggestions"),

    # on-demand refresh
    path("facilities/me/refresh/", FacilityRefreshView.as_view(), name="ml-facility-me-refresh"),
    path("facilities/<uuid:facility_id>/refresh/", FacilityRefreshView.as_view(), name="ml-facility-refresh"),
]

urlpatterns = _internal + _client
