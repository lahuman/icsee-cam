import json, socket, struct
from urllib.parse import urlsplit
import requests
from icsee_ptz.config import Config
from icsee_ptz.dvrip import HEADER,sofia_hash
c=Config.from_env(); c.require_password()
s=requests.Session(); s.trust_env=False
soap=b'''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><GetSystemDateAndTime xmlns="http://www.onvif.org/ver10/device/wsdl"/></s:Body></s:Envelope>'''
for port in (8899,80):
    try:
        r=s.post(f'http://{c.host}:{port}/onvif/device_service',data=soap,headers={'Content-Type':'application/soap+xml; charset=utf-8'},allow_redirects=False,timeout=3)
        u=urlsplit(r.headers.get('Location',''))
        print(json.dumps({'port':port,'status':r.status_code,'content_type':r.headers.get('Content-Type'),'length':len(r.content),'redirect_host':u.hostname,'redirect_path':u.path,'xml_start':r.content.lstrip().startswith(b'<?xml') or r.content.lstrip().startswith(b'<s:Envelope'),'html':b'<html' in r.content.lower()}))
    except Exception as e: print({'port':port,'error':type(e).__name__})
def recv(sock,n):
    b=b''
    while len(b)<n:
        x=sock.recv(n-len(b))
        if not x: raise RuntimeError('EOF')
        b+=x
    return b
with socket.create_connection((c.host,34567),3) as sock:
    sock.settimeout(3)
    body=json.dumps({'EncryptType':'MD5','LoginType':'DVRIP-Web','UserName':c.username,'PassWord':sofia_hash(c.password)}).encode()+b'\n\0'
    sock.sendall(HEADER.pack(255,0,0,0,1000,len(body))+body)
    head=recv(sock,20)
    magic,ver,session,seq,msg,length=HEADER.unpack(head)
    print({'dvrip_header':{'magic':magic,'version':ver,'seq':seq,'msg':msg,'length':length}})
    if 0<length<1048576:
        raw=recv(sock,length).rstrip(b'\0\r\n ')
        try:
            reply=json.loads(raw)
            print({'login_ret':reply.get('Ret'),'reply_keys':list(reply),'session_match':int(reply.get('SessionID','0'),16)==session})
        except Exception as e: print({'body_error':type(e).__name__})
