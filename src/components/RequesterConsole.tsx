import React, { useEffect, useState } from 'react';
import {
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  ShieldAlert,
  Play,
  Smartphone,
  Shield,
  Activity,
  KeyRound,
  FileCheck2,
  Lock,
} from 'lucide-react';
import type { BreakGlassRequest, Employee, MockExecutionReceipt } from '../types';
import { PRESET_OPERATIONS } from '../lib/voiceGateEngine';
import { AudioRecorder } from './AudioRecorder';
import { VaultGate3D } from './3d/VaultGate3D';
import type { GateVisualState } from './3d/FallbackGate';
import { GlassCard } from './ui/GlassCard';
import { Tilt3D } from './ui/Tilt3D';
import { Float } from './ui/Float';
import { PressableButton } from './ui/PressableButton';
import { StatusBadge } from './ui/StatusBadge';
import { CountdownRing } from './ui/CountdownRing';

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

const STAGES = [
  { id: 'request', label: '1. Request', desc: 'Action Bound' },
  { id: 'challenge', label: '2. Challenge', desc: 'Random Phrase' },
  { id: 'voice', label: '3. Voice Gate', desc: 'Neural Check' },
  { id: 'approval', label: '4. 2nd Device', desc: 'Number Match' },
  { id: 'execution', label: '5. Execution', desc: 'Simulated' },
];

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
  const [phraseFlipped, setPhraseFlipped] = useState(false);

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

  const handleRefreshPhrase = async (reqId: string) => {
    setPhraseFlipped(true);
    setTimeout(() => setPhraseFlipped(false), 500);
    await onRefreshChallenge(reqId);
  };

  // Determine gate visual state
  const getGateState = (): GateVisualState => {
    if (busy) return 'analyzing';
    if (!activeRequest) return 'idle';
    if (activeRequest.status === 'EXECUTED') return 'EXECUTED';
    if (activeRequest.status === 'BLOCKED') return 'FAIL';
    if (activeRequest.voice_result?.risk === 'FAIL') return 'FAIL';
    if (activeRequest.voice_result?.risk === 'BORDERLINE') return 'BORDERLINE';
    if (activeRequest.voice_result?.risk === 'PASS') return 'PASS';
    return 'idle';
  };

  // Determine current timeline stage index
  const getActiveStageIndex = (): number => {
    if (!activeRequest) return 0;
    if (activeRequest.status === 'EXECUTED') return 4;
    if (activeRequest.status === 'APPROVED') return 4;
    if (activeRequest.active_approval) return 3;
    if (activeRequest.voice_result) return 2;
    if (activeRequest.challenge_phrase) return 1;
    return 0;
  };

  const stageIdx = getActiveStageIndex();

  if (!activeRequest) {
    return (
      <div data-testid="requester-console" className="space-y-6">
        {/* Hero 3D Gate in Idle state */}
        <GlassCard className="p-6 text-center overflow-hidden">
          <VaultGate3D state="idle" />
          <div className="max-w-md mx-auto -mt-2 space-y-2">
            <h1 className="text-2xl font-bold tracking-tight text-white font-display">
              Initiate Break-Glass Request
            </h1>
            <p className="text-xs text-slate-400 leading-relaxed">
              Bind an emergency operation to cryptographic challenge-response, neural deepfake inspection, and an independent second-device approval.
            </p>
          </div>
        </GlassCard>

        {/* Request Form */}
        <GlassCard className="p-6 sm:p-8 space-y-6">
          <form onSubmit={handleSubmitCreate} data-testid="create-request-form" className="space-y-6">
            <div>
              <p className="text-xs font-mono uppercase tracking-wider text-cyan-400 mb-3 flex items-center gap-2">
                <KeyRound className="w-4 h-4" />
                Emergency Operations Presets
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {PRESET_OPERATIONS.map((preset, idx) => {
                  const selected = actionSummary === preset.action_summary;
                  return (
                    <button
                      key={preset.action_summary}
                      type="button"
                      data-testid={`preset-btn-${idx}`}
                      onClick={() => {
                        setActionSummary(preset.action_summary);
                        setTargetResource(preset.target_resource);
                        setJustification(preset.justification);
                      }}
                      className={`text-left p-3.5 rounded-xl border transition-all cursor-pointer ${
                        selected
                          ? 'border-cyan-400 bg-cyan-500/15 shadow-[0_0_15px_rgba(6,182,212,0.25)] text-white'
                          : 'border-white/10 hover:border-white/20 bg-slate-900/40 text-slate-300'
                      }`}
                    >
                      <div className="text-sm font-semibold truncate">{preset.action_summary}</div>
                      <div className="text-xs text-cyan-400/80 font-mono truncate mt-0.5">
                        {preset.target_resource}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label htmlFor="requester-select" className="block text-xs font-medium text-slate-300 mb-1.5 font-mono">
                  Requesting Identity
                </label>
                <select
                  id="requester-select"
                  data-testid="requester-select"
                  value={employeeId}
                  onChange={(e) => setEmployeeId(e.target.value)}
                  className="w-full h-10 px-3 text-sm bg-slate-950/80 border border-white/10 rounded-xl text-white focus:outline-none focus:border-cyan-400"
                >
                  {employees.map((emp) => (
                    <option key={emp.employee_id} value={emp.employee_id} className="bg-slate-900">
                      {emp.display_name} ({emp.employee_id})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label htmlFor="target-resource" className="block text-xs font-medium text-slate-300 mb-1.5 font-mono">
                  Target Production Resource
                </label>
                <input
                  id="target-resource"
                  data-testid="target-resource-input"
                  type="text"
                  required
                  minLength={2}
                  value={targetResource}
                  onChange={(e) => setTargetResource(e.target.value)}
                  className="w-full h-10 px-3 text-sm font-mono bg-slate-950/80 border border-white/10 rounded-xl text-white focus:outline-none focus:border-cyan-400"
                />
              </div>
            </div>

            <div>
              <label htmlFor="action-summary" className="block text-xs font-medium text-slate-300 mb-1.5 font-mono">
                Exact Emergency Action
              </label>
              <input
                id="action-summary"
                data-testid="action-summary-input"
                type="text"
                required
                minLength={3}
                value={actionSummary}
                onChange={(e) => setActionSummary(e.target.value)}
                className="w-full h-10 px-3 text-sm bg-slate-950/80 border border-white/10 rounded-xl text-white focus:outline-none focus:border-cyan-400"
              />
            </div>

            <div>
              <label htmlFor="justification" className="block text-xs font-medium text-slate-300 mb-1.5 font-mono">
                Incident Justification
              </label>
              <textarea
                id="justification"
                data-testid="justification-input"
                required
                minLength={5}
                rows={3}
                value={justification}
                onChange={(e) => setJustification(e.target.value)}
                className="w-full px-3 py-2 text-sm bg-slate-950/80 border border-white/10 rounded-xl text-white focus:outline-none focus:border-cyan-400"
              />
            </div>

            {errorMessage && (
              <p className="text-xs font-mono text-rose-400 p-3 rounded-lg bg-rose-950/30 border border-rose-500/30">
                {errorMessage}
              </p>
            )}

            <div className="pt-2 flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-t border-white/[0.08]">
              <span className="text-xs text-slate-400 font-mono">
                Simulated execution · Strictly fail-closed
              </span>
              <PressableButton
                type="submit"
                data-testid="submit-create-request"
                variant="primary"
                loading={busy}
                size="lg"
                glow
              >
                Generate Voice Challenge
              </PressableButton>
            </div>
          </form>
        </GlassCard>
      </div>
    );
  }

  const voice = activeRequest.voice_result;
  const approval = activeRequest.active_approval;
  const isApprovalExpired =
    approval !== null && (approval.status === 'EXPIRED' || (approval.status === 'PENDING' && secondsLeft === 0));

  const canRecordVoice =
    activeRequest.status === 'PENDING_VOICE' ||
    activeRequest.status === 'ESCALATED_REVIEW' ||
    activeRequest.status === 'PENDING_APPROVAL';

  return (
    <div data-testid="requester-console" className="space-y-6">
      {/* 5-Stage Stepper / Timeline */}
      <GlassCard className="p-4 sm:p-5">
        <div className="flex items-center justify-between gap-2 overflow-x-auto pb-1">
          {STAGES.map((s, idx) => {
            const isDone = idx < stageIdx;
            const isCurrent = idx === stageIdx;
            const isBlocked = activeRequest.status === 'BLOCKED' && idx >= 2;

            return (
              <div key={s.id} className="flex items-center gap-2 flex-1 min-w-[100px]">
                <div className="flex flex-col items-center flex-1 text-center">
                  <div
                    className={`w-7 h-7 rounded-full flex items-center justify-center font-mono text-xs font-bold transition-all duration-300 ${
                      isBlocked
                        ? 'bg-rose-500/20 text-rose-400 border border-rose-500/40 shadow-[0_0_10px_#f43f5e]'
                        : isDone
                          ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 shadow-[0_0_10px_#34d399]'
                          : isCurrent
                            ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-400 shadow-[0_0_12px_#38bdf8] animate-pulse'
                            : 'bg-slate-800/60 text-slate-500 border border-white/5'
                    }`}
                  >
                    {isDone ? '✓' : idx + 1}
                  </div>
                  <span className={`text-[11px] font-mono mt-1 font-semibold truncate ${isCurrent ? 'text-cyan-300' : isDone ? 'text-slate-200' : 'text-slate-500'}`}>
                    {s.label}
                  </span>
                </div>
                {idx < STAGES.length - 1 && (
                  <div className={`h-[2px] flex-1 -mt-4 transition-colors ${idx < stageIdx ? 'bg-emerald-500/60' : 'bg-slate-800'}`} />
                )}
              </div>
            );
          })}
        </div>
      </GlassCard>

      {/* Hero 3D Gate displaying dynamic live state */}
      <GlassCard className="p-6 relative overflow-hidden">
        <VaultGate3D state={getGateState()} />
      </GlassCard>

      {/* Bound Action Summary Header */}
      <GlassCard className="p-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-mono text-cyan-400">{activeRequest.request_id}</span>
              <span aria-hidden="true" className="text-slate-600">·</span>
              <span className="text-slate-300">{activeRequest.employee_name || activeRequest.employee_id}</span>
              <span aria-hidden="true" className="text-slate-600">·</span>
              <StatusBadge status={activeRequest.status} />
            </div>
            <h1 className="text-xl font-bold tracking-tight text-white font-display">
              {activeRequest.action_summary}
            </h1>
            <p className="text-xs text-slate-400 leading-relaxed">{activeRequest.justification}</p>
          </div>

          <PressableButton
            type="button"
            variant="secondary"
            size="sm"
            onClick={onStartNew}
          >
            Start New Request
          </PressableButton>
        </div>

        <div className="pt-4 border-t border-white/[0.08] grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs font-mono">
          <div>
            <div className="text-slate-500">Target Resource</div>
            <div className="text-cyan-300 font-semibold truncate mt-0.5">{activeRequest.target_resource}</div>
          </div>
          <div>
            <div className="text-slate-500">Action Hash (SHA-256)</div>
            <div className="text-slate-400 truncate mt-0.5" title={activeRequest.action_hash}>
              {activeRequest.action_hash.slice(0, 18)}...
            </div>
          </div>
          <div>
            <div className="text-slate-500">Known Callback Channel</div>
            <div className="text-slate-300 font-medium mt-0.5">
              {activeRequest.known_callback_hint || 'Directory lookup required'}
            </div>
          </div>
        </div>
      </GlassCard>

      {/* Step 1: Random Spoken Challenge with Flip Animation */}
      {canRecordVoice && (
        <GlassCard className="p-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs font-mono uppercase tracking-wider text-cyan-400 font-semibold">
                Stage 01 · Spoken Liveness Challenge
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Read the phrase aloud clearly. DetectifAI Aletheia and Eidos evaluate the same recording.
              </p>
            </div>
            <PressableButton
              type="button"
              data-testid="refresh-challenge-btn"
              variant="secondary"
              size="sm"
              icon={RefreshCw}
              onClick={() => handleRefreshPhrase(activeRequest.request_id)}
              disabled={busy}
            >
              New Phrase
            </PressableButton>
          </div>

          <Tilt3D>
            <div
              data-testid="challenge-phrase-card"
              className={`p-6 rounded-2xl bg-gradient-to-br from-slate-900/90 via-slate-950/90 to-cyan-950/20 border border-cyan-500/30 shadow-[0_0_25px_rgba(6,182,212,0.15)] transition-transform duration-500 ${
                phraseFlipped ? 'rotate-x-180 scale-95 opacity-50' : 'rotate-x-0 scale-100 opacity-100'
              }`}
            >
              <div className="text-xs font-mono text-cyan-400 uppercase tracking-widest mb-2 flex items-center justify-between">
                <span>Spoken Challenge Phrase</span>
                <span className="text-[10px] text-slate-500">Non-reusable</span>
              </div>
              <blockquote className="text-lg sm:text-xl font-display font-semibold text-white leading-relaxed tracking-wide">
                “{activeRequest.challenge_phrase}”
              </blockquote>
            </div>
          </Tilt3D>

          <AudioRecorder
            disabled={busy}
            targetMinSeconds={4}
            actionLabel={busy ? 'Running DetectifAI Checks...' : 'Verify Voice with Aletheia + Eidos'}
            onAudioReady={(file, duration) => onVerifyVoice(activeRequest.request_id, file, duration)}
          />
        </GlassCard>
      )}

      {/* Step 2: 3D Gauge / Orb Cards for Aletheia + Eidos */}
      {voice && (
        <GlassCard className="p-6 space-y-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <div className="text-xs font-mono uppercase tracking-wider text-cyan-400 font-semibold">
                Stage 02 · Voice Verification Evidence
              </div>
              <p className="text-xs text-slate-400 mt-0.5">A score is evidence, not proof.</p>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-400">Policy Verdict:</span>
              <StatusBadge status={voice.risk} size="lg" />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Aletheia Gauge Card */}
            <Tilt3D>
              <div
                data-testid="aletheia-card"
                className="p-5 rounded-2xl bg-slate-950/80 border border-white/10 hover:border-cyan-500/30 transition-all space-y-3"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-slate-300 font-semibold">
                    Aletheia Deepfake Check
                  </span>
                  <StatusBadge
                    status={
                      voice.aletheia?.verdict === 'real'
                        ? 'PASS'
                        : voice.aletheia?.verdict === 'fake'
                          ? 'FAIL'
                          : 'BORDERLINE'
                    }
                    label={voice.aletheia ? voice.aletheia.verdict.toUpperCase() : 'UNAVAILABLE'}
                    size="sm"
                  />
                </div>

                {voice.aletheia ? (
                  <>
                    <div className="flex items-baseline justify-between text-xs font-mono">
                      <span className="text-slate-400">Synthetic Indicator Score</span>
                      <span className="text-xl font-bold text-white tabular-nums">
                        {voice.aletheia.score.toFixed(2)}
                      </span>
                    </div>

                    {/* Progress Bar */}
                    <div className="h-2 w-full bg-slate-900 rounded-full overflow-hidden border border-white/5">
                      <div
                        className={`h-full transition-all duration-500 ${
                          voice.aletheia.verdict === 'fake'
                            ? 'bg-rose-500 shadow-[0_0_8px_#f43f5e]'
                            : voice.aletheia.score >= 0.4
                              ? 'bg-amber-500 shadow-[0_0_8px_#f59e0b]'
                              : 'bg-emerald-500 shadow-[0_0_8px_#10b981]'
                        }`}
                        style={{ width: `${Math.min(100, Math.max(5, voice.aletheia.score * 100))}%` }}
                      />
                    </div>

                    <div className="flex items-center justify-between text-[11px] font-mono text-slate-500 pt-1">
                      <span>{voice.aletheia.duration_seconds.toFixed(1)}s sample</span>
                      <span className="truncate max-w-[140px]" title={voice.aletheia.operation_id}>
                        {voice.aletheia.operation_id}
                      </span>
                    </div>
                  </>
                ) : (
                  <p className="text-xs text-amber-300/80 font-mono">
                    Model signal missing — failed closed.
                  </p>
                )}
              </div>
            </Tilt3D>

            {/* Eidos Gauge Card */}
            <Tilt3D>
              <div
                data-testid="eidos-card"
                className="p-5 rounded-2xl bg-slate-950/80 border border-white/10 hover:border-cyan-500/30 transition-all space-y-3"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-slate-300 font-semibold">
                    Eidos Speaker Match
                  </span>
                  <StatusBadge
                    status={
                      voice.eidos?.decision === 'match'
                        ? 'PASS'
                        : voice.eidos?.decision === 'no_match'
                          ? 'FAIL'
                          : 'BORDERLINE'
                    }
                    label={voice.eidos ? voice.eidos.decision.toUpperCase() : 'UNAVAILABLE'}
                    size="sm"
                  />
                </div>

                {voice.eidos ? (
                  <>
                    <div className="flex items-baseline justify-between text-xs font-mono">
                      <span className="text-slate-400">Speaker Similarity</span>
                      <span className="text-xl font-bold text-white tabular-nums">
                        {voice.eidos.similarity.toFixed(2)}
                      </span>
                    </div>

                    {/* Progress Bar */}
                    <div className="h-2 w-full bg-slate-900 rounded-full overflow-hidden border border-white/5">
                      <div
                        className={`h-full transition-all duration-500 ${
                          voice.eidos.decision === 'match'
                            ? 'bg-emerald-500 shadow-[0_0_8px_#10b981]'
                            : 'bg-rose-500 shadow-[0_0_8px_#f43f5e]'
                        }`}
                        style={{
                          width: `${Math.min(100, Math.max(5, ((voice.eidos.similarity + 1) / 2) * 100))}%`,
                        }}
                      />
                    </div>

                    <div className="flex items-center justify-between text-[11px] font-mono text-slate-500 pt-1">
                      <span>{voice.eidos.duration_seconds.toFixed(1)}s sample</span>
                      <span className="truncate max-w-[140px]" title={voice.eidos.operation_id}>
                        {voice.eidos.operation_id}
                      </span>
                    </div>
                  </>
                ) : (
                  <p className="text-xs text-amber-300/80 font-mono">
                    Speaker signal missing — failed closed.
                  </p>
                )}
              </div>
            </Tilt3D>
          </div>

          <p className="text-xs text-slate-300 leading-relaxed font-mono p-3 rounded-xl bg-slate-950/60 border border-white/5">
            {voice.reason}
          </p>
        </GlassCard>
      )}

      {/* Hard Block Failure State */}
      {activeRequest.status === 'BLOCKED' && (
        <GlassCard variant="rose" className="p-6">
          <div className="flex items-start gap-4">
            <div className="p-3 rounded-xl bg-rose-500/20 text-rose-400 border border-rose-500/40">
              <ShieldAlert className="w-6 h-6" />
            </div>
            <div className="space-y-1.5">
              <h2 className="text-base font-bold text-rose-300 font-display">
                Action Permanently Blocked by Policy
              </h2>
              <p className="text-xs text-slate-300 leading-relaxed">
                A hard voice-verification failure can never be overridden by second-device approval. No approval code was issued.
                Call back on the verified company number ({activeRequest.known_callback_hint || 'corporate directory'}) before taking any emergency action.
              </p>
            </div>
          </div>
        </GlassCard>
      )}

      {/* Step 3: Second-Device Number-Matching Approval */}
      {approval && activeRequest.status !== 'BLOCKED' && (
        <GlassCard className="p-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs font-mono uppercase tracking-wider text-cyan-400 font-semibold">
                Stage 03 · Second-Device Number-Matching Code
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                {approval.requires_peer_review
                  ? 'Borderline outcome: locked for independent peer review by a different authorized approver.'
                  : 'Ask a second authorized approver to tap this matching code on their phone within 2 minutes.'}
              </p>
            </div>
            {approval.status === 'PENDING' && !isApprovalExpired && (
              <PressableButton
                type="button"
                data-testid="open-phone-btn"
                variant="primary"
                size="sm"
                icon={Smartphone}
                onClick={onOpenApproverPhone}
              >
                Open Approver Phone
              </PressableButton>
            )}
          </div>

          <div className="p-6 rounded-2xl bg-slate-950/90 border border-cyan-500/30 flex flex-col sm:flex-row sm:items-center justify-between gap-6 shadow-[0_0_30px_rgba(6,182,212,0.15)]">
            <div>
              <div className="text-xs font-mono text-cyan-400">Match Number On Approver Device</div>
              <div
                data-testid="matching-code-display"
                className="mt-1 font-mono text-5xl font-black tracking-widest tabular-nums text-white drop-shadow-[0_0_15px_rgba(255,255,255,0.4)]"
              >
                {approval.matching_number}
              </div>
              <div className="mt-1 text-xs font-mono text-slate-500">
                {approval.approval_id} · Single-use token
              </div>
            </div>

            <div className="flex items-center gap-5 sm:text-right">
              <CountdownRing
                remainingSeconds={approval.status === 'PENDING' ? secondsLeft : 0}
                totalSeconds={120}
                size={72}
              />
              <div className="space-y-1">
                <div className="text-xs font-mono text-slate-400">Approval State</div>
                <div className="text-sm font-mono font-bold text-white">
                  {approval.status === 'PENDING' && !isApprovalExpired
                    ? `${secondsLeft}s remaining`
                    : isApprovalExpired
                      ? 'EXPIRED (120s limit)'
                      : approval.status}
                </div>
                {isApprovalExpired && (
                  <PressableButton
                    type="button"
                    data-testid="reissue-code-btn"
                    variant="primary"
                    size="sm"
                    icon={RefreshCw}
                    onClick={() => onReissueApproval(activeRequest.request_id)}
                    disabled={busy}
                  >
                    Issue Fresh Code
                  </PressableButton>
                )}
              </div>
            </div>
          </div>
        </GlassCard>
      )}

      {/* Step 4: Simulated Action Execution */}
      {(activeRequest.status === 'APPROVED' || activeRequest.status === 'EXECUTED') && (
        <GlassCard variant="emerald" className="p-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="text-xs font-mono uppercase tracking-wider text-emerald-400 font-semibold">
                Stage 04 · Simulated Action Execution
              </div>
              <p className="text-xs text-slate-300 mt-0.5">
                Backend verifies action-hash binding and consumes approval atomically.
              </p>
            </div>

            {activeRequest.status === 'APPROVED' && (
              <PressableButton
                type="button"
                data-testid="execute-action-btn"
                variant="emerald"
                size="lg"
                icon={Play}
                loading={busy}
                onClick={() => onExecuteRequest(activeRequest.request_id, activeRequest.employee_id)}
                glow
              >
                Execute Simulated Action
              </PressableButton>
            )}
          </div>

          {activeRequest.status === 'EXECUTED' && activeRequest.execution_receipt && (
            <div className="p-4 rounded-xl bg-slate-950/80 border border-emerald-500/30 flex items-start gap-3">
              <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
              <div className="space-y-1 text-xs">
                <div className="font-bold text-emerald-300 font-mono">
                  Simulated Execution Completed (Approval Consumed Atomically)
                </div>
                <p className="font-mono text-slate-300 leading-relaxed">
                  {activeRequest.execution_receipt}
                </p>
              </div>
            </div>
          )}
        </GlassCard>
      )}

      {activeRequest.status === 'REJECTED' && (
        <GlassCard variant="rose" className="p-6 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
          <div className="text-xs space-y-1">
            <div className="font-bold text-rose-300 font-mono">Rejected by Approver</div>
            <p className="text-slate-300">
              {activeRequest.decision_notes || 'Approver declined this emergency operation.'}
            </p>
          </div>
        </GlassCard>
      )}

      {errorMessage && (
        <p className="text-xs font-mono text-rose-400 p-3.5 rounded-xl bg-rose-950/40 border border-rose-500/30">
          {errorMessage}
        </p>
      )}
    </div>
  );
};
