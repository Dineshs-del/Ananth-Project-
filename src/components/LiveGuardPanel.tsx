import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  Radio,
  ShieldAlert,
  ShieldCheck,
  Square,
  Volume2,
  PhoneCall,
  Activity,
} from 'lucide-react';
import type {
  BackendMode,
  BreakGlassRequest,
  Employee,
  FixtureScenario,
  LiveGuardSession,
  LiveGuardState,
} from '../types';
import { voiceGateEngine } from '../lib/voiceGateEngine';
import { GlassCard } from './ui/GlassCard';
import { PressableButton } from './ui/PressableButton';
import { StatusBadge } from './ui/StatusBadge';

const CHUNK_DURATION_MS = 7000;
const STATUS_POLL_MS = 2000;

interface LiveGuardPanelProps {
  employees: Employee[];
  requests: BreakGlassRequest[];
  backendMode: BackendMode;
  onSyncAll: () => Promise<void>;
  onCreateRequestRedirect: () => void;
}

function pickAudioMimeType(): { mimeType: string; ext: string } {
  if (typeof MediaRecorder === 'undefined') {
    return { mimeType: '', ext: '.webm' };
  }
  const candidates = [
    { mimeType: 'audio/webm;codecs=opus', ext: '.webm' },
    { mimeType: 'audio/webm', ext: '.webm' },
    { mimeType: 'audio/ogg;codecs=opus', ext: '.ogg' },
    { mimeType: 'audio/ogg', ext: '.ogg' },
  ];
  for (const c of candidates) {
    if (MediaRecorder.isTypeSupported(c.mimeType)) {
      return c;
    }
  }
  return { mimeType: '', ext: '.webm' };
}

export const LiveGuardPanel: React.FC<LiveGuardPanelProps> = ({
  employees,
  requests,
  backendMode,
  onSyncAll,
  onCreateRequestRedirect,
}) => {
  const [selectedRequestId, setSelectedRequestId] = useState<string>('');
  const [claimedEmployeeId, setClaimedEmployeeId] = useState<string>('');
  const [consentChecked, setConsentChecked] = useState(false);
  const [fixtureOverride, setFixtureOverride] = useState<FixtureScenario | ''>('');

  const [session, setSession] = useState<LiveGuardSession | null>(null);
  const [isCapturing, setIsCapturing] = useState(false);
  const [inFlightChunks, setInFlightChunks] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [noticeMessage, setNoticeMessage] = useState<string | null>(null);

  const audioStreamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const rotateTimerRef = useRef<number | null>(null);
  const capturingRef = useRef<boolean>(false);
  const activeRequestRef = useRef<string>('');
  const claimedEmployeeRef = useRef<string>('');
  const fixtureOverrideRef = useRef<FixtureScenario | ''>('');
  const dispatchedCountRef = useRef<number>(0);
  const maxChunksRef = useRef<number>(12);

  useEffect(() => {
    fixtureOverrideRef.current = fixtureOverride;
  }, [fixtureOverride]);

  useEffect(() => {
    if (!selectedRequestId && requests.length > 0) {
      const first = requests[0];
      setSelectedRequestId(first.request_id);
      if (!claimedEmployeeId) {
        setClaimedEmployeeId(first.employee_id);
      }
    } else if (!claimedEmployeeId && employees.length > 0) {
      setClaimedEmployeeId(employees[0].employee_id);
    }
  }, [requests, employees, selectedRequestId, claimedEmployeeId]);

  const teardownMedia = useCallback(() => {
    capturingRef.current = false;
    setIsCapturing(false);

    if (rotateTimerRef.current !== null) {
      window.clearTimeout(rotateTimerRef.current);
      rotateTimerRef.current = null;
    }

    const rec = recorderRef.current;
    recorderRef.current = null;
    if (rec && rec.state !== 'inactive') {
      try {
        rec.ondataavailable = null;
        rec.onstop = null;
        rec.stop();
      } catch {
        // ignore recorder shutdown race
      }
    }

    const stream = audioStreamRef.current;
    audioStreamRef.current = null;
    if (stream) {
      for (const track of stream.getTracks()) {
        track.onended = null;
        track.stop();
      }
    }
  }, []);

  const stopLiveGuard = useCallback(
    async (reasonNote?: string) => {
      const reqId = activeRequestRef.current;
      teardownMedia();
      if (!reqId) return;
      try {
        const stopped = await voiceGateEngine.stopLiveGuard(reqId);
        setSession(stopped);
        if (reasonNote) {
          setNoticeMessage(reasonNote);
        } else if (stopped.ended_reason) {
          setNoticeMessage(stopped.ended_reason);
        }
        await onSyncAll();
      } catch (err) {
        setErrorMessage(err instanceof Error ? err.message : 'Failed to stop Live Call Guard.');
      }
    },
    [onSyncAll, teardownMedia],
  );

  useEffect(() => {
    return () => {
      teardownMedia();
    };
  }, [teardownMedia]);

  useEffect(() => {
    if (!session || !session.active) return;
    const reqId = session.request_id;
    const intervalId = window.setInterval(async () => {
      try {
        const latest = await voiceGateEngine.getLiveGuardStatus(reqId);
        setSession(latest);
        if (!latest.active && capturingRef.current) {
          teardownMedia();
          setNoticeMessage(latest.ended_reason || 'Live Call Guard session ended.');
          await onSyncAll();
        }
      } catch {
        // Keep current view on transient poll hiccup
      }
    }, STATUS_POLL_MS);
    return () => window.clearInterval(intervalId);
  }, [session?.request_id, session?.active, onSyncAll, teardownMedia]);

  const uploadChunkFireAndForget = useCallback(
    (blob: Blob, ext: string, seqNum: number) => {
      if (blob.size === 0) return;
      const reqId = activeRequestRef.current;
      const empId = claimedEmployeeRef.current;
      if (!reqId || !empId) return;

      setInFlightChunks((n) => n + 1);
      void voiceGateEngine
        .uploadLiveChunk({
          request_id: reqId,
          employee_id: empId,
          blob,
          filename: `live_chunk_${seqNum}${ext}`,
          fixture_scenario: fixtureOverrideRef.current,
        })
        .then(async (res) => {
          setSession(res.session);
          if (!res.session.active && capturingRef.current) {
            teardownMedia();
            setNoticeMessage(
              res.session.ended_reason ||
                `Stopped after reaching the cap of ${res.session.max_chunks} chunks.`,
            );
          }
          if (res.session.state === 'FAIL' || !res.session.active) {
            await onSyncAll();
          }
        })
        .catch((err) => {
          setErrorMessage(
            err instanceof Error ? err.message : 'Live audio chunk upload failed.',
          );
        })
        .finally(() => {
          setInFlightChunks((n) => Math.max(0, n - 1));
        });
    },
    [onSyncAll, teardownMedia],
  );

  const startNextChunkRecorder = useCallback(
    (stream: MediaStream, mimeType: string, ext: string) => {
      if (!capturingRef.current) return;
      if (dispatchedCountRef.current >= maxChunksRef.current) {
        void stopLiveGuard(
          `Stopped automatically after reaching the cap of ${maxChunksRef.current} chunks.`,
        );
        return;
      }

      const chunks: BlobPart[] = [];
      let recorder: MediaRecorder;
      try {
        recorder = mimeType
          ? new MediaRecorder(stream, { mimeType })
          : new MediaRecorder(stream);
      } catch {
        recorder = new MediaRecorder(stream);
      }
      recorderRef.current = recorder;

      recorder.ondataavailable = (ev: BlobEvent) => {
        if (ev.data && ev.data.size > 0) {
          chunks.push(ev.data);
        }
      };

      recorder.onstop = () => {
        if (chunks.length > 0) {
          const seq = dispatchedCountRef.current + 1;
          dispatchedCountRef.current = seq;
          const outBlob = new Blob(chunks, { type: recorder.mimeType || mimeType || 'audio/webm' });
          uploadChunkFireAndForget(outBlob, ext, seq);
        }
      };

      recorder.start();

      rotateTimerRef.current = window.setTimeout(() => {
        if (!capturingRef.current) return;
        const willHitCap = dispatchedCountRef.current + 1 >= maxChunksRef.current;
        if (recorder.state !== 'inactive') {
          recorder.stop();
        }
        if (willHitCap) {
          teardownMedia();
          setNoticeMessage(
            `Reached the cap of ${maxChunksRef.current} chunks. Final chunk is being checked.`,
          );
          return;
        }
        startNextChunkRecorder(stream, mimeType, ext);
      }, CHUNK_DURATION_MS);
    },
    [stopLiveGuard, teardownMedia, uploadChunkFireAndForget],
  );

  const handleStartLiveGuard = async () => {
    setErrorMessage(null);
    setNoticeMessage(null);

    if (!selectedRequestId) {
      setErrorMessage('Select a break-glass request before starting Live Call Guard.');
      return;
    }
    if (!claimedEmployeeId) {
      setErrorMessage('Select the claimed employee (who the caller says they are).');
      return;
    }
    if (!consentChecked) {
      setErrorMessage('Tick the participant consent checkbox before starting.');
      return;
    }
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getDisplayMedia !== 'function') {
      setErrorMessage(
        'This browser does not support getDisplayMedia tab audio capture. Use Chrome or Edge on desktop.',
      );
      return;
    }

    let displayStream: MediaStream;
    try {
      displayStream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: true,
      });
    } catch (err) {
      setErrorMessage(
        err instanceof Error && err.name === 'NotAllowedError'
          ? 'Tab sharing was cancelled. Click Start Live Guard again and select the call tab with "Share tab audio" ticked.'
          : 'Could not open the browser tab audio picker.',
      );
      return;
    }

    const audioTracks = displayStream.getAudioTracks();
    const videoTracks = displayStream.getVideoTracks();

    for (const vt of videoTracks) {
      vt.stop();
    }

    if (audioTracks.length === 0) {
      setErrorMessage(
        'No audio track was captured. In the browser picker, select a specific Browser Tab and tick "Share tab audio".',
      );
      return;
    }

    const audioOnlyStream = new MediaStream(audioTracks);

    let startedSession: LiveGuardSession;
    try {
      startedSession = await voiceGateEngine.startLiveGuard({
        request_id: selectedRequestId,
        employee_id: claimedEmployeeId,
        consent_confirmed: true,
      });
    } catch (err) {
      for (const at of audioTracks) {
        at.stop();
      }
      setErrorMessage(err instanceof Error ? err.message : 'Failed to start Live Call Guard session.');
      return;
    }

    audioStreamRef.current = audioOnlyStream;
    activeRequestRef.current = startedSession.request_id;
    claimedEmployeeRef.current = claimedEmployeeId;
    dispatchedCountRef.current = startedSession.chunks_processed;
    maxChunksRef.current = startedSession.max_chunks;
    capturingRef.current = true;

    setSession(startedSession);
    setIsCapturing(true);

    audioTracks[0].onended = () => {
      if (capturingRef.current) {
        void stopLiveGuard('Browser tab sharing ended; Live Call Guard stopped.');
      }
    };

    const { mimeType, ext } = pickAudioMimeType();
    startNextChunkRecorder(audioOnlyStream, mimeType, ext);
    await onSyncAll();
  };

  const selectedRequest = requests.find((r) => r.request_id === selectedRequestId) || null;

  return (
    <div data-testid="live-guard-panel" className="space-y-6 max-w-[1100px] mx-auto">
      {/* Header & Disclosures */}
      <GlassCard className="p-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Radio className="w-5 h-5 text-cyan-400 animate-pulse" />
              <h1 className="text-xl font-bold tracking-tight text-white font-display">
                Live Call Guard Monitor
              </h1>
            </div>
            <p className="text-xs text-slate-400">
              Guard &amp; approver view only — do not share this screen with the caller. Evidence, not proof.
            </p>
          </div>

          <div className="text-right text-xs font-mono space-y-0.5">
            <div className="text-cyan-300 font-semibold">Near-live: ~7–10s delay, chunk-based</div>
            <div className="text-slate-500">Audio deleted immediately after verification</div>
          </div>
        </div>

        {/* Call rules & info cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-mono">
          <div className="flex items-start gap-2.5 p-3 rounded-xl bg-slate-950/60 border border-white/5 text-slate-300">
            <Volume2 className="w-4 h-4 text-cyan-400 shrink-0 mt-0.5" />
            <div>
              <strong className="text-white">Tab audio capture: </strong>
              Captures audio stream from shared browser tab (Google Meet / Zoom Web).
            </div>
          </div>
          <div className="flex items-start gap-2.5 p-3 rounded-xl bg-slate-950/60 border border-white/5 text-slate-300">
            <Info className="w-4 h-4 text-cyan-400 shrink-0 mt-0.5" />
            <div>
              <strong className="text-white">Decision Policy: </strong>
              PASS requires 2 consecutive PASS chunks. Any FAIL latches permanently and blocks request.
            </div>
          </div>
        </div>
      </GlassCard>

      {/* Participant Consent & Configuration */}
      <GlassCard className="p-6 space-y-5">
        <div className="p-4 rounded-xl bg-amber-950/30 border border-amber-500/40 space-y-3">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
            <div className="text-xs space-y-0.5 text-slate-200">
              <div className="font-bold text-amber-300 font-mono">PARTICIPANT CONSENT REQUIRED</div>
              <p className="text-slate-400 leading-relaxed">
                Only monitor calls where all participants have consented to automated voice security analysis. Consent is recorded in the hash-chained audit log.
              </p>
            </div>
          </div>
          <label className="flex items-center gap-2.5 text-xs text-amber-200 font-medium cursor-pointer select-none">
            <input
              type="checkbox"
              data-testid="consent-checkbox"
              checked={consentChecked}
              disabled={isCapturing}
              onChange={(e) => setConsentChecked(e.target.checked)}
              className="h-4 w-4 rounded border-amber-400/40 text-cyan-500 focus:ring-cyan-400"
            />
            <span>I confirm all call participants have explicitly consented to live voice monitoring.</span>
          </label>
        </div>

        {/* Request and Employee Selectors */}
        {requests.length === 0 ? (
          <div className="p-6 rounded-xl border border-dashed border-white/10 bg-slate-950/40 text-center space-y-3">
            <p className="text-xs text-slate-400 font-mono">
              No break-glass requests found. Create a request in Requester Gate first.
            </p>
            <PressableButton variant="primary" size="sm" onClick={onCreateRequestRedirect}>
              Create Break-Glass Request
            </PressableButton>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label htmlFor="live-request-select" className="block text-xs font-mono text-slate-300 mb-1.5 uppercase tracking-wider">
                Target Break-Glass Request
              </label>
              <select
                id="live-request-select"
                disabled={isCapturing}
                value={selectedRequestId}
                onChange={(e) => {
                  const nextId = e.target.value;
                  setSelectedRequestId(nextId);
                  const found = requests.find((r) => r.request_id === nextId);
                  if (found) setClaimedEmployeeId(found.employee_id);
                }}
                className="w-full h-10 px-3 text-xs font-mono bg-slate-950/80 border border-white/10 rounded-xl text-white focus:outline-none focus:border-cyan-400"
              >
                {requests.map((r) => (
                  <option key={r.request_id} value={r.request_id} className="bg-slate-950">
                    {r.request_id} — {r.action_summary} ({r.status})
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="live-employee-select" className="block text-xs font-mono text-slate-300 mb-1.5 uppercase tracking-wider">
                Claimed Employee Identity
              </label>
              <select
                id="live-employee-select"
                disabled={isCapturing}
                value={claimedEmployeeId}
                onChange={(e) => setClaimedEmployeeId(e.target.value)}
                className="w-full h-10 px-3 text-xs font-mono bg-slate-950/80 border border-white/10 rounded-xl text-white focus:outline-none focus:border-cyan-400"
              >
                {employees.map((emp) => (
                  <option key={emp.employee_id} value={emp.employee_id} className="bg-slate-950">
                    {emp.display_name} ({emp.employee_id})
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}

        {/* Start / Stop Bar */}
        <div className="flex flex-wrap items-center justify-between gap-4 pt-3 border-t border-white/[0.08]">
          <div className="flex items-center gap-2 text-xs font-mono text-slate-400">
            <span>Scenario override:</span>
            <select
              value={fixtureOverride}
              onChange={(e) => setFixtureOverride(e.target.value as FixtureScenario | '')}
              className="h-8 px-2 text-xs font-mono bg-slate-950 border border-white/10 rounded-md text-slate-200"
            >
              <option value="">Default</option>
              <option value="pass">Pass</option>
              <option value="borderline">Borderline</option>
              <option value="fail">Fail (triggers latch)</option>
              <option value="error">Error</option>
            </select>
          </div>

          <div>
            {!isCapturing ? (
              <PressableButton
                type="button"
                data-testid="start-live-guard-btn"
                variant="primary"
                disabled={!consentChecked || !selectedRequestId || !claimedEmployeeId}
                icon={Radio}
                onClick={handleStartLiveGuard}
                glow
              >
                Start Live Call Guard
              </PressableButton>
            ) : (
              <PressableButton
                type="button"
                data-testid="stop-live-guard-btn"
                variant="rose"
                icon={Square}
                onClick={() => void stopLiveGuard()}
              >
                Stop Live Guard
              </PressableButton>
            )}
          </div>
        </div>

        {errorMessage && (
          <p className="p-3 rounded-lg bg-rose-950/40 border border-rose-500/40 text-xs font-mono text-rose-300">
            {errorMessage}
          </p>
        )}
        {noticeMessage && (
          <p className="p-3 rounded-lg bg-slate-950/80 border border-white/10 text-xs font-mono text-slate-300">
            {noticeMessage}
          </p>
        )}
      </GlassCard>

      {/* Active Session Risk Meter & Chunk Timeline */}
      {session && (
        <GlassCard
          variant={session.state === 'FAIL' ? 'rose' : session.state === 'PASS' ? 'emerald' : 'default'}
          className="p-6 space-y-6"
        >
          {/* Metrics Row */}
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="space-y-1">
              <span className="text-[10px] font-mono uppercase tracking-wider text-slate-400 block">
                Session Monitor · {session.request_id}
              </span>
              <div className="flex items-center gap-3">
                <StatusBadge status={session.state} size="lg" />
                <span className="text-xs font-mono text-slate-400">
                  Request: <strong className="text-white">{session.request_status}</strong>
                </span>
              </div>
            </div>

            <div className="flex items-center gap-6 font-mono text-xs tabular-nums text-slate-300">
              <div>
                <span className="text-[10px] text-slate-500 block uppercase">PASS Streak</span>
                <span className="font-bold text-white">
                  {session.fail_latched ? 'LATCHED (FAIL)' : `${session.pass_streak} / 2`}
                </span>
              </div>
              <div>
                <span className="text-[10px] text-slate-500 block uppercase">Chunks</span>
                <span className="font-bold text-white">
                  {session.chunks_processed} / {session.max_chunks}
                </span>
              </div>
              <div>
                <span className="text-[10px] text-slate-500 block uppercase">Status</span>
                <span className={`font-bold ${isCapturing ? 'text-cyan-400 animate-pulse' : 'text-slate-400'}`}>
                  {isCapturing ? 'STREAMING' : 'STOPPED'}
                </span>
              </div>
            </div>
          </div>

          {/* Rolling Risk Progress Bar */}
          <div className="space-y-1.5" data-testid="guard-risk-meter">
            <div className="h-2.5 w-full bg-slate-950 rounded-full overflow-hidden border border-white/5">
              <div
                className={`h-full transition-all duration-300 ${
                  session.state === 'PASS'
                    ? 'w-full bg-emerald-400 shadow-[0_0_10px_#10b981]'
                    : session.state === 'FAIL'
                      ? 'w-full bg-rose-500 shadow-[0_0_10px_#f43f5e]'
                      : session.state === 'BORDERLINE'
                        ? 'w-2/3 bg-amber-400 shadow-[0_0_10px_#f59e0b]'
                        : 'w-1/3 bg-cyan-400'
                }`}
              />
            </div>
            <div className="flex justify-between text-[11px] font-mono text-slate-400">
              <span>{session.state} indicator</span>
              <span>Claimed: {session.claimed_employee_name || session.claimed_employee_id}</span>
            </div>
          </div>

          {/* Last Reason & Actionable Next Step */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="p-4 rounded-xl bg-slate-950/70 border border-white/5 space-y-1">
              <span className="text-[10px] font-mono text-slate-400 uppercase tracking-wider block">
                Last Evaluated Reason
              </span>
              <p className="text-xs text-slate-200 font-mono leading-relaxed">{session.last_reason}</p>
            </div>

            <div
              className={`p-4 rounded-xl border space-y-1 ${
                session.state === 'FAIL'
                  ? 'bg-rose-950/40 border-rose-500/40 text-rose-200'
                  : 'bg-slate-950/70 border-white/5 text-slate-200'
              }`}
            >
              <span className="text-[10px] font-mono uppercase tracking-wider block text-cyan-400">
                Actionable Next Step
              </span>
              <p className="text-xs font-mono leading-relaxed">{session.next_step}</p>
              {selectedRequest?.known_callback_hint && (
                <div className="pt-1 flex items-center gap-1.5 text-[11px] font-mono text-slate-400">
                  <PhoneCall className="w-3.5 h-3.5 text-cyan-400" />
                  <span>Call back: {selectedRequest.known_callback_hint}</span>
                </div>
              )}
            </div>
          </div>

          {/* Chunk History Table */}
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="font-bold text-white uppercase tracking-wider">Chunk History</span>
              <span className="text-slate-500">Zero raw audio retained</span>
            </div>

            {session.chunks.length === 0 ? (
              <div className="p-4 rounded-xl border border-dashed border-white/10 text-center text-xs font-mono text-slate-500">
                Listening for 7-second audio chunks...
              </div>
            ) : (
              <div className="border border-white/10 rounded-xl overflow-hidden">
                <table className="w-full text-left border-collapse text-xs font-mono">
                  <thead>
                    <tr className="bg-slate-950/60 border-b border-white/[0.08] text-slate-400 text-[11px]">
                      <th className="py-2.5 px-3">Chunk</th>
                      <th className="py-2.5 px-3">Time</th>
                      <th className="py-2.5 px-3">Risk</th>
                      <th className="py-2.5 px-3">Evidence Details</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04]">
                    {[...session.chunks].reverse().map((chunk) => (
                      <tr key={chunk.chunk_index} className="hover:bg-white/[0.02]">
                        <td className="py-2.5 px-3 text-cyan-400 font-bold">#{chunk.chunk_index}</td>
                        <td className="py-2.5 px-3 text-slate-400">
                          {new Date(chunk.received_at).toLocaleTimeString()}
                        </td>
                        <td className="py-2.5 px-3">
                          <StatusBadge status={chunk.risk} size="sm" />
                        </td>
                        <td className="py-2.5 px-3 text-slate-300">{chunk.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </GlassCard>
      )}
    </div>
  );
};
