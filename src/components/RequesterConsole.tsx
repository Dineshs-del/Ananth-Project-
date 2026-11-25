import React, { useEffect, useState } from 'react';
import { RefreshCw, CheckCircle2, AlertTriangle, ShieldAlert, Play, Smartphone } from 'lucide-react';
import type { BreakGlassRequest, Employee, MockExecutionReceipt } from '../types';
import { PRESET_OPERATIONS } from '../lib/voiceGateEngine';
import { AudioRecorder } from './AudioRecorder';

interface RequesterConsoleProps {
  employees: Employee[];
  activeRequest: BreakGlassRequest | null;
  busy: boolean;
  errorMessage: string | null;
  onCreateRequest: (input: {
    employee_id: string;
    action_summary: string;
    target_resource: string;
    justification: string;
  }) => Promise<void>;
  onRefreshChallenge: (requestId: string) => Promise<void>;
  onVerifyVoice: (requestId: string, file: File, durationSeconds: number) => Promise<void>;
  onReissueApproval: (requestId: string) => Promise<void>;
  onExecuteRequest: (requestId: string, actorId: string) => Promise<MockExecutionReceipt | null>;
  onOpenApproverPhone: () => void;
  onStartNew: () => void;
}

function formatCountdown(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

export const RequesterConsole: React.FC<RequesterConsoleProps> = ({
  employees,
  activeRequest,
  busy,
  errorMessage,
  onCreateRequest,
  onRefreshChallenge,
  onVerifyVoice,
  onReissueApproval,
  onExecuteRequest,
  onOpenApproverPhone,
  onStartNew,
}) => {
  const [employeeId, setEmployeeId] = useState(employees[0]?.employee_id || 'emp_rahul');
  const [actionSummary, setActionSummary] = useState(PRESET_OPERATIONS[0].action_summary);
  const [targetResource, setTargetResource] = useState(PRESET_OPERATIONS[0].target_resource);
  const [justification, setJustification] = useState(PRESET_OPERATIONS[0].justification);
  const [secondsLeft, setSecondsLeft] = useState(0);

  useEffect(() => {
    if (employees.length > 0 && !employees.some((e) => e.employee_id === employeeId)) {
      setEmployeeId(employees[0].employee_id);
    }
  }, [employees, employeeId]);

  useEffect(() => {
    const expiresAt = activeRequest?.active_approval?.expires_at;
    if (!expiresAt) {
      setSecondsLeft(0);
      return;
    }
    const tick = () => {
      const rem = Math.max(0, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000));
      setSecondsLeft(rem);
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [activeRequest?.active_approval?.expires_at]);

  const handleSubmitCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    await onCreateRequest({
      employee_id: employeeId,
      action_summary: actionSummary,
      target_resource: targetResource,
      justification,
    });
  };

  if (!activeRequest) {
    return (
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <div className="px-6 py-5 border-b border-slate-200">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            Initiate Break-Glass Request
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Bind an emergency operation to live voice verification and an independent second-device approval.
          </p>
        </div>

        <form onSubmit={handleSubmitCreate} className="p-6 space-y-6">
          <div>
            <p className="text-xs font-medium text-slate-500 mb-2.5">Common Emergency Presets</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {PRESET_OPERATIONS.map((preset) => {
                const selected = actionSummary === preset.action_summary;
                return (
                  <button
                    key={preset.action_summary}
                    type="button"
                    onClick={() => {
                      setActionSummary(preset.action_summary);
                      setTargetResource(preset.target_resource);
                      setJustification(preset.justification);
                    }}
                    className={`text-left px-3.5 py-2.5 rounded-lg border transition-colors cursor-pointer ${
                      selected
                        ? 'border-[#2563EB] bg-blue-50/40 text-slate-900'
                        : 'border-slate-200 hover:border-slate-300 bg-white text-slate-700'
                    }`}
                  >
                    <div className="text-sm font-medium truncate">{preset.action_summary}</div>
                    <div className="text-xs text-slate-500 font-mono truncate mt-0.5">
                      {preset.target_resource}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label htmlFor="requester-select" className="block text-xs font-medium text-slate-700 mb-1.5">
                Requesting Employee
              </label>
              <select
                id="requester-select"
                value={employeeId}
                onChange={(e) => setEmployeeId(e.target.value)}
                className="w-full h-10 px-3 text-sm bg-white border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-[#2563EB]/30 focus:border-[#2563EB]"
              >
                {employees.map((emp) => (
                  <option key={emp.employee_id} value={emp.employee_id}>
                    {emp.display_name} ({emp.employee_id})
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="target-resource" className="block text-xs font-medium text-slate-700 mb-1.5">
                Target Production Resource
              </label>
              <input
                id="target-resource"
                type="text"
                required
                minLength={2}
                value={targetResource}
                onChange={(e) => setTargetResource(e.target.value)}
                className="w-full h-10 px-3 text-sm font-mono bg-white border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-[#2563EB]/30 focus:border-[#2563EB]"
              />
            </div>
          </div>

          <div>
            <label htmlFor="action-summary" className="block text-xs font-medium text-slate-700 mb-1.5">
              Exact Emergency Action
            </label>
            <input
              id="action-summary"
              type="text"
              required
              minLength={3}
              value={actionSummary}
              onChange={(e) => setActionSummary(e.target.value)}
              className="w-full h-10 px-3 text-sm bg-white border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-[#2563EB]/30 focus:border-[#2563EB]"
            />
          </div>

          <div>
            <label htmlFor="justification" className="block text-xs font-medium text-slate-700 mb-1.5">
              Incident Justification
            </label>
            <textarea
              id="justification"
              required
              minLength={5}
              rows={3}
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-white border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-[#2563EB]/30 focus:border-[#2563EB]"
            />
          </div>

          {errorMessage && (
            <p className="text-sm text-[#B91C1C] font-medium">{errorMessage}</p>
          )}

          <div className="pt-2 flex items-center justify-between gap-4 border-t border-slate-200">
            <p className="text-xs text-slate-500">
              All operations are simulated and locked until voice checks and second-device approval pass.
            </p>
            <button
              type="submit"
              disabled={busy}
              className="px-5 py-2.5 text-sm font-medium text-white bg-[#0F172A] hover:bg-slate-800 disabled:opacity-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              {busy ? 'Creating Request...' : 'Generate Voice Challenge'}
            </button>
          </div>
        </form>
      </div>
    );
  }

  const voice = activeRequest.voice_result;
  const approval = activeRequest.active_approval;
  const isApprovalExpired =
    approval !== null && (approval.status === 'EXPIRED' || (approval.status === 'PENDING' && secondsLeft === 0));

  const riskColor =
    voice?.risk === 'PASS'
      ? 'text-[#15803D]'
      : voice?.risk === 'FAIL'
        ? 'text-[#B91C1C]'
        : 'text-[#B45309]';

  const statusColor =
    activeRequest.status === 'EXECUTED' || activeRequest.status === 'APPROVED'
      ? 'text-[#15803D]'
      : activeRequest.status === 'BLOCKED' || activeRequest.status === 'REJECTED'
        ? 'text-[#B91C1C]'
        : activeRequest.status === 'ESCALATED_REVIEW'
          ? 'text-[#B45309]'
          : 'text-slate-900';

  const canRecordVoice =
    activeRequest.status === 'PENDING_VOICE' ||
    activeRequest.status === 'ESCALATED_REVIEW' ||
    activeRequest.status === 'PENDING_APPROVAL';

  return (
    <div className="bg-white border border-slate-200 rounded-xl divide-y divide-slate-200">
      {/* Bound Action Summary */}
      <div className="p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span className="font-mono text-slate-700">{activeRequest.request_id}</span>
              <span aria-hidden="true">·</span>
              <span>{activeRequest.employee_name || activeRequest.employee_id}</span>
              <span aria-hidden="true">·</span>
              <span className={`font-semibold ${statusColor}`}>
                {activeRequest.status.replace(/_/g, ' ')}
              </span>
            </div>
            <h1 className="mt-1.5 text-xl font-semibold tracking-tight text-slate-900">
              {activeRequest.action_summary}
            </h1>
            <p className="mt-1 text-sm text-slate-600">{activeRequest.justification}</p>
          </div>
          <button
            type="button"
            onClick={onStartNew}
            className="px-3.5 py-2 text-xs font-medium text-slate-700 bg-white border border-slate-300 hover:bg-slate-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
          >
            New Request
          </button>
        </div>

        <div className="mt-4 pt-4 border-t border-slate-100 grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
          <div>
            <div className="text-slate-500">Target Resource</div>
            <div className="mt-0.5 font-mono font-medium text-slate-900 truncate">
              {activeRequest.target_resource}
            </div>
          </div>
          <div>
            <div className="text-slate-500">Action Binding Hash (SHA-256)</div>
            <div className="mt-0.5 font-mono text-slate-700 truncate" title={activeRequest.action_hash}>
              {activeRequest.action_hash.slice(0, 20)}...
            </div>
          </div>
          <div>
            <div className="text-slate-500">Known Callback Channel</div>
            <div className="mt-0.5 font-medium text-slate-900">
              {activeRequest.known_callback_hint || 'Directory lookup required'}
            </div>
          </div>
        </div>
      </div>

      {/* Step 1: Random Spoken Challenge & Voice Capture */}
      {canRecordVoice && (
        <div className="p-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">
                01. Spoken Liveness Challenge
              </h2>
              <p className="text-xs text-slate-500 mt-0.5">
                Read the full phrase aloud clearly so DetectifAI Aletheia and Eidos evaluate the same recording.
              </p>
            </div>
            <button
              type="button"
              onClick={() => onRefreshChallenge(activeRequest.request_id)}
              disabled={busy}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-700 border border-slate-300 hover:bg-slate-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              New Phrase
            </button>
          </div>

          <blockquote className="p-4 bg-[#F8FAFC] border border-slate-200 rounded-lg text-[15px] leading-relaxed font-medium text-slate-900">
            “{activeRequest.challenge_phrase}”
          </blockquote>

          <AudioRecorder
            disabled={busy}
            targetMinSeconds={4}
            actionLabel={busy ? 'Running DetectifAI Checks...' : 'Verify Voice with Aletheia + Eidos'}
            onAudioReady={(file, duration) => onVerifyVoice(activeRequest.request_id, file, duration)}
          />
        </div>
      )}

      {/* Step 2: Voice Evidence (Aletheia + Eidos) */}
      {voice && (
        <div className="p-6 space-y-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold text-slate-900">
              02. Voice Verification Evidence
            </h2>
            <div className="flex items-center gap-2 text-xs">
              <span className="text-slate-500">Policy Risk</span>
              <span aria-hidden="true" className="text-slate-300">·</span>
              <span className={`font-mono font-bold ${riskColor}`}>{voice.risk}</span>
              <span aria-hidden="true" className="text-slate-300">·</span>
              <span className="text-slate-500 tabular-nums">Attempt {activeRequest.voice_attempts}</span>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Aletheia */}
            <div className="p-4 bg-[#F8FAFC] border border-slate-200 rounded-lg space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-900">
                  Aletheia Deepfake Check
                </span>
                <span
                  className={`font-mono text-xs font-semibold ${
                    voice.aletheia?.verdict === 'real'
                      ? 'text-[#15803D]'
                      : voice.aletheia?.verdict === 'fake'
                        ? 'text-[#B91C1C]'
                        : 'text-[#B45309]'
                  }`}
                >
                  {voice.aletheia ? voice.aletheia.verdict.toUpperCase() : 'UNAVAILABLE'}
                </span>
              </div>
              {voice.aletheia ? (
                <>
                  <div className="flex items-baseline justify-between text-xs text-slate-600">
                    <span>Synthetic Indicator Score</span>
                    <span className="font-mono text-base font-semibold tabular-nums text-slate-900">
                      {voice.aletheia.score.toFixed(2)}
                    </span>
                  </div>
                  <div className="h-1.5 w-full bg-slate-200 rounded-full overflow-hidden">
                    <div
                      className={`h-full transition-all ${
                        voice.aletheia.verdict === 'fake'
                          ? 'bg-[#B91C1C]'
                          : voice.aletheia.score >= 0.4
                            ? 'bg-[#B45309]'
                            : 'bg-[#15803D]'
                      }`}
                      style={{ width: `${Math.min(100, Math.max(4, voice.aletheia.score * 100))}%` }}
                    />
                  </div>
                  <div className="flex items-center justify-between text-[11px] font-mono text-slate-500 tabular-nums">
                    <span>{voice.aletheia.duration_seconds.toFixed(1)}s audio</span>
                    <span>{voice.aletheia.operation_id}</span>
                  </div>
                </>
              ) : (
                <p className="text-xs text-slate-600">
                  Model returned no usable score — failed closed to require review.
                </p>
              )}
            </div>

            {/* Eidos */}
            <div className="p-4 bg-[#F8FAFC] border border-slate-200 rounded-lg space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-900">
                  Eidos Speaker Identity
                </span>
                <span
                  className={`font-mono text-xs font-semibold ${
                    voice.eidos?.decision === 'match'
                      ? 'text-[#15803D]'
                      : voice.eidos?.decision === 'no_match'
                        ? 'text-[#B91C1C]'
                        : 'text-[#B45309]'
                  }`}
                >
                  {voice.eidos ? voice.eidos.decision.toUpperCase() : 'UNAVAILABLE'}
                </span>
              </div>
              {voice.eidos ? (
                <>
                  <div className="flex items-baseline justify-between text-xs text-slate-600">
                    <span>Speaker Similarity</span>
                    <span className="font-mono text-base font-semibold tabular-nums text-slate-900">
                      {voice.eidos.similarity.toFixed(2)}
                    </span>
                  </div>
                  <div className="h-1.5 w-full bg-slate-200 rounded-full overflow-hidden">
                    <div
                      className={`h-full transition-all ${
                        voice.eidos.decision === 'match' ? 'bg-[#15803D]' : 'bg-[#B91C1C]'
                      }`}
                      style={{
                        width: `${Math.min(100, Math.max(4, ((voice.eidos.similarity + 1) / 2) * 100))}%`,
                      }}
                    />
                  </div>
                  <div className="flex items-center justify-between text-[11px] font-mono text-slate-500 tabular-nums">
                    <span>{voice.eidos.duration_seconds.toFixed(1)}s audio</span>
                    <span>{voice.eidos.operation_id}</span>
                  </div>
                </>
              ) : (
                <p className="text-xs text-slate-600">
                  Speaker check unavailable — failed closed until verified.
                </p>
              )}
            </div>
          </div>

          <p className="text-sm text-slate-700 leading-relaxed">{voice.reason}</p>
        </div>
      )}

      {/* Step 3: Second-Device Number-Matching Approval or Hard Block */}
      {activeRequest.status === 'BLOCKED' && (
        <div className="p-6 bg-red-50/40">
          <div className="flex items-start gap-3">
            <ShieldAlert className="w-5 h-5 text-[#B91C1C] shrink-0 mt-0.5" />
            <div className="space-y-1.5">
              <h2 className="text-sm font-semibold text-[#B91C1C]">
                03. Action Permanently Blocked by Decision Policy
              </h2>
              <p className="text-sm text-slate-700 leading-relaxed">
                A hard voice-verification failure cannot be overridden by second-device approval. No approval code was issued. Call back on a previously verified company number ({activeRequest.known_callback_hint || 'corporate directory'}) before taking any action.
              </p>
            </div>
          </div>
        </div>
      )}

      {approval && activeRequest.status !== 'BLOCKED' && (
        <div className="p-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">
                03. Second-Device Number-Matching Approval
              </h2>
              <p className="text-xs text-slate-500 mt-0.5">
                {approval.requires_peer_review
                  ? 'Borderline result: locked for independent peer review by a different authorised approver.'
                  : 'Ask a second authorised approver to tap this matching code on their phone within 2 minutes.'}
              </p>
            </div>
            {approval.status === 'PENDING' && !isApprovalExpired && (
              <button
                type="button"
                onClick={onOpenApproverPhone}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-medium text-[#2563EB] bg-blue-50/60 hover:bg-blue-50 border border-blue-200 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              >
                <Smartphone className="w-3.5 h-3.5" />
                Open Approver Phone
              </button>
            )}
          </div>

          <div className="p-5 bg-[#F8FAFC] border border-slate-200 rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-6">
            <div>
              <div className="text-xs text-slate-500">Number-Matching Code</div>
              <div className="mt-1 font-mono text-4xl font-bold tracking-wider tabular-nums text-slate-900">
                {approval.matching_number}
              </div>
              <div className="mt-1 text-xs font-mono text-slate-500">
                {approval.approval_id} · One-time use
              </div>
            </div>

            <div className="sm:text-right space-y-1.5">
              <div className="text-xs text-slate-500">Approval Status & Expiry</div>
              <div className="font-mono text-lg font-semibold tabular-nums text-slate-900">
                {approval.status === 'PENDING' && !isApprovalExpired
                  ? `Expires in ${formatCountdown(secondsLeft)}`
                  : isApprovalExpired
                    ? 'EXPIRED (2m limit)'
                    : approval.status}
              </div>
              {approval.decided_by && (
                <div className="text-xs text-slate-600">
                  Reviewed by <span className="font-mono font-medium">{approval.decided_by}</span>
                </div>
              )}
              {isApprovalExpired && (
                <button
                  type="button"
                  onClick={() => onReissueApproval(activeRequest.request_id)}
                  disabled={busy}
                  className="mt-2 inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-[#0F172A] hover:bg-slate-800 rounded-md cursor-pointer"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  Issue Fresh 2-Min Code
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Step 4: One-Time Simulated Execution */}
      {(activeRequest.status === 'APPROVED' || activeRequest.status === 'EXECUTED') && (
        <div className="p-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">
                04. Simulated Action Execution
              </h2>
              <p className="text-xs text-slate-500 mt-0.5">
                Backend verifies the action hash, 2-minute window, and consumes the approval atomically once.
              </p>
            </div>
            {activeRequest.status === 'APPROVED' && (
              <button
                type="button"
                onClick={() => onExecuteRequest(activeRequest.request_id, activeRequest.employee_id)}
                disabled={busy}
                className="inline-flex items-center gap-2 px-4 py-2.5 text-sm font-medium text-white bg-[#15803D] hover:bg-green-800 disabled:opacity-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              >
                <Play className="w-4 h-4 fill-current" />
                Execute Simulated Action
              </button>
            )}
          </div>

          {activeRequest.status === 'EXECUTED' && activeRequest.execution_receipt && (
            <div className="p-4 bg-emerald-50/50 border border-emerald-200 rounded-lg flex items-start gap-3">
              <CheckCircle2 className="w-5 h-5 text-[#15803D] shrink-0 mt-0.5" />
              <div className="space-y-1 text-sm">
                <div className="font-semibold text-[#15803D]">
                  Simulated Execution Completed (Approval Consumed)
                </div>
                <p className="font-mono text-xs text-slate-700 leading-relaxed">
                  {activeRequest.execution_receipt}
                </p>
              </div>
            </div>
          )}
        </div>
      )}

      {activeRequest.status === 'REJECTED' && (
        <div className="p-6 bg-red-50/30 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-[#B91C1C] shrink-0 mt-0.5" />
          <div className="text-sm">
            <div className="font-semibold text-[#B91C1C]">Rejected by Second-Device Approver</div>
            <p className="text-slate-600 mt-1">
              {activeRequest.decision_notes || 'Approver declined this emergency action.'}
            </p>
          </div>
        </div>
      )}

      {errorMessage && (
        <div className="px-6 py-3 bg-red-50/60 text-sm text-[#B91C1C] font-medium">
          {errorMessage}
        </div>
      )}
    </div>
  );
};
