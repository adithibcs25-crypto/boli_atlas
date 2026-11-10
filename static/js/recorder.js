(() => {
  const button = document.getElementById('record-button');
  const timer = document.getElementById('record-timer');
  const status = document.getElementById('record-status');
  const fileInput = document.getElementById('audio-file');
  const durationInput = document.getElementById('duration-audio');
  const preview = document.getElementById('audio-preview');
  const transcribeButton = document.getElementById('transcribe-button');
  const transcribeStatus = document.getElementById('transcribe-status');
  const detectedStatus = document.getElementById('detected-language-status');
  const languageInput = document.getElementById('language');
  const transcriptionLanguageInput = document.getElementById('transcription-language');
  const formatInput = document.getElementById('transcription-format');
  const phrase = document.querySelector('[name=phrase]');
  const raw = document.getElementById('raw-transcript');
  const corrected = document.getElementById('corrected-transcript');
  const model = document.getElementById('model-used');
  const detectedLanguage = document.getElementById('detected-language-code');
  const requestId = document.getElementById('transcription-request-id');
  const transcriptTime = document.getElementById('transcription-timestamp');
  const transcriptMode = document.getElementById('transcription-mode');
  const locales = {Kannada: 'kn-IN', Tamil: 'ta-IN', Telugu: 'te-IN', Malayalam: 'ml-IN'};
  const localeNames = Object.fromEntries(Object.entries(locales).map(([name, locale]) => [locale, name]));
  let recorder = null;
  let chunks = [];
  let stream = null;
  let started = 0;
  let interval = null;

  function showBlob(blob, name) {
    fileInput._recordedFile = new File([blob], name, {type: blob.type || 'audio/webm'});
    preview.src = URL.createObjectURL(blob);
    preview.hidden = false;
    status.textContent = 'Recording ready. Playback is available below.';
  }

  function elapsed() {
    const seconds = Math.min(30, Math.floor((Date.now() - started) / 1000));
    timer.textContent = `00:${String(seconds).padStart(2, '0')} / 00:30`;
    if (seconds >= 30 && recorder?.state === 'recording') recorder.stop();
  }

  async function toWav(file) {
    const context = new AudioContext();
    try {
      const decoded = await context.decodeAudioData(await file.arrayBuffer());
      if (decoded.duration > 30.1) throw new Error('Audio must be 30 seconds or shorter.');
      const channels = Math.min(decoded.numberOfChannels, 2);
      const samples = decoded.length;
      const bytes = new ArrayBuffer(44 + samples * channels * 2);
      const view = new DataView(bytes);
      const write = (offset, value) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
      write(0, 'RIFF'); view.setUint32(4, bytes.byteLength - 8, true); write(8, 'WAVE');
      write(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
      view.setUint16(22, channels, true); view.setUint32(24, decoded.sampleRate, true);
      view.setUint32(28, decoded.sampleRate * channels * 2, true); view.setUint16(32, channels * 2, true);
      view.setUint16(34, 16, true); write(36, 'data'); view.setUint32(40, samples * channels * 2, true);
      const input = Array.from({length: channels}, (_, channel) => decoded.getChannelData(channel));
      let offset = 44;
      for (let i = 0; i < samples; i++) {
        for (let channel = 0; channel < channels; channel++) {
          const sample = Math.max(-1, Math.min(1, input[channel][i]));
          view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
          offset += 2;
        }
      }
      const stem = file.name.replace(/\.[^.]+$/, '') || 'recording';
      return new File([bytes], `${stem}.wav`, {type: 'audio/wav'});
    } finally {
      await context.close();
    }
  }

  async function gnaniReadyFile(file) {
    const extension = file.name.split('.').pop().toLowerCase();
    if (extension === 'webm') return toWav(file);
    if (!['wav', 'mp3', 'm4a', 'ogg', 'flac', 'aac'].includes(extension)) {
      throw new Error('Use WAV, MP3, M4A, OGG, FLAC, or AAC audio.');
    }
    return file;
  }

  button.addEventListener('click', async () => {
    if (recorder?.state === 'recording') { recorder.stop(); return; }
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: true});
      const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(value => MediaRecorder.isTypeSupported(value));
      recorder = new MediaRecorder(stream, type ? {mimeType: type} : {});
      chunks = [];
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        clearInterval(interval);
        stream.getTracks().forEach(track => track.stop());
        const blob = new Blob(chunks, {type: recorder.mimeType});
        showBlob(blob, `recording.${blob.type.includes('mp4') ? 'm4a' : 'webm'}`);
        button.innerHTML = '<span>●</span> Record again';
      };
      recorder.start(); started = Date.now(); interval = setInterval(elapsed, 250);
      button.innerHTML = '<span>■</span> Stop recording'; status.textContent = 'Recording…';
    } catch {
      status.textContent = 'Microphone unavailable. Check browser permission or upload an audio file instead.';
    }
  });

  fileInput.addEventListener('change', () => {
    fileInput._recordedFile = null;
    const file = fileInput.files[0];
    if (file) {
      preview.src = URL.createObjectURL(file); preview.hidden = false;
      status.textContent = `${file.name} selected. Keep clips under 30 seconds.`;
    }
  });
  preview.addEventListener('loadedmetadata', () => {
    if (Number.isFinite(preview.duration) && preview.duration > 30.1) status.textContent = 'This clip is longer than 30 seconds. Trim it before saving or transcribing.';
  });

  transcribeButton.addEventListener('click', async () => {
    const source = fileInput._recordedFile || fileInput.files[0];
    const languageCode = transcriptionLanguageInput.value === 'phrase-language'
      ? locales[languageInput.value]
      : transcriptionLanguageInput.value;
    if (!languageCode) { transcribeStatus.textContent = 'Choose a phrase language or a transcription language first.'; return; }
    if (!source) { transcribeStatus.textContent = 'Record or choose an audio file first.'; return; }
    if (source.size > 15 * 1024 * 1024) { transcribeStatus.textContent = 'Audio must be no larger than 15 MB.'; return; }
    if (preview.duration && preview.duration > 30.1) { transcribeStatus.textContent = 'Audio must be 30 seconds or shorter.'; return; }
    transcribeButton.disabled = true;
    detectedStatus.textContent = '';
    transcribeStatus.textContent = 'Preparing audio for Gnani Prisma v3.0…';
    try {
      const audio = await gnaniReadyFile(source);
      const form = new FormData();
      form.append('audio', audio, audio.name);
      form.append('language_code', languageCode);
      form.append('format', formatInput.value);
      form.append('csrf_token', document.querySelector('#contribution-form [name=csrf_token]').value);
      transcribeStatus.textContent = 'Sending audio securely to Gnani Prisma v3.0…';
      const response = await fetch('/api/transcribe', {method: 'POST', body: form});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Transcription failed.');
      raw.value = data.transcript;
      corrected.value = data.transcript;
      if (!phrase.value.trim()) phrase.value = data.transcript;
      model.value = data.model;
      detectedLanguage.value = data.detected_language_code || '';
      requestId.value = data.request_id || '';
      transcriptTime.value = data.transcription_timestamp || '';
      transcriptMode.value = data.format || formatInput.value;
      if (data.detected_language_code) {
        detectedStatus.textContent = `Detected language: ${localeNames[data.detected_language_code] || data.detected_language_code}`;
        if (!languageInput.value && localeNames[data.detected_language_code]) {
          languageInput.value = localeNames[data.detected_language_code];
        } else if (!languageInput.value) {
          transcribeStatus.textContent = `Transcript received, but ${data.detected_language_code} is outside this atlas's four archive languages. Choose Kannada, Tamil, Telugu, or Malayalam before saving.`;
        }
      } else {
        detectedStatus.textContent = 'Language detection is unavailable for Latin transliteration; select the phrase language above.';
      }
      transcribeStatus.textContent = `Transcript received from ${data.model}. Review it, add meaning and context, then save the Phrase Passport.`;
      if (data.detected_language_code && !localeNames[data.detected_language_code] && !languageInput.value) {
        transcribeStatus.textContent = `Transcript received, but ${data.detected_language_code} is outside this atlas's four archive languages. Choose Kannada, Tamil, Telugu, or Malayalam before saving.`;
      }
    } catch (error) {
      transcribeStatus.textContent = `${error.message} You can retry or enter the transcript manually.`;
    } finally {
      transcribeButton.disabled = false;
    }
  });

  document.getElementById('contribution-form').addEventListener('submit', async event => {
    const source = fileInput._recordedFile || fileInput.files[0];
    if (!Object.prototype.hasOwnProperty.call(locales, languageInput.value)) {
      event.preventDefault();
      status.textContent = 'Select the detected phrase language before saving. If automatic detection did not identify it, choose Kannada, Tamil, Telugu, or Malayalam.';
      languageInput.focus();
      return;
    }
    if (!source) { event.preventDefault(); status.textContent = 'Add a recording or audio file before saving.'; return; }
    if (!Number.isFinite(preview.duration)) { event.preventDefault(); status.textContent = 'Wait for the audio duration to load, then submit again.'; return; }
    if (preview.duration > 30.1) { event.preventDefault(); status.textContent = 'This clip is longer than 30 seconds. Trim it before saving.'; return; }
    event.preventDefault();
    try {
      if (fileInput._recordedFile) {
        const transfer = new DataTransfer(); transfer.items.add(fileInput._recordedFile); fileInput.files = transfer.files;
      }
      if (source.name.toLowerCase().endsWith('.webm')) {
        const wav = await toWav(source);
        const transfer = new DataTransfer(); transfer.items.add(wav); durationInput.files = transfer.files;
      } else {
        durationInput.value = '';
      }
      event.target.submit();
    } catch (error) {
      status.textContent = `${error.message} The original recording was not submitted.`;
    }
  });
})();
