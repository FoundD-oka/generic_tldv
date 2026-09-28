import json,pathlib,time,urllib.request,uuid,sys
root=pathlib.Path(__file__).resolve().parent
cases=json.loads((root/'manifest.json').read_text());rows=[]
for case in cases:
 boundary='vexa-validation-'+uuid.uuid4().hex
 data=bytearray()
 for key,value in {'model':'whisper-1','response_format':'verbose_json','language':'ja','timestamp_granularities':'word','max_speech_duration_s':'5','min_silence_duration_ms':'100'}.items():
  data.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
 data.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode());data.extend(pathlib.Path(case['path']).read_bytes());data.extend(f'\r\n--{boundary}--\r\n'.encode())
 request=urllib.request.Request('http://127.0.0.1:18092/v1/audio/transcriptions',data=bytes(data),headers={'Content-Type':f'multipart/form-data; boundary={boundary}'})
 start=time.perf_counter()
 with urllib.request.urlopen(request,timeout=60) as r: body=json.load(r)
 elapsed=time.perf_counter()-start
 row={'id':case['id'],'reference':case['text'],'duration':case['duration'],'seconds':elapsed,'response':body};rows.append(row)
 print(json.dumps({'id':row['id'],'text':body['text'],'seconds':elapsed},ensure_ascii=False),flush=True)
(root/('api-'+sys.argv[1]+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
