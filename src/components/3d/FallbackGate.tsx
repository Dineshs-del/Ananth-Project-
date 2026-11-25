import React from 'react';
import { Shield, CheckCircle2, AlertTriangle, ShieldAlert, Lock, Unlock, Radio } from 'lucide-react';

export type GateVisualState =
  | 'idle'
  | 'recording'
  | 'analyzing'
  | 'PASS'
  | 'BORDERLINE'
  | 'FAIL'
  | 'EXECUTED';

interface FallbackGateProps {
  state: GateVisualState;
  className?: string;
}

export const FallbackGate: React.FC<FallbackGateProps> = ({ state, className = '' }) => {
  const getVisuals = () => {
    switch (state) {
      case 'recording':
        return {
          ring: 'border-cyan-400 animate-pulse shadow-[0_0_30px_rgba(6,182,212,0.4)]',
          inner: 'bg-cyan-500/20 text-cyan-300',
          icon: Radio,
          label: 'CAPTURING MIC AUDIO',
          desc: 'Voice challenge waveform active',
        };
      case 'analyzing':
        return {
          ring: 'border-blue-500 animate-spin shadow-[0_0_30px_rgba(59,130,246,0.4)]',
          inner: 'bg-blue-500/20 text-blue-300',
          icon: Shield,
          label: 'ANALYZING VOICE SIGNALS',
          desc: 'Running Aletheia + Eidos neural checks',
        };
      case 'PASS':
        return {
          ring: 'border-emerald-500 shadow-[0_0_40px_rgba(16,185,129,0.5)]',
          inner: 'bg-emerald-500/20 text-emerald-300',
          icon: Unlock,
          label: 'VOICE GATE UNLOCKED',
          desc: 'Synthetic & biometric tests passed',
        };
      case 'BORDERLINE':
        return {
          ring: 'border-amber-500 animate-pulse shadow-[0_0_35px_rgba(245,158,11,0.45)]',
          inner: 'bg-amber-500/20 text-amber-300',
          icon: AlertTriangle,
          label: 'BORDERLINE — GATE LOCKED',
          desc: 'Peer verification required on second device',
        };
      case 'FAIL':
        return {
          ring: 'border-rose-500 shadow-[0_0_45px_rgba(244,63,94,0.6)] animate-bounce',
          inner: 'bg-rose-500/20 text-rose-300',
          icon: ShieldAlert,
          label: 'GATE SEALED — ACCESS BLOCKED',
          desc: 'Synthetic clone or speaker mismatch detected',
        };
      case 'EXECUTED':
        return {
          ring: 'border-emerald-400 shadow-[0_0_50px_rgba(16,185,129,0.6)]',
          inner: 'bg-emerald-500/30 text-emerald-200',
          icon: CheckCircle2,
          label: 'GATE OPEN — ACTION EXECUTED',
          desc: 'Simulated operation consumed atomically',
        };
      default:
        return {
          ring: 'border-cyan-500/30 shadow-[0_0_20px_rgba(6,182,212,0.15)]',
          inner: 'bg-slate-900/60 text-cyan-400',
          icon: Lock,
          label: 'SECURITY VAULT ARMED',
          desc: 'Ready for emergency break-glass request',
        };
    }
  };

  const v = getVisuals();
  const Icon = v.icon;

  return (
    <div
      data-testid="vault-gate-fallback"
      className={`relative flex flex-col items-center justify-center p-8 text-center select-none ${className}`}
    >
      {/* Outer Vault Rings */}
      <div className="relative w-48 h-48 sm:w-56 sm:h-56 flex items-center justify-center">
        {/* Outer segmented ring */}
        <div
          className={`absolute inset-0 rounded-full border-2 border-dashed ${v.ring} transition-all duration-700`}
        />
        {/* Middle pulsing ring */}
        <div className="absolute inset-3 rounded-full border border-white/10" />
        {/* Inner Core Portal */}
        <div
          className={`w-32 h-32 sm:w-36 sm:h-36 rounded-full flex flex-col items-center justify-center border border-white/20 backdrop-blur-xl ${v.inner} transition-all duration-500`}
        >
          <Icon className="w-10 h-10 sm:w-12 sm:h-12 mb-1 drop-shadow-md" />
        </div>
      </div>

      <div className="mt-5 space-y-1">
        <div className="font-mono text-xs tracking-widest font-bold uppercase text-white">
          {v.label}
        </div>
        <div className="text-xs text-slate-400 font-sans max-w-xs">{v.desc}</div>
      </div>
    </div>
  );
};
