import React, { useMemo, useState } from 'react';
import {
  ShieldCheck,
  ShieldAlert,
  Search,
  Bug,
  List,
  Network,
  Activity,
  Layers,
  FileSpreadsheet,
} from 'lucide-react';
import type {
  AuditChainVerification,
  AuditEvent,
  BackendMode,
  BreakGlassRequest,
} from '../types';
import { GlassCard } from './ui/GlassCard';
import { PressableButton } from './ui/PressableButton';
import { StatusBadge } from './ui/StatusBadge';
import { AuditChainVisualizer } from './ui/AuditChainVisualizer';

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
  const [viewMode, setViewMode] = useState<'both' | 'chain' | 'table'>('both');
  const [isVerifying, setIsVerifying] = useState(false);

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

  const handleVerify = async () => {
    setIsVerifying(true);
    try {
      await onVerifyChain();
    } finally {
      setTimeout(() => setIsVerifying(false), 800);
    }
  };

  return (
    <div data-testid="audit-dashboard" className="space-y-6">
      {/* KPI Stats Row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <GlassCard className="p-5 space-y-1">
          <div className="text-[11px] font-mono uppercase tracking-wider text-slate-400">Total Requests</div>
          <div className="font-mono text-2xl font-bold tabular-nums text-white">
            {counts.total}
          </div>
        </GlassCard>

        <GlassCard className="p-5 space-y-1">
          <div className="text-[11px] font-mono uppercase tracking-wider text-amber-400">Awaiting Gate</div>
          <div className="font-mono text-2xl font-bold tabular-nums text-amber-300">
            {counts.pending}
          </div>
        </GlassCard>

        <GlassCard className="p-5 space-y-1">
          <div className="text-[11px] font-mono uppercase tracking-wider text-emerald-400">Simulated Executed</div>
          <div className="font-mono text-2xl font-bold tabular-nums text-emerald-300">
            {counts.executed}
          </div>
        </GlassCard>

        <GlassCard className="p-5 space-y-1">
          <div className="text-[11px] font-mono uppercase tracking-wider text-rose-400">Blocked / Rejected</div>
          <div className="font-mono text-2xl font-bold tabular-nums text-rose-300">
            {counts.blocked}
          </div>
        </GlassCard>
      </div>

      {/* Tamper-Evident SHA-256 Hash Chain Visualization */}
      <GlassCard className="p-6 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-bold text-white font-display flex items-center gap-2">
              <Network className="w-5 h-5 text-cyan-400" />
              Tamper-Evident SHA-256 Audit Chain
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Every break-glass transition is hashed with the previous event block to ensure immutability.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            {backendMode === 'local' && (
              <PressableButton
                type="button"
                data-testid="simulate-tamper-btn"
                variant="amber"
                size="sm"
                icon={Bug}
                onClick={onSimulateTamper}
              >
                Simulate Tamper
              </PressableButton>
            )}
            <PressableButton
              type="button"
              data-testid="verify-chain-btn"
              variant="primary"
              size="sm"
              icon={ShieldCheck}
              loading={isVerifying}
              onClick={handleVerify}
              glow
            >
              Verify Hash Chain
            </PressableButton>
          </div>
        </div>

        {/* Verification Banner */}
        {chainVerification && (
          <div
            data-testid="chain-verification-banner"
            className={`p-4 rounded-xl border flex items-center justify-between gap-4 text-xs font-mono ${
              chainVerification.valid
                ? 'bg-emerald-950/40 border-emerald-500/40 text-emerald-300 shadow-[0_0_20px_rgba(16,185,129,0.2)]'
                : 'bg-rose-950/50 border-rose-500/50 text-rose-300 shadow-[0_0_20px_rgba(244,63,94,0.3)] animate-pulse'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold">
              {chainVerification.valid ? (
                <ShieldCheck className="w-5 h-5 text-emerald-400 shrink-0" />
              ) : (
                <ShieldAlert className="w-5 h-5 text-rose-400 shrink-0" />
              )}
              <span>
                {chainVerification.valid
                  ? `Integrity verified across ${chainVerification.checked_events} chained audit event block(s).`
                  : `Tampering detected! Hash chain broke at audit event #${chainVerification.broken_at_id}.`}
              </span>
            </div>
            <span className="font-bold px-2.5 py-1 rounded-md bg-white/10 uppercase tracking-widest">
              {chainVerification.valid ? 'VALID' : 'CORRUPTED'}
            </span>
          </div>
        )}

        {/* 3D Visualizer Component */}
        <AuditChainVisualizer
          events={auditEvents}
          verification={chainVerification}
          onVerify={handleVerify}
          isVerifying={isVerifying}
        />
      </GlassCard>

      {/* Break-Glass Request Ledger Table */}
      <GlassCard className="overflow-hidden">
        <div className="px-6 py-4 border-b border-white/[0.08] flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <FileSpreadsheet className="w-4 h-4 text-cyan-400" />
            <h2 className="text-base font-bold text-white font-display">
              Break-Glass Request Ledger
            </h2>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                data-testid="audit-search-input"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search action or ID..."
                className="h-8 pl-8 pr-3 text-xs font-mono bg-slate-950/80 border border-white/10 rounded-lg text-white focus:outline-none focus:border-cyan-400"
              />
            </div>

            <div className="flex items-center gap-1 p-1 bg-slate-950/80 border border-white/10 rounded-lg">
              {STATUS_FILTERS.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  data-testid={`filter-tab-${tab.toLowerCase()}`}
                  onClick={() => setStatusFilter(tab)}
                  className={`px-2.5 py-1 text-xs font-medium rounded-md transition-all cursor-pointer ${
                    statusFilter === tab
                      ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30 font-semibold'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {tab === 'ALL' ? 'All' : tab.charAt(0) + tab.slice(1).toLowerCase()}
                </button>
              ))}
            </div>
          </div>
        </div>

        {filteredRequests.length === 0 ? (
          <div className="px-6 py-10 text-center text-xs text-slate-400 font-mono">
            No break-glass requests match the current filter.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/[0.08] bg-slate-950/40 text-[11px] font-mono uppercase tracking-wider text-slate-400">
                  <th className="py-3 px-6">Request</th>
                  <th className="py-3 px-4">Requester</th>
                  <th className="py-3 px-4">Voice Risk</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Reviewer</th>
                  <th className="py-3 px-6 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04] text-xs font-mono">
                {filteredRequests.map((req) => (
                  <tr
                    key={req.request_id}
                    data-testid={`audit-row-${req.request_id}`}
                    className="hover:bg-white/[0.02] transition-colors"
                  >
                    <td className="py-3 px-6">
                      <div className="font-semibold text-white font-sans text-sm">{req.action_summary}</div>
                      <div className="text-[11px] text-cyan-400/80">
                        {req.request_id} · {req.target_resource}
                      </div>
                    </td>
                    <td className="py-3 px-4 text-slate-300">
                      {req.employee_name || req.employee_id}
                    </td>
                    <td className="py-3 px-4">
                      {req.voice_result ? (
                        <StatusBadge status={req.voice_result.risk} size="sm" />
                      ) : (
                        <span className="text-slate-500">PENDING</span>
                      )}
                    </td>
                    <td className="py-3 px-4">
                      <StatusBadge status={req.status} size="sm" />
                    </td>
                    <td className="py-3 px-4 text-slate-400">
                      {req.reviewer_id || '—'}
                    </td>
                    <td className="py-3 px-6 text-right">
                      <button
                        type="button"
                        data-testid={`inspect-request-${req.request_id}`}
                        onClick={() => onSelectRequest(req)}
                        className="text-cyan-400 hover:text-cyan-300 underline font-medium cursor-pointer"
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
      </GlassCard>
    </div>
  );
};
