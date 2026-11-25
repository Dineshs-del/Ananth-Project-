import React, { useMemo, useState } from 'react';
import { ShieldCheck, ShieldAlert, Search, Bug } from 'lucide-react';
import type {
  AuditChainVerification,
  AuditEvent,
  BackendMode,
  BreakGlassRequest,
} from '../types';

interface AuditDashboardProps {
  requests: BreakGlassRequest[];
  auditEvents: AuditEvent[];
  chainVerification: AuditChainVerification | null;
  backendMode: BackendMode;
  onSelectRequest: (req: BreakGlassRequest) => void;
  onVerifyChain: () => Promise<void>;
  onSimulateTamper: () => Promise<void>;
}

const STATUS_FILTERS = ['ALL', 'PENDING', 'APPROVED', 'EXECUTED', 'BLOCKED'] as const;

export const AuditDashboard: React.FC<AuditDashboardProps> = ({
  requests,
  auditEvents,
  chainVerification,
  backendMode,
  onSelectRequest,
  onVerifyChain,
  onSimulateTamper,
}) => {
  const [statusFilter, setStatusFilter] = useState<(typeof STATUS_FILTERS)[number]>('ALL');
  const [searchQuery, setSearchQuery] = useState('');

  const filteredRequests = useMemo(() => {
    return requests.filter((req) => {
      if (statusFilter === 'PENDING') {
        if (!['PENDING_VOICE', 'PENDING_APPROVAL', 'ESCALATED_REVIEW'].includes(req.status)) {
          return false;
        }
      } else if (statusFilter === 'BLOCKED') {
        if (!['BLOCKED', 'REJECTED', 'EXPIRED'].includes(req.status)) return false;
      } else if (statusFilter !== 'ALL' && req.status !== statusFilter) {
        return false;
      }
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        return (
          req.request_id.toLowerCase().includes(q) ||
          req.action_summary.toLowerCase().includes(q) ||
          req.target_resource.toLowerCase().includes(q) ||
          req.employee_id.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [requests, statusFilter, searchQuery]);

  const counts = useMemo(() => {
    const executed = requests.filter((r) => r.status === 'EXECUTED').length;
    const blocked = requests.filter((r) => r.status === 'BLOCKED' || r.status === 'REJECTED').length;
    const pending = requests.filter((r) =>
      ['PENDING_VOICE', 'PENDING_APPROVAL', 'ESCALATED_REVIEW'].includes(r.status),
    ).length;
    return { total: requests.length, pending, executed, blocked };
  }, [requests]);

  return (
    <div className="space-y-6">
      {/* Compact KPI Row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 bg-white border border-slate-200 rounded-xl divide-y lg:divide-y-0 lg:divide-x divide-slate-200">
        <div className="p-5">
          <div className="text-xs text-slate-500">Total Requests</div>
          <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-slate-900">
            {counts.total}
          </div>
        </div>
        <div className="p-5">
          <div className="text-xs text-slate-500">Awaiting Gate</div>
          <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-[#B45309]">
            {counts.pending}
          </div>
        </div>
        <div className="p-5">
          <div className="text-xs text-slate-500">Simulated Executed</div>
          <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-[#15803D]">
            {counts.executed}
          </div>
        </div>
        <div className="p-5">
          <div className="text-xs text-slate-500">Blocked / Rejected</div>
          <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-[#B91C1C]">
            {counts.blocked}
          </div>
        </div>
      </div>

      {/* Break-Glass Request Ledger */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 flex flex-wrap items-center justify-between gap-4">
          <h2 className="text-base font-semibold text-slate-900">Break-Glass Request Ledger</h2>

          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search action or ID..."
                className="h-8 pl-8 pr-3 text-xs bg-[#F8FAFC] border border-slate-200 rounded-md focus:outline-none focus:border-[#2563EB]"
              />
            </div>

            <div className="flex items-center gap-1 p-1 bg-slate-100 rounded-lg">
              {STATUS_FILTERS.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setStatusFilter(tab)}
                  className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
                    statusFilter === tab
                      ? 'bg-white text-slate-900 shadow-xs'
                      : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  {tab === 'ALL' ? 'All' : tab.charAt(0) + tab.slice(1).toLowerCase()}
                </button>
              ))}
            </div>
          </div>
        </div>

        {filteredRequests.length === 0 ? (
          <div className="px-6 py-10 text-center text-sm text-slate-500">
            No break-glass requests match the current filter. Create a request in Requester Gate to populate the ledger.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-200 bg-[#F8FAFC] text-[11px] font-medium text-slate-500">
                  <th className="py-2.5 px-6">Request</th>
                  <th className="py-2.5 px-4">Requester</th>
                  <th className="py-2.5 px-4">Voice Risk</th>
                  <th className="py-2.5 px-4">Status</th>
                  <th className="py-2.5 px-4">Reviewer</th>
                  <th className="py-2.5 px-6 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 text-sm">
                {filteredRequests.map((req) => (
                  <tr key={req.request_id} className="hover:bg-slate-50/80 transition-colors">
                    <td className="py-3 px-6">
                      <div className="font-medium text-slate-900">{req.action_summary}</div>
                      <div className="text-xs font-mono text-slate-500">
                        {req.request_id} · {req.target_resource}
                      </div>
                    </td>
                    <td className="py-3 px-4 text-xs text-slate-700">
                      {req.employee_name || req.employee_id}
                    </td>
                    <td className="py-3 px-4 font-mono text-xs font-semibold">
                      <span
                        className={
                          req.voice_result?.risk === 'PASS'
                            ? 'text-[#15803D]'
                            : req.voice_result?.risk === 'FAIL'
                              ? 'text-[#B91C1C]'
                              : req.voice_result?.risk === 'BORDERLINE'
                                ? 'text-[#B45309]'
                                : 'text-slate-400'
                        }
                      >
                        {req.voice_result?.risk || 'PENDING'}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-xs font-medium text-slate-800">
                      {req.status.replace(/_/g, ' ')}
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-slate-600">
                      {req.reviewer_id || '—'}
                    </td>
                    <td className="py-3 px-6 text-right">
                      <button
                        type="button"
                        onClick={() => onSelectRequest(req)}
                        className="text-xs font-medium text-[#2563EB] hover:underline whitespace-nowrap cursor-pointer"
                      >
                        Inspect
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Tamper-Evident Audit Chain */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">
              Tamper-Evident Audit Log (SHA-256 Chain)
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Every state transition is hashed with the previous entry to detect unauthorized record edits.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            {backendMode === 'local' && (
              <button
                type="button"
                onClick={onSimulateTamper}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-[#B45309] bg-white border border-amber-300 hover:bg-amber-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              >
                <Bug className="w-3.5 h-3.5" />
                Simulate Tamper
              </button>
            )}
            <button
              type="button"
              onClick={onVerifyChain}
              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-medium text-white bg-[#0F172A] hover:bg-slate-800 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              <ShieldCheck className="w-3.5 h-3.5" />
              Verify Hash Chain
            </button>
          </div>
        </div>

        {chainVerification && (
          <div
            className={`px-6 py-3 border-b border-slate-200 flex items-center justify-between gap-4 text-xs ${
              chainVerification.valid ? 'bg-emerald-50/50 text-[#15803D]' : 'bg-red-50/60 text-[#B91C1C]'
            }`}
          >
            <div className="flex items-center gap-2 font-medium">
              {chainVerification.valid ? (
                <ShieldCheck className="w-4 h-4 shrink-0" />
              ) : (
                <ShieldAlert className="w-4 h-4 shrink-0" />
              )}
              <span>
                {chainVerification.valid
                  ? `Integrity verified across ${chainVerification.checked_events} chained audit event(s).`
                  : `Tampering detected! Hash chain broke at audit event #${chainVerification.broken_at_id}.`}
              </span>
            </div>
            <span className="font-mono tabular-nums">
              {chainVerification.valid ? 'VALID' : 'BROKEN'}
            </span>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-slate-200 bg-[#F8FAFC] text-[11px] font-medium text-slate-500">
                <th className="py-2.5 px-6">#</th>
                <th className="py-2.5 px-4">Event</th>
                <th className="py-2.5 px-4">Actor</th>
                <th className="py-2.5 px-4">Request</th>
                <th className="py-2.5 px-4">Timestamp</th>
                <th className="py-2.5 px-6 text-right">Event Hash</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 text-xs">
              {auditEvents.map((ev) => (
                <tr key={ev.id} className="hover:bg-slate-50/70">
                  <td className="py-2.5 px-6 font-mono tabular-nums text-slate-500">{ev.id}</td>
                  <td className="py-2.5 px-4 font-mono font-medium text-slate-900">
                    {ev.event_type}
                  </td>
                  <td className="py-2.5 px-4 font-mono text-slate-700">{ev.actor_id}</td>
                  <td className="py-2.5 px-4 font-mono text-slate-600">{ev.request_id || '—'}</td>
                  <td className="py-2.5 px-4 font-mono tabular-nums text-slate-500">
                    {new Date(ev.created_at).toLocaleTimeString()}
                  </td>
                  <td
                    className="py-2.5 px-6 font-mono text-slate-500 text-right"
                    title={ev.event_hash}
                  >
                    {ev.event_hash.slice(0, 14)}...
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
