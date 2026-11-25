import React, { useEffect, useRef, useState } from 'react';
import { Mic, Square, Upload, Play, RotateCcw, FileAudio, CheckCircle2 } from 'lucide-react';
import { PressableButton } from './ui/PressableButton';

interface AudioRecorderProps {
  onAudioReady: (file: File, durationSeconds: number) => void;
  disabled?: boolean;
  targetMinSeconds?: number;
  actionLabel?: string;
}

/** Generate a tiny valid 4.8s WAV clip in memory for mic-less demo environments. */
function buildDemoWavFile(durationSeconds = 4.8): File {
  const sampleRate = 8000;
  const numSamples = Math.floor(sampleRate * durationSeconds);
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);

  const writeStr = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) {
      view.setUint8(offset + i, value.charCodeAt(i));
    }
  };

  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + numSamples * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, numSamples * 2, true);

  for (let i = 0; i < numSamples; i++) {
    const t = i / sampleRate;
    const envelope = Math.sin(Math.PI * Math.min(1, t / durationSeconds));
    const sample = Math.sin(2 * Math.PI * 220 * t) * 0.12 * envelope;
    view.setInt16(44 + i * 2, Math.floor(sample * 32767), true);
  }

  return new File([buffer], 'challenge_sample_4_8s.wav', { type: 'audio/wav' });
}

export const AudioRecorder: React.FC<AudioRecorderProps> = ({
  onAudioReady,
  disabled = false,
  targetMinSeconds = 4,
  actionLabel = 'Submit Recording for Verification',
}) => {
  const [isRecording, setIsRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [recordedFile, setRecordedFile] = useState<File | null>(null);
  const [recordedUrl, setRecordedUrl] = useState<string | null>(null);
  const [recordedDuration, setRecordedDuration] = useState(0);
  const [micError, setMicError] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);
  const startTimeRef = useRef<number>(0);
  const chunksRef = useRef<Blob[]>([]);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const cleanupMedia = () => {
    if (animFrameRef.current !== null) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    analyserRef.current = null;
  };

  useEffect(() => {
    return () => {
      cleanupMedia();
      if (recordedUrl) URL.revokeObjectURL(recordedUrl);
    };
  }, [recordedUrl]);

  const drawWaveform = () => {
    const canvas = canvasRef.current;
    const analyser = analyserRef.current;
    if (!canvas || !analyser) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    const render = () => {
      if (!analyserRef.current) return;
      analyser.getByteFrequencyData(dataArray);

      const { width, height } = canvas;
      ctx.clearRect(0, 0, width, height);

      const barCount = 48;
      const step = Math.max(1, Math.floor(bufferLength / barCount));
      const barWidth = width / barCount;

      for (let i = 0; i < barCount; i++) {
        const value = dataArray[i * step] / 255;
        const barHeight = Math.max(4, value * (height - 8));
        const x = i * barWidth + 2;
        const y = (height - barHeight) / 2;

        if (value > 0.15) {
          ctx.fillStyle = '#38bdf8'; // bright cyan
          ctx.shadowColor = '#06b6d4';
          ctx.shadowBlur = 6;
        } else {
          ctx.fillStyle = 'rgba(255, 255, 255, 0.12)';
          ctx.shadowBlur = 0;
        }
        ctx.fillRect(x, y, Math.max(2, barWidth - 3), barHeight);
      }

      animFrameRef.current = requestAnimationFrame(render);
    };

    animFrameRef.current = requestAnimationFrame(render);
  };

  const startRecording = async () => {
    setMicError(null);
    if (recordedUrl) {
      URL.revokeObjectURL(recordedUrl);
      setRecordedUrl(null);
    }
    setRecordedFile(null);

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setMicError('Microphone capture is unavailable in this browser. Upload an audio clip or use a sample recording.');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const audioCtx = new AudioContext();
      audioCtxRef.current = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 128;
      source.connect(analyser);
      analyserRef.current = analyser;

      const mimeType = MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : MediaRecorder.isTypeSupported('audio/ogg')
          ? 'audio/ogg'
          : '';
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);

      chunksRef.current = [];
      recorder.ondataavailable = (ev) => {
        if (ev.data.size > 0) chunksRef.current.push(ev.data);
      };

      recorder.onstop = () => {
        const finalDuration = Math.max(0.5, (Date.now() - startTimeRef.current) / 1000);
        const type = recorder.mimeType || 'audio/webm';
        const ext = type.includes('ogg') ? 'ogg' : type.includes('mp4') ? 'm4a' : 'webm';
        const blob = new Blob(chunksRef.current, { type });
        const file = new File([blob], `challenge_recording.${ext}`, { type });
        setRecordedFile(file);
        setRecordedDuration(finalDuration);
        setRecordedUrl(URL.createObjectURL(blob));
        cleanupMedia();
        setIsRecording(false);
      };

      mediaRecorderRef.current = recorder;
      startTimeRef.current = Date.now();
      setElapsed(0);
      setIsRecording(true);
      recorder.start();
      drawWaveform();

      timerRef.current = window.setInterval(() => {
        setElapsed((Date.now() - startTimeRef.current) / 1000);
      }, 100);
    } catch {
      cleanupMedia();
      setMicError(
        'Microphone permission was denied or no mic was detected. Upload an audio clip (.wav, .ogg, .mp3) or use a sample recording.',
      );
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
  };

  const handleFileUpload = (ev: React.ChangeEvent<HTMLInputElement>) => {
    const file = ev.target.files?.[0];
    if (!file) return;
    setMicError(null);
    if (recordedUrl) URL.revokeObjectURL(recordedUrl);
    const url = URL.createObjectURL(file);
    setRecordedFile(file);
    setRecordedUrl(url);
    setRecordedDuration(5.5);

    const probe = new Audio(url);
    probe.onloadedmetadata = () => {
      if (Number.isFinite(probe.duration) && probe.duration > 0) {
        setRecordedDuration(probe.duration);
      }
    };
    ev.target.value = '';
  };

  const useSampleClip = () => {
    setMicError(null);
    if (recordedUrl) URL.revokeObjectURL(recordedUrl);
    const sample = buildDemoWavFile(Math.max(4.8, targetMinSeconds));
    setRecordedFile(sample);
    setRecordedDuration(Math.max(4.8, targetMinSeconds));
    setRecordedUrl(URL.createObjectURL(sample));
  };

  const resetClip = () => {
    if (recordedUrl) URL.revokeObjectURL(recordedUrl);
    setRecordedFile(null);
    setRecordedUrl(null);
    setRecordedDuration(0);
    setElapsed(0);
    setMicError(null);
  };

  const formatDuration = (sec: number) => {
    const mins = Math.floor(sec / 60);
    const rem = sec % 60;
    return `${String(mins).padStart(2, '0')}:${rem.toFixed(1).padStart(4, '0')}`;
  };

  const currentDuration = isRecording ? elapsed : recordedDuration;
  const isTargetMet = currentDuration >= targetMinSeconds;
  const progressPercent = Math.min(100, (currentDuration / targetMinSeconds) * 100);

  return (
    <div data-testid="audio-recorder" className="space-y-4">
      <div className="bg-slate-900/80 border border-white/10 rounded-2xl p-5 shadow-xl backdrop-blur-xl space-y-4">
        {/* Status Bar */}
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5 text-xs">
            {isRecording ? (
              <span className="flex items-center gap-2 text-rose-400 font-mono font-semibold animate-pulse">
                <span className="w-2.5 h-2.5 rounded-full bg-rose-500 shadow-[0_0_10px_#f43f5e]" />
                RECORDING MICROPHONE
              </span>
            ) : recordedFile ? (
              <span className="flex items-center gap-2 text-emerald-400 font-mono font-medium truncate max-w-[200px]">
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                {recordedFile.name}
              </span>
            ) : (
              <span className="text-slate-400 font-medium">Ready to record challenge phrase</span>
            )}
            <span aria-hidden="true" className="text-slate-700">·</span>
            <span className="text-slate-400 text-[11px] font-mono">Min {targetMinSeconds}s required</span>
          </div>

          <span
            data-testid="recorder-timer"
            className="font-mono text-base font-bold tabular-nums text-cyan-400 drop-shadow-[0_0_8px_rgba(6,182,212,0.5)]"
          >
            {formatDuration(currentDuration)}
          </span>
        </div>

        {/* Minimum Duration Indicator Bar */}
        <div className="space-y-1">
          <div className="h-1.5 w-full bg-slate-950/80 rounded-full overflow-hidden border border-white/5">
            <div
              className={`h-full transition-all duration-150 ${
                isTargetMet ? 'bg-emerald-400 shadow-[0_0_8px_#34d399]' : 'bg-cyan-400'
              }`}
              style={{ width: `${progressPercent}%` }}
            />
          </div>
          <div className="flex justify-between text-[10px] font-mono text-slate-500">
            <span>0s</span>
            <span>{targetMinSeconds}s target threshold {isTargetMet && '✓'}</span>
          </div>
        </div>

        {/* Live Audio Visualizer Canvas */}
        <div className="relative rounded-xl overflow-hidden bg-slate-950/90 border border-white/5 p-2">
          <canvas
            ref={canvasRef}
            width={580}
            height={56}
            className="w-full h-[56px] rounded-lg"
          />
        </div>

        {/* Control Buttons */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
          <div className="flex flex-wrap items-center gap-2.5">
            {!isRecording ? (
              <PressableButton
                type="button"
                data-testid="start-record-btn"
                variant="primary"
                onClick={startRecording}
                disabled={disabled}
                icon={Mic}
              >
                Start Recording
              </PressableButton>
            ) : (
              <PressableButton
                type="button"
                data-testid="stop-record-btn"
                variant="rose"
                onClick={stopRecording}
                icon={Square}
              >
                Stop Recording
              </PressableButton>
            )}

            <PressableButton
              type="button"
              variant="secondary"
              size="sm"
              icon={Upload}
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled || isRecording}
            >
              Upload Audio
            </PressableButton>
            <input
              ref={fileInputRef}
              type="file"
              accept=".wav,.mp3,.m4a,.flac,.ogg,.webm,audio/*"
              onChange={handleFileUpload}
              className="hidden"
            />

            <PressableButton
              type="button"
              variant="ghost"
              size="sm"
              icon={FileAudio}
              onClick={useSampleClip}
              disabled={disabled || isRecording}
            >
              Demo Sample
            </PressableButton>
          </div>

          {recordedFile && !isRecording && (
            <button
              type="button"
              onClick={resetClip}
              disabled={disabled}
              className="inline-flex items-center gap-1.5 text-xs font-mono text-slate-400 hover:text-white transition-colors cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Reset
            </button>
          )}
        </div>

        {micError && (
          <p className="p-3 rounded-lg bg-amber-950/30 border border-amber-500/30 text-xs text-amber-300 leading-relaxed font-mono">
            {micError}
          </p>
        )}

        {/* Playback & Submit Section */}
        {recordedUrl && recordedFile && !isRecording && (
          <div className="pt-4 border-t border-white/[0.08] flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3 min-w-0">
              <Play className="w-4 h-4 text-cyan-400 shrink-0" />
              <audio src={recordedUrl} controls className="h-8 max-w-[280px] rounded" />
            </div>
            <PressableButton
              type="button"
              data-testid="submit-audio-btn"
              variant="emerald"
              onClick={() => onAudioReady(recordedFile, recordedDuration)}
              disabled={disabled}
              glow
            >
              {actionLabel}
            </PressableButton>
          </div>
        )}
      </div>
    </div>
  );
};
