const fs = require('fs');
const path = require('path');
const { SpeakerStreamManager } = require(path.resolve('services/vexa-bot/core/src/services/speaker-streams.ts'));
const { SileroVAD, VadGate } = require(path.resolve('services/vexa-bot/core/src/services/vad.ts'));
const { TranscriptionClient } = require(path.resolve('services/vexa-bot/core/src/services/transcription-client.ts'));
const root = path.resolve('.hw/evidence/realtime-transcription-quality/benchmark');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
const pause = (ms: number) => new Promise(r => setTimeout(r, ms));
(async () => {
 const rows = [];
 const vad = process.argv.includes('--vad') ? await SileroVAD.create() : null;
 const client = new TranscriptionClient({ serviceUrl: 'http://127.0.0.1:18092', maxRetries: 0, maxSpeechDurationSec: 5, minSilenceDurationMs: 100 });
 for (const item of manifest.filter((x: any) => ['short_yes','short_no','date','amount','negation','technical','repeat'].includes(x.id))) {
  const bytes = fs.readFileSync(item.path);
  // Locate the PCM data chunk; ffmpeg may insert LIST metadata before data.
  let pos = 12;
  while (bytes.toString('ascii', pos, pos+4) !== 'data') pos += 8 + bytes.readUInt32LE(pos+4) + (bytes.readUInt32LE(pos+4) % 2);
  const length = bytes.readUInt32LE(pos+4) / 2;
  const audio = new Float32Array(length);
  for (let i = 0; i < length; i++) audio[i] = bytes.readInt16LE(pos+8+2*i) / 32768;
  const mgr = new SpeakerStreamManager({sampleRate:16000,minAudioDuration:1,submitInterval:1,confirmThreshold:2,maxBufferDuration:15,idleTimeoutSec:5});
  const gate = vad ? new VadGate(vad) : null;
  const emitted: any[] = [], responses: any[] = [], errors: string[] = [], tasks: Promise<any>[] = [];
  let n=0;
  mgr.onSegmentReady = (id: string, name: string, samples: Float32Array, request: any) => {
   const job = (async()=>{
    try {
     const result = await client.transcribe(samples, 'ja', process.argv.includes('--no-prompt') ? undefined : (mgr.getLastConfirmedText(id) || undefined));
     responses.push({samples:samples.length,text:result.text,segments:result.segments,request});
     mgr.handleTranscriptionResult(id,result.text,result.segments.at(-1)?.end,result.segments,request);
    } catch(e: any) { errors.push(e.message); if(mgr.handleTranscriptionError)mgr.handleTranscriptionError(id,e,request);else mgr.handleTranscriptionResult(id,''); }
   })();tasks.push(job);return job;
  };
  const start = Date.now();
  mgr.onSegmentConfirmed = (_id:string,_name:string,text:string,begin:number,end:number)=>emitted.push({text,begin,end,emittedMs:Date.now()-start});
  mgr.addSpeaker('s','検証話者');
  for (let offset=0;offset<audio.length;offset+=4096) {
   const chunk = audio.slice(offset,Math.min(offset+4096,audio.length));
   const captureEndMs = start+Math.min(offset+4096,audio.length)/16;
   if(gate) { const gated = await gate.process(chunk,captureEndMs); for(const c of gated.chunks)mgr.feedAudio('s',c.data,c.captureEndMs); }
   else mgr.feedAudio('s',chunk,captureEndMs);
   await pause(Math.max(0,start+Math.min(offset+4096,audio.length)/16-Date.now()));
  }
  await mgr.flushSpeaker('s',true);
  await Promise.allSettled(tasks);
  await mgr.removeAll();
  const row={id:item.id,reference:item.text,duration:item.duration,emitted,responses,errors,stats:mgr.getStats?.(),elapsedMs:Date.now()-start};rows.push(row);
  console.log(JSON.stringify({id:item.id,emitted:emitted.map(x=>x.text),responses:responses.map(x=>x.text),errors}));
 }
 fs.writeFileSync(path.join(root,`replay-${process.argv[2]}.json`),JSON.stringify(rows,null,2));
})().catch((e:any)=>{console.error(e);process.exitCode=1});
