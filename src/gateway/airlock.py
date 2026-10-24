"""
SQLite-backed idempotency registry and operator review queue.

The default :memory: database keeps isolated demos and tests self-contained. Pass a
file path to persist gateway state across process restarts.
"""
import copy
import hashlib
import hmac
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional, Union

from src.models.types import RiskLevel, GateVerdict, ToolCallRequest, ToolCallResult


class Airlock:
    """Transactional store for idempotency records and operator review state."""

    def __init__(self, operator_tokens=(), database_path: Union[str, Path] = ":memory:"):
        if isinstance(operator_tokens, str):
            operator_tokens = (operator_tokens,)
        self._operator_tokens = tuple(operator_tokens)
        if any(not isinstance(token, str) or not token for token in self._operator_tokens):
            raise ValueError("operator_tokens must contain only non-empty strings")

        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            expanded_path = Path(self.database_path).expanduser()
            expanded_path.parent.mkdir(parents=True, exist_ok=True)
            self.database_path = str(expanded_path)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.database_path, timeout=30, check_same_thread=False, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        if self.database_path != ":memory:":
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS idempotency_records (
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    recorded_at REAL NOT NULL,
                    PRIMARY KEY (session_id, agent_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS review_queue (
                    review_id TEXT PRIMARY KEY,
                    entry_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS review_queue_status_created
                    ON review_queue(status, created_at);
                """
            )

    @contextmanager
    def transaction(self):
        """Serialize check-then-act dispatches across threads and DB-sharing processes."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    @staticmethod
    def compute_payload_hash(request: ToolCallRequest) -> str:
        canonical_json = json.dumps(
            {"tool": request.tool_name, "args": request.arguments},
            sort_keys=True,
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def check_idempotency(self, request: ToolCallRequest, payload_hash: str) -> Optional[ToolCallResult]:
        if not request.idempotency_key:
            return None
        with self._lock:
            record = self._connection.execute(
                """SELECT payload_hash, result_json FROM idempotency_records
                   WHERE session_id = ? AND agent_id = ? AND idempotency_key = ?""",
                (request.session_id, request.agent_id, request.idempotency_key),
            ).fetchone()
        if record is None:
            return None
        if record["payload_hash"] != payload_hash:
            raise ValueError(
                f"Idempotency conflict for key '{request.idempotency_key}': payload hash does not match previous execution"
            )

        cached_result = ToolCallResult.model_validate_json(record["result_json"])
        return ToolCallResult(
            call_id=str(uuid.uuid4()),
            session_id=request.session_id,
            tool_name=request.tool_name,
            verdict=GateVerdict.REPLAY_IDEMPOTENT,
            risk_level=cached_result.risk_level,
            payload_hash=payload_hash,
            output=cached_result.output,
            reason="Idempotent replay: returning verified prior execution record",
            execution_time_ms=0.1,
        )

    def record_idempotent_request(self, request: ToolCallRequest, payload_hash: str, result: ToolCallResult):
        if not request.idempotency_key:
            return
        with self._lock:
            self._connection.execute(
                """INSERT INTO idempotency_records
                   (session_id, agent_id, idempotency_key, payload_hash, result_json, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id, agent_id, idempotency_key) DO UPDATE SET
                     payload_hash = excluded.payload_hash,
                     result_json = excluded.result_json,
                     recorded_at = excluded.recorded_at""",
                (request.session_id, request.agent_id, request.idempotency_key,
                 payload_hash, result.model_dump_json(), time.time()),
            )

    def stage_for_review(self, request: ToolCallRequest, risk_level: RiskLevel, payload_hash: str, reason: str) -> str:
        review_id = f"rev-{uuid.uuid4().hex}"
        created_at = time.time()
        entry = {
            "review_id": review_id,
            "session_id": request.session_id,
            "agent_id": request.agent_id,
            "tool_name": request.tool_name,
            "arguments": copy.deepcopy(request.arguments),
            "risk_level": risk_level.value,
            "payload_hash": payload_hash,
            "reason": reason,
            "status": "PENDING_APPROVAL",
            "created_at": created_at,
        }
        with self._lock:
            self._connection.execute(
                "INSERT INTO review_queue (review_id, entry_json, status, created_at) VALUES (?, ?, ?, ?)",
                (review_id, json.dumps(entry, sort_keys=True), entry["status"], created_at),
            )
        return review_id

    def get_pending_reviews(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT review_id, entry_json FROM review_queue WHERE status = 'PENDING_APPROVAL' ORDER BY created_at"
            ).fetchall()
        return {row["review_id"]: json.loads(row["entry_json"]) for row in rows}

    def get_review(self, review_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._connection.execute(
                "SELECT entry_json FROM review_queue WHERE review_id = ?", (review_id,)
            ).fetchone()
        return json.loads(row["entry_json"]) if row is not None else None

    def resolve_review(self, review_id: str, operator_token: str, approved: bool) -> Dict[str, Any]:
        if not isinstance(operator_token, str) or not operator_token or not any(
            hmac.compare_digest(operator_token.encode("utf-8"), expected.encode("utf-8"))
            for expected in self._operator_tokens
        ):
            raise PermissionError("Invalid operator credentials")

        with self._lock:
            row = self._connection.execute(
                "SELECT entry_json, status FROM review_queue WHERE review_id = ?", (review_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Review entry {review_id} not found")
            if row["status"] != "PENDING_APPROVAL":
                raise ValueError(f"Review entry {review_id} is already {row['status'].lower()}")
            status = "APPROVED" if approved else "REJECTED"
            entry = json.loads(row["entry_json"])
            entry.update({
                "status": status,
                "resolved_by": "[configured operator]",
                "resolved_at": time.time(),
            })
            cursor = self._connection.execute(
                """UPDATE review_queue SET entry_json = ?, status = ?
                   WHERE review_id = ? AND status = 'PENDING_APPROVAL'""",
                (json.dumps(entry, sort_keys=True), status, review_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Review entry {review_id} is no longer pending approval")
            return entry

    def mark_review_executed(self, review_id: str, output: Dict[str, Any]) -> None:
        self._update_review(review_id, "EXECUTED", {"execution_output": copy.deepcopy(output)})

    def mark_review_executing(self, review_id: str) -> None:
        self._update_review(review_id, "EXECUTING", {})

    def mark_review_execution_failed(self, review_id: str) -> None:
        self._update_review(review_id, "EXECUTION_FAILED", {})

    def _update_review(self, review_id: str, status: str, additions: Dict[str, Any]) -> None:
        with self._lock:
            row = self._connection.execute(
                "SELECT entry_json, status FROM review_queue WHERE review_id = ?", (review_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Review entry {review_id} not found")
            if row["status"] != "APPROVED" and not (
                status in ("EXECUTED", "EXECUTION_FAILED") and row["status"] == "EXECUTING"
            ):
                raise ValueError(f"Review entry {review_id} is not in a valid state for {status.lower()}")
            entry = json.loads(row["entry_json"])
            entry.update(additions)
            entry["status"] = status
            cursor = self._connection.execute(
                "UPDATE review_queue SET entry_json = ?, status = ? WHERE review_id = ? AND status = ?",
                (json.dumps(entry, sort_keys=True), status, review_id, row["status"]),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Review entry {review_id} is no longer approved")

    def close(self) -> None:
        with self._lock:
            self._connection.close()
