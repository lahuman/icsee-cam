/* Mono microphone -> 8 kHz signed PCM. Audio is never played locally or stored. */
class CameraMicrophone extends AudioWorkletProcessor {
  constructor() { super(); this.phase=0; this.sum=0; this.count=0; this.offset=0; this.pcm=new Int16Array(320); }
  process(inputs) {
    const samples=inputs[0]?.[0];
    if(!samples) return true;
    for(const sample of samples) {
      this.sum+=Math.max(-1,Math.min(1,sample)); this.count++; this.phase+=8000;
      if(this.phase>=sampleRate) {
        this.phase-=sampleRate;
        const value=this.sum/this.count;
        this.pcm[this.offset++]=Math.round(value*(value<0?32768:32767)); this.sum=0; this.count=0;
        if(this.offset===320) {
          this.port.postMessage(this.pcm.buffer,[this.pcm.buffer]);
          this.pcm=new Int16Array(320); this.offset=0;
        }
      }
    }
    return true;
  }
}
registerProcessor('camera-microphone',CameraMicrophone);
