"""Explicit hardware test helper. Default inspect never moves a motor."""
import argparse,json,logging
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
import requests
from requests.auth import HTTPDigestAuth
from icsee_ptz.config import Config,redact,safe_error
from icsee_ptz.control import ONVIFControl
from icsee_ptz.onvif_client import Media,MEDIA_NS,CameraOnlyAdapter
logging.disable(logging.CRITICAL)
p=argparse.ArgumentParser(); p.add_argument('operation',choices=['inspect','right','left','up','down']); args=p.parse_args()
c=Config.from_env(); ctrl=ONVIFControl(c)
root=Path('research/motor-evidence'); root.mkdir(exist_ok=True)
def capture(label):
    try:
        media=ctrl.probe.service(Media,ctrl.evidence['resolved_services'][MEDIA_NS])
        uri=media.GetSnapshotUri(ProfileToken=ctrl.profile_token).Uri
        u=urlsplit(uri)
        if u.scheme!='http': return {'snapshot':'unsupported_scheme'}
        url=urlunsplit(('http',f'{c.host}:{u.port or 80}',u.path,u.query,''))
        with requests.Session() as s:
            s.trust_env=False; s.auth=HTTPDigestAuth(c.username,c.password)
            s.mount('http://',CameraOnlyAdapter(c.host))
            r=s.get(url,timeout=3)
            if r.status_code==200 and r.content.startswith(b'\xff\xd8'):
                path=root/f'{label}.jpg'; path.write_bytes(r.content)
                return {'snapshot':str(path),'bytes':len(r.content)}
            return {'snapshot':'failed','http_status':r.status_code,'type':r.headers.get('Content-Type')}
    except Exception as e: return {'snapshot_error':safe_error(e)}
result={}
try:
    result['before_status']=ctrl.status()
    result['before_image']=capture(f'{args.operation}-before')
    if args.operation=='inspect':
        result['stop']=ctrl.stop()
        try: result['presets']=ctrl.presets()
        except Exception as e: result['preset_error']=safe_error(e)
    else:
        result['move']=ctrl.move(args.operation,duration=0.5,speed=0.2)
        result['after_status']=ctrl.status()
        result['after_image']=capture(f'{args.operation}-after')
finally: ctrl.close()
value=json.dumps(redact(result,c.password),ensure_ascii=False,indent=2,default=str)
(root/f'{args.operation}.json').write_text(value+'\n')
print(value)
