import argparse,json,pathlib,time,unicodedata,platform,importlib.metadata
import numpy as np
import soundfile as sf
p=argparse.ArgumentParser();p.add_argument('backend',choices=['cpu','mlx']);p.add_argument('model');args=p.parse_args()
root=pathlib.Path(__file__).resolve().parent
cases=json.loads((root/'manifest.json').read_text())
def norm(t):
 return ''.join(c for c in unicodedata.normalize('NFKC',t).lower() if not c.isspace() and unicodedata.category(c)[0] not in ['P','S'])
def dist(a,b):
 prev=list(range(len(b)+1))
 for i,x in enumerate(a,1):
  cur=[i]
  for j,y in enumerate(b,1):cur.append(min(cur[-1]+1,prev[j]+1,prev[j-1]+(x!=y)))
  prev=cur
 return prev[-1]
load=time.perf_counter()
if args.backend=='cpu':
 from faster_whisper import WhisperModel
 model=WhisperModel(args.model,device='cpu',compute_type='int8',cpu_threads=4,download_root=str(root/'models'))
 def run(audio):
  segs,info=model.transcribe(audio,language='ja',beam_size=5,temperature=0,condition_on_previous_text=False,word_timestamps=True,vad_filter=False,repetition_penalty=1.1,no_repeat_ngram_size=3)
  segs=list(segs)
  return ''.join(s.text for s in segs),[dict(start=s.start,end=s.end,text=s.text,words=[dict(word=w.word,start=w.start,end=w.end,probability=w.probability) for w in (s.words or [])]) for s in segs]
else:
 import mlx_whisper
 def run(audio):
  r=mlx_whisper.transcribe(audio,path_or_hf_repo=args.model,language='ja',temperature=0,condition_on_previous_text=False,word_timestamps=True,verbose=None)
  return r['text'],r['segments']
# First case as warm-up; model download and loading are not steady-state latency.
audio,_=sf.read(cases[0]['path'],dtype='float32');run(audio)
warm=time.perf_counter()-load
records=[]
for case in cases:
 audio,sr=sf.read(case['path'],dtype='float32');assert sr==16000
 start=time.perf_counter();text,segs=run(audio);elapsed=time.perf_counter()-start
 a,b=norm(case['text']),norm(text)
 row={**case,'hypothesis':text,'seconds':elapsed,'rtf':elapsed/case['duration'],'edits':dist(a,b),'reference_chars':len(a),'segments':segs}
 records.append(row)
 print(json.dumps({k:row[k] for k in ['id','hypothesis','seconds','rtf','edits','reference_chars']},ensure_ascii=False),flush=True)
result=dict(backend=args.backend,model=args.model,platform=platform.platform(),packages={n:importlib.metadata.version(n) for n in ['faster-whisper','mlx-whisper','ctranslate2','mlx']},warmup_and_load_seconds=warm,settings={'language':'ja','word_timestamps':True,'vad_filter':False,'temperature':0,'condition_on_previous_text':False,'cpu_beam_size':5,'cpu_threads':4,'note':'CPU repetition penalty=1.1/no-repeat-ngram=3; MLX defaults otherwise; backend/decoding are not identical'},cases=records)
result['summary']=dict(cer=sum(r['edits'] for r in records)/sum(r['reference_chars'] for r in records),audio_seconds=sum(r['duration'] for r in records),processing_seconds=sum(r['seconds'] for r in records))
result['summary']['rtf']=result['summary']['processing_seconds']/result['summary']['audio_seconds']
name=args.backend+'-'+args.model.split('/')[-1]
(root/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('SUMMARY',json.dumps(result['summary']),flush=True)
