import React, { useEffect, useRef, useState } from 'react';
import { Mic, Square, Upload, Play, RotateCcw, FileAudio } from 'lucide-react';

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

      const barCount = 44;
      const step = Math.max(1, Math.floor(bufferLength / barCount));
      const barWidth = width / barCount;

      for (let i = 0; i < barCount; i++) {
        const value = dataArray[i * step] / 255;
        const barHeight = Math.max(4, value * (height - 8));
        const x = i * barWidth + 2;
        const y = (height - barHeight) / 2;
        ctx.fillStyle = value > 0.15 ? '#0F172A' : '#CBD5E1';
        ctx.fillRect(x, y, Math.max(2, barWidth - 4), barHeight);
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

  return (
    <div className="space-y-4">
      <div className="bg-[#F8FAFC] border border-slate-200 rounded-lg p-4">
        <div className="flex items-center justify-between gap-4 mb-3">
          <div className="flex items-center gap-2 text-xs text-slate-600">
            <span className="font-medium text-slate-900">
              {isRecording
                ? 'Recording microphone input'
                : recordedFile
                  ? recordedFile.name
                  : 'Ready to record voice challenge'}
            </span>
            <span aria-hidden="true">·</span>
            <span>Target {targetMinSeconds}s–60s</span>
          </div>
          <span className="font-mono text-sm font-semibold tabular-nums text-slate-900">
            {isRecording ? formatDuration(elapsed) : formatDuration(recordedDuration)}
          </span>
        </div>

        <canvas
          ref={canvasRef}
          width={560}
          height={52}
          className="w-full h-[52px] rounded bg-white border border-slate-200/80"
        />

        <div className="flex flex-wrap items-center justify-between gap-3 mt-4">
          <div className="flex flex-wrap items-center gap-2">
            {!isRecording ? (
              <button
                type="button"
                onClick={startRecording}
                disabled={disabled}
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-[#0F172A] hover:bg-slate-800 disabled:opacity-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              >
                <Mic className="w-4 h-4" />
                Start Recording
              </button>
            ) : (
              <button
                type="button"
                onClick={stopRecording}
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-[#B91C1C] hover:bg-red-800 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
                Stop Recording
              </button>
            )}

            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled || isRecording}
              className="inline-flex items-center gap-2 px-3.5 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 hover:bg-slate-50 disabled:opacity-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              <Upload className="w-4 h-4" />
              Upload Clip
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".wav,.mp3,.m4a,.flac,.ogg,.webm,audio/*"
              onChange={handleFileUpload}
              className="hidden"
            />

            <button
              type="button"
              onClick={useSampleClip}
              disabled={disabled || isRecording}
              className="inline-flex items-center gap-2 px-3.5 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 bg-transparent hover:bg-slate-100 disabled:opacity-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              <FileAudio className="w-4 h-4" />
              Use Demo Sample
            </button>
          </div>

          {recordedFile && !isRecording && (
            <button
              type="button"
              onClick={resetClip}
              disabled={disabled}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-900 whitespace-nowrap shrink-0 cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Clear
            </button>
          )}
        </div>

        {micError && (
          <p className="mt-3 text-xs text-[#B45309] leading-relaxed">{micError}</p>
        )}

        {recordedUrl && recordedFile && !isRecording && (
          <div className="mt-4 pt-4 border-t border-slate-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <Play className="w-4 h-4 text-slate-500 shrink-0" />
              <audio src={recordedUrl} controls className="h-8 max-w-[260px]" />
            </div>
            <button
              type="button"
              onClick={() => onAudioReady(recordedFile, recordedDuration)}
              disabled={disabled}
              className="inline-flex items-center justify-center px-4 py-2 text-sm font-medium text-white bg-[#2563EB] hover:bg-blue-700 disabled:opacity-50 rounded-md transition-colors whitespace-nowrap shrink-0 cursor-pointer"
            >
              {actionLabel}
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
