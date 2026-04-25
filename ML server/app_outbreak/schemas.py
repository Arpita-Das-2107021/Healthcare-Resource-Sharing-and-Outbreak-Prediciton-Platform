from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class OutbreakRequest(BaseModel):
    data_path: str = Field(..., examples=["minio://ml-outbreak-input/sales.csv"])
    callback_url: str = Field(..., examples=["http://server-a/api/outbreak_callback"])

    @field_validator("data_path")
    @classmethod
    def validate_data_path(cls, value: str) -> str:
        if not value.startswith("minio://"):
            raise ValueError("data_path must start with minio://")
        return value


class OutbreakAcceptedResponse(BaseModel):
    status: str = "accepted"
    message: str = "Outbreak job started"


class OutbreakResultItem(BaseModel):
    facility_id: str
    upazila: str
    model_prob: float = Field(..., ge=0.0, le=1.0)
    social_score: float = Field(..., ge=0.0, le=1.0)
    final_confidence: float = Field(..., ge=0.0, le=1.0)
    status: str
    likely_disease: str
    social_confirmation: str


class CallbackPayload(BaseModel):
    results: list[OutbreakResultItem]
    file_path: str
    generated_at: datetime


class V1OutbreakInput(BaseModel):
    snapshot_id: UUID
    sales_file_path: str = Field(..., examples=["minio://ml-input/snapshots/123/sales.csv"])
    facilities_file_path: str = Field(..., examples=["minio://ml-input/snapshots/123/facilities.csv"])

    @field_validator("sales_file_path", "facilities_file_path")
    @classmethod
    def validate_minio_paths(cls, value: str) -> str:
        if not value.startswith("minio://"):
            raise ValueError("input file paths must start with minio://")
        return value


class V1CallbackConfig(BaseModel):
    url: str = Field(..., examples=["https://server-a/api/v1/ml/callbacks/server-b/"])
    timeout_seconds: int = Field(default=10, gt=0)


class V1OutbreakJobRequest(BaseModel):
    job_id: UUID
    prediction_horizon_days: int = Field(..., gt=0)
    input: V1OutbreakInput
    model_version: str = Field(default="outbreak-stgnn-v2")
    max_neighbors: int = Field(default=20, gt=0)
    callback: V1CallbackConfig


class V1AcceptedResponse(BaseModel):
    accepted: bool = True
    job_id: UUID
    job_type: str
    external_job_id: str
    status: str = "accepted"


class V1OutbreakResultItem(BaseModel):
    facility_id: str
    upazila: str
    model_prob: float = Field(..., ge=0.0, le=1.0)
    social_score: float = Field(..., ge=0.0, le=1.0)
    final_confidence: float = Field(..., ge=0.0, le=1.0)
    status: str
    likely_disease: str
    social_confirmation: str
    outbreak_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    outbreak_flag: bool = False


class V1NeighborItem(BaseModel):
    facility_id: str
    upazila: str = ""
    distance_km: float = Field(..., ge=0.0)


class V1ErrorInfo(BaseModel):
    code: str
    message: str


class V1OutbreakCallbackPayload(BaseModel):
    job_id: UUID
    job_type: str = "outbreak"
    external_job_id: str
    model_version: str
    prediction_horizon_days: int = Field(..., gt=0)
    status: str
    completed_at: datetime
    results: list[V1OutbreakResultItem]
    neighbors: dict[str, list[V1NeighborItem]]
    error: V1ErrorInfo | None = None


class V1ErrorResponse(BaseModel):
    accepted: bool = False
    error: dict[str, Any]
