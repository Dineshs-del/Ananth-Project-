import React from 'react';
import { Canvas } from '@react-three/fiber';
import { VaultScene } from './VaultScene';
import type { GateVisualState } from './FallbackGate';

export interface LazyCanvasWrapperProps {
  state: GateVisualState;
}

export const LazyCanvasWrapper: React.FC<LazyCanvasWrapperProps> = ({ state }) => {
  return (
    <Canvas
      camera={{ position: [0, 0, 5.2], fov: 45 }}
      style={{ width: '100%', height: '100%', pointerEvents: 'none' }}
      gl={{ antialias: true, alpha: true }}
    >
      <VaultScene state={state} />
    </Canvas>
  );
};
