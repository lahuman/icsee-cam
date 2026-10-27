import json,threading,time
from icsee_ptz.config import Config,safe_error
from icsee_ptz.dvrip import DVRIPClient
c=Config.from_env()
with DVRIPClient(c) as cam, DVRIPClient(c) as guard:
    stop_results=[]
    def stop(client,source):
        try: stop_results.append({'source':source,**client.ptz('up',stop=True,captured_shape=True)})
        except Exception as e: stop_results.append({'source':source,'error':safe_error(e)})
    cam.ptz('up',stop=True,captured_shape=True)
    guard.ptz('up',stop=True,captured_shape=True)
    timer=threading.Timer(0.5,stop,(guard,'watchdog'))
    try:
        timer.start()
        result=cam.ptz('right',step=1,captured_shape=True)
        time.sleep(0.5)
        print({'move':result,'shape':'SetBegin + POINT','direction':'right','step':1})
    finally:
        stop(cam,'finally');timer.cancel();timer.join()
        print({'stops':stop_results})
