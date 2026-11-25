import type {
  ApprovalRecord,
  AuditChainVerification,
  AuditEvent,
  BackendMode,
  BreakGlassRequest,
  Employee,
  FixtureScenario,
  MockExecutionReceipt,
  VoiceCheckResult,
} from '../types';

const DEFAULT_API_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ||
  'http://127.0.0.1:8000';

const GENESIS_HASH = '0'.repeat(64);
const APPROVAL_TTL_SECONDS = 120;
const CHALLENGE_TTL_SECONDS = 300;

const CHALLENGE_WORDS = [
  'acorn', 'anchor', 'apricot', 'archway', 'aspen', 'autumn', 'bakery', 'bamboo',
  'banner', 'barley', 'beacon', 'bicycle', 'birch', 'blanket', 'blossom', 'bonfire',
  'boulder', 'breeze', 'bridge', 'brook', 'cabin', 'cactus', 'calendar', 'campfire',
  'canal', 'candle', 'canyon', 'captain', 'caravan', 'castle', 'cedar', 'cherry',
  'chestnut', 'chimney', 'cider', 'cinnamon', 'citadel', 'clover', 'cobalt', 'compass',
  'copper', 'coral', 'cottage', 'courtyard', 'cricket', 'crystal', 'cypress', 'daybreak',
  'delta', 'desert', 'diamond', 'dolphin', 'driftwood', 'drizzle', 'eagle', 'eclipse',
  'emerald', 'envelope', 'falcon', 'feather', 'ferry', 'festival', 'firefly', 'flannel',
  'foothill', 'forest', 'fossil', 'fountain', 'galaxy', 'garden', 'gateway', 'geyser',
  'glacier', 'granite', 'greenhouse', 'harbor', 'harvest', 'hawthorn', 'hazelnut', 'heather',
  'heron', 'hickory', 'hillside', 'horizon', 'island', 'ivory', 'jasmine', 'journal',
  'juniper', 'kayak', 'kettle', 'lantern', 'laurel', 'lavender', 'library', 'lighthouse',
  'lilac', 'limestone', 'linden', 'locket', 'magnet', 'mahogany', 'maple', 'marble',
  'marigold', 'marina', 'meadow', 'mercury', 'monarch', 'monsoon', 'nectar', 'orchard',
];

const DIGIT_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'];

export const PRESET_OPERATIONS = [
  {
    action_summary: 'Deploy build 142 to production',
    target_resource: 'prod-checkout-api',
    justification: 'SEV-1 checkout timeout affecting live payment retries; hotfix validated in staging.',
  },
  {
    action_summary: 'Execute emergency Terraform apply',
    target_resource: 'edge-waf-prod',
    justification: 'Applying emergency rate-limit policy to mitigate sustained credential-stuffing spike.',
  },
  {
    action_summary: 'Reset privileged administrator password',
    target_resource: 'iam-root-breakglass',
    justification: 'Primary SSO IdP unreachable during regional outage; need emergency console access.',
  },
  {
    action_summary: 'Re-enrol hardware MFA device',
    target_resource: 'okta-oncall-admin',
    justification: 'Lost primary security key while responding to active database replication failover.',
  },
];

function randomInt(maxExclusive: number): number {
  const arr = new Uint32Array(1);
  crypto.getRandomValues(arr);
  return arr[0] % maxExclusive;
}

function randomHex(bytes: number): string {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  return Array.from(arr, (b) => b.toString(16).padStart(2, '0')).join('');
}

async function sha256Hex(input: string): Promise<string> {
  const encoded = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest('SHA-256', encoded);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

export function generateLocalChallenge(): string {
  const pool = [...CHALLENGE_WORDS];
  const chosen: string[] = [];
  for (let i = 0; i < 3; i++) {
    const idx = randomInt(pool.length);
    chosen.push(pool.splice(idx, 1)[0]);
  }
  const d1 = randomInt(10);
  const d2 = randomInt(10);
  return `I am requesting break-glass approval right now. My verification words are ${chosen[0]}, ${chosen[1]}, and ${chosen[2]}, followed by the digits ${d1} (${DIGIT_WORDS[d1]}) and ${d2} (${DIGIT_WORDS[d2]}).`;
}

function generateNumberMatching(): { matching: string; options: string[] } {
  const codes: string[] = [];
  while (codes.length < 3) {
    const num = String(10 + randomInt(90));
    if (!codes.includes(num)) codes.push(num);
  }
  const matching = codes[0];
  const options = [...codes];
  for (let i = options.length - 1; i > 0; i--) {
    const j = randomInt(i + 1);
    [options[i], options[j]] = [options[j], options[i]];
  }
  return { matching, options };
}

function buildFixtureVoiceResult(scenario: FixtureScenario, durationSeconds: number): VoiceCheckResult {
  const dur = Number(Math.max(3.2, Math.min(durationSeconds || 5.4, 59.0)).toFixed(1));
  if (scenario === 'pass') {
    return {
      aletheia: {
        verdict: 'real',
        score: 0.08,
        duration_seconds: dur,
        operation_id: `op_aletheia_${randomHex(4)}`,
      },
      eidos: {
        decision: 'match',
        similarity: 0.89,
        duration_seconds: dur,
        operation_id: `op_eidos_${randomHex(4)}`,
      },
      risk: 'PASS',
      reason:
        'Voice matched the enrolled speaker with no synthetic speech indicators detected. Remember that these scores are evidence, not proof. Next step: complete independent second-device number-matching approval before execution.',
    };
  }
  if (scenario === 'borderline') {
    return {
      aletheia: {
        verdict: 'real',
        score: 0.48,
        duration_seconds: dur,
        operation_id: `op_aletheia_${randomHex(4)}`,
      },
      eidos: {
        decision: 'no_match',
        similarity: 0.56,
        duration_seconds: dur,
        operation_id: `op_eidos_${randomHex(4)}`,
      },
      risk: 'BORDERLINE',
      reason:
        'Voice check returned a near-miss close to the review cutoff. A score is evidence, not proof. Next step: request stays locked for different-person peer review or callback on a number you already know.',
    };
  }
  if (scenario === 'fail') {
    return {
      aletheia: {
        verdict: 'fake',
        score: 0.94,
        duration_seconds: dur,
        operation_id: `op_aletheia_${randomHex(4)}`,
      },
      eidos: {
        decision: 'no_match',
        similarity: 0.21,
        duration_seconds: dur,
        operation_id: `op_eidos_${randomHex(4)}`,
      },
      risk: 'FAIL',
      reason:
        'Voice check flagged synthetic speech indicators and speaker mismatch. This score is evidence, not proof, so the request is hard-blocked and cannot be overridden by second-device approval. Next step: call back on a number you already know.',
    };
  }
  return {
    aletheia: null,
    eidos: null,
    risk: 'BORDERLINE',
    reason:
      'Voice check service returned an error or unusable audio and failed closed. A score is evidence, not proof. Next step: ask the requester to re-record clearly, or call back on a number you already know.',
  };
}

function remainingSeconds(iso: string): number {
  const diff = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
  return Math.max(0, diff);
}

/**
 * Dual-mode engine:
 * - "live": calls Member A's FastAPI backend (http://127.0.0.1:8000 by default)
 * - "local": executes the exact same Break-Glass rules, 2-min expiry, one-time consumption,
 *   number matching, Section-6 decision table, and Web Crypto SHA-256 audit hash chain in browser.
 */
export class VoiceGateEngine {
  private mode: BackendMode;
  private apiBaseUrl: string;
  private scenario: FixtureScenario;
  private employees: Employee[];
  private requests: BreakGlassRequest[];
  private approvals: ApprovalRecord[];
  private auditEvents: AuditEvent[];
  private nextAuditId: number;
  private initialized: boolean;

  constructor(options?: { mode?: BackendMode; apiBaseUrl?: string; scenario?: FixtureScenario }) {
    this.mode = options?.mode ?? 'local';
    this.apiBaseUrl = (options?.apiBaseUrl ?? DEFAULT_API_URL).replace(/\/$/, '');
    this.scenario = options?.scenario ?? 'pass';
    this.employees = [];
    this.requests = [];
    this.approvals = [];
    this.auditEvents = [];
    this.nextAuditId = 1;
    this.initialized = false;
  }

  setMode(mode: BackendMode, apiBaseUrl?: string): void {
    this.mode = mode;
    if (apiBaseUrl) {
      this.apiBaseUrl = apiBaseUrl.replace(/\/$/, '');
    }
  }

  setScenario(scenario: FixtureScenario): void {
    this.scenario = scenario;
  }

  getMode(): BackendMode {
    return this.mode;
  }

  getApiBaseUrl(): string {
    return this.apiBaseUrl;
  }

  getScenario(): FixtureScenario {
    return this.scenario;
  }

  private async ensureSeeded(): Promise<void> {
    if (this.initialized) return;
    this.initialized = true;
    const now = new Date().toISOString();
    this.employees = [
      {
        employee_id: 'emp_rahul',
        display_name: 'Rahul — On-call Engineer',
        role: 'requester',
        known_callback_hint: 'Corp Directory Ext. 4102',
        enrolled_samples: 3,
        created_at: now,
      },
      {
        employee_id: 'emp_priya',
        display_name: 'Priya — Production Admin',
        role: 'approver',
        known_callback_hint: 'On-Call Directory Ext. 4208',
        enrolled_samples: 3,
        created_at: now,
      },
      {
        employee_id: 'emp_carol',
        display_name: 'Carol — Security Lead',
        role: 'security_admin',
        known_callback_hint: 'SOC Desk Ext. 4900',
        enrolled_samples: 4,
        created_at: now,
      },
    ];

    await this.appendAudit('SYSTEM_INITIALIZED', 'emp_carol', null, {
      policy: 'Break-Glass Voice Gate v1 (Aletheia + Eidos + Action-Bound 2-min Approval)',
    });
  }

  private async computeEventHash(
    prevHash: string,
    eventType: string,
    actorId: string,
    requestId: string | null,
    detailsJson: string,
    createdAt: string,
  ): Promise<string> {
    const payload = JSON.stringify({
      actor_id: actorId,
      created_at: createdAt,
      details_json: detailsJson,
      event_type: eventType,
      prev_hash: prevHash,
      request_id: requestId,
    });
    return sha256Hex(payload);
  }

  private async appendAudit(
    eventType: string,
    actorId: string,
    requestId: string | null,
    details: Record<string, unknown>,
  ): Promise<AuditEvent> {
    const prevHash =
      this.auditEvents.length > 0
        ? this.auditEvents[this.auditEvents.length - 1].event_hash
        : GENESIS_HASH;
    const createdAt = new Date().toISOString();
    const detailsJson = JSON.stringify(details);
    const eventHash = await this.computeEventHash(
      prevHash,
      eventType,
      actorId,
      requestId,
      detailsJson,
      createdAt,
    );
    const event: AuditEvent = {
      id: this.nextAuditId++,
      event_type: eventType,
      actor_id: actorId,
      request_id: requestId,
      details,
      created_at: createdAt,
      prev_hash: prevHash,
      event_hash: eventHash,
    };
    this.auditEvents.push(event);
    return event;
  }

  private refreshApprovalExpiries(): void {
    const now = Date.now();
    for (const apr of this.approvals) {
      apr.remaining_seconds = remainingSeconds(apr.expires_at);
      if (apr.status === 'PENDING' && new Date(apr.expires_at).getTime() <= now) {
        apr.status = 'EXPIRED';
      }
    }
    for (const req of this.requests) {
      const latest = this.approvals
        .filter((a) => a.request_id === req.request_id)
        .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
      req.active_approval = latest ? { ...latest } : null;
    }
  }

  private async liveFetch<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${this.apiBaseUrl}${path}`, init);
    if (!res.ok) {
      let detail = `Request failed (${res.status})`;
      try {
        const body = await res.json();
        if (typeof body?.detail === 'string') detail = body.detail;
      } catch {
        // ignore parse error
      }
      throw new Error(detail);
    }
    return (await res.json()) as T;
  }

  async checkHealth(): Promise<boolean> {
    if (this.mode === 'local') return true;
    try {
      const res = await this.liveFetch<{ status: string }>('/health');
      return res.status === 'ok';
    } catch {
      return false;
    }
  }

  async listEmployees(): Promise<Employee[]> {
    if (this.mode === 'live') {
      return this.liveFetch<Employee[]>('/api/v1/employees');
    }
    await this.ensureSeeded();
    return this.employees.map((e) => ({ ...e }));
  }

  async createEmployee(input: {
    employee_id: string;
    display_name: string;
    role: Employee['role'];
    known_callback_hint: string;
  }): Promise<Employee> {
    if (this.mode === 'live') {
      return this.liveFetch<Employee>('/api/v1/employees', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      });
    }
    await this.ensureSeeded();
    if (this.employees.some((e) => e.employee_id === input.employee_id)) {
      throw new Error(`Employee '${input.employee_id}' already exists.`);
    }
    const created: Employee = {
      ...input,
      enrolled_samples: 0,
      created_at: new Date().toISOString(),
    };
    this.employees.push(created);
    await this.appendAudit('EMPLOYEE_CREATED', input.employee_id, null, {
      display_name: input.display_name,
      role: input.role,
    });
    return { ...created };
  }

  async enrollEmployeeSample(employeeId: string, file: File): Promise<{ employee: Employee; message: string }> {
    if (this.mode === 'live') {
      const form = new FormData();
      form.append('file', file);
      return this.liveFetch<{ employee: Employee; message: string }>(
        `/api/v1/employees/${encodeURIComponent(employeeId)}/enroll`,
        { method: 'POST', body: form },
      );
    }
    await this.ensureSeeded();
    const emp = this.employees.find((e) => e.employee_id === employeeId);
    if (!emp) throw new Error(`Employee '${employeeId}' not found.`);
    if (emp.enrolled_samples >= 5) {
      throw new Error(
        `Speaker '${employeeId}' already reached the maximum of 5 enrollment samples (HTTP 409 too_many_samples).`,
      );
    }
    emp.enrolled_samples += 1;
    await this.appendAudit('EMPLOYEE_ENROLLED', employeeId, null, {
      file_name: file.name,
      enrolled_samples: emp.enrolled_samples,
    });
    return {
      employee: { ...emp },
      message: `Enrolled sample ${emp.enrolled_samples} of 5 for ${emp.display_name}.`,
    };
  }

  async listRequests(): Promise<BreakGlassRequest[]> {
    if (this.mode === 'live') {
      return this.liveFetch<BreakGlassRequest[]>('/api/v1/requests');
    }
    await this.ensureSeeded();
    this.refreshApprovalExpiries();
    return this.requests.map((r) => ({ ...r }));
  }

  async createRequest(input: {
    employee_id: string;
    action_summary: string;
    target_resource: string;
    justification: string;
  }): Promise<BreakGlassRequest> {
    if (this.mode === 'live') {
      return this.liveFetch<BreakGlassRequest>('/api/v1/requests', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      });
    }
    await this.ensureSeeded();
    const emp = this.employees.find((e) => e.employee_id === input.employee_id);
    if (!emp) throw new Error(`Employee '${input.employee_id}' not found.`);

    const now = new Date();
    const requestId = `bg_${randomHex(6)}`;
    const actionHash = await sha256Hex(
      JSON.stringify({
        action_summary: input.action_summary.trim(),
        employee_id: input.employee_id,
        justification: input.justification.trim(),
        target_resource: input.target_resource.trim(),
      }),
    );

    const req: BreakGlassRequest = {
      request_id: requestId,
      employee_id: emp.employee_id,
      employee_name: emp.display_name,
      known_callback_hint: emp.known_callback_hint,
      action_summary: input.action_summary.trim(),
      target_resource: input.target_resource.trim(),
      justification: input.justification.trim(),
      action_hash: actionHash,
      status: 'PENDING_VOICE',
      challenge_phrase: generateLocalChallenge(),
      challenge_expires_at: new Date(now.getTime() + CHALLENGE_TTL_SECONDS * 1000).toISOString(),
      voice_attempts: 0,
      voice_result: null,
      active_approval: null,
      reviewer_id: null,
      decision_notes: null,
      execution_receipt: null,
      executed_at: null,
      created_at: now.toISOString(),
      updated_at: now.toISOString(),
      decided_at: null,
    };

    this.requests.unshift(req);
    await this.appendAudit('REQUEST_CREATED', emp.employee_id, requestId, {
      action_summary: req.action_summary,
      target_resource: req.target_resource,
      action_hash: actionHash,
    });
    return { ...req };
  }

  async refreshChallenge(requestId: string): Promise<BreakGlassRequest> {
    if (this.mode === 'live') {
      return this.liveFetch<BreakGlassRequest>(
        `/api/v1/requests/${encodeURIComponent(requestId)}/challenge`,
        { method: 'POST' },
      );
    }
    await this.ensureSeeded();
    const req = this.requests.find((r) => r.request_id === requestId);
    if (!req) throw new Error('Request not found.');
    req.challenge_phrase = generateLocalChallenge();
    req.challenge_expires_at = new Date(Date.now() + CHALLENGE_TTL_SECONDS * 1000).toISOString();
    req.updated_at = new Date().toISOString();
    await this.appendAudit('CHALLENGE_REFRESHED', req.employee_id, requestId, {
      challenge_expires_at: req.challenge_expires_at,
    });
    return { ...req };
  }

  async verifyVoice(
    requestId: string,
    audioFile: File,
    durationSeconds = 5.2,
  ): Promise<BreakGlassRequest> {
    if (this.mode === 'live') {
      const form = new FormData();
      form.append('file', audioFile);
      return this.liveFetch<BreakGlassRequest>(
        `/api/v1/requests/${encodeURIComponent(requestId)}/verify-voice`,
        { method: 'POST', body: form },
      );
    }
    await this.ensureSeeded();
    const req = this.requests.find((r) => r.request_id === requestId);
    if (!req) throw new Error('Request not found.');
    if (['BLOCKED', 'REJECTED', 'EXECUTED', 'EXPIRED', 'APPROVED'].includes(req.status)) {
      throw new Error(`Cannot verify voice while request is ${req.status}.`);
    }

    // Small realistic latency so transition feels tangible without sluggishness
    await new Promise((resolve) => setTimeout(resolve, 450));

    const result = buildFixtureVoiceResult(this.scenario, durationSeconds);
    req.voice_attempts += 1;
    req.voice_result = result;
    req.updated_at = new Date().toISOString();

    if (result.risk === 'PASS') {
      req.status = 'PENDING_APPROVAL';
    } else if (result.risk === 'BORDERLINE') {
      req.status = 'ESCALATED_REVIEW';
    } else {
      req.status = 'BLOCKED';
    }

    await this.appendAudit('VOICE_VERIFIED', req.employee_id, req.request_id, {
      risk: result.risk,
      next_status: req.status,
      aletheia: result.aletheia,
      eidos: result.eidos,
      audio_filename: audioFile.name,
    });

    // Expire any previous pending approval records for this request
    for (const apr of this.approvals) {
      if (apr.request_id === req.request_id && apr.status === 'PENDING') {
        apr.status = 'EXPIRED';
      }
    }

    if (result.risk !== 'FAIL') {
      await this.createLocalApproval(req, result.risk === 'BORDERLINE');
    } else {
      req.active_approval = null;
    }

    this.refreshApprovalExpiries();
    return { ...req };
  }

  private async createLocalApproval(
    req: BreakGlassRequest,
    requiresPeerReview: boolean,
  ): Promise<ApprovalRecord> {
    const { matching, options } = generateNumberMatching();
    const now = new Date();
    const expiresAt = new Date(now.getTime() + APPROVAL_TTL_SECONDS * 1000).toISOString();
    const approval: ApprovalRecord = {
      approval_id: `apr_${randomHex(6)}`,
      request_id: req.request_id,
      action_hash: req.action_hash,
      action_summary: req.action_summary,
      target_resource: req.target_resource,
      justification: req.justification,
      requester_id: req.employee_id,
      requester_name: req.employee_name,
      known_callback_hint: req.known_callback_hint,
      voice_risk: req.voice_result?.risk ?? null,
      voice_reason: req.voice_result?.reason ?? null,
      matching_number: matching,
      number_options: options,
      requires_peer_review: requiresPeerReview,
      status: 'PENDING',
      expires_at: expiresAt,
      remaining_seconds: APPROVAL_TTL_SECONDS,
      decided_by: null,
      decision_notes: null,
      peer_review_confirmed: false,
      created_at: now.toISOString(),
      decided_at: null,
      consumed_at: null,
    };
    this.approvals.unshift(approval);
    req.active_approval = { ...approval };
    await this.appendAudit('APPROVAL_ISSUED', req.employee_id, req.request_id, {
      approval_id: approval.approval_id,
      requires_peer_review: requiresPeerReview,
      expires_at: expiresAt,
    });
    return approval;
  }

  async reissueApproval(requestId: string): Promise<ApprovalRecord> {
    if (this.mode === 'live') {
      return this.liveFetch<ApprovalRecord>(
        `/api/v1/requests/${encodeURIComponent(requestId)}/approval`,
        { method: 'POST' },
      );
    }
    await this.ensureSeeded();
    const req = this.requests.find((r) => r.request_id === requestId);
    if (!req) throw new Error('Request not found.');
    if (!['PENDING_APPROVAL', 'ESCALATED_REVIEW'].includes(req.status)) {
      throw new Error(`Cannot issue a new approval code while request is ${req.status}.`);
    }
    for (const apr of this.approvals) {
      if (apr.request_id === requestId && apr.status === 'PENDING') {
        apr.status = 'EXPIRED';
      }
    }
    const created = await this.createLocalApproval(req, req.voice_result?.risk === 'BORDERLINE');
    this.refreshApprovalExpiries();
    return { ...created };
  }

  async listPendingApprovals(): Promise<ApprovalRecord[]> {
    if (this.mode === 'live') {
      return this.liveFetch<ApprovalRecord[]>('/api/v1/approvals/pending');
    }
    await this.ensureSeeded();
    this.refreshApprovalExpiries();
    return this.approvals.filter((a) => a.status === 'PENDING').map((a) => ({ ...a }));
  }

  async decideApproval(
    approvalId: string,
    payload: {
      reviewer_id: string;
      action: 'approve' | 'reject';
      matching_number?: string;
      notes: string;
      peer_review_confirmed: boolean;
    },
  ): Promise<BreakGlassRequest> {
    if (this.mode === 'live') {
      return this.liveFetch<BreakGlassRequest>(
        `/api/v1/approvals/${encodeURIComponent(approvalId)}/decide`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        },
      );
    }
    await this.ensureSeeded();
    this.refreshApprovalExpiries();

    const apr = this.approvals.find((a) => a.approval_id === approvalId);
    if (!apr) throw new Error('Approval record not found.');
    const req = this.requests.find((r) => r.request_id === apr.request_id);
    if (!req) throw new Error('Linked request not found.');
    const reviewer = this.employees.find((e) => e.employee_id === payload.reviewer_id);
    if (!reviewer) throw new Error('Reviewer not found.');

    if (req.voice_result?.risk === 'FAIL') {
      throw new Error('Hard voice-verification failure cannot be overridden by second-device approval.');
    }
    if (apr.status === 'CONSUMED' || apr.status === 'APPROVED' || apr.status === 'REJECTED') {
      await this.appendAudit('APPROVAL_REUSE_BLOCKED', reviewer.employee_id, req.request_id, {
        approval_id: approvalId,
      });
      throw new Error('This approval record has already been used (one-time use enforced).');
    }
    if (apr.status === 'EXPIRED' || remainingSeconds(apr.expires_at) === 0) {
      apr.status = 'EXPIRED';
      await this.appendAudit('APPROVAL_EXPIRED', reviewer.employee_id, req.request_id, {
        approval_id: approvalId,
      });
      throw new Error('Approval code expired after 2 minutes. Generate a new approval record.');
    }
    if (reviewer.role !== 'approver' && reviewer.role !== 'security_admin') {
      throw new Error(
        `${reviewer.display_name} has role "${reviewer.role}" and cannot approve break-glass actions.`,
      );
    }
    if (reviewer.employee_id === req.employee_id) {
      throw new Error(
        'Different-person rule: the requester cannot approve or reject their own break-glass request.',
      );
    }

    const now = new Date().toISOString();
    const notes = payload.notes.trim();

    if (payload.action === 'approve') {
      if (!payload.matching_number || payload.matching_number.trim() !== apr.matching_number) {
        await this.appendAudit('APPROVAL_CODE_MISMATCH', reviewer.employee_id, req.request_id, {
          approval_id: approvalId,
        });
        throw new Error('Number-matching code did not match. Select the exact number shown to the requester.');
      }
      if (apr.requires_peer_review && (!payload.peer_review_confirmed || notes.length < 5)) {
        throw new Error(
          'Borderline voice result requires checking peer review confirmation and documenting callback/verification notes (5+ chars).',
        );
      }

      apr.status = 'APPROVED';
      apr.decided_by = reviewer.employee_id;
      apr.decision_notes = notes;
      apr.peer_review_confirmed = payload.peer_review_confirmed;
      apr.decided_at = now;

      req.status = 'APPROVED';
      req.reviewer_id = reviewer.employee_id;
      req.decision_notes = notes;
      req.decided_at = now;
      req.updated_at = now;

      await this.appendAudit('APPROVAL_GRANTED', reviewer.employee_id, req.request_id, {
        approval_id: approvalId,
        voice_risk: req.voice_result?.risk,
        peer_review_confirmed: payload.peer_review_confirmed,
        notes,
      });
    } else {
      apr.status = 'REJECTED';
      apr.decided_by = reviewer.employee_id;
      apr.decision_notes = notes;
      apr.decided_at = now;

      req.status = 'REJECTED';
      req.reviewer_id = reviewer.employee_id;
      req.decision_notes = notes;
      req.decided_at = now;
      req.updated_at = now;

      await this.appendAudit('APPROVAL_REJECTED', reviewer.employee_id, req.request_id, {
        approval_id: approvalId,
        notes,
      });
    }

    this.refreshApprovalExpiries();
    return { ...req };
  }

  async executeRequest(requestId: string, actorId: string): Promise<MockExecutionReceipt> {
    if (this.mode === 'live') {
      return this.liveFetch<MockExecutionReceipt>(
        `/api/v1/requests/${encodeURIComponent(requestId)}/execute`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ actor_id: actorId }),
        },
      );
    }
    await this.ensureSeeded();
    this.refreshApprovalExpiries();

    const req = this.requests.find((r) => r.request_id === requestId);
    if (!req) throw new Error('Request not found.');
    if (req.status === 'EXECUTED') {
      await this.appendAudit('EXECUTION_REUSE_BLOCKED', actorId, requestId, {});
      throw new Error('This break-glass request has already been executed (one-time use enforced).');
    }
    if (req.voice_result?.risk === 'FAIL') {
      throw new Error('Hard voice-verification failure blocks execution permanently.');
    }
    const apr = this.approvals.find(
      (a) => a.request_id === requestId && a.approval_id === req.active_approval?.approval_id,
    );
    if (!apr || apr.status !== 'APPROVED') {
      throw new Error('Valid second-device approval is required before simulated execution.');
    }
    if (remainingSeconds(apr.expires_at) === 0) {
      apr.status = 'EXPIRED';
      throw new Error('Approval expired before execution. Request a fresh 2-minute approval code.');
    }

    const now = new Date().toISOString();
    const receiptText = `[SIMULATED] Executed '${req.action_summary}' on '${req.target_resource}' for ${req.employee_id} (approved by ${apr.decided_by} via ${apr.approval_id}). No real production infrastructure was modified.`;

    apr.status = 'CONSUMED';
    apr.consumed_at = now;
    req.status = 'EXECUTED';
    req.execution_receipt = receiptText;
    req.executed_at = now;
    req.updated_at = now;

    await this.appendAudit('ACTION_EXECUTED', actorId, requestId, {
      approval_id: apr.approval_id,
      simulated: true,
      action_summary: req.action_summary,
      target_resource: req.target_resource,
    });

    this.refreshApprovalExpiries();
    return {
      request_id: req.request_id,
      approval_id: apr.approval_id,
      status: 'EXECUTED',
      simulated: true,
      action_summary: req.action_summary,
      target_resource: req.target_resource,
      execution_receipt: receiptText,
      executed_at: now,
    };
  }

  async listAuditEvents(): Promise<AuditEvent[]> {
    if (this.mode === 'live') {
      return this.liveFetch<AuditEvent[]>('/api/v1/audit');
    }
    await this.ensureSeeded();
    return [...this.auditEvents].reverse();
  }

  async verifyAuditChain(): Promise<AuditChainVerification> {
    if (this.mode === 'live') {
      return this.liveFetch<AuditChainVerification>('/api/v1/audit/verify');
    }
    await this.ensureSeeded();
    let expectedPrev = GENESIS_HASH;
    let checked = 0;
    for (const ev of this.auditEvents) {
      checked += 1;
      if (ev.prev_hash !== expectedPrev) {
        return { valid: false, checked_events: checked, broken_at_id: ev.id };
      }
      const recomputed = await this.computeEventHash(
        ev.prev_hash,
        ev.event_type,
        ev.actor_id,
        ev.request_id,
        JSON.stringify(ev.details),
        ev.created_at,
      );
      if (recomputed !== ev.event_hash) {
        return { valid: false, checked_events: checked, broken_at_id: ev.id };
      }
      expectedPrev = ev.event_hash;
    }
    return { valid: true, checked_events: checked, broken_at_id: null };
  }

  /** Local demo utility: tamper with the oldest audit event to demonstrate hash-chain detection. */
  simulateLocalAuditTamper(): void {
    if (this.mode !== 'local' || this.auditEvents.length === 0) return;
    this.auditEvents[0] = {
      ...this.auditEvents[0],
      actor_id: 'emp_tampered_actor',
    };
  }
}

export const voiceGateEngine = new VoiceGateEngine();
