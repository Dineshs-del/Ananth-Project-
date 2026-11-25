import React, { useEffect, useState } from 'react';
import { ShieldCheck, XCircle, Clock, AlertTriangle, PhoneCall } from 'lucide-react';
import type { ApprovalRecord, Employee } from '../types';

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

function formatSeconds(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
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
    if (action === 'approve' && !pickedNumber) {
      setLocalError('Tap the matching 2-digit number shown on the requester screen first.');
      return;
    }
    try {
      await onDecide(activeApproval.approval_id, {
        reviewer_id: reviewerId,
        action,
        matching_number: action === 'approve' ? pickedNumber : undefined,
        notes,
        peer_review_confirmed: peerReviewConfirmed,
      });
      setPickedNumber('');
      setNotes('');
      setPeerReviewConfirmed(false);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Approval action failed.');
    }
  };

  return (
    <div className={compact ? 'w-full' : 'max-w-[420px] mx-auto'}>
      <div className="bg-[#0F172A] rounded-[28px] p-2.5 shadow-sm">
        <div className="bg-white rounded-[20px] overflow-hidden border border-slate-200 flex flex-col">
          {/* Phone Header */}
          <div className="px-4 py-3.5 bg-[#F8FAFC] border-b border-slate-200 space-y-2.5">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold text-slate-900">Approver Device</span>
              <span className="font-mono tabular-nums text-slate-500">
                {pendingApprovals.length} pending
              </span>
            </div>

            <div>
              <label htmlFor="approver-identity" className="block text-[11px] text-slate-500 mb-1">
                Signed-in Approver
              </label>
              <select
                id="approver-identity"
                value={reviewerId}
                onChange={(e) => {
                  setReviewerId(e.target.value);
                  setLocalError(null);
                }}
                className="w-full h-9 px-2.5 text-xs font-medium bg-white border border-slate-300 rounded-md focus:outline-none focus:border-[#2563EB]"
              >
                {employees.map((emp) => (
                  <option key={emp.employee_id} value={emp.employee_id}>
                    {emp.display_name} ({emp.role})
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Pending Queue Tabs if multiple */}
          {pendingApprovals.length > 1 && (
            <div className="px-3 py-2 border-b border-slate-200 flex items-center gap-1.5 overflow-x-auto bg-white">
              {pendingApprovals.map((item) => (
                <button
                  key={item.approval_id}
                  type="button"
                  onClick={() => {
                    setSelectedId(item.approval_id);
                    setPickedNumber('');
                    setLocalError(null);
                  }}
                  className={`px-2.5 py-1 text-xs font-mono rounded transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                    item.approval_id === activeApproval?.approval_id
                      ? 'bg-[#0F172A] text-white'
                      : 'text-slate-600 hover:bg-slate-100'
                  }`}
                >
                  {item.request_id}
                </button>
              ))}
            </div>
          )}

          {/* Body */}
          {!activeApproval ? (
            <div className="px-5 py-12 text-center space-y-2">
              <ShieldCheck className="w-8 h-8 text-slate-300 mx-auto" />
              <p className="text-sm font-medium text-slate-900">No Pending Break-Glass Approvals</p>
              <p className="text-xs text-slate-500 leading-relaxed">
                When a requester completes voice verification with PASS or BORDERLINE, an action-bound 2-minute approval appears here.
              </p>
            </div>
          ) : (
            <div className="p-4 space-y-4">
              {/* Countdown & Risk Summary */}
              <div className="flex items-center justify-between text-xs pb-3 border-b border-slate-200">
                <div className="flex items-center gap-1.5">
                  <span className="text-slate-500">Voice</span>
                  <span aria-hidden="true" className="text-slate-300">·</span>
                  <span
                    className={`font-mono font-bold ${
                      activeApproval.voice_risk === 'PASS'
                        ? 'text-[#15803D]'
                        : 'text-[#B45309]'
                    }`}
                  >
                    {activeApproval.voice_risk || 'REVIEW'}
                  </span>
                </div>
                <div
                  className={`inline-flex items-center gap-1 font-mono font-semibold tabular-nums ${
                    remaining <= 30 ? 'text-[#B91C1C]' : 'text-slate-900'
                  }`}
                >
                  <Clock className="w-3.5 h-3.5" />
                  {remaining > 0 ? formatSeconds(remaining) : 'EXPIRED'}
                </div>
              </div>

              {/* Exact Action Bound Details */}
              <div className="space-y-2">
                <div className="text-[11px] font-medium text-slate-500">Exact Requested Operation</div>
                <div className="text-base font-semibold text-slate-900 leading-snug">
                  {activeApproval.action_summary}
                </div>
                <div className="text-xs font-mono text-slate-600">
                  Target: {activeApproval.target_resource}
                </div>
                <div className="text-xs text-slate-600 leading-relaxed">
                  {activeApproval.justification}
                </div>
                <div className="pt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
                  <span>Requester: {activeApproval.requester_name || activeApproval.requester_id}</span>
                  <span aria-hidden="true">·</span>
                  <span className="inline-flex items-center gap-1 text-slate-700 font-medium">
                    <PhoneCall className="w-3 h-3" />
                    {activeApproval.known_callback_hint || 'Use known directory number'}
                  </span>
                </div>
              </div>

              {/* Different-Person Guard Warning */}
              {(isSamePerson || isNonApproverRole) && (
                <div className="p-3 bg-amber-50/70 border border-amber-200 rounded-lg text-xs text-[#B45309] flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                  <span>
                    {isSamePerson
                      ? 'Different-person rule active: switch to a different authorised approver (e.g. Priya or Carol) to decide this request.'
                      : 'This employee does not hold an approver role. Backend will enforce authorisation.'}
                  </span>
                </div>
              )}

              {/* Borderline Escalation Peer Review */}
              {activeApproval.requires_peer_review && (
                <div className="p-3 bg-[#F8FAFC] border border-amber-300 rounded-lg space-y-2.5">
                  <div className="text-xs font-semibold text-[#B45309]">
                    Borderline Escalation — Peer Review Required
                  </div>
                  <p className="text-xs text-slate-600 leading-relaxed">
                    Voice scores were uncertain. Confirm out-of-band on a known directory number before approving.
                  </p>
                  <label className="flex items-start gap-2 text-xs text-slate-800 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={peerReviewConfirmed}
                      onChange={(e) => setPeerReviewConfirmed(e.target.checked)}
                      className="mt-0.5 rounded border-slate-300"
                    />
                    <span>I verified the requester independently on a known channel.</span>
                  </label>
                </div>
              )}

              {/* Number Matching Buttons */}
              <div className="space-y-2">
                <div className="text-xs font-medium text-slate-700">
                  Tap the Matching Approval Number
                </div>
                <div className="grid grid-cols-3 gap-2.5">
                  {activeApproval.number_options.map((num) => {
                    const selected = pickedNumber === num;
                    return (
                      <button
                        key={num}
                        type="button"
                        onClick={() => {
                          setPickedNumber(num);
                          setLocalError(null);
                        }}
                        className={`h-12 rounded-lg font-mono text-lg font-bold tabular-nums border transition-colors cursor-pointer ${
                          selected
                            ? 'bg-[#0F172A] text-white border-[#0F172A]'
                            : 'bg-white text-slate-900 border-slate-300 hover:bg-slate-50'
                        }`}
                      >
                        {num}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Decision Notes */}
              <div>
                <label htmlFor="approver-notes" className="block text-[11px] font-medium text-slate-600 mb-1">
                  Verification Notes {activeApproval.requires_peer_review ? '(required for borderline)' : '(optional)'}
                </label>
                <input
                  id="approver-notes"
                  type="text"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="e.g. Confirmed INC-402 on Ext. 4102"
                  className="w-full h-9 px-2.5 text-xs bg-white border border-slate-300 rounded-md focus:outline-none focus:border-[#2563EB]"
                />
              </div>

              {localError && (
                <p className="text-xs font-medium text-[#B91C1C] leading-relaxed">{localError}</p>
              )}

              {/* Action Buttons */}
              <div className="grid grid-cols-2 gap-2.5 pt-1">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => handleAction('reject')}
                  className="inline-flex items-center justify-center gap-1.5 h-11 px-3 text-xs font-semibold text-[#B91C1C] bg-white border border-red-200 hover:bg-red-50 disabled:opacity-50 rounded-lg transition-colors whitespace-nowrap cursor-pointer"
                >
                  <XCircle className="w-4 h-4" />
                  Reject
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => handleAction('approve')}
                  className="inline-flex items-center justify-center gap-1.5 h-11 px-3 text-xs font-semibold text-white bg-[#15803D] hover:bg-green-800 disabled:opacity-50 rounded-lg transition-colors whitespace-nowrap cursor-pointer"
                >
                  <ShieldCheck className="w-4 h-4" />
                  Approve Action
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
