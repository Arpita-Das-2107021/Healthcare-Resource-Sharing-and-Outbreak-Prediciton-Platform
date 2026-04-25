import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app_outbreak.schemas import (
    CallbackPayload,
    OutbreakRequest,
    V1ErrorInfo,
    V1OutbreakCallbackPayload,
    V1OutbreakJobRequest,
)
from app_outbreak.services.ml_service import OutbreakMLService
from shared.schemas import Model2InferenceRequest, Model2TrainingRequest
from shared.services.callback_service import CallbackService
from shared.services.job_status_service import JobStatusService
from shared.services.model_registry_service import ModelRegistryService
from shared.services.minio_service import MinIOService

logger = logging.getLogger(__name__)


class OutbreakJobOrchestrator:
    def __init__(
        self,
        ml_service: OutbreakMLService,
        minio_service: MinIOService,
        callback_service: CallbackService,
        job_status_service: JobStatusService | None = None,
        model_registry_service: ModelRegistryService | None = None,
    ) -> None:
        self.ml_service = ml_service
        self.minio_service = minio_service
        self.callback_service = callback_service
        self.job_status_service = job_status_service
        self.model_registry_service = model_registry_service

    @staticmethod
    def _snapshot_file_uri(snapshot_uri: str, file_name: str) -> str:
        bucket, object_name = MinIOService.parse_minio_uri(snapshot_uri)
        base = object_name.rstrip("/")
        return f"minio://{bucket}/{base}/{file_name}"

    @staticmethod
    def _to_row_payload(rows: list[Any]) -> list[dict]:
        payload: list[dict] = []
        for row in rows:
            if hasattr(row, "model_dump"):
                payload.append(row.model_dump())
            else:
                payload.append(dict(row))
        return payload

    def _download_model2_training_inputs(
        self,
        request: Model2TrainingRequest,
        runtime_data_dir: Path,
    ) -> None:
        runtime_data_dir.mkdir(parents=True, exist_ok=True)

        if request.input.dataset_snapshot_uri:
            sales_uri = self._snapshot_file_uri(request.input.dataset_snapshot_uri, "sales.csv")
            facilities_uri = self._snapshot_file_uri(request.input.dataset_snapshot_uri, "facilities.csv")
            medicines_uri = self._snapshot_file_uri(request.input.dataset_snapshot_uri, "medicines.csv")
            outbreaks_uri = self._snapshot_file_uri(
                request.input.dataset_snapshot_uri,
                "outbreaks_ground_truth.csv",
            )
        else:
            sales_uri = request.input.sales_file_path
            facilities_uri = request.input.facilities_file_path
            medicines_uri = request.input.medicines_file_path
            outbreaks_uri = request.input.outbreaks_file_path

        if not sales_uri or not facilities_uri:
            raise ValueError("Missing required training dataset URIs for model2")

        self.minio_service.download_file(sales_uri, runtime_data_dir / "sales.csv")
        self.minio_service.download_file(facilities_uri, runtime_data_dir / "facilities.csv")

        if medicines_uri:
            self.minio_service.download_file(medicines_uri, runtime_data_dir / "medicines.csv")
        else:
            baseline_medicines = self.ml_service.base_data_dir / "medicines.csv"
            if baseline_medicines.exists():
                (runtime_data_dir / "medicines.csv").write_bytes(baseline_medicines.read_bytes())

        if outbreaks_uri:
            try:
                self.minio_service.download_file(outbreaks_uri, runtime_data_dir / "outbreaks_ground_truth.csv")
            except Exception:
                baseline_outbreak = self.ml_service.base_data_dir / "outbreaks_ground_truth.csv"
                if baseline_outbreak.exists():
                    (runtime_data_dir / "outbreaks_ground_truth.csv").write_bytes(
                        baseline_outbreak.read_bytes()
                    )
        else:
            baseline_outbreak = self.ml_service.base_data_dir / "outbreaks_ground_truth.csv"
            if baseline_outbreak.exists():
                (runtime_data_dir / "outbreaks_ground_truth.csv").write_bytes(baseline_outbreak.read_bytes())

    @staticmethod
    def _apply_sales_date_range(runtime_data_dir: Path, start_date: str, end_date: str) -> int:
        import pandas as pd

        sales_path = runtime_data_dir / "sales.csv"
        sales_df = pd.read_csv(sales_path)
        sales_df["date"] = pd.to_datetime(sales_df["date"], errors="coerce")
        sales_df = sales_df.dropna(subset=["date"])

        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        filtered = sales_df[(sales_df["date"] >= start) & (sales_df["date"] <= end)].copy()

        if filtered.empty:
            raise ValueError("No sales rows found within requested date range")

        filtered["date"] = filtered["date"].dt.strftime("%Y-%m-%d")
        filtered.to_csv(sales_path, index=False)
        return int(len(filtered))

    def process_outbreak_job(self, request: OutbreakRequest) -> None:
        started_at = datetime.now(timezone.utc)
        logger.info("Outbreak job started for data_path=%s", request.data_path)

        with tempfile.TemporaryDirectory(prefix="hrsp_outbreak_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_sales_path = tmp_path / "source_sales.csv"
            runtime_data_dir = tmp_path / "runtime_data"

            # 1) Download sales data from MinIO.
            self.minio_service.download_file(request.data_path, source_sales_path)

            # 2) Prepare expected files and run outbreak inference.
            prepared_dir = self.ml_service.prepare_runtime_dataset(source_sales_path, runtime_data_dir)
            result_df, result_rows = self.ml_service.run_outbreak(prepared_dir)

            # 3) Upload CSV backup to MinIO.
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            object_name = f"outbreak_results_{ts}.csv"
            file_path = self.minio_service.upload_results_csv(result_df, object_name)

            payload = CallbackPayload(
                results=result_rows,
                file_path=file_path,
                generated_at=started_at,
            ).model_dump(mode="json")

            # 4) Send callback to Server A with retry policy.
            self.callback_service.send_results(request.callback_url, payload)

        logger.info("Outbreak job completed for data_path=%s", request.data_path)

    def process_outbreak_job_v1(self, request: V1OutbreakJobRequest, external_job_id: str) -> None:
        logger.info("Outbreak v1 job started for job_id=%s", request.job_id)

        try:
            with tempfile.TemporaryDirectory(prefix="hrsp_outbreak_v1_") as tmp_dir:
                tmp_path = Path(tmp_dir)
                source_sales_path = tmp_path / "source_sales.csv"
                source_facilities_path = tmp_path / "source_facilities.csv"
                runtime_data_dir = tmp_path / "runtime_data"

                self.minio_service.download_file(request.input.sales_file_path, source_sales_path)
                self.minio_service.download_file(request.input.facilities_file_path, source_facilities_path)

                prepared_dir = self.ml_service.prepare_runtime_dataset_from_inputs(
                    sales_csv_path=source_sales_path,
                    facilities_csv_path=source_facilities_path,
                    runtime_dir=runtime_data_dir,
                )

                _, result_rows, neighbors = self.ml_service.run_outbreak_v1(
                    prepared_dir,
                    max_neighbors=request.max_neighbors,
                )

                payload = V1OutbreakCallbackPayload(
                    job_id=request.job_id,
                    job_type="outbreak",
                    external_job_id=external_job_id,
                    model_version=request.model_version,
                    prediction_horizon_days=request.prediction_horizon_days,
                    status="completed",
                    completed_at=datetime.now(timezone.utc),
                    results=result_rows,
                    neighbors=neighbors,
                    error=None,
                ).model_dump(mode="json")

                self.callback_service.send_results(
                    request.callback.url,
                    payload,
                    timeout_seconds=request.callback.timeout_seconds,
                )

        except Exception as exc:  # pragma: no cover - covered via callback payload assertions
            logger.exception("Outbreak v1 job failed for job_id=%s", request.job_id)
            failure_payload = V1OutbreakCallbackPayload(
                job_id=request.job_id,
                job_type="outbreak",
                external_job_id=external_job_id,
                model_version=request.model_version,
                prediction_horizon_days=request.prediction_horizon_days,
                status="failed",
                completed_at=datetime.now(timezone.utc),
                results=[],
                neighbors={},
                error=V1ErrorInfo(
                    code="model_execution_failed",
                    message=f"Outbreak pipeline failure: {exc}",
                ),
            ).model_dump(mode="json")
            try:
                self.callback_service.send_results(
                    request.callback.url,
                    failure_payload,
                    timeout_seconds=request.callback.timeout_seconds,
                )
            except Exception:
                logger.exception("Outbreak v1 failure callback also failed for job_id=%s", request.job_id)

            raise

        logger.info("Outbreak v1 job completed for job_id=%s", request.job_id)

    def process_outbreak_inference_job(self, request: Model2InferenceRequest, external_job_id: str) -> None:
        if self.job_status_service is None or self.model_registry_service is None:
            raise RuntimeError("Outbreak inference workflow requires job status and model registry services")

        self.job_status_service.update_status(external_job_id=external_job_id, status="processing")
        logger.info("Outbreak inference job started for job_id=%s", request.job_id)

        try:
            resolved_version = self.model_registry_service.resolve_version("model2", request.model_version)
            _, result_rows, neighbors = self.ml_service.run_outbreak_json_inference(
                rows=self._to_row_payload(request.input.rows),
                artifact_uri=str(resolved_version.get("artifact_uri", "")),
                max_neighbors=request.max_neighbors,
                neighbors_input=request.input.neighbors,
            )

            payload = V1OutbreakCallbackPayload(
                job_id=request.job_id,
                job_type="outbreak",
                external_job_id=external_job_id,
                model_version=str(resolved_version.get("version")),
                prediction_horizon_days=request.prediction_horizon_days,
                status="completed",
                completed_at=datetime.now(timezone.utc),
                results=result_rows,
                neighbors=neighbors,
                error=None,
            ).model_dump(mode="json")

            self.callback_service.send_results(
                request.callback.url,
                payload,
                timeout_seconds=request.callback.timeout_seconds,
            )
            self.job_status_service.update_status(
                external_job_id=external_job_id,
                status="completed",
                metadata_updates={
                    "resolved_model_version": str(resolved_version.get("version")),
                    "result_count": len(result_rows),
                },
            )

        except Exception as exc:  # pragma: no cover - callback path validated through API tests
            logger.exception("Outbreak inference job failed for job_id=%s", request.job_id)
            resolved_version = request.model_version or "unknown"
            failure_payload = V1OutbreakCallbackPayload(
                job_id=request.job_id,
                job_type="outbreak",
                external_job_id=external_job_id,
                model_version=resolved_version,
                prediction_horizon_days=request.prediction_horizon_days,
                status="failed",
                completed_at=datetime.now(timezone.utc),
                results=[],
                neighbors={},
                error=V1ErrorInfo(
                    code="model_execution_failed",
                    message=f"Inference failed: {exc}",
                ),
            ).model_dump(mode="json")

            try:
                self.callback_service.send_results(
                    request.callback.url,
                    failure_payload,
                    timeout_seconds=request.callback.timeout_seconds,
                )
            except Exception:
                logger.exception("Outbreak inference failure callback also failed for job_id=%s", request.job_id)

            self.job_status_service.update_status(
                external_job_id=external_job_id,
                status="failed",
                error={"code": "model_execution_failed", "message": str(exc)},
            )
            raise

        logger.info("Outbreak inference job completed for job_id=%s", request.job_id)

    def process_outbreak_training_job(self, request: Model2TrainingRequest, external_job_id: str) -> None:
        if self.job_status_service is None or self.model_registry_service is None:
            raise RuntimeError("Outbreak training workflow requires job status and model registry services")

        self.job_status_service.update_status(external_job_id=external_job_id, status="processing")
        logger.info("Outbreak training job started for job_id=%s", request.job_id)

        try:
            with tempfile.TemporaryDirectory(prefix="hrsp_outbreak_train_v1_") as tmp_dir:
                runtime_data_dir = Path(tmp_dir) / "runtime_data"
                self._download_model2_training_inputs(request, runtime_data_dir)

                if request.date_range is not None:
                    row_count = self._apply_sales_date_range(
                        runtime_data_dir,
                        str(request.date_range.start_date),
                        str(request.date_range.end_date),
                    )
                else:
                    row_count = None

                training_artifacts = self.ml_service.train_outbreak_model_from_csv(
                    runtime_dir=runtime_data_dir,
                    requested_version_label=request.requested_version_label,
                    training_params=request.training_params,
                )

            model_version = str(training_artifacts["model_version"])
            self.model_registry_service.register_version(
                model_type="model2",
                version=model_version,
                artifact_uri=str(training_artifacts["artifact_dir"]),
                status="trained",
                approval_status="pending_approval",
                metadata={
                    "job_id": str(request.job_id),
                    "workflow": "training",
                    "requested_version_label": request.requested_version_label,
                    "date_range": (
                        {
                            "start_date": str(request.date_range.start_date),
                            "end_date": str(request.date_range.end_date),
                        }
                        if request.date_range
                        else None
                    ),
                    "filtered_sales_rows": row_count,
                },
            )

            if request.callback is not None:
                callback_payload = {
                    "job_id": str(request.job_id),
                    "job_type": "outbreak_training",
                    "external_job_id": external_job_id,
                    "model_version": model_version,
                    "status": "trained",
                    "approval_status": "pending_approval",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "artifact_uri": str(training_artifacts["artifact_dir"]),
                }
                self.callback_service.send_results(
                    request.callback.url,
                    callback_payload,
                    timeout_seconds=request.callback.timeout_seconds,
                )

            self.job_status_service.update_status(
                external_job_id=external_job_id,
                status="completed",
                metadata_updates={
                    "resolved_model_version": model_version,
                    "training_state": "trained",
                    "approval_status": "pending_approval",
                },
            )

        except Exception as exc:  # pragma: no cover - failure handling validated by API tests
            logger.exception("Outbreak training job failed for job_id=%s", request.job_id)

            if request.callback is not None:
                failure_payload = {
                    "job_id": str(request.job_id),
                    "job_type": "outbreak_training",
                    "external_job_id": external_job_id,
                    "status": "failed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "error": {
                        "code": "training_failed",
                        "message": str(exc),
                    },
                }
                try:
                    self.callback_service.send_results(
                        request.callback.url,
                        failure_payload,
                        timeout_seconds=request.callback.timeout_seconds,
                    )
                except Exception:
                    logger.exception("Outbreak training failure callback also failed for job_id=%s", request.job_id)

            self.job_status_service.update_status(
                external_job_id=external_job_id,
                status="failed",
                error={"code": "training_failed", "message": str(exc)},
            )
            raise

        logger.info("Outbreak training job completed for job_id=%s", request.job_id)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
