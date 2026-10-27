"""Explicit DVRIP hardware command; never invoked by probe/status/tests."""
import argparse,json
from pathlib import Path
from icsee_ptz.config import Config,redact,safe_error
from icsee_ptz.dvrip_control import DVRIPControl
p=argparse.ArgumentParser();p.add_argument('operation',choices=['stop','right','left','up','down']);args=p.parse_args()
c=Config.from_env();ctrl=DVRIPControl(c)
try:
    result=ctrl.stop() if args.operation=='stop' else ctrl.move(args.operation,0.5,0.2)
    value=json.dumps(redact(result,c.password),ensure_ascii=False,indent=2)
    root=Path('research/motor-evidence');root.mkdir(exist_ok=True)
    (root/f'dvrip-{args.operation}.json').write_text(value+'\n')
    print(value)
except Exception as exc: print(json.dumps(safe_error(exc),ensure_ascii=False))
finally:ctrl.close()
