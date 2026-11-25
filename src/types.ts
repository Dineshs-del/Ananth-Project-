export type EmployeeRole = 'requester' | 'approver' | 'security_admin';

export type RequestStatus =
  | 'PENDING_VOICE'
  | 'PENDING_APPROVAL'
  | 'ESCALATED_REVIEW'
  | 'BLOCKED'
  | 'APPROVED'
  | 'REJECTED'
  | 'EXECUTED'
  | 'EXPIRED';

export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'EXPIRED' | 'CONSUMED';

export type RiskLevel = 'PASS' | 'BORDERLINE' | 'FAIL';

export type FixtureScenario = 'pass' | 'borderline' | 'fail' | 'error';

export type BackendMode = 'local' | 'live';

export interface Employee {
  employee_id: string;
  display_name: string;
  role: EmployeeRole;
  known_callback_hint: string;
  enrolled_samples: number;
  created_at: string;
}

export interface AletheiaSignal {
  verdict: 'real' | 'fake';
  score: number;
  duration_seconds: number;
  operation_id: string;
}

export interface EidosSignal {
  decision: 'match' | 'no_match';
  similarity: number;
  duration_seconds: number;
  operation_id: string;
}

/** Exact 4-key contract agreed between Member A, Member B, and Member C. */
export interface VoiceCheckResult {
  aletheia: AletheiaSignal | null;
  eidos: EidosSignal | null;
  risk: RiskLevel;
  reason: string;
}

export interface ApprovalRecord {
  approval_id: string;
  request_id: string;
  action_hash: string;
  action_summary: string;
  target_resource: string;
  justification: string;
  requester_id: string;
  requester_name: string | null;
  known_callback_hint: string;
  voice_risk: RiskLevel | null;
  voice_reason: string | null;
  matching_number: string;
  number_options: string[];
  requires_peer_review: boolean;
  status: ApprovalStatus;
  expires_at: string;
  remaining_seconds: number;
  decided_by: string | null;
  decision_notes: string | null;
  peer_review_confirmed: boolean;
  created_at: string;
  decided_at: string | null;
  consumed_at: string | null;
}

export interface BreakGlassRequest {
  request_id: string;
  employee_id: string;
  employee_name: string | null;
  known_callback_hint: string;
  action_summary: string;
  target_resource: string;
  justification: string;
  action_hash: string;
  status: RequestStatus;
  challenge_phrase: string;
  challenge_expires_at: string;
  voice_attempts: number;
  voice_result: VoiceCheckResult | null;
  active_approval: ApprovalRecord | null;
  reviewer_id: string | null;
  decision_notes: string | null;
  execution_receipt: string | null;
  executed_at: string | null;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
}

export interface MockExecutionReceipt {
  request_id: string;
  approval_id: string;
  status: 'EXECUTED';
  simulated: boolean;
  action_summary: string;
  target_resource: string;
  execution_receipt: string;
  executed_at: string;
}

export interface AuditEvent {
  id: number;
  event_type: string;
  actor_id: string;
  request_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
  prev_hash: string;
  event_hash: string;
}

export interface AuditChainVerification {
  valid: boolean;
  checked_events: number;
  broken_at_id: number | null;
}
