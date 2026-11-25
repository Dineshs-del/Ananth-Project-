/**
 * Pure TypeScript WAV encoder and audio converter.
 * Encodes audio samples to standard 16-bit PCM, RIFF/WAVE at 16 kHz mono.
 * Matches backend/app/bot/audio.py TARGET_SAMPLE_RATE = 16000, TARGET_CHANNELS = 1.
 */

export const TARGET_SAMPLE_RATE = 16000;
export const TARGET_CHANNELS = 1;

/**
 * Encode Float32Array samples (-1.0 to 1.0) into a 16-bit PCM RIFF/WAVE ArrayBuffer.
 */
export function encodeWav(samples: Float32Array, sampleRate: number = TARGET_SAMPLE_RATE): ArrayBuffer {
  const numChannels = 1;
  const bytesPerSample = 2; // 16-bit
  const blockAlign = numChannels * bytesPerSample;
  const byteRate = sampleRate * blockAlign;
  const dataByteLength = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataByteLength);
  const view = new DataView(buffer);

  const writeString = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  };

  // 1. RIFF Chunk Descriptor
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + dataByteLength, true); // ChunkSize = 36 + SubChunk2Size
  writeString(8, 'WAVE');

  // 2. "fmt " Subchunk
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true); // Subchunk1Size for PCM (16 bytes)
  view.setUint16(20, 1, true); // AudioFormat = 1 (PCM)
  view.setUint16(22, numChannels, true); // NumChannels = 1
  view.setUint32(24, sampleRate, true); // SampleRate
  view.setUint32(28, byteRate, true); // ByteRate = SampleRate * NumChannels * BitsPerSample/8
  view.setUint16(32, blockAlign, true); // BlockAlign = NumChannels * BitsPerSample/8
  view.setUint16(34, 16, true); // BitsPerSample = 16

  // 3. "data" Subchunk
  writeString(36, 'data');
  view.setUint32(40, dataByteLength, true); // Subchunk2Size

  // 4. Write 16-bit PCM samples with clipping
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    // Scale -1.0..1.0 to -32768..32767
    const val = s < 0 ? s * 0x8000 : s * 0x7fff;
    view.setInt16(offset, Math.floor(val), true);
  }

  return buffer;
}

/**
 * Decode an arbitrary audio Blob or File (e.g. webm, ogg, mp4, wav), downmix to mono,
 * resample to 16 kHz using OfflineAudioContext, and encode to 16-bit mono WAV File.
 */
export async function convertBlobTo16kWav(
  blob: Blob,
  fileName: string = 'recording.wav',
): Promise<File> {
  const arrayBuffer = await blob.arrayBuffer();

  const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  if (!AudioCtx) {
    throw new Error('Web Audio API (AudioContext) is not supported in this browser.');
  }

  const decodeCtx = new AudioCtx();
  let decoded: AudioBuffer;
  try {
    decoded = await decodeCtx.decodeAudioData(arrayBuffer);
  } finally {
    void decodeCtx.close().catch(() => {});
  }

  // Downmix to mono into a single channel Float32Array
  const numChannels = decoded.numberOfChannels;
  const length = decoded.length;
  const monoSamples = new Float32Array(length);

  for (let ch = 0; ch < numChannels; ch++) {
    const chData = decoded.getChannelData(ch);
    for (let i = 0; i < length; i++) {
      monoSamples[i] += chData[i] / numChannels;
    }
  }

  // Resample to TARGET_SAMPLE_RATE (16 kHz) using OfflineAudioContext
  const targetDuration = decoded.duration;
  const targetLength = Math.max(1, Math.round(targetDuration * TARGET_SAMPLE_RATE));

  const offlineCtx = new OfflineAudioContext(1, targetLength, TARGET_SAMPLE_RATE);
  const monoBuffer = offlineCtx.createBuffer(1, length, decoded.sampleRate);
  monoBuffer.copyToChannel(monoSamples, 0);

  const source = offlineCtx.createBufferSource();
  source.buffer = monoBuffer;
  source.connect(offlineCtx.destination);
  source.start(0);

  const renderedBuffer = await offlineCtx.startRendering();
  const resampledSamples = renderedBuffer.getChannelData(0);

  const wavBuffer = encodeWav(resampledSamples, TARGET_SAMPLE_RATE);
  const finalName = fileName.replace(/\.[^/.]+$/, '') + '.wav';
  return new File([wavBuffer], finalName, { type: 'audio/wav' });
}
