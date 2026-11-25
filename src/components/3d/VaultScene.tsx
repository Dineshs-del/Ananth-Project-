import React, { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import type { GateVisualState } from './FallbackGate';

interface VaultSceneProps {
  state: GateVisualState;
}

export const VaultScene: React.FC<VaultSceneProps> = ({ state }) => {
  const outerRingRef = useRef<THREE.Mesh>(null);
  const midRingRef = useRef<THREE.Mesh>(null);
  const innerRingRef = useRef<THREE.Mesh>(null);
  const coreRef = useRef<THREE.Mesh>(null);

  // Colors based on state
  const getColor = () => {
    switch (state) {
      case 'PASS':
      case 'EXECUTED':
        return new THREE.Color('#10b981'); // emerald
      case 'BORDERLINE':
        return new THREE.Color('#f59e0b'); // amber
      case 'FAIL':
        return new THREE.Color('#f43f5e'); // rose
      case 'recording':
        return new THREE.Color('#06b6d4'); // cyan
      case 'analyzing':
        return new THREE.Color('#3b82f6'); // blue
      default:
        return new THREE.Color('#6366f1'); // indigo
    }
  };

  useFrame((_, delta) => {
    const speed = state === 'analyzing' ? 2.5 : state === 'recording' ? 1.8 : 0.6;

    if (outerRingRef.current) {
      outerRingRef.current.rotation.z += delta * speed * 0.4;
    }
    if (midRingRef.current) {
      midRingRef.current.rotation.z -= delta * speed * 0.6;
    }
    if (innerRingRef.current) {
      innerRingRef.current.rotation.z += delta * speed * 0.9;
    }
    if (coreRef.current) {
      if (state === 'recording') {
        const s = 1 + Math.sin(Date.now() * 0.008) * 0.12;
        coreRef.current.scale.set(s, s, s);
      } else if (state === 'PASS' || state === 'EXECUTED') {
        coreRef.current.scale.lerp(new THREE.Vector3(1.2, 1.2, 1.2), 0.1);
      } else {
        coreRef.current.scale.lerp(new THREE.Vector3(1, 1, 1), 0.1);
      }
    }
  });

  const activeColor = getColor();

  return (
    <group>
      <ambientLight intensity={0.7} />
      <pointLight position={[0, 0, 5]} intensity={1.8} color={activeColor} />
      <pointLight position={[3, 3, 2]} intensity={0.9} color="#38bdf8" />

      {/* Outer Vault Gear/Teeth Ring */}
      <mesh ref={outerRingRef} position={[0, 0, 0]}>
        <torusGeometry args={[2.2, 0.08, 16, 64]} />
        <meshStandardMaterial
          color={activeColor}
          emissive={activeColor}
          emissiveIntensity={0.4}
          metalness={0.8}
          roughness={0.2}
        />
      </mesh>

      {/* Mid Ring */}
      <mesh ref={midRingRef} position={[0, 0, 0.1]}>
        <torusGeometry args={[1.7, 0.06, 16, 32]} />
        <meshStandardMaterial
          color="#ffffff"
          emissive={activeColor}
          emissiveIntensity={0.2}
          wireframe
        />
      </mesh>

      {/* Inner Vault Gate Teeth */}
      <mesh ref={innerRingRef} position={[0, 0, 0.2]}>
        <ringGeometry args={[1.1, 1.3, 12]} />
        <meshStandardMaterial
          color={activeColor}
          emissive={activeColor}
          emissiveIntensity={0.5}
          side={THREE.DoubleSide}
        />
      </mesh>

      {/* Core Glowing Iris / Shield */}
      <mesh ref={coreRef} position={[0, 0, -0.1]}>
        <circleGeometry args={[0.9, 32]} />
        <meshStandardMaterial
          color={activeColor}
          emissive={activeColor}
          emissiveIntensity={0.8}
          transparent
          opacity={0.85}
        />
      </mesh>
    </group>
  );
};
