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

function stateStyles(state: LiveGuardState): {
  label: string;
  barWidth: string;
  barColor: string;
  badgeClass: string;
  panelBorder: string;
} {
  switch (state) {
    case 'PASS':
      return {
        label: 'PASS (2+ consecutive chunks)',
        barWidth: 'w-full',
        barColor: 'bg-emerald-600',
        badgeClass: 'bg-emerald-50 text-emerald-800 border-emerald-200',
        panelBorder: 'border-emerald-200',
      };
    case 'BORDERLINE':
      return {
        label: 'BORDERLINE — Callback Recommended',
        barWidth: 'w-2/3',
        barColor: 'bg-amber-500',
        badgeClass: 'bg-amber-50 text-amber-800 border-amber-200',
        panelBorder: 'border-amber-200',
      };
    case 'FAIL':
      return {
        label: 'FAIL (LATCHED — Request Blocked)',
        barWidth: 'w-full',
        barColor: 'bg-red-600',
        badgeClass: 'bg-red-50 text-red-800 border-red-200',
        panelBorder: 'border-red-300',
      };
    default:
      return {
        label: 'MONITORING — Collecting Evidence',
        barWidth: 'w-1/3',
        barColor: 'bg-blue-600',
        badgeClass: 'bg-blue-50 text-blue-800 border-blue-200',
        panelBorder: 'border-slate-200',
      };
  }
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

  // Pre-select the newest request & its employee if none selected yet.
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

  // Short-interval HTTP polling keeps the guard screen synced with backend & Telegram block actions.
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
          // Fire-and-forget POST so recording never pauses waiting for the previous response.
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
          // Stop local capture cleanly; backend marks session ended once the final chunk finishes.
          teardownMedia();
          setNoticeMessage(
            `Reached the cap of ${maxChunksRef.current} chunks. Final chunk is being checked.`,
          );
          return;
        }
        // Immediately begin the next non-overlapping ~7-second chunk.
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

    // Immediately discard video tracks; only the audio track is ever used.
    for (const vt of videoTracks) {
      vt.stop();
    }

    if (audioTracks.length === 0) {
      setErrorMessage(
        'No audio track was captured. In the browser picker, select a specific Browser Tab (for example Google Meet) and make sure the "Share tab audio" checkbox is ticked.',
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
  const meter = session ? stateStyles(session.state) : stateStyles('MONITORING');

  return (
    <div className="space-y-6 max-w-[1040px]">
      {/* Header & Honesty Disclosures */}
      <div className="bg-white border border-slate-200 rounded-xl p-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Radio className="w-5 h-5 text-[#2563EB]" />
              <h1 className="text-xl font-semibold tracking-tight text-slate-900">
                Live Call Guard
              </h1>
            </div>
            <p className="mt-1 text-sm text-slate-600">
              Guard &amp; approver view only — do not share this screen with the caller.
            </p>
          </div>
          <div className="text-right text-xs text-slate-600 space-y-1">
            <div className="font-semibold text-slate-900">
              Near-live: about 7 to 10 second delay, chunk-based
            </div>
            <div>Evidence, not proof.</div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
          <div className="flex items-start gap-2.5 p-3 rounded-lg bg-slate-50 border border-slate-200 text-slate-700">
            <Volume2 className="w-4 h-4 text-slate-500 shrink-0 mt-0.5" />
            <div>
              <span className="font-semibold text-slate-900">Browser tab audio only: </span>
              Captures audio from a shared browser tab (such as Google Meet or Zoom Web). It does
              not hear installed desktop apps or phone calls.
            </div>
          </div>
          <div className="flex items-start gap-2.5 p-3 rounded-lg bg-slate-50 border border-slate-200 text-slate-700">
            <Info className="w-4 h-4 text-slate-500 shrink-0 mt-0.5" />
            <div>
              <span className="font-semibold text-slate-900">Decision rules: </span>
              PASS requires 2 consecutive PASS chunks. BORDERLINE or errors reset the streak. Any
              FAIL latches permanently for the session and blocks the request.
            </div>
          </div>
        </div>
      </div>

      {/* Consent & Session Configuration */}
      <div className="bg-white border border-slate-200 rounded-xl p-6 space-y-5">
        {/* Consent Banner */}
        <div className="p-4 rounded-lg bg-amber-50 border border-amber-200 space-y-3">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="w-4 h-4 text-amber-700 shrink-0 mt-0.5" />
            <div className="text-sm text-amber-950">
              <div className="font-semibold">Participant consent required</div>
              <p className="mt-0.5 text-xs text-amber-900">
                Only monitor calls where everyone on the call has agreed to live voice checking.
                Audio chunks are verified and deleted immediately on the server; consent is recorded
                in the hash-chained audit log.
              </p>
            </div>
          </div>
          <label className="flex items-center gap-2.5 text-xs font-medium text-amber-950 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={consentChecked}
              disabled={isCapturing}
              onChange={(e) => setConsentChecked(e.target.checked)}
              className="h-4 w-4 rounded border-amber-400 text-slate-900 focus:ring-slate-900"
            />
            <span>I confirm everyone on this call has agreed to live voice monitoring.</span>
          </label>
        </div>

        {/* Selectors */}
        {requests.length === 0 ? (
          <div className="p-4 rounded-lg border border-dashed border-slate-300 bg-slate-50 flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm text-slate-700">
              No break-glass requests exist yet. Create a request first to attach Live Call Guard.
            </div>
            <button
              type="button"
              onClick={onCreateRequestRedirect}
              className="px-3.5 py-2 text-xs font-medium text-white bg-slate-900 hover:bg-slate-800 rounded-md cursor-pointer"
            >
              Create Request
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label
                htmlFor="live-request-select"
                className="block text-xs font-medium text-slate-700"
              >
                Break-Glass Request
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
                className="w-full h-10 px-3 text-sm bg-white border border-slate-300 rounded-lg focus:outline-none focus:border-[#2563EB] disabled:bg-slate-100"
              >
                {requests.map((r) => (
                  <option key={r.request_id} value={r.request_id}>
                    {r.request_id} — {r.action_summary} ({r.status})
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <label
                htmlFor="live-employee-select"
                className="block text-xs font-medium text-slate-700"
              >
                Claimed Employee (who the caller says they are)
              </label>
              <select
                id="live-employee-select"
                disabled={isCapturing}
                value={claimedEmployeeId}
                onChange={(e) => setClaimedEmployeeId(e.target.value)}
                className="w-full h-10 px-3 text-sm bg-white border border-slate-300 rounded-lg focus:outline-none focus:border-[#2563EB] disabled:bg-slate-100"
              >
                {employees.map((emp) => (
                  <option key={emp.employee_id} value={emp.employee_id}>
                    {emp.display_name} ({emp.employee_id})
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}

        {/* Fixture Demo Override (ignored by backend when AI_MODE=live) */}
        <div className="flex flex-wrap items-center justify-between gap-4 pt-2 border-t border-slate-100">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
            <label htmlFor="live-fixture-override" className="font-medium text-slate-700">
              Next-chunk fixture scenario ({backendMode === 'live' ? 'fixture AI_MODE only' : 'Demo Engine'}):
            </label>
            <select
              id="live-fixture-override"
              value={fixtureOverride}
              onChange={(e) => setFixtureOverride(e.target.value as FixtureScenario | '')}
              className="h-8 px-2.5 text-xs bg-white border border-slate-300 rounded-md focus:outline-none focus:border-[#2563EB]"
            >
              <option value="">Default (env / sidebar)</option>
              <option value="pass">Pass</option>
              <option value="borderline">Borderline</option>
              <option value="fail">Fail (triggers latch &amp; block)</option>
              <option value="error">Error (fails closed to Borderline)</option>
            </select>
          </div>

          <div className="flex items-center gap-3">
            {!isCapturing ? (
              <button
                type="button"
                disabled={!consentChecked || !selectedRequestId || !claimedEmployeeId}
                onClick={handleStartLiveGuard}
                className="inline-flex items-center gap-2 px-4 py-2.5 text-sm font-medium text-white bg-[#0F172A] hover:bg-slate-800 disabled:bg-slate-300 disabled:cursor-not-allowed rounded-lg transition-colors cursor-pointer"
              >
                <Radio className="w-4 h-4" />
                Start Live Guard
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void stopLiveGuard()}
                className="inline-flex items-center gap-2 px-4 py-2.5 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-lg transition-colors cursor-pointer"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
                Stop Live Guard
              </button>
            )}
          </div>
        </div>

        {errorMessage && (
          <div className="p-3.5 rounded-lg bg-red-50 border border-red-200 text-xs text-red-800 flex items-start gap-2">
            <ShieldAlert className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />
            <span>{errorMessage}</span>
          </div>
        )}

        {noticeMessage && (
          <div className="p-3.5 rounded-lg bg-slate-100 border border-slate-200 text-xs text-slate-800">
            {noticeMessage}
          </div>
        )}
      </div>

      {/* Live Risk Meter, Reason, Next Step & Chunk History */}
      {session && (
        <div className={`bg-white border ${meter.panelBorder} rounded-xl p-6 space-y-6`}>
          {/* Top Metrics Row */}
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="space-y-1">
              <div className="text-xs font-medium text-slate-500">
                Guard Risk Meter — {session.request_id}
              </div>
              <div className="flex flex-wrap items-center gap-2.5">
                <span
                  className={`inline-flex items-center gap-1.5 px-3 py-1 text-xs font-semibold rounded-md border ${meter.badgeClass}`}
                >
                  {session.state === 'PASS' ? (
                    <ShieldCheck className="w-3.5 h-3.5" />
                  ) : session.state === 'FAIL' ? (
                    <ShieldAlert className="w-3.5 h-3.5" />
                  ) : (
                    <Radio className="w-3.5 h-3.5" />
                  )}
                  {meter.label}
                </span>
                <span className="text-xs font-mono text-slate-600 tabular-nums">
                  Request status: {session.request_status}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-5 text-xs font-mono tabular-nums text-slate-700">
              <div>
                <div className="text-[11px] font-sans text-slate-500">PASS Streak</div>
                <div className="font-semibold text-slate-900">
                  {session.fail_latched ? 'LOCKED (FAIL)' : `${session.pass_streak} / 2`}
                </div>
              </div>
              <div>
                <div className="text-[11px] font-sans text-slate-500">Chunks Used</div>
                <div className="font-semibold text-slate-900">
                  {session.chunks_processed} / {session.max_chunks}
                </div>
              </div>
              <div>
                <div className="text-[11px] font-sans text-slate-500">Capture</div>
                <div className="font-semibold text-slate-900">
                  {isCapturing
                    ? inFlightChunks > 0
                      ? `Live (${inFlightChunks} checking)`
                      : 'Live (7s chunks)'
                    : 'Stopped'}
                </div>
              </div>
            </div>
          </div>

          {/* Risk Meter Bar */}
          <div className="space-y-1.5">
            <div className="h-2.5 w-full bg-slate-100 rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-300 ${meter.barWidth} ${meter.barColor}`}
              />
            </div>
            <div className="flex justify-between text-[11px] text-slate-500">
              <span>Near-live (7–10s delay, chunk-based)</span>
              <span>
                Claimed caller: {session.claimed_employee_name || session.claimed_employee_id}
              </span>
            </div>
          </div>

          {/* Last Reason & Plain Next Step */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 space-y-1">
              <div className="text-xs font-semibold text-slate-900">Last Check Reason</div>
              <p className="text-sm text-slate-700 leading-relaxed">{session.last_reason}</p>
            </div>
            <div
              className={`p-4 rounded-lg border space-y-1 ${
                session.state === 'FAIL'
                  ? 'bg-red-50 border-red-200'
                  : session.state === 'BORDERLINE'
                    ? 'bg-amber-50 border-amber-200'
                    : 'bg-slate-50 border-slate-200'
              }`}
            >
              <div className="text-xs font-semibold text-slate-900">Plain Next Step</div>
              <p className="text-sm text-slate-800 leading-relaxed">{session.next_step}</p>
              {selectedRequest?.known_callback_hint && (
                <p className="text-xs text-slate-600 pt-1">
                  Known callback directory: {selectedRequest.known_callback_hint}
                </p>
              )}
            </div>
          </div>

          {/* Chunk History (no raw audio, no numeric scores for caller) */}
          <div className="space-y-2.5">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-900">Chunk History</h2>
              <span className="text-xs text-slate-500">
                Audio chunks are deleted immediately after each check.
              </span>
            </div>

            {session.chunks.length === 0 ? (
              <div className="p-4 rounded-lg border border-dashed border-slate-200 text-xs text-slate-500">
                Waiting for the first 7-second audio chunk to finish recording and return from verification...
              </div>
            ) : (
              <div className="border border-slate-200 rounded-lg overflow-hidden">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="bg-slate-50 border-b border-slate-200 text-slate-500">
                      <th className="py-2.5 px-3 font-medium">Chunk</th>
                      <th className="py-2.5 px-3 font-medium">Time</th>
                      <th className="py-2.5 px-3 font-medium">Risk</th>
                      <th className="py-2.5 px-3 font-medium">Reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {[...session.chunks].reverse().map((chunk) => (
                      <tr key={chunk.chunk_index} className="bg-white">
                        <td className="py-2.5 px-3 font-mono tabular-nums text-slate-700 whitespace-nowrap">
                          #{chunk.chunk_index}
                        </td>
                        <td className="py-2.5 px-3 font-mono tabular-nums text-slate-500 whitespace-nowrap">
                          {new Date(chunk.received_at).toLocaleTimeString()}
                        </td>
                        <td className="py-2.5 px-3 whitespace-nowrap">
                          <span
                            className={`inline-flex items-center gap-1 px-2 py-0.5 font-semibold rounded border ${
                              chunk.risk === 'PASS'
                                ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
                                : chunk.risk === 'FAIL'
                                  ? 'bg-red-50 text-red-800 border-red-200'
                                  : 'bg-amber-50 text-amber-800 border-amber-200'
                            }`}
                          >
                            {chunk.risk === 'PASS' && <CheckCircle2 className="w-3 h-3" />}
                            {chunk.risk}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 text-slate-700">{chunk.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
