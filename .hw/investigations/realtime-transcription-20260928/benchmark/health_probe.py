import pathlib,json,urllib.request,threading,time,uuid,sys
root=pathlib.Path(__file__).resolve().parent
case=next(c for c in json.loads((root/'manifest.json').read_text()) if c['id']=='technical')
b='probe-'+uuid.uuid4().hex
d=bytearray()
for k,v in {'model':'whisper-1','language':'ja','response_format':'verbose_json','timestamp_granularities':'word','max_speech_duration_s':'5','min_silence_duration_ms':'100'}.items():
 d.extend(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
d.extend(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode());d.extend(pathlib.Path(case['path']).read_bytes());d.extend(f'\r\n--{b}--\r\n'.encode())
result={}
def infer():
 start=time.perf_counter()
 req=urllib.request.Request('http://127.0.0.1:18092/v1/audio/transcriptions',data=bytes(d),headers={'Content-Type':f'multipart/form-data; boundary={b}'})
 try:
  with urllib.request.urlopen(req,timeout=60) as r: result['response']=json.load(r)
 except Exception as e:result['error']=str(e)
 result['inference_seconds']=time.perf_counter()-start
thread=threading.Thread(target=infer);thread.start();time.sleep(.5)
start=time.perf_counter()
with urllib.request.urlopen('http://127.0.0.1:18092/health',timeout=30) as r:health=json.load(r)
result['health_latency_seconds_during_inference']=time.perf_counter()-start
result['health_status']=health['status'];result['inference_running_at_health_response']=thread.is_alive();thread.join()
(root/f'api-event-loop-{sys.argv[1]}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='response'},ensure_ascii=False))
if 'error' in result or not result['inference_running_at_health_response']:sys.exit(1)
