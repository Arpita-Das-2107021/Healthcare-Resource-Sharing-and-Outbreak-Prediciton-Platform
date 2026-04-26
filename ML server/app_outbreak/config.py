import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("OUTBREAK_APP_NAME", "HRSP Outbreak Microservice")
    app_version: str = os.getenv("APP_VERSION", "1.0.0")

    minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    minio_secure: bool = os.getenv("MINIO_SECURE", "false").lower() == "true"
    minio_outbreak_bucket: str = os.getenv("MINIO_OUTBREAK_BUCKET", "ml-outbreak")

    callback_timeout_seconds: int = int(os.getenv("CALLBACK_TIMEOUT_SECONDS", "10"))
    callback_max_retries: int = int(os.getenv("CALLBACK_MAX_RETRIES", "3"))

    outbreak_model_path: str = os.getenv(
        "OUTBREAK_MODEL_PATH",
        "final_outbreak_model/models/stgnn_model.pt",
    )
    graph_radius: float = float(os.getenv("GRAPH_RADIUS", "10"))
    sequence_length: int = int(os.getenv("SEQUENCE_LENGTH", "7"))

    # Folder containing static CSV dependencies for outbreak pipeline.
    base_data_dir: str = os.getenv("OUTBREAK_BASE_DATA_DIR", "final_outbreak_model/data")


settings = Settings()
