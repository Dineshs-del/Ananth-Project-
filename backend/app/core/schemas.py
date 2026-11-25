"""Pydantic schemas for the Break-Glass Voice Gate HTTP API."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

EMPLOYEE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

EmployeeRole = Literal["requester", "approver", "security_admin"]
RequestStatus = Literal[
    "PENDING_VOICE",
    "PENDING_APPROVAL",
    "ESCALATED_REVIEW",
    "BLOCKED",
    "APPROVED",
    "REJECTED",
    "EXECUTED",
    "EXPIRED",
]
ApprovalStatus = Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED", "CONSUMED"]
RiskLevel = Literal["PASS", "BORDERLINE", "FAIL"]
DecisionAction = Literal["approve", "reject"]


class EmployeeCreate(BaseModel):
    employee_id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=120)
    role: EmployeeRole = "requester"
    known_callback_hint: str = Field(default="", max_length=160)

    @field_validator("employee_id")
    @classmethod
    def check_employee_id(cls, value: str) -> str:
        if not EMPLOYEE_ID_PATTERN.match(value):
            raise ValueError("employee_id must match ^[A-Za-z0-9_.:-]{1,64}$")
        return value


class EmployeeOut(BaseModel):
    employee_id: str
    display_name: str
    role: EmployeeRole
    known_callback_hint: str
    enrolled_samples: int
    created_at: str


class BreakGlassRequestCreate(BaseModel):
    employee_id: str = Field(..., min_length=1, max_length=64)
    action_summary: str = Field(..., min_length=3, max_length=200)
    target_resource: str = Field(..., min_length=2, max_length=160)
    justification: str = Field(..., min_length=5, max_length=1000)

    @field_validator("employee_id")
    @classmethod
    def check_employee_id(cls, value: str) -> str:
        if not EMPLOYEE_ID_PATTERN.match(value):
            raise ValueError("employee_id must match ^[A-Za-z0-9_.:-]{1,64}$")
        return value


class VoiceCheckResultOut(BaseModel):
    """Exact 4-key contract agreed with Member B and Member C."""

    aletheia: dict[str, Any] | None
    eidos: dict[str, Any] | None
    risk: RiskLevel
    reason: str


class ApprovalRecordOut(BaseModel):
    approval_id: str
    request_id: str
    action_hash: str
    action_summary: str
    target_resource: str
    justification: str
    requester_id: str
    requester_name: str | None = None
    known_callback_hint: str = ""
    voice_risk: RiskLevel | None = None
    voice_reason: str | None = None
    matching_number: str
    number_options: list[str]
    requires_peer_review: bool
    status: ApprovalStatus
    expires_at: str
    remaining_seconds: int
    decided_by: str | None = None
    decision_notes: str | None = None
    peer_review_confirmed: bool = False
    created_at: str
    decided_at: str | None = None
    consumed_at: str | None = None


class BreakGlassRequestOut(BaseModel):
    request_id: str
    employee_id: str
    employee_name: str | None = None
    known_callback_hint: str = ""
    action_summary: str
    target_resource: str
    justification: str
    action_hash: str
    status: RequestStatus
    challenge_phrase: str
    challenge_expires_at: str
    voice_attempts: int
    voice_result: VoiceCheckResultOut | None
    active_approval: ApprovalRecordOut | None = None
    reviewer_id: str | None
    decision_notes: str | None
    execution_receipt: str | None = None
    executed_at: str | None = None
    created_at: str
    updated_at: str
    decided_at: str | None


class BreakGlassRequestWithSourceOut(BreakGlassRequestOut):
    source: Literal["live", "fixture"]


class ApprovalDecisionRequest(BaseModel):
    reviewer_id: str = Field(..., min_length=1, max_length=64)
    action: DecisionAction
    matching_number: str | None = Field(default=None, min_length=2, max_length=2)
    notes: str = Field(default="", max_length=1000)
    peer_review_confirmed: bool = False

    @field_validator("reviewer_id")
    @classmethod
    def check_reviewer_id(cls, value: str) -> str:
        if not EMPLOYEE_ID_PATTERN.match(value):
            raise ValueError("reviewer_id must match ^[A-Za-z0-9_.:-]{1,64}$")
        return value


class ExecuteActionRequest(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=64)

    @field_validator("actor_id")
    @classmethod
    def check_actor_id(cls, value: str) -> str:
        if not EMPLOYEE_ID_PATTERN.match(value):
            raise ValueError("actor_id must match ^[A-Za-z0-9_.:-]{1,64}$")
        return value


class MockExecutionOut(BaseModel):
    request_id: str
    approval_id: str
    status: Literal["EXECUTED"]
    simulated: bool = True
    action_summary: str
    target_resource: str
    execution_receipt: str
    executed_at: str


class AuditEventOut(BaseModel):
    id: int
    event_type: str
    actor_id: str
    request_id: str | None
    details: dict[str, Any]
    created_at: str
    prev_hash: str
    event_hash: str


class AuditChainVerificationOut(BaseModel):
    valid: bool
    checked_events: int
    broken_at_id: int | None


LiveGuardState = Literal["MONITORING", "PASS", "BORDERLINE", "FAIL"]


class LiveSessionStartRequest(BaseModel):
    employee_id: str = Field(..., min_length=1, max_length=64)
    consent_confirmed: bool = False
    guard_actor_id: str | None = Field(default=None, max_length=64)

    @field_validator("employee_id")
    @classmethod
    def check_live_employee_id(cls, value: str) -> str:
        if not EMPLOYEE_ID_PATTERN.match(value):
            raise ValueError("employee_id must match ^[A-Za-z0-9_.:-]{1,64}$")
        return value


class LiveChunkResultOut(BaseModel):
    chunk_index: int
    received_at: str
    risk: RiskLevel
    reason: str
    error: bool = False


class LiveSessionOut(BaseModel):
    request_id: str
    claimed_employee_id: str
    claimed_employee_name: str | None = None
    known_callback_hint: str = ""
    active: bool
    state: LiveGuardState
    fail_latched: bool
    pass_streak: int
    chunks_processed: int
    max_chunks: int
    last_reason: str
    next_step: str
    request_status: str
    ai_mode: str
    telegram_notified: bool = False
    started_at: str
    updated_at: str
    ended_at: str | None = None
    ended_reason: str | None = None
    chunks: list[LiveChunkResultOut]


class LiveChunkUploadOut(BaseModel):
    ignored: bool = False
    message: str
    chunk: LiveChunkResultOut | None = None
    session: LiveSessionOut
    source: Literal["live", "fixture"]
