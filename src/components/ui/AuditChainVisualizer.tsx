import React, { useState } from 'react';
import { ShieldCheck, ShieldAlert, Link, Hash, Activity } from 'lucide-react';
import type { AuditChainVerification, AuditEvent } from '../../types';

interface AuditChainVisualizerProps {
  events: AuditEvent[];
  verification: AuditChainVerification | null;
  onVerify: () => Promise<void>;
  isVerifying: boolean;
}

export const AuditChainVisualizer: React.FC<AuditChainVisualizerProps> = ({
  events,
  verification,
  onVerify,
  isVerifying,
}) => {
  // Show recent 6 events in the visual chain
  const displayEvents = events.slice(0, 6);

  return (
    <div className="space-y-4">
      {/* Visual Chain Nodes */}
      <div className="relative p-6 rounded-2xl bg-slate-950/80 border border-white/10 overflow-hidden backdrop-blur-xl">
        {/* Animated Scanning Beam during verification */}
        {isVerifying && (
          <div className="absolute inset-y-0 w-24 bg-gradient-to-r from-transparent via-cyan-400/30 to-transparent animate-[scan_1.5s_linear_infinite] pointer-events-none" />
        )}
        <style>{`
          @keyframes scan {
            0% { transform: translateX(-100%); }
            100% { transform: translateX(1000%); }
          }
        `}</style>

        <div className="flex items-center justify-between gap-4 mb-4">
          <div className="flex items-center gap-2">
            <Hash className="w-4 h-4 text-cyan-400" />
            <span className="text-xs font-mono uppercase tracking-wider text-white font-semibold">
              Cryptographic SHA-256 Hash Chain
            </span>
          </div>

          <div className="text-xs font-mono text-slate-400">
            {events.length} block(s) chained
          </div>
        </div>

        {displayEvents.length === 0 ? (
          <div className="py-8 text-center text-xs text-slate-500 font-mono">
            No events recorded yet. State changes will form chained blocks here.
          </div>
        ) : (
          <div className="flex items-center gap-3 overflow-x-auto pb-2 pt-1">
            {displayEvents.map((ev, idx) => {
              const isBroken =
                verification && !verification.valid && verification.broken_at_id === ev.id;
              const isVerified = verification && verification.valid;

              return (
                <React.Fragment key={ev.id}>
                  {/* Block Node */}
                  <div
                    data-testid={`audit-block-${ev.id}`}
                    className={`relative shrink-0 w-48 p-3.5 rounded-xl border transition-all duration-300 transform-gpu hover:-translate-y-1 ${
                      isBroken
                        ? 'bg-rose-950/50 border-rose-500/80 shadow-[0_0_20px_rgba(244,63,94,0.4)] animate-pulse'
                        : isVerified
                          ? 'bg-slate-900/90 border-emerald-500/50 shadow-[0_0_15px_rgba(16,185,129,0.2)]'
                          : 'bg-slate-900/80 border-cyan-500/30 hover:border-cyan-400'
                    }`}
                  >
                    <div className="flex items-center justify-between text-[10px] font-mono mb-1.5">
                      <span className="font-bold text-slate-300">BLOCK #{ev.id}</span>
                      <span className="text-slate-500">{new Date(ev.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                    </div>

                    <div className="text-xs font-mono font-bold text-white truncate mb-1" title={ev.event_type}>
                      {ev.event_type}
                    </div>

                    <div className="space-y-0.5 text-[10px] font-mono text-slate-400">
                      <div className="truncate">Actor: <span className="text-cyan-300">{ev.actor_id}</span></div>
                      <div className="truncate" title={ev.prev_hash}>Prev: <span className="text-slate-500">{ev.prev_hash.slice(0, 8)}...</span></div>
                      <div className="truncate font-semibold text-slate-300" title={ev.event_hash}>
                        Hash: <span className={isBroken ? 'text-rose-400' : 'text-emerald-400'}>{ev.event_hash.slice(0, 10)}...</span>
                      </div>
                    </div>
                  </div>

                  {/* Connecting Link */}
                  {idx < displayEvents.length - 1 && (
                    <div className="shrink-0 flex items-center justify-center text-slate-600">
                      <Link className={`w-4 h-4 ${isBroken ? 'text-rose-500 animate-bounce' : 'text-cyan-500/40'}`} />
                    </div>
                  )}
                </React.Fragment>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
