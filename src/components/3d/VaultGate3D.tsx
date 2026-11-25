import React, { Suspense, useEffect, useState } from 'react';
import { FallbackGate, type GateVisualState } from './FallbackGate';

const LazyCanvasWrapper = React.lazy(() =>
  import('./LazyCanvasWrapper').then((m) => ({ default: m.LazyCanvasWrapper }))
);

function isWebGLAvailable(): boolean {
  try {
    const canvas = document.createElement('canvas');
    return !!(
      window.WebGLRenderingContext &&
      (canvas.getContext('webgl') || canvas.getContext('experimental-webgl'))
    );
  } catch {
    return false;
  }
}

interface VaultGate3DProps {
  state: GateVisualState;
  className?: string;
}

export const VaultGate3D: React.FC<VaultGate3DProps> = ({ state, className = '' }) => {
  const [canRender3D, setCanRender3D] = useState(false);
  const [isReducedMotion, setIsReducedMotion] = useState(false);

  useEffect(() => {
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setIsReducedMotion(motion);
    setCanRender3D(!motion && isWebGLAvailable());
  }, []);

  if (!canRender3D || isReducedMotion) {
    return <FallbackGate state={state} className={className} />;
  }

  return (
    <div
      data-testid="vault-gate-3d"
      className={`relative w-full h-[280px] sm:h-[320px] flex items-center justify-center ${className}`}
    >
      <Suspense fallback={<FallbackGate state={state} />}>
        <LazyCanvasWrapper state={state} />
      </Suspense>

      {/* Overlay Status Badge */}
      <div className="absolute bottom-2 inset-x-0 flex flex-col items-center text-center pointer-events-none">
        <span className="font-mono text-[11px] font-bold tracking-widest uppercase text-cyan-300 drop-shadow">
          {state === 'recording'
            ? 'CAPTURING VOICE'
            : state === 'analyzing'
              ? 'RUNNING NEURAL VERIFICATION'
              : state === 'PASS'
                ? 'VOICE PASS · GATE UNLOCKED'
                : state === 'BORDERLINE'
                  ? 'BORDERLINE · PEER REVIEW REQUIRED'
                  : state === 'FAIL'
                    ? 'ACCESS BLOCKED · HARD SEAL'
                    : state === 'EXECUTED'
                      ? 'ACTION EXECUTED'
                      : 'SECURITY GATE IDLE'}
        </span>
      </div>
    </div>
  );
};
