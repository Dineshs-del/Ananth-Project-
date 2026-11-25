"""FastAPI entry point for Break-Glass Voice Gate (Member A core backend)."""

from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware

from .ai.config import ConfigError, load_config
from .ai.detectif_client import remaining_live_calls
from .ai.enrollment import EnrollmentError, enroll_employee
from .approval.service import (
    ApprovalError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    create_employee,
    create_request,
    decide_approval,
    execute_request,
    get_approval,
    get_employee,
    get_request,
    issue_approval_record,
    list_employees,
    list_pending_approvals,
    list_requests,
    record_enrollment_count,
    refresh_challenge,
    submit_voice_verification,
)
from .audit.service import list_events, verify_chain
from .core.config import load_core_config
from .core.database import get_db, init_db
from .core.schemas import (
    ApprovalDecisionRequest,
    ApprovalRecordOut,
    AuditChainVerificationOut,
    AuditEventOut,
    BreakGlassRequestCreate,
    BreakGlassRequestOut,
    BreakGlassRequestWithSourceOut,
    EmployeeCreate,
    EmployeeOut,
    ExecuteActionRequest,
    LiveChunkUploadOut,
    LiveSessionOut,
    LiveSessionStartRequest,
    MockExecutionOut,
)
from .core.storage import UploadValidationError, save_audio_upload
from .live.service import LiveGuardAuditError, get_live_guard_service


def _verify_reviewer_token(authorization: str | None) -> None:
    """If REVIEWER_API_TOKEN is configured, require a matching Bearer token."""
    expected = load_core_config().reviewer_api_token
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token for reviewer action.",
        )
    provided = authorization[len("Bearer ") :].strip()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid reviewer Bearer token.",
        )


def _ai_source() -> str:
    try:
        return load_config().ai_mode
    except ConfigError:
        return "fixture"


def _raise_http(exc: ApprovalError) -> None:
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ForbiddenError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, ConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


def create_app() -> FastAPI:
    config = load_core_config()
    application = FastAPI(
        title="Break-Glass Voice Gate API",
        version="1.0.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @application.get("/health")
    def health() -> dict[str, str | int]:
        try:
            ai_mode = load_config().ai_mode
        except ConfigError:
            ai_mode = "misconfigured"
        return {
            "status": "ok",
            "ai_mode": ai_mode,
            "remaining_live_calls": remaining_live_calls(),
        }

    # --- Employees ---

    @application.get("/api/v1/employees", response_model=list[EmployeeOut])
    def api_list_employees() -> list[dict]:
        with get_db() as conn:
            return list_employees(conn)

    @application.post(
        "/api/v1/employees",
        response_model=EmployeeOut,
        status_code=status.HTTP_201_CREATED,
    )
    def api_create_employee(
        payload: EmployeeCreate,
        authorization: str | None = Header(default=None),
    ) -> dict:
        _verify_reviewer_token(authorization)
        with get_db() as conn:
            try:
                return create_employee(
                    conn,
                    employee_id=payload.employee_id,
                    display_name=payload.display_name,
                    role=payload.role,
                    known_callback_hint=payload.known_callback_hint,
                )
            except ApprovalError as exc:
                _raise_http(exc)

    @application.post("/api/v1/employees/{employee_id}/enroll")
    def api_enroll_employee(
        employee_id: str,
        file: UploadFile = File(...),
        display_name: str | None = Form(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict:
        _verify_reviewer_token(authorization)
        cfg = load_core_config()
        with get_db() as conn:
            try:
                emp = get_employee(conn, employee_id)
            except ApprovalError as exc:
                _raise_http(exc)

        try:
            saved_path = save_audio_upload(
                filename=file.filename,
                stream=file.file,
                upload_dir=cfg.upload_dir,
                max_bytes=cfg.max_upload_bytes,
            )
        except UploadValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        try:
            summary = enroll_employee(
                employee_id,
                [saved_path],
                display_name=display_name or emp["display_name"],
            )
        except EnrollmentError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        finally:
            saved_path.unlink(missing_ok=True)

        with get_db() as conn:
            updated_emp = record_enrollment_count(
                conn,
                employee_id=employee_id,
                added_samples=len(summary["enrolled"]),
                cap_reached=bool(summary["cap_reached"]),
            )

        if summary["cap_reached"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=summary["message"],
            )

        return {
            "employee": updated_emp,
            "message": summary["message"],
            "enrolled": summary["enrolled"],
        }

    # --- Break-Glass Requests (Requester UI) ---

    @application.post(
        "/api/v1/requests",
        response_model=BreakGlassRequestOut,
        status_code=status.HTTP_201_CREATED,
    )
    def api_create_request(payload: BreakGlassRequestCreate) -> dict:
        cfg = load_core_config()
        with get_db() as conn:
            try:
                return create_request(
                    conn,
                    employee_id=payload.employee_id,
                    action_summary=payload.action_summary,
                    target_resource=payload.target_resource,
                    justification=payload.justification,
                    challenge_ttl_seconds=cfg.challenge_ttl_seconds,
                )
            except ApprovalError as exc:
                _raise_http(exc)

    @application.get("/api/v1/requests", response_model=list[BreakGlassRequestOut])
    def api_list_requests(
        status_filter: str | None = Query(default=None, alias="status"),
        employee_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict]:
        with get_db() as conn:
            return list_requests(
                conn,
                status=status_filter,
                employee_id=employee_id,
                limit=limit,
            )

    @application.get("/api/v1/requests/{request_id}", response_model=BreakGlassRequestOut)
    def api_get_request(request_id: str) -> dict:
        with get_db() as conn:
            try:
                return get_request(conn, request_id)
            except ApprovalError as exc:
                _raise_http(exc)

    @application.post(
        "/api/v1/requests/{request_id}/challenge",
        response_model=BreakGlassRequestOut,
    )
    def api_refresh_challenge(request_id: str) -> dict:
        cfg = load_core_config()
        with get_db() as conn:
            try:
                return refresh_challenge(
                    conn,
                    request_id=request_id,
                    challenge_ttl_seconds=cfg.challenge_ttl_seconds,
                )
            except ApprovalError as exc:
                _raise_http(exc)

    @application.post(
        "/api/v1/requests/{request_id}/verify-voice",
        response_model=BreakGlassRequestWithSourceOut,
    )
    def api_verify_voice(
        request_id: str,
        file: UploadFile = File(...),
    ) -> dict:
        cfg = load_core_config()
        try:
            saved_path = save_audio_upload(
                filename=file.filename,
                stream=file.file,
                upload_dir=cfg.upload_dir,
                max_bytes=cfg.max_upload_bytes,
            )
        except UploadValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        try:
            with get_db() as conn:
                try:
                    result = submit_voice_verification(
                        conn,
                        request_id=request_id,
                        audio_path=str(saved_path),
                        approval_ttl_seconds=cfg.approval_ttl_seconds,
                    )
                except ApprovalError as exc:
                    _raise_http(exc)
        finally:
            saved_path.unlink(missing_ok=True)

        result["source"] = _ai_source()
        return result

    @application.post(
        "/api/v1/requests/{request_id}/approval",
        response_model=ApprovalRecordOut,
        status_code=status.HTTP_201_CREATED,
    )
    def api_reissue_approval(request_id: str) -> dict:
        cfg = load_core_config()
        with get_db() as conn:
            try:
                return issue_approval_record(
                    conn,
                    request_id=request_id,
                    approval_ttl_seconds=cfg.approval_ttl_seconds,
                )
            except ApprovalError as exc:
                _raise_http(exc)

    @application.post(
        "/api/v1/requests/{request_id}/execute",
        response_model=MockExecutionOut,
    )
    def api_execute_request(
        request_id: str,
        payload: ExecuteActionRequest,
    ) -> dict:
        with get_db() as conn:
            try:
                return execute_request(
                    conn,
                    request_id=request_id,
                    actor_id=payload.actor_id,
                )
            except ApprovalError as exc:
                _raise_http(exc)

    # --- Second-Device Approvals (Approver Phone Page) ---

    @application.get("/api/v1/approvals/pending", response_model=list[ApprovalRecordOut])
    def api_list_pending_approvals() -> list[dict]:
        with get_db() as conn:
            return list_pending_approvals(conn)

    @application.get("/api/v1/approvals/{approval_id}", response_model=ApprovalRecordOut)
    def api_get_approval(approval_id: str) -> dict:
        with get_db() as conn:
            try:
                return get_approval(conn, approval_id)
            except ApprovalError as exc:
                _raise_http(exc)

    @application.post(
        "/api/v1/approvals/{approval_id}/decide",
        response_model=BreakGlassRequestOut,
    )
    def api_decide_approval(
        approval_id: str,
        payload: ApprovalDecisionRequest,
        authorization: str | None = Header(default=None),
    ) -> dict:
        _verify_reviewer_token(authorization)
        with get_db() as conn:
            try:
                return decide_approval(
                    conn,
                    approval_id=approval_id,
                    reviewer_id=payload.reviewer_id,
                    action=payload.action,
                    matching_number=payload.matching_number,
                    notes=payload.notes,
                    peer_review_confirmed=payload.peer_review_confirmed,
                )
            except ApprovalError as exc:
                _raise_http(exc)

    # --- Live Call Guard (Near-Live Chunk Monitoring) ---

    @application.post(
        "/api/v1/live/{request_id}/start",
        response_model=LiveSessionOut,
        status_code=status.HTTP_201_CREATED,
    )
    def api_live_start(
        request_id: str,
        payload: LiveSessionStartRequest,
    ) -> dict:
        service = get_live_guard_service()
        try:
            return service.start_session(
                request_id=request_id,
                employee_id=payload.employee_id,
                consent_confirmed=payload.consent_confirmed,
                guard_actor_id=payload.guard_actor_id,
            )
        except ApprovalError as exc:
            _raise_http(exc)
        except LiveGuardAuditError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Audit logging failed; Live Call Guard did not start (fail closed).",
            ) from exc

    @application.post(
        "/api/v1/live/{request_id}/chunk",
        response_model=LiveChunkUploadOut,
    )
    def api_live_chunk(
        request_id: str,
        file: UploadFile = File(...),
        employee_id: str = Form(...),
        fixture_scenario: str | None = Query(default=None),
    ) -> dict:
        service = get_live_guard_service()
        try:
            return service.process_chunk(
                request_id=request_id,
                employee_id=employee_id,
                filename=file.filename,
                content_type=file.content_type,
                stream=file.file,
                fixture_scenario=fixture_scenario,
            )
        except UploadValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except ApprovalError as exc:
            _raise_http(exc)
        except LiveGuardAuditError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Audit logging failed; chunk could not be committed (fail closed).",
            ) from exc

    @application.get("/api/v1/live/{request_id}/status", response_model=LiveSessionOut)
    def api_live_status(request_id: str) -> dict:
        service = get_live_guard_service()
        try:
            return service.get_session(request_id)
        except ApprovalError as exc:
            _raise_http(exc)

    @application.post("/api/v1/live/{request_id}/stop", response_model=LiveSessionOut)
    def api_live_stop(request_id: str) -> dict:
        service = get_live_guard_service()
        try:
            return service.stop_session(request_id=request_id)
        except ApprovalError as exc:
            _raise_http(exc)
        except LiveGuardAuditError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Audit logging failed while stopping Live Call Guard.",
            ) from exc

    # --- Audit Trail ---

    @application.get("/api/v1/audit", response_model=list[AuditEventOut])
    def api_list_audit(
        request_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict]:
        with get_db() as conn:
            return list_events(conn, request_id=request_id, limit=limit)

    @application.get("/api/v1/audit/verify", response_model=AuditChainVerificationOut)
    def api_verify_audit_chain() -> dict:
        with get_db() as conn:
            return verify_chain(conn)

    return application


app = create_app()
