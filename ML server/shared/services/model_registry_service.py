from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


ALLOWED_MODEL_TYPES = {"model1", "model2"}
ALLOWED_LIFECYCLE_STATES = {
    "draft",
    "trained",
    "validated",
    "approved",
    "active",
    "deprecated",
    "rolled_back",
}


class ModelRegistryService:
    """File-backed registry for model version lifecycle and active selection."""

    def __init__(
        self,
        registry_path: Path,
        model2_default_artifact: str | None = None,
    ) -> None:
        self.registry_path = registry_path
        self._lock = Lock()
        self.model2_default_artifact = model2_default_artifact
        self._ensure_registry()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _default_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "models": {
                "model1": {
                    "active_version": None,
                    "versions": [],
                    "history": [],
                },
                "model2": {
                    "active_version": None,
                    "versions": [],
                    "history": [],
                },
            },
        }

    def _read(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return self._default_payload()

        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._default_payload()

        if not isinstance(payload, dict):
            return self._default_payload()

        payload.setdefault("schema_version", "1.0")
        models = payload.setdefault("models", {})
        for model_type in ALLOWED_MODEL_TYPES:
            model_entry = models.setdefault(model_type, {})
            model_entry.setdefault("active_version", None)
            model_entry.setdefault("versions", [])
            model_entry.setdefault("history", [])

        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _ensure_registry(self) -> None:
        with self._lock:
            payload = self._read()
            changed = False

            changed |= self._bootstrap_model1_from_legacy(payload)
            changed |= self._bootstrap_model2_default(payload)

            if changed or not self.registry_path.exists():
                self._write(payload)

    def _bootstrap_model1_from_legacy(self, payload: dict[str, Any]) -> bool:
        model_state = payload["models"]["model1"]
        if model_state["versions"]:
            return False

        legacy_path = Path(__file__).resolve().parents[2] / "model1_artifacts" / "registry.json"
        if not legacy_path.exists():
            return False

        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False

        versions = legacy.get("model_versions", [])
        active_version = legacy.get("active_model_version")
        if not versions:
            return False

        now = self._utc_now()
        for item in versions:
            version = str(item.get("version", "")).strip()
            if not version:
                continue
            status = "active" if version == active_version else "deprecated"
            model_state["versions"].append(
                {
                    "version": version,
                    "status": status,
                    "approval_status": "approved" if status == "active" else "pending_approval",
                    "artifact_uri": str(item.get("artifact_dir", "")),
                    "metadata": {
                        "legacy_metadata_path": item.get("metadata_path"),
                        "source": "legacy_model1_registry",
                    },
                    "created_at": item.get("created_at", now),
                    "updated_at": now,
                }
            )

        model_state["active_version"] = active_version
        if active_version:
            model_state["history"].append(
                {
                    "action": "bootstrap_activate",
                    "from_version": None,
                    "to_version": active_version,
                    "at": now,
                }
            )

        return True

    def _bootstrap_model2_default(self, payload: dict[str, Any]) -> bool:
        model_state = payload["models"]["model2"]
        if model_state["versions"]:
            return False
        if not self.model2_default_artifact:
            return False

        version = "outbreak-stgnn-v2"
        now = self._utc_now()
        model_state["versions"].append(
            {
                "version": version,
                "status": "active",
                "approval_status": "approved",
                "artifact_uri": self.model2_default_artifact,
                "metadata": {"source": "default_outbreak_model_path"},
                "created_at": now,
                "updated_at": now,
            }
        )
        model_state["active_version"] = version
        model_state["history"].append(
            {
                "action": "bootstrap_activate",
                "from_version": None,
                "to_version": version,
                "at": now,
            }
        )
        return True

    @staticmethod
    def _assert_model_type(model_type: str) -> None:
        if model_type not in ALLOWED_MODEL_TYPES:
            raise ValueError(f"Unsupported model_type: {model_type}")

    @staticmethod
    def _assert_state(state: str) -> None:
        if state not in ALLOWED_LIFECYCLE_STATES:
            raise ValueError(f"Unsupported lifecycle state: {state}")

    def _find_version(self, model_state: dict[str, Any], version: str) -> dict[str, Any] | None:
        for item in model_state["versions"]:
            if item.get("version") == version:
                return item
        return None

    def register_version(
        self,
        *,
        model_type: str,
        version: str,
        artifact_uri: str,
        status: str,
        metadata: dict[str, Any] | None = None,
        approval_status: str = "pending_approval",
    ) -> dict[str, Any]:
        self._assert_model_type(model_type)
        self._assert_state(status)

        now = self._utc_now()

        with self._lock:
            payload = self._read()
            model_state = payload["models"][model_type]
            existing = self._find_version(model_state, version)

            if existing is None:
                entry = {
                    "version": version,
                    "status": status,
                    "approval_status": approval_status,
                    "artifact_uri": artifact_uri,
                    "metadata": metadata or {},
                    "created_at": now,
                    "updated_at": now,
                }
                model_state["versions"].append(entry)
            else:
                existing["status"] = status
                existing["approval_status"] = approval_status
                existing["artifact_uri"] = artifact_uri
                merged_metadata = dict(existing.get("metadata", {}))
                merged_metadata.update(metadata or {})
                existing["metadata"] = merged_metadata
                existing["updated_at"] = now
                entry = existing

            model_state["history"].append(
                {
                    "action": "register",
                    "version": version,
                    "state": status,
                    "at": now,
                }
            )
            self._write(payload)
            return dict(entry)

    def activate_version(self, model_type: str, version: str) -> dict[str, Any]:
        self._assert_model_type(model_type)
        now = self._utc_now()

        with self._lock:
            payload = self._read()
            model_state = payload["models"][model_type]
            target = self._find_version(model_state, version)
            if target is None:
                raise ValueError(f"Version not found: {version}")

            previous_active = model_state.get("active_version")
            if previous_active and previous_active != version:
                previous = self._find_version(model_state, previous_active)
                if previous:
                    previous["status"] = "deprecated"
                    previous["updated_at"] = now

            target["status"] = "active"
            target["approval_status"] = "approved"
            target["updated_at"] = now
            model_state["active_version"] = version
            model_state["history"].append(
                {
                    "action": "activate",
                    "from_version": previous_active,
                    "to_version": version,
                    "at": now,
                }
            )

            self._write(payload)
            return dict(target)

    def rollback_version(self, model_type: str, target_version: str | None = None) -> dict[str, Any]:
        self._assert_model_type(model_type)
        now = self._utc_now()

        with self._lock:
            payload = self._read()
            model_state = payload["models"][model_type]
            versions = model_state["versions"]
            if not versions:
                raise ValueError("No versions available for rollback")

            active_version = model_state.get("active_version")
            active_entry = self._find_version(model_state, active_version) if active_version else None

            if target_version:
                target = self._find_version(model_state, target_version)
                if target is None:
                    raise ValueError(f"Target rollback version not found: {target_version}")
            else:
                candidates = [item for item in versions if item.get("version") != active_version]
                if not candidates:
                    raise ValueError("No previous version available for rollback")
                target = sorted(candidates, key=lambda item: str(item.get("created_at", "")))[-1]

            if active_entry and active_entry.get("version") != target.get("version"):
                active_entry["status"] = "rolled_back"
                active_entry["updated_at"] = now

            target["status"] = "active"
            target["approval_status"] = "approved"
            target["updated_at"] = now
            model_state["active_version"] = target["version"]
            model_state["history"].append(
                {
                    "action": "rollback",
                    "from_version": active_version,
                    "to_version": target["version"],
                    "at": now,
                }
            )

            self._write(payload)
            return dict(target)

    def resolve_version(self, model_type: str, requested_version: str | None = None) -> dict[str, Any]:
        self._assert_model_type(model_type)

        with self._lock:
            payload = self._read()
            model_state = payload["models"][model_type]

            if requested_version:
                target = self._find_version(model_state, requested_version)
                if target is None:
                    raise ValueError(f"Requested version not found: {requested_version}")
                return dict(target)

            active_version = model_state.get("active_version")
            if not active_version:
                raise ValueError(f"No active version configured for {model_type}")

            active = self._find_version(model_state, active_version)
            if active is None:
                raise ValueError(f"Active version missing in registry for {model_type}")
            return dict(active)

    def list_versions(self, model_type: str) -> list[dict[str, Any]]:
        self._assert_model_type(model_type)

        with self._lock:
            payload = self._read()
            versions = payload["models"][model_type]["versions"]
            return [dict(item) for item in sorted(versions, key=lambda item: str(item.get("created_at", "")))]

    def list_models(self) -> list[dict[str, Any]]:
        with self._lock:
            payload = self._read()
            output: list[dict[str, Any]] = []
            for model_type in sorted(ALLOWED_MODEL_TYPES):
                model_state = payload["models"][model_type]
                output.append(
                    {
                        "model_type": model_type,
                        "active_version": model_state.get("active_version"),
                        "version_count": len(model_state.get("versions", [])),
                    }
                )
            return output
