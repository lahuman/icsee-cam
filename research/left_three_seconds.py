"""Single three-second left test explicitly requested by the user."""
import json
from pathlib import Path
from icsee_ptz.config import Config,redact,safe_error
from icsee_ptz.dvrip_control import DVRIPControl
c=Config.from_env();ctrl=DVRIPControl(c)
try:
    result=ctrl.move('left',duration=3,speed=0.25,max_duration=3,legacy_start=True)
    value=json.dumps(redact(result,c.password),ensure_ascii=False,indent=2)
    Path('research/motor-evidence/dvrip-left-three-seconds.json').write_text(value+'\n')
    print(value)
finally:ctrl.close()
