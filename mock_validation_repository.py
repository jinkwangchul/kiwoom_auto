# -*- coding: utf-8 -*-
"""Atomic, path-confined persistence for the isolated Mock Validation domain."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Callable

from mock_validation_contract import (
    FOUNDATION_EVENT_TYPES,
    MOCK_CURRENT_INDEX_SCHEMA_VERSION,
    MOCK_EVENT_SCHEMA_VERSION,
    MOCK_HISTORY_SCHEMA_VERSION,
    MockValidationError,
    canonical_json_bytes,
    clean_text,
    normalized_stock_code,
    payload_hash,
    validate_session_document,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MOCK_VALIDATION_ROOT = PROJECT_ROOT / "mock_validation"


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


class MockValidationRepository:
    """The only persistence owner for Phase-1 Mock state.

    The repository intentionally does not import or delegate to any Production
    writer.  Every accepted write target must resolve below ``self.root``.
    """

    def __init__(
        self,
        root: str | Path = DEFAULT_MOCK_VALIDATION_ROOT,
        *,
        project_root: str | Path = PROJECT_ROOT,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.root = Path(root).resolve()
        if self.root == self.project_root:
            raise MockValidationError("MOCK_ROOT_CANNOT_BE_PROJECT_ROOT")
        forbidden_roots = (
            self.project_root / "runtime",
            self.project_root / "stocks",
            self.project_root / "routine_instances",
            self.project_root / "performance_ledger",
        )
        for forbidden in forbidden_roots:
            resolved = forbidden.resolve()
            if self.root == resolved or _inside(self.root, resolved):
                raise MockValidationError(f"MOCK_ROOT_OVERLAPS_PRODUCTION:{resolved}")
        self._lock = threading.RLock()

    def _target(self, relative: str | Path) -> Path:
        candidate = Path(relative)
        target = candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        if target == self.root or not _inside(target, self.root):
            raise MockValidationError("MOCK_WRITE_PATH_OUTSIDE_ROOT")
        return target

    @staticmethod
    def _decode_object(raw: bytes, *, label: str) -> dict[str, Any]:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MockValidationError(f"{label}_MALFORMED") from exc
        if not isinstance(value, dict):
            raise MockValidationError(f"{label}_NOT_OBJECT")
        return value

    def read_object(self, relative: str | Path, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
        target = self._target(relative)
        if not target.exists():
            return deepcopy(default) if default is not None else {}
        return self._decode_object(target.read_bytes(), label="MOCK_DOCUMENT")

    def _atomic_write(
        self,
        relative: str | Path,
        payload: dict[str, Any],
        *,
        validator: Callable[[Any], dict[str, Any]] | None = None,
        immutable: bool = False,
    ) -> dict[str, Any]:
        target = self._target(relative)
        normalized = validator(payload) if validator is not None else deepcopy(payload)
        if not isinstance(normalized, dict):
            raise MockValidationError("MOCK_WRITE_PAYLOAD_INVALID")
        encoded = canonical_json_bytes(normalized) + b"\n"
        digest = payload_hash(normalized)
        target.parent.mkdir(parents=True, exist_ok=True)
        if immutable and target.exists():
            existing = self._decode_object(target.read_bytes(), label="MOCK_IMMUTABLE_DOCUMENT")
            if payload_hash(existing) != digest:
                raise MockValidationError("MOCK_IMMUTABLE_DOCUMENT_CONFLICT")
            return {"written": False, "duplicate": True, "path": str(target), "sha256": digest}
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(3):
                try:
                    os.replace(temp_path, target)
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.01 * (attempt + 1))
            temp_path = None
            read_back = self._decode_object(target.read_bytes(), label="MOCK_READ_BACK")
            if payload_hash(read_back) != digest:
                raise MockValidationError("MOCK_READ_BACK_HASH_MISMATCH")
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
        return {"written": True, "duplicate": False, "path": str(target), "sha256": digest}

    @staticmethod
    def _session_relative(session_id: str) -> Path:
        clean = clean_text(session_id)
        if not clean.startswith("MV-") or any(value in clean for value in ("/", "\\", "..")):
            raise MockValidationError("MOCK_VALIDATION_SESSION_ID_INVALID")
        return Path("runtime") / "sessions" / f"{clean}.json"

    def read_session(self, session_id: str) -> dict[str, Any]:
        document = self.read_object(self._session_relative(session_id))
        if not document:
            raise MockValidationError("MOCK_SESSION_NOT_FOUND")
        return validate_session_document(document)

    def current_session_id(self, stock_code: str) -> str:
        index = self.read_object(
            Path("runtime") / "current_sessions.json",
            default={
                "schema_version": MOCK_CURRENT_INDEX_SCHEMA_VERSION,
                "revision": 0,
                "current_by_stock": {},
            },
        )
        if index.get("schema_version") != MOCK_CURRENT_INDEX_SCHEMA_VERSION:
            raise MockValidationError("MOCK_CURRENT_INDEX_SCHEMA_INVALID")
        current = index.get("current_by_stock")
        if not isinstance(current, dict):
            raise MockValidationError("MOCK_CURRENT_INDEX_INVALID")
        return clean_text(current.get(normalized_stock_code(stock_code)))

    def current_session_ids(self) -> dict[str, str]:
        """Return the validated stock -> current Mock session index."""

        index = self.read_object(
            Path("runtime") / "current_sessions.json",
            default={
                "schema_version": MOCK_CURRENT_INDEX_SCHEMA_VERSION,
                "revision": 0,
                "current_by_stock": {},
            },
        )
        if index.get("schema_version") != MOCK_CURRENT_INDEX_SCHEMA_VERSION:
            raise MockValidationError("MOCK_CURRENT_INDEX_SCHEMA_INVALID")
        current = index.get("current_by_stock")
        if not isinstance(current, dict):
            raise MockValidationError("MOCK_CURRENT_INDEX_INVALID")
        result: dict[str, str] = {}
        for raw_code, raw_session_id in current.items():
            code = normalized_stock_code(raw_code)
            session_id = clean_text(raw_session_id)
            if not session_id.startswith("MV-"):
                raise MockValidationError("MOCK_CURRENT_INDEX_SESSION_INVALID")
            result[code] = session_id
        return result

    def current_sessions(self) -> tuple[dict[str, Any], ...]:
        """Read all current session documents in stable stock order."""

        return tuple(
            self.read_session(session_id)
            for _stock_code, session_id in sorted(self.current_session_ids().items())
        )

    def _write_current_index(self, stock_code: str, session_id: str | None) -> None:
        relative = Path("runtime") / "current_sessions.json"
        index = self.read_object(
            relative,
            default={
                "schema_version": MOCK_CURRENT_INDEX_SCHEMA_VERSION,
                "revision": 0,
                "current_by_stock": {},
            },
        )
        if index.get("schema_version") != MOCK_CURRENT_INDEX_SCHEMA_VERSION:
            raise MockValidationError("MOCK_CURRENT_INDEX_SCHEMA_INVALID")
        current = index.get("current_by_stock")
        if not isinstance(current, dict):
            raise MockValidationError("MOCK_CURRENT_INDEX_INVALID")
        current = dict(current)
        if session_id:
            if current.get(stock_code) == session_id:
                return
            current[stock_code] = session_id
        else:
            if stock_code not in current:
                return
            current.pop(stock_code, None)
        index["current_by_stock"] = current
        index["revision"] = int(index.get("revision", 0)) + 1
        self._atomic_write(relative, index)

    def create_session(self, document: dict[str, Any]) -> dict[str, Any]:
        checked = validate_session_document(document)
        session = checked["session"]
        session_id = session["validation_session_id"]
        stock_code = session["stock_code"]
        with self._lock:
            current_id = self.current_session_id(stock_code)
            if current_id and current_id != session_id:
                current = self.read_session(current_id)
                if current["session"]["state"] != "ENDED":
                    raise MockValidationError("MOCK_STOCK_SESSION_ALREADY_CURRENT")
            relative = self._session_relative(session_id)
            target = self._target(relative)
            if target.exists():
                existing = self.read_session(session_id)
                if (
                    existing["session"]["stock_code"] != stock_code
                    or existing["session"]["reference_snapshot_hash"]
                    != session["reference_snapshot_hash"]
                ):
                    raise MockValidationError("MOCK_SESSION_IDENTITY_CONFLICT")
                if existing["session"]["state"] != "ENDED":
                    self._write_current_index(stock_code, session_id)
                return {"created": False, "duplicate": True, "document": existing}
            write_result = self._atomic_write(relative, checked, validator=validate_session_document)
            self._write_current_index(stock_code, session_id)
            return {"created": True, "duplicate": False, "document": checked, "write": write_result}

    def mutate_session(
        self,
        session_id: str,
        mutation: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            before = self.read_session(session_id)
            revision = int(before["revision"])
            if expected_revision is not None and revision != expected_revision:
                raise MockValidationError("MOCK_SESSION_REVISION_CONFLICT")
            candidate = mutation(deepcopy(before))
            after = candidate if isinstance(candidate, dict) else before
            checked = validate_session_document(after)
            if payload_hash(checked) == payload_hash(before):
                return {"changed": False, "document": before, "revision": revision}
            checked["revision"] = revision + 1
            checked = validate_session_document(checked)
            write_result = self._atomic_write(
                self._session_relative(session_id),
                checked,
                validator=validate_session_document,
            )
            return {
                "changed": True,
                "document": checked,
                "revision": checked["revision"],
                "write": write_result,
            }

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        session_id = clean_text(event.get("validation_session_id"))
        event_id = clean_text(event.get("event_id"))
        if not session_id.startswith("MV-") or not event_id.startswith("ME-"):
            raise MockValidationError("MOCK_EVENT_IDENTITY_INVALID")
        if event.get("event_type") not in FOUNDATION_EVENT_TYPES:
            raise MockValidationError("MOCK_EVENT_TYPE_INVALID")
        relative = Path("events") / f"{session_id}.json"
        with self._lock:
            journal = self.read_object(
                relative,
                default={
                    "schema_version": MOCK_EVENT_SCHEMA_VERSION,
                    "validation_session_id": session_id,
                    "events": [],
                },
            )
            if (
                journal.get("schema_version") != MOCK_EVENT_SCHEMA_VERSION
                or journal.get("validation_session_id") != session_id
                or not isinstance(journal.get("events"), list)
            ):
                raise MockValidationError("MOCK_EVENT_JOURNAL_INVALID")
            for existing in journal["events"]:
                if isinstance(existing, dict) and existing.get("event_id") == event_id:
                    if payload_hash(existing) != payload_hash(event):
                        raise MockValidationError("MOCK_EVENT_ID_CONFLICT")
                    return {"appended": False, "duplicate": True, "event": deepcopy(existing)}
            journal["events"].append(deepcopy(event))
            write = self._atomic_write(relative, journal)
            return {"appended": True, "duplicate": False, "event": deepcopy(event), "write": write}

    def read_events(self, session_id: str) -> list[dict[str, Any]]:
        journal = self.read_object(
            Path("events") / f"{clean_text(session_id)}.json",
            default={
                "schema_version": MOCK_EVENT_SCHEMA_VERSION,
                "validation_session_id": clean_text(session_id),
                "events": [],
            },
        )
        if journal.get("schema_version") != MOCK_EVENT_SCHEMA_VERSION or not isinstance(journal.get("events"), list):
            raise MockValidationError("MOCK_EVENT_JOURNAL_INVALID")
        return deepcopy(journal["events"])

    def archive_session(self, session_id: str, *, archived_at: str) -> dict[str, Any]:
        document = self.read_session(session_id)
        if document["session"]["state"] != "ENDED":
            raise MockValidationError("MOCK_HISTORY_REQUIRES_ENDED_SESSION")
        history = {
            "schema_version": MOCK_HISTORY_SCHEMA_VERSION,
            "validation_session_id": session_id,
            "archived_at": clean_text(archived_at),
            "session_document": document,
            "events": self.read_events(session_id),
        }
        history["history_hash"] = payload_hash(history)
        with self._lock:
            write = self._atomic_write(
                Path("history") / f"{session_id}.json",
                history,
                immutable=True,
            )
            self._write_current_index(document["session"]["stock_code"], None)
        return {"history": history, "write": write}

    def read_history(self, session_id: str) -> dict[str, Any]:
        history = self.read_object(Path("history") / f"{clean_text(session_id)}.json")
        if not history or history.get("schema_version") != MOCK_HISTORY_SCHEMA_VERSION:
            raise MockValidationError("MOCK_HISTORY_NOT_FOUND_OR_INVALID")
        supplied = clean_text(history.get("history_hash"))
        check = deepcopy(history)
        check.pop("history_hash", None)
        if supplied != payload_hash(check):
            raise MockValidationError("MOCK_HISTORY_HASH_MISMATCH")
        return history


__all__ = ["DEFAULT_MOCK_VALIDATION_ROOT", "MockValidationRepository"]
