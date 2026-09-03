  const WAVEFORM_SECONDS = 8;
  const tracks = new Map();

  function waveformKey(source, channels) {
    return `${source}\u0000${channels.join(",")}`;
  }

  function setWaveformLayout(layout) {
    for (const key of tracks.keys()) {
      if (key.startsWith(`${layout.source}\u0000`)) tracks.delete(key);
    }
    for (const track of layout.tracks) {
      tracks.set(waveformKey(layout.source, track.channels), {
        generation: layout.generation,
        sampleRate: layout.sample_rate,
        bucketFrames: layout.bucket_frames,
        channels: track.channels,
        buckets: [],
        sequence: null,
        previousEnd: null,
      });
    }
  }

  function addWaveformBatch(batch) {
    const seconds = batch.bucket_frames / batch.sample_rate;
    for (const data of batch.tracks) {
      const track = tracks.get(waveformKey(batch.source, data.channels));
      if (!track || track.generation !== batch.generation) continue;
      if (track.sequence !== null &&
          (batch.sequence !== track.sequence + 1 || batch.dropped_batches ||
           Math.abs(batch.start_timestamp - track.previousEnd) > seconds)) {
        track.buckets.push({ gap: true, time: batch.start_timestamp });
      }
      track.sequence = batch.sequence;
      track.previousEnd = batch.start_timestamp + batch.present.length * seconds;
      for (let i = 0; i < batch.present.length; i += 1) {
        track.buckets.push({
          time: batch.start_timestamp + i * seconds,
          present: batch.present[i],
          minimum: data.minimum.map(values => values[i]),
          maximum: data.maximum.map(values => values[i]),
        });
      }
      const oldest = batch.start_timestamp - WAVEFORM_SECONDS;
      track.buckets = track.buckets.filter(bucket => bucket.time >= oldest);
    }
  }

  function resizeCanvas(canvas) {
    const scale = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(canvas.clientWidth * scale));
    const height = Math.max(1, Math.round(canvas.clientHeight * scale));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    return scale;
  }

  function drawWaveform(canvas, track, now) {
    resizeCanvas(canvas);
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    context.strokeStyle = "#aaa";
    context.beginPath();
    context.moveTo(0, height / 2);
    context.lineTo(width, height / 2);
    context.stroke();
    if (!track) return;
    const secondsPerPixel = WAVEFORM_SECONDS / width;
    context.strokeStyle = "#14FF3d";
    context.beginPath();
    const bandHeight = height / track.channels.length;
    for (let channel = 0; channel < track.channels.length; channel += 1) {
      const columns = new Map();
      for (const bucket of track.buckets) {
        if (bucket.gap || !bucket.present) continue;
        const column = Math.floor(width - (now - bucket.time) / secondsPerPixel);
        if (column < 0 || column >= width) continue;
        const existing = columns.get(column);
        const minimum = bucket.minimum[channel];
        const maximum = bucket.maximum[channel];
        if (existing) {
          existing.minimum = Math.min(existing.minimum, minimum);
          existing.maximum = Math.max(existing.maximum, maximum);
        } else {
          columns.set(column, { minimum, maximum });
        }
      }
      const center = (channel + 0.5) * bandHeight;
      for (const [column, envelope] of columns) {
        const top = center - Math.min(1, Math.max(-1, envelope.maximum)) * bandHeight / 2;
        const bottom = center - Math.min(1, Math.max(-1, envelope.minimum)) * bandHeight / 2;
        context.moveTo(column, top);
        context.lineTo(column, bottom);
      }
    }
    context.stroke();
  }

  function renderWaveforms() {
    const now = Date.now() / 1000;
    for (const form of document.querySelectorAll("#channels .level")) {
      const key = waveformKey(
        form.dataset.device,
        form.dataset.channels.split(",").map(Number),
      );
      const track = tracks.get(key);
      const canvas = form.querySelector(".waveform");
      if (canvas) drawWaveform(canvas, track, now);
    }
    requestAnimationFrame(renderWaveforms);
  }

  if (document.getElementById("channels")) {
    const events = new EventSource("/waveforms");
    events.addEventListener("waveform_layout", event => {
      setWaveformLayout(JSON.parse(event.data));
    });
    events.addEventListener("waveform", event => {
      addWaveformBatch(JSON.parse(event.data));
    });
    events.addEventListener("waveform_resync", () => {
      tracks.clear();
    });
    requestAnimationFrame(renderWaveforms);
  }
