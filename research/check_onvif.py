import json, logging
from icsee_ptz.config import Config, ProtocolError
from icsee_ptz.onvif_client import ONVIFProbe,Device
logging.disable(logging.CRITICAL)
c=Config.from_env()
for authenticated in (False, True):
    p=ONVIFProbe(c,8899)
    try:
        d=p.service(Device,f'http://{c.host}:8899/onvif/device_service',authenticated)
        for name,kw in [('GetSystemDateAndTime',{}),('GetServices',{'IncludeCapability':False}),('GetCapabilities',{'Category':'All'})]:
            try:
                value=getattr(d,name)(**kw)
                from zeep.helpers import serialize_object
                from icsee_ptz.config import redact
                print(json.dumps({'auth':authenticated,'operation':name,'value':redact(serialize_object(value),c.password)},default=str))
            except Exception as e:
                original=getattr(e,'original_exception',e)
                print({'auth':authenticated,'op':name,'type':type(original).__name__,'safe_detail':str(original) if isinstance(original,ProtocolError) else None})
    finally: p.close()
