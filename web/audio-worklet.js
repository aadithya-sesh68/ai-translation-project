class Pcm16Resampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.sourcePosition = 0;
    this.pendingInput = new Float32Array(0);
    this.output = new Int16Array(1536);
    this.outputIndex = 0;

    this.port.onmessage = (event) => {
      if (event.data?.type === "flush") {
        this.flush();
      }
    };
  }

  appendInput(samples) {
    const combined = new Float32Array(
      this.pendingInput.length + samples.length,
    );
    combined.set(this.pendingInput);
    combined.set(samples, this.pendingInput.length);
    this.pendingInput = combined;
  }

  writeSample(sample) {
    const clamped = Math.max(-1, Math.min(1, sample));
    this.output[this.outputIndex] =
      clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    this.outputIndex += 1;

    if (this.outputIndex === this.output.length) {
      this.postOutput(this.output);
      this.output = new Int16Array(1536);
      this.outputIndex = 0;
    }
  }

  postOutput(samples) {
    this.port.postMessage(samples.buffer, [samples.buffer]);
  }

  flush() {
    if (this.outputIndex > 0) {
      this.postOutput(this.output.slice(0, this.outputIndex));
      this.output = new Int16Array(1536);
      this.outputIndex = 0;
    }
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input?.length) {
      return true;
    }

    this.appendInput(input);
    const ratio = sampleRate / this.targetSampleRate;

    while (this.sourcePosition + 1 < this.pendingInput.length) {
      const leftIndex = Math.floor(this.sourcePosition);
      const fraction = this.sourcePosition - leftIndex;
      const left = this.pendingInput[leftIndex];
      const right = this.pendingInput[leftIndex + 1];
      this.writeSample(left + (right - left) * fraction);
      this.sourcePosition += ratio;
    }

    // Keep the last source sample so interpolation remains continuous when an
    // output position straddles this block and the next browser audio block.
    const consumed = Math.min(
      Math.floor(this.sourcePosition),
      this.pendingInput.length - 1,
    );
    if (consumed > 0) {
      this.pendingInput = this.pendingInput.slice(consumed);
      this.sourcePosition -= consumed;
    }

    return true;
  }
}

registerProcessor("pcm16-resampler", Pcm16Resampler);
