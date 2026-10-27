# 문제 해결

## No route to host / errno 65

초기 실패 시점 관측: Mac `192.168.1.159`, 대상 `192.168.1.168`, 인터페이스 `en0`, ARP `incomplete`, 네 포트 모두 연결 실패입니다. 경로 항목은 있지만 카메라와 통신할 수 없다는 뜻이며 이것만으로 원인을 하나로 확정할 수 없습니다.

1. ASUS 공유기 접속 장치 목록에서 카메라의 현재 IP와 접속 상태를 확인합니다. DHCP 주소가 바뀌었으면 `--host`를 사용합니다.
2. Mac과 카메라가 같은 LAN에 있는지 확인합니다. 게스트 Wi-Fi·AP/클라이언트 격리·VLAN 설정을 확인합니다.
3. macOS 시스템 설정의 개인정보 보호 및 보안 → 로컬 네트워크에서 사용 중인 터미널/앱 권한을 확인합니다. 이 작업의 샌드박스 제한을 해제한 실행에서도 실패했으므로 샌드박스만의 문제라고 단정하지 않았습니다.
4. VPN이 로컬 경로에 영향을 주는지 확인합니다.
5. WAN 차단을 유지한 상태로 터미널에서 아래 명령을 실행합니다.

```sh
route -n get 192.168.1.168
arp -n 192.168.1.168
nc -G 3 -vz 192.168.1.168 554
cd ~/DEV/icsee-ptz
source .venv/bin/activate
python -m icsee_ptz probe
```

카메라 초기화, 펌웨어 변경, SSH 활성화, WAN 허용은 필요하지 않습니다.

## ModuleNotFoundError: No module named 'onvif'

```sh
cd ~/DEV/icsee-ptz
.venv/bin/python -m pip show onvif-python
.venv/bin/python -c 'import sys,onvif; print(sys.executable); print(onvif.__file__)'
```

설치와 실행에 동일한 `.venv/bin/python`을 사용합니다. `onvif-python`은 설치 이름, `onvif`는 import 이름입니다. 이 프로젝트의 고정 API는 0.4.7입니다.

## ICSEE_PASSWORD 없음

값을 출력하는 `printenv ICSEE_PASSWORD`는 사용하지 마세요. README의 존재 확인 명령을 사용하세요. export한 셸에서 CLI를 직접 실행하거나, 셸 초기화 파일에서 설정한 경우 `zsh -ic` 환경에서 실행합니다. 비밀번호를 채팅이나 `.env`에 붙여 넣지 마세요.

## ONVIF 오류

- `authentication`: HTTP 401/403 또는 ONVIF 인증 SOAP fault. 사용자/비밀번호, 카메라 ONVIF 계정 설정, 시각 차이를 확인합니다. 장비 시각은 조회만 하며 자동으로 수정하지 않습니다.
- `soap_fault`: SOAP 응답은 있지만 해당 기능이 없거나 요청을 거부했습니다. 다른 명령 성공과 함께 판단합니다.
- `protocol`: HTML·비정상 응답·차단된 서비스 주소 등입니다. 열린 8899/80 포트만으로 ONVIF라고 판정하지 않습니다.
- `connection`/`timeout`: 서비스 지원 여부를 판단할 응답을 받지 못했습니다.

원격 오류 본문·SOAP XML은 자격증명을 포함할 수 있어 출력하지 않습니다. 오류 분류와 실패 operation만 보고합니다. ONVIF 서비스가 다른 IP·HTTPS 주소를 광고하면 현재 정책에서 거부됩니다. 실제 주소를 검토한 후 제한적으로 지원을 추가해야 합니다. WS-Discovery 멀티캐스트 및 추가 포트 탐지는 아직 구현하지 않았습니다.

## DVRIP 오류

정상 계정의 Sofia MD5 인증을 사용합니다. `Ret=106/203`은 인증 오류, 103/107은 접근 권한 오류일 수 있습니다. 계정 잠금 방지를 위해 비밀번호 추측이나 반복 인증을 하지 않습니다. 로그인 성공과 PTZ 지원/모터 동작은 별개입니다.

현재 일반 JSON 전송과 헤더 버전 0/1을 지원합니다. 이 장비는 버전1 헤더에 일반 JSON 본문을 반환합니다. 빈 본문·암호화 본문·알 수 없는 헤더/시퀀스는 성공 처리하지 않습니다.

## 영상과 웹 UI

ffprobe로 H.264 1920×1080 및 pcm_alaw 8000Hz를 실제 확인했습니다. 비밀번호가 포함된 RTSP URL을 로그·브라우저·셸 히스토리에 남기지 마세요. 웹 UI는 `python -m icsee_ptz serve`로 실행하며 127.0.0.1:8000에서 RTSP→MJPEG를 중계합니다. go2rtc의 DVRIP 영상 수신 지원만으로 PTZ 제어도 된다고 판단하지 않습니다.


## 정상 응답인데 모터가 안 움직임

현재 카메라는 최신 DVRIP Preset65535/Step1과 ONVIF 저속 요청에 정상 응답했지만 모터 이동을 확인하지 못했습니다. 구형 v4.3.0 형태의 Preset0/Step2에서 왼쪽3초 이동과 정지를 사용자가 확인했습니다. 일반 CLI는 검증 기록과 실제 장비/펌웨어가 일치할 때 이 형태를 선택합니다. `--speed 0.25 --duration 1`로 직접 타이밍을 확인하거나 로컬 UI의 버튼을 눌러 확인하세요. 자동 반복이나 속도/시간의 임의 상향은 하지 않습니다.

## 웹 UI 영상이 안 나옴

PATH의 ffmpeg가 필요한지 확인합니다. 이 Mac의 ffmpeg RTSP 입력 timeout 옵션은 `-timeout`이며 `-rw_timeout`으로는 영상이 생성되지 않았습니다. 실제 MJPEG 프레임과 브라우저 영상 출력까지 확인했습니다. 오류 로그에는 RTSP 주소를 출력하지 않습니다. UI 서버는 외부 IP로 바인딩하지 않습니다.

## ONVIF advertised XAddr가 192.168.1.10으로 나옴

실제 대상은 192.168.1.168인데 펌웨어가 다른 주소를 반환하는 것을 관측했습니다. 경로와 포트는 보존하고 host만 실제 카메라로 보정합니다. 외부 주소, 다른 포트, 자격증명이 포함된 광고 주소는 거부합니다. ONVIF GetStatus의 좌표도 항상0,0이므로 실제 모터 위치 증거로 사용하지 않습니다.
