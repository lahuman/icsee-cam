# 확인한 공식 자료 (2026-10-25)

## ONVIF

- 프리셋 구현(2026-10-26): [ONVIF PTZ Service Specification](https://www.onvif.org/specs/srv/ptz/ONVIF-PTZ-Service-Spec.pdf)의 preset operations 및 설치된 onvif-python 0.4.7 `services/ptz.py`, `wsdl/ver20/ptz/wsdl/ptz.wsdl`을 확인했다. 신규 SetPreset은 PresetToken 없이 요청하고 반환된 토큰을 재조회한다. 덮어쓰기·삭제·이동은 카메라 목록에 존재하는 토큰만 사용한다. GotoPreset에는 ContinuousMove와 같은 Timeout 인자를 임의로 추가하지 않으며 독립 Stop watchdog을 사용한다. 실제 저장·삭제 성공과 물리 이동 검증은 별개로 기록한다.
- [onvif-python releases](https://github.com/nirsimetri/onvif-python/releases): 0.4.7 확인 및 해당 버전 실제 설치.
- [ONVIFClient parameters](https://github.com/nirsimetri/onvif-python/blob/main/docs/core/onvif_client.md): `from onvif import ONVIFClient`, `http_digest`, `CacheMode.MEM`, `devicemgmt()/media()/ptz()` 확인.
- 설치 소스 `.venv/lib/python3.14/site-packages/onvif/client.py`, `operator.py`, `services/devicemgmt.py`, `services/media.py`, `services/ptz.py`를 직접 확인했습니다. 공식 API의 버전 차이에 대비해 0.4.7에 고정했습니다.
- 자동 GetServices/GetCapabilities 및 추측 서비스 URL fallback이 있는 상위 클라이언트 대신 서비스 클래스를 구성합니다. 실제 광고 주소만 사용하며 기본 후보 경로도 응답 전에는 미확정입니다.

## DVRIP

- [dbuezas/icsee-ptz README](https://github.com/dbuezas/icsee-ptz/blob/main/README.md)
- [asyncio_dvrip.py](https://github.com/dbuezas/icsee-ptz/blob/main/custom_components/icsee_ptz/asyncio_dvrip.py)
- [device.py](https://github.com/dbuezas/icsee-ptz/blob/main/custom_components/icsee_ptz/device.py)

확인: 20바이트 little-endian 헤더, JSON + LF/NUL, 로그인 message 1000, `DVRIP-Web`/`EncryptType=MD5`, 16바이트 MD5 인접 바이트 합을 62문자에 매핑하는 Sofia hash, `SystemInfo=1020`, `SystemFunction=1360`.

PTZ source 확인: `OPPTZControl=1400`, 네 방향 `DirectionLeft/Right/Up/Down`, 최신 시작 preset 65535, 정지는 `DirectionUp` 명령 + preset -1, 채널 동일 유지. `SetPreset/GotoPreset` 명령도 존재합니다. 실제 장비에서는 아래 구형 시작 형태로 사용자 관찰 검증을 받았습니다. 프리셋 저장/이동은 실행하지 않았습니다.

최신 upstream은 일부 펌웨어의 암호화 전송도 처리합니다. 이번 최소 클라이언트는 일반 전송만 구현하며 지원하지 않는 응답은 명확히 실패합니다. 설정 변경·계정 조회/수정·펌웨어 작업 코드는 가져오지 않았습니다.

저장장치 검사: [coordinator.py](https://github.com/dbuezas/icsee-ptz/blob/main/custom_components/icsee_ptz/coordinator.py)의 StorageInfo/WorkState=1020, Record/Detect.MotionDetect/Simplify.Encode=1042 읽기 명령과 [sensor.py](https://github.com/dbuezas/icsee-ptz/blob/main/custom_components/icsee_ptz/sensor.py)의 16진수 TotalSpace/RemainSpace(MB) 해석을 확인했습니다. 저장장치·녹화 설정은 읽기 전용이며 해당 설정 쓰기1040·포맷 명령은 허용하지 않습니다. 2026-10-26 추가한 자동 추적 설정만 아래와 같이 제한적으로 쓰기를 허용합니다.

## go2rtc

- [README](https://github.com/AlexxIT/go2rtc/blob/master/README.md)
- [DVRIP documentation](https://github.com/AlexxIT/go2rtc/blob/master/internal/dvrip/README.md)
- [DVRIP client](https://github.com/AlexxIT/go2rtc/blob/master/pkg/dvrip/client.go)

README와 소스에서 DVRIP TCP 영상 및 backchannel 지원을 확인했습니다. 로그인/hash/헤더를 교차 확인했습니다. 확인한 client 코드에는 PTZ 제어가 없으며, 영상 수신 지원을 PTZ 지원 근거로 사용하지 않았습니다. 현재 프로젝트는 go2rtc를 설치하거나 실행하지 않았습니다.

자료는 위 날짜의 main/master 소스를 읽었습니다. 배포 코드에 upstream 파일을 그대로 포함하지 않으며, 실제 설치한 ONVIF API와 모의 테스트 결과를 기준으로 구현했습니다.

## 자동 추적 설정 (2026-10-26)

- [upstream switch.py](https://github.com/dbuezas/icsee-ptz/blob/main/custom_components/icsee_ptz/switch.py): `motion_tracking`은 `Detect.DetectTrack.Enable`을 1/0으로 변경하며, 지원 능력은 `SystemFunction.OtherFunction.SupportDetectTrack`입니다.
- [upstream asyncio_dvrip.py](https://github.com/dbuezas/icsee-ptz/blob/main/custom_components/icsee_ptz/asyncio_dvrip.py): 설정 조회는 1042, 설정 쓰기는 1040이며 `Name`, `SessionID`, 설정 이름을 키로 한 전체 값을 전송합니다.
- 실제 카메라의 읽기 전용 확인: 지원 능력 true, Ret=100, `Enable=0`, `ReturnTime=300`, `Sensitivity=2`. 웹은 켜기·끄기만 노출하며 감도·복귀 시간을 포함한 다른 필드는 읽은 값을 보존합니다. 1040 명령은 자동 추적 설정 이름에만 허용합니다.

## 추가 호환성 검증

- 시간 동기화: [upstream asyncio_dvrip.py](https://github.com/dbuezas/icsee-ptz/blob/main/custom_components/icsee_ptz/asyncio_dvrip.py)의 `OPTimeQuery=1452`, `OPTimeSetting=1450`, `%Y-%m-%d %H:%M:%S` 문자열을 확인했습니다. 1450은 재부팅 명령과 번호가 같으므로 이 프로젝트는 `Name=OPTimeSetting`과 유효한 시간 문자열만 허용합니다. 한국 시간으로 명시적인 단발 동기화 후 재조회 검증을 수행하며, ACK만으로 성공을 표시하지 않습니다.

- [v4.3.0 binary_sensor.py](https://github.com/dbuezas/icsee-ptz/blob/v4.3.0/custom_components/icsee_ptz/binary_sensor.py): async_move의 기본 preset=0, step=2 및 STOP preset=-1을 직접 확인했습니다. 현재 장비는 이 시작 값으로 실제 좌 이동·정지와 위 이동이 관찰됐습니다.
- [실제 제조사 패킷 기록](https://gist.github.com/knight-of-ni/26be98747cef99cd4fe7): Pattern=SetBegin 및 POINT 포함 형식의 근거입니다. 실제 시험에서는 Ret100만 확인됐고 사용자 모터 관찰은 미확인입니다.
- DVRIP version=1 헤더와 일반 JSON 로그인/SystemInfo 응답을 실제로 확인했습니다. 버전 값만으로 AES 암호화 여부를 단정하지 않고 본문을 검사합니다.
- ONVIF 응답이 반환한 사설 IP가 실제 대상과 달랐습니다. 서비스 주소의 경로와 포트를 보존하고 접속 host만 사용자가 지정한 카메라 IP로 고정합니다. 광고된 다른 host로 접속하지 않습니다.
- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/) 및 [StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/) 공식 문서 확인 후 로컬 웹 UI 구현.
- 설치 ffmpeg의 `-h demuxer=rtsp`, `-h muxer=mpjpeg`에서 `-timeout` 및 `-boundary_tag`를 직접 확인했습니다. 실제 ffprobe 영상 코덱은 H.264, 1920×1080입니다.
