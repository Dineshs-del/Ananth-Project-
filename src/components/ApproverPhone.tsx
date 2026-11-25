import React, { useEffect, useState } from 'react';
import {
  ShieldCheck,
  XCircle,
  Clock,
  AlertTriangle,
  PhoneCall,
  UserCheck,
  CheckCircle2,
  Smartphone,
  Lock,
} from 'lucide-react';
import type { ApprovalRecord, Employee } from '../types';
import { Tilt3D } from './ui/Tilt3D';
import { Float } from './ui/Float';
import { CountdownRing } from './ui/CountdownRing';
import { StatusBadge } from './ui/StatusBadge';
import { PressableButton } from './ui/PressableButton';

interface ApproverPhoneProps {
  employees: Employee[];
  pendingApprovals: ApprovalRecord[];
  busy: boolean;
  onDecide: (
    approvalId: string,
    payload: {
      reviewer_id: string;
      action: 'approve' | 'reject';
      matching_number?: string;
      notes: string;
      peer_review_confirmed: boolean;
    },
  ) => Promise<void>;
  compact?: boolean;
}

export const ApproverPhone: React.FC<ApproverPhoneProps> = ({
  employees,
  pendingApprovals,
  busy,
  onDecide,
  compact = false,
}) => {
  const defaultReviewer =
    employees.find((e) => e.role === 'approver' || e.role === 'security_admin')?.employee_id ||
    employees[0]?.employee_id ||
    'emp_priya';

  const [reviewerId, setReviewerId] = useState(defaultReviewer);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pickedNumber, setPickedNumber] = useState<string>('');
  const [notes, setNotes] = useState('');
  const [peerReviewConfirmed, setPeerReviewConfirmed] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [successAnimation, setSuccessAnimation] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (pendingApprovals.length === 0) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !pendingApprovals.some((a) => a.approval_id === selectedId)) {
      setSelectedId(pendingApprovals[0].approval_id);
      setPickedNumber('');
      setNotes('');
      setPeerReviewConfirmed(false);
      setLocalError(null);
      setSuccessAnimation(false);
    }
  }, [pendingApprovals, selectedId]);

  const activeApproval =
    pendingApprovals.find((a) => a.approval_id === selectedId) || pendingApprovals[0] || null;

  const remaining = activeApproval
    ? Math.max(0, Math.floor((new Date(activeApproval.expires_at).getTime() - nowMs) / 1000))
    : 0;

  const isSamePerson =
    activeApproval !== null && reviewerId === activeApproval.requester_id;

  const selectedReviewer = employees.find((e) => e.employee_id === reviewerId);
  const isNonApproverRole =
    selectedReviewer !== undefined &&
    selectedReviewer.role !== 'approver' &&
    selectedReviewer.role !== 'security_admin';

  const handleAction = async (action: 'approve' | 'reject') => {
    if (!activeApproval) return;
    setLocalError(null);

    if (action === 'approve') {
      if (!pickedNumber) {
        setLocalError('Tap the matching 2-digit number shown on the requester screen first.');
        return;
      }
      if (activeApproval.requires_peer_review && !peerReviewConfirmed) {
        setLocalError('You must confirm out-of-band peer verification for Borderline requests.');
        return;
      }
    }

    try {
      await onDecide(activeApproval.approval_id, {
        reviewer_id: reviewerId,
        action,
        matching_number: action === 'approve' ? pickedNumber : undefined,
        notes,
        peer_review_confirmed: peerReviewConfirmed,
      });

      if (action === 'approve') {
        setSuccessAnimation(true);
        setTimeout(() => {
          setSuccessAnimation(false);
          setPickedNumber('');
          setNotes('');
          setPeerReviewConfirmed(false);
        }, 1200);
      } else {
        setPickedNumber('');
        setNotes('');
        setPeerReviewConfirmed(false);
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Approval action failed.');
    }
  };

  const phoneContent = (
    <div
      data-testid="approver-phone"
      className="relative w-full max-w-[390px] mx-auto rounded-[46px] p-3.5 bg-gradient-to-b from-slate-800 via-slate-900 to-slate-950 border-4 border-slate-700/80 shadow-[0_25px_60px_-15px_rgba(0,0,0,0.8),0_0_35px_rgba(6,182,212,0.15)] ring-1 ring-white/10 select-none overflow-hidden"
    >
      {/* Phone chassis top speaker / notch */}
      <div className="absolute top-5 inset-x-0 flex justify-center z-30 pointer-events-none">
        <div className="w-28 h-4 rounded-full bg-black/90 flex items-center justify-center gap-2 border border-white/10">
          <div className="w-2.5 h-2.5 rounded-full bg-slate-900 border border-white/20" />
          <div className="w-10 h-1.5 rounded-full bg-slate-900" />
        </div>
      </div>

      {/* Internal Phone Screen */}
      <div className="relative rounded-[36px] bg-[#070d1e] border border-white/10 overflow-hidden flex flex-col min-h-[620px] pt-8 pb-4 text-slate-100">
        {/* Status Bar */}
        <div className="px-5 pt-1 pb-3 flex items-center justify-between text-[11px] font-mono text-slate-400 border-b border-white/[0.06]">
          <div className="flex items-center gap-1.5 font-semibold text-slate-300">
            <span>9:41</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-400" />
            <span>5G SAFE-NET</span>
          </div>
        </div>

        {/* Approver Header & Selector */}
        <div className="px-4 py-3 bg-slate-950/60 border-b border-white/[0.06] space-y-2">
          <div className="flex items-center justify-between text-xs font-mono">
            <span className="text-cyan-400 font-semibold tracking-wider uppercase text-[10px]">
              APPROVER CONSOLE
            </span>
            <span className="text-slate-400 tabular-nums">
              {pendingApprovals.length} pending
            </span>
          </div>

          <div>
            <label htmlFor="approver-identity" className="block text-[10px] font-mono text-slate-400 mb-1 uppercase tracking-wider">
              Signed-in Approver Identity
            </label>
            <select
              id="approver-identity"
              data-testid="approver-identity-select"
              value={reviewerId}
              onChange={(e) => {
                setReviewerId(e.target.value);
                setLocalError(null);
              }}
              className="w-full h-8 px-2.5 text-xs font-mono bg-slate-900 border border-white/15 rounded-lg text-white focus:outline-none focus:border-cyan-400"
            >
              {employees.map((emp) => (
                <option key={emp.employee_id} value={emp.employee_id} className="bg-slate-950">
                  {emp.display_name} ({emp.role})
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Multi-Request Switcher Tabs */}
        {pendingApprovals.length > 1 && (
          <div className="px-3 py-2 border-b border-white/[0.06] flex items-center gap-1.5 overflow-x-auto bg-slate-950/40">
            {pendingApprovals.map((item) => (
              <button
                key={item.approval_id}
                type="button"
                onClick={() => {
                  setSelectedId(item.approval_id);
                  setPickedNumber('');
                  setLocalError(null);
                }}
                className={`px-2.5 py-1 text-xs font-mono rounded-md transition-all whitespace-nowrap cursor-pointer ${
                  item.approval_id === activeApproval?.approval_id
                    ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40'
                    : 'text-slate-400 hover:text-white bg-slate-900/60'
                }`}
              >
                {item.request_id}
              </button>
            ))}
          </div>
        )}

        {/* Empty State */}
        {!activeApproval ? (
          <div className="flex-1 flex flex-col items-center justify-center p-6 text-center space-y-3">
            <div className="w-14 h-14 rounded-2xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 shadow-[0_0_20px_rgba(6,182,212,0.15)]">
              <ShieldCheck className="w-7 h-7" />
            </div>
            <p className="text-sm font-semibold text-white font-display">No Pending Requests</p>
            <p className="text-xs text-slate-400 leading-relaxed max-w-xs">
              When a requester completes voice verification with PASS or BORDERLINE, an action-bound 2-minute approval appears here.
            </p>
          </div>
        ) : (
          <div className="flex-1 p-4 space-y-4 overflow-y-auto">
            {/* Top Bar: Risk badge + Countdown Ring */}
            <div className="flex items-center justify-between pb-3 border-b border-white/[0.08]">
              <div className="space-y-0.5">
                <span className="text-[10px] font-mono text-slate-400 uppercase tracking-wider block">
                  Voice Evidence
                </span>
                <StatusBadge status={activeApproval.voice_risk || 'REVIEW'} size="sm" />
              </div>

              <div className="flex items-center gap-3">
                <div className="text-right">
                  <span className="text-[10px] font-mono text-slate-400 block uppercase">Window</span>
                  <span className={`text-xs font-mono font-bold ${remaining <= 30 ? 'text-rose-400 animate-pulse' : 'text-slate-200'}`}>
                    {remaining > 0 ? `${remaining}s` : 'EXPIRED'}
                  </span>
                </div>
                <CountdownRing remainingSeconds={remaining} totalSeconds={120} size={46} strokeWidth={4} />
              </div>
            </div>

            {/* Exact Requested Operation (Action Binding) */}
            <div className="p-3.5 rounded-xl bg-slate-950/70 border border-white/10 space-y-2">
              <span className="text-[10px] font-mono text-cyan-400 uppercase tracking-wider block font-semibold">
                Bound Operation
              </span>
              <div className="text-sm font-bold text-white leading-snug">
                {activeApproval.action_summary}
              </div>
              <div className="text-xs font-mono text-cyan-300/80 truncate">
                Target: {activeApproval.target_resource}
              </div>
              <p className="text-xs text-slate-400 leading-relaxed">
                {activeApproval.justification}
              </p>
              <div className="pt-2 border-t border-white/[0.06] flex flex-wrap items-center justify-between text-[11px] text-slate-400">
                <span>By: <strong className="text-slate-200">{activeApproval.requester_name || activeApproval.requester_id}</strong></span>
                <span className="inline-flex items-center gap-1 text-cyan-300 font-mono">
                  <PhoneCall className="w-3 h-3" />
                  {activeApproval.known_callback_hint || 'Verify directory'}
                </span>
              </div>
            </div>

            {/* Hard Security Warning: Different-Person Rule */}
            {(isSamePerson || isNonApproverRole) && (
              <div className="p-3 bg-amber-950/40 border border-amber-500/40 rounded-xl text-xs text-amber-300 flex items-start gap-2.5 shadow-[0_0_15px_rgba(245,158,11,0.15)]">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-amber-400" />
                <span className="leading-relaxed font-mono text-[11px]">
                  {isSamePerson
                    ? 'DIFFERENT-PERSON RULE: You cannot approve your own request. Switch identity to an independent approver (e.g. Priya or Carol).'
                    : 'AUTHORIZATION ERROR: This identity lacks approver rights. Backend will reject.'}
                </span>
              </div>
            )}

            {/* Borderline Escalation Peer Review Banner */}
            {activeApproval.requires_peer_review && (
              <div className="p-3 bg-amber-950/30 border border-amber-500/40 rounded-xl space-y-2">
                <div className="text-xs font-bold text-amber-400 font-mono flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5" />
                  BORDERLINE ESCALATION · PEER REVIEW
                </div>
                <p className="text-xs text-slate-300 leading-relaxed">
                  Voice score was inconclusive. You must independently contact the requester on a known corporate directory number.
                </p>
                <label className="flex items-start gap-2 text-xs text-slate-200 cursor-pointer pt-1 font-medium">
                  <input
                    type="checkbox"
                    data-testid="peer-review-checkbox"
                    checked={peerReviewConfirmed}
                    onChange={(e) => setPeerReviewConfirmed(e.target.checked)}
                    className="mt-0.5 rounded border-white/20 text-cyan-500 focus:ring-cyan-400"
                  />
                  <span>I independently verified requester on known directory channel.</span>
                </label>
              </div>
            )}

            {/* 3D Chunky Number Matching Keys */}
            <div className="space-y-2">
              <div className="text-[11px] font-mono text-cyan-400 uppercase tracking-wider font-semibold">
                Tap The Matching 2-Digit Code
              </div>
              <div className="grid grid-cols-3 gap-2.5">
                {activeApproval.number_options.map((num) => {
                  const selected = pickedNumber === num;
                  return (
                    <button
                      key={num}
                      type="button"
                      data-testid={`number-option-${num}`}
                      onClick={() => {
                        setPickedNumber(num);
                        setLocalError(null);
                      }}
                      className={`h-14 rounded-2xl font-mono text-2xl font-black tabular-nums transition-all duration-150 cursor-pointer transform-gpu
                        hover:-translate-y-0.5 active:translate-y-1 active:scale-95
                        ${
                          selected
                            ? 'bg-gradient-to-tr from-cyan-500 to-blue-600 text-white shadow-[0_0_20px_rgba(6,182,212,0.5)] border-2 border-cyan-300 ring-2 ring-cyan-400/40'
                            : 'bg-slate-900/90 text-slate-200 border border-white/15 hover:border-white/30 hover:bg-slate-800'
                        }`}
                    >
                      {num}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Verification Notes */}
            <div>
              <label htmlFor="approver-notes" className="block text-[10px] font-mono text-slate-400 mb-1 uppercase tracking-wider">
                Verification Notes {activeApproval.requires_peer_review ? '(required)' : '(optional)'}
              </label>
              <input
                id="approver-notes"
                data-testid="approver-notes-input"
                type="text"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="e.g. Verified INC-402 on Ext. 4102"
                className="w-full h-8 px-2.5 text-xs font-mono bg-slate-950/80 border border-white/10 rounded-lg text-white focus:outline-none focus:border-cyan-400"
              />
            </div>

            {localError && (
              <p className="p-2.5 rounded-lg bg-rose-950/40 border border-rose-500/40 text-xs text-rose-300 font-mono leading-relaxed">
                {localError}
              </p>
            )}

            {/* Success Overlay Confirmation */}
            {successAnimation && (
              <div className="p-3 bg-emerald-950/80 border border-emerald-500 rounded-xl text-center space-y-1 animate-pulse">
                <CheckCircle2 className="w-6 h-6 text-emerald-400 mx-auto" />
                <div className="text-xs font-bold font-mono text-emerald-300">
                  DECISION RECORDED & CONSUMED
                </div>
              </div>
            )}

            {/* Action Buttons */}
            <div className="grid grid-cols-2 gap-2.5 pt-1">
              <PressableButton
                type="button"
                data-testid="reject-action-btn"
                variant="rose"
                size="md"
                disabled={busy || remaining <= 0}
                icon={XCircle}
                onClick={() => handleAction('reject')}
              >
                Reject
              </PressableButton>

              <PressableButton
                type="button"
                data-testid="approve-action-btn"
                variant="emerald"
                size="md"
                disabled={busy || remaining <= 0 || isSamePerson}
                icon={ShieldCheck}
                onClick={() => handleAction('approve')}
                glow
              >
                Approve
              </PressableButton>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  if (compact) {
    return <Tilt3D maxTilt={5}>{phoneContent}</Tilt3D>;
  }

  return (
    <Float duration={5} distance={6}>
      <Tilt3D maxTilt={6}>{phoneContent}</Tilt3D>
    </Float>
  );
};
