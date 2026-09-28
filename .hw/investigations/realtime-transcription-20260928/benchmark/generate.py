import json, pathlib, subprocess, wave
root=pathlib.Path(__file__).resolve().parent
cases=[
('short_yes','はい。','Kyoko'),
('short_no','違います。','Kyoko'),
('date','来週の月曜です。','Kyoko'),
('amount','予算は5万円です。','Kyoko'),
('negation','承認していません。まだ送信しないでください。','Kyoko'),
('meeting','田中さん、見積もりの金額を確認してください。納期は来月の15日です。','Kyoko'),
('technical','リアルタイム文字起こしの精度を改善します。音声の欠落と重複を確認してください。','Kyoko'),
('repeat','違います。違います。承認はまだです。','Kyoko'),
]
manifest=[]
for name,text,voice in cases:
 aiff=root/(name+'.aiff'); wav=root/(name+'.wav')
 subprocess.run(['say','-v',voice,'-r','180','-o',str(aiff),text],check=True)
 subprocess.run(['ffmpeg','-y','-v','error','-i',str(aiff),'-af','adelay=300,apad=pad_dur=0.6','-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)],check=True)
 with wave.open(str(wav),'rb') as f: duration=f.getnframes()/f.getframerate()
 manifest.append(dict(id=name,text=text,voice=voice,path=str(wav),duration=duration))
 aiff.unlink()
(root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
print(json.dumps({'cases':len(manifest),'audio_seconds':sum(c['duration'] for c in manifest)},ensure_ascii=False))
