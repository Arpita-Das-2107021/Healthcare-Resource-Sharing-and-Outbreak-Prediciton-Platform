"""Standard API response envelope builder."""
from typing import Any, Optional


def success_response(data: Any = None, message: str = "Success", meta: Optional[dict] = None) -> dict:
    """Build a successful envelope response."""
    envelope: dict = {
        "success": True,
        "message": message,
        "data": data,
    }
    if meta is not None:
        envelope["meta"] = meta
    return envelope


def error_response(code: str, message: str, details: Optional[dict] = None) -> dict:
    """Build an error envelope response."""
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }
