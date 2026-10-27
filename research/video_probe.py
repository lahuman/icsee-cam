import json,subprocess
from urllib.parse import quote
from icsee_ptz.config import Config
c=Config.from_env();c.require_password()
u=f'rtsp://{c.host}:554/user={quote(c.username,safe="")}&password={quote(c.password,safe="")}&channel=1&stream=0.sdp?real_stream'
try:
    p=subprocess.run(['ffprobe','-v','quiet','-rtsp_transport','tcp','-show_streams','-of','json',u],capture_output=True,timeout=15)
    if p.returncode: print(json.dumps({'video_probe':'failed','returncode':p.returncode}))
    else:
        streams=json.loads(p.stdout).get('streams',[])
        print(json.dumps({'video_probe':'ok','streams':[{k:s[k] for k in ('codec_type','codec_name','width','height','sample_rate') if k in s} for s in streams]}))
except subprocess.TimeoutExpired: print(json.dumps({'video_probe':'timeout'}))
