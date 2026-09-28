const fs = require('fs'), path=require('path');
const { SpeakerStreamManager }=require(path.resolve('services/vexa-bot/core/src/services/speaker-streams.ts'));
const { TranscriptionClient }=require(path.resolve('services/vexa-bot/core/src/services/transcription-client.ts'));
const root=path.resolve('.hw/evidence/realtime-transcription-quality/benchmark');
(async()=>{
 const item=JSON.parse(fs.readFileSync(root+'/manifest.json','utf8')).find((x:any)=>x.id==='technical');
 const bytes=fs.readFileSync(item.path);let p=12;
 while(bytes.toString('ascii',p,p+4)!=='data')p+=8+bytes.readUInt32LE(p+4)+(bytes.readUInt32LE(p+4)%2);
 const audio=new Float32Array(bytes.readUInt32LE(p+4)/2);for(let i=0;i<audio.length;i++)audio[i]=bytes.readInt16LE(p+8+i*2)/32768;
 let now=100000, job:Promise<any>=Promise.resolve();
 const mgr=new SpeakerStreamManager({sampleRate:16000,minAudioDuration:1,confirmThreshold:2,maxBufferDuration:15,now:()=>now,autoTimers:false});
 const client=new TranscriptionClient({serviceUrl:'http://127.0.0.1:18092',maxRetries:0,maxSpeechDurationSec:5,minSilenceDurationMs:100});
 const responses:any[]=[],emitted:any[]=[];
 mgr.onSegmentConfirmed=(_id:any,_name:any,text:any,start:any,end:any)=>emitted.push({text,start,end});
 mgr.onSegmentReady=(id:any,name:any,samples:any,info:any)=>{
  job=(async()=>{const result=await client.transcribe(samples,'ja');responses.push({info,result});mgr.handleTranscriptionResult(id,result.text,result.segments.at(-1)?.end,result.segments,info);})();return job;
 };
 mgr.addSpeaker('s','検証');let last=0;
 for(const end of [16384,49152,98304]){now=100000+end/16;mgr.feedAudio('s',audio.slice(last,end),now);last=end;await mgr.tick();await job;}
 now=100000+audio.length/16;mgr.feedAudio('s',audio.slice(last),now);await mgr.flushSpeaker('s',true);
 fs.writeFileSync(root+`/cut-repro-${process.argv[2]}.json`,JSON.stringify({responses,emitted,stats:mgr.getStats()},null,2));
 console.log(JSON.stringify({responses:responses.map(x=>x.result.text),emitted},null,2));mgr.removeAll();
})().catch((e:any)=>{console.error(e);process.exitCode=1;});
