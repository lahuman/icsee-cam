# iCSee 로컬 PTZ 제어

제조사 클라우드 없이 로컬 네트워크에서 iCSee/Xiongmai 카메라의 영상, 방향 제어, 오디오, SD 녹화와 타임랩스를 사용하는 도구입니다.

**처음 설치한다면 [설치·설정·장비 등록 가이드](docs/GETTING-STARTED.md)부터 시작하세요.**

설정이 어렵다면 [AI 에이전트와 함께 설치하기](docs/AI-SETUP.md)의 요청문을 프로젝트 폴더를 연 AI에 복사하세요. 환경 점검과 설치를 맡기고, 비밀번호 입력과 실제 영상 확인은 직접 진행할 수 있습니다.

1. `setup`: 카메라 IP와 웹 관리자 계정을 설정합니다. 카메라 비밀번호는 저장하지 않습니다.
2. `doctor`: 설치와 설정을 점검합니다. 카메라에 접속하지 않습니다.
3. `register`: 카메라에 로그인해 모델·펌웨어를 읽고 등록합니다. 모터는 움직이지 않습니다.
4. `serve`: 로그인 후 영상을 확인합니다. 검증 전 방향 제어는 잠겨 있습니다.
5. 선택적으로 `verify-motion`: 카메라를 직접 보면서 네 방향 이동·정지를 확인한 뒤 제어를 활성화합니다.

새 설치에는 `compose.quickstart.yaml`을 사용합니다. 기존 운영용 `compose.yaml`, 계정, 영상과 검증 기록은 그대로 유지됩니다. 지원 조건과 실제 검증 장비는 아래 [지원 장비](#지원-장비)를 참고하세요.

아래는 기존 장비의 운영 기록과 상세 기능 안내입니다. 처음 설치할 때는 위 가이드를 기준으로 진행하세요.

**보안·동시 사용 개선:** 클라이언트별 요청 제한, 세션 만료 시 연결 종료, 동시 시청 3세션, 영상 변환 합계 3개, 역할 분리와 영속 작업 큐를 제공합니다. 외부 HTTPS·내부 HTTP 구성은 유지합니다. [운영 적용·계정 이관·제한값](docs/SECURITY-OPERATIONS.md)을 확인하세요.

**카메라 SD 녹화 보기:** 날짜·시간별 목록 조회 후 필요한 10·30·60초 구간만 가져와 웹에서 재생할 수 있습니다. 자동 조회·전체 복사는 하지 않습니다. [사용법과 검증 범위](SD-RECORDINGS.md)를 참고하세요.

**실시간 영상 옆에서 주변 소리 듣기·말하기를 클릭으로 켜고 끄는 UI를 제공합니다.** 실제 듣기 데이터 수신과 사용자 청취로 스피커 테스트음 재생을 확인했습니다. 브라우저 마이크 실사용은 휴대폰 시험 때까지 보류했습니다. [오디오 사용법과 검증 범위](AUDIO.md)를 참고하세요.

**30초 간격 타임랩스 촬영·일별 H.264 요약 기능을 추가했습니다.** 실제 수집·중지·재시작·MP4 생성 시험을 거쳐 기간·컷·용량 제한을 적용했습니다. 현재 상태는 CLI로 조회하며, 사용법은 [TIMELAPSE.md](TIMELAPSE.md)를 참고하세요.

**Docker Compose 구성:** 웹과 선택적 타임랩스 서비스를 분리했습니다. ARM64 빌드·컨테이너 자동 테스트·카메라 읽기 연결을 검증했습니다. 기존 Mac 실행에서 전환하는 방법과 검증 범위는 [DOCKER.md](DOCKER.md)에 있습니다.

macOS의 로컬 네트워크에서 iCSee/Xiongmai 카메라 영상을 보고 PTZ를 제어합니다. 제조사 클라우드 API는 사용하지 않으며 카메라의 WAN 차단을 유지합니다.

현재 카메라 `192.168.1.168`에서 **DVRIP 구형 시작 방식(Preset=0, Step=2)의 실제 왼쪽 이동과 정지를 사용자 관찰로 확인**했습니다. 위쪽 이동도 확인했습니다. 오른쪽·아래쪽은 명령/STOP 응답까지 확인했으나 실제 방향 검증은 아직 불확실합니다. 모든 방향이 완전히 검증됐다고 표시하지 않습니다.

## 지원 장비

일반 JSON DVRIP 인증을 지원하는 iCSee/Xiongmai 계열의 로컬 IP 카메라를 대상으로 합니다. 현재 실제 검증 기록은 아래 하드웨어·펌웨어 조합 1대입니다. iCSee 앱 사용 여부나 ONVIF 지원 표시만으로 호환성을 보장하지 않습니다.

| 구분 | 실제 검증 장비 |
| --- | --- |
| 하드웨어 식별자 | `XM530_RH50X20_8M` — 카메라가 보고한 값이며 판매 제품명은 미확인 |
| 펌웨어 | `V5.00.R02.000306bf.10010.343917.0000000` |
| DVRIP | TCP 34567, 일반 JSON 요청·응답 및 MD5 방식 로그인 확인 |
| RTSP | TCP 554, H.264 1920×1080 영상·G.711 A-law 8kHz 오디오 확인 |
| ONVIF | TCP 8899, 서비스 조회·스냅샷·프리셋 기능 확인 |

이 조합에서 확인한 기능과 제한은 다음과 같습니다. 실제 검증 기록은 2026-10-25~26 기준입니다.

| 기능 | 검증 범위 |
| --- | --- |
| 실시간 영상 | RTSP 수신, 웹 MJPEG 출력 확인 |
| 방향 제어 | DVRIP `legacy` 방식(Preset 0, Step 2)으로 왼쪽 이동·정지와 위쪽 이동 확인. 오른쪽·아래쪽은 명령·STOP 응답만 확인 |
| 위치 프리셋 | 조회·저장·삭제, 가까운 수평 위치 왕복·중간 정지·원위치 복귀 확인 |
| 오디오 | 듣기 데이터 수신과 스피커 테스트음 재생 확인. 브라우저 실제 마이크 송출은 미검증 |
| SD 녹화 | 단일 카메라 채널 0의 목록 조회·시간 지정 구간 수신·MP4 재생 확인. 검증 구간에는 음성 트랙 없음 |
| 타임랩스 | ONVIF 스냅샷 수집과 H.264 MP4 생성 확인 |

다른 하드웨어나 펌웨어는 `register`로 인증·장비 정보를 확인하고, `serve`에서 영상을 확인하세요. 방향 제어는 직접 관찰하며 `verify-motion`을 완료해야 활성화됩니다. 등록 성공은 영상·오디오·모터 기능의 검증을 뜻하지 않습니다.

암호화 DVRIP 전용 장비와 ONVIF만 지원하는 장비는 현재 등록 흐름에서 지원하지 않습니다. 다채널 NVR/DVR, H.265 영상, 다른 오디오 코덱의 호환성은 검증하지 않았습니다. 줌·홈은 지원 미확인으로 비활성화돼 있으며, 현재 검증 장비의 ONVIF PTZ는 정상 응답에도 실제 이동을 확인하지 못해 자동 제어에 사용하지 않습니다.

## 설치

```sh
cd ~/DEV/icsee-ptz
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m pytest -q
```

Python 3.10 이상을 대상으로 작성했습니다. 실제 설치/실행 환경은 macOS Apple Silicon arm64, Python 3.14.3입니다. 다른 Python 버전은 아직 실제 검증하지 않았습니다. `ffmpeg`와 `ffprobe`는 PATH에 있어야 합니다. 현재 Mac에는 둘 다 설치돼 있습니다.

| 주요 의존성 | 설치 버전 | 목적 |
| --- | --- | --- |
| onvif-python | 0.4.7 | 공식 API 확인, 번들 WSDL, ONVIF 기능 탐지 |
| zeep | 4.3.3 | SOAP 통신 |
| requests | 2.34.2 | 카메라에 한정된 ONVIF HTTP 통신 |
| FastAPI | 0.162.0 | 로컬 제어 API |
| Uvicorn | 0.54.0 | 127.0.0.1 서버 |
| websockets | 17.3 | 로컬 실시간 오디오 송수신 |
| pytest | 9.1.1 | 모터 없는 단위/모의 테스트 |
| httpx | 0.28.1 | API 테스트 |
| setuptools | 84.2.1 | Python 3.14 editable 설치 |

DVRIP는 별도 라이브러리를 설치하지 않고 공식 참고 저장소에서 검증한 wire 형식으로 최소 클라이언트를 구현했습니다. 전체 설치 버전은 `requirements-lock.txt`에 기록했습니다.

## 환경 변수

비밀번호는 `ICSEE_PASSWORD`에서만 읽습니다. 소스, 설정 파일, 로그, Git 또는 브라우저로 전달하지 않습니다. 사용자 기본값은 `admin`, 변경하려면 `ICSEE_USERNAME`을 설정합니다.

이미 변수를 설정한 터미널에서 실행하세요. 새로 입력할 때는 zsh에서 숨김 입력을 사용할 수 있습니다.

```sh
read -rs 'ICSEE_PASSWORD?Camera password: '; echo
export ICSEE_PASSWORD
.venv/bin/python -c 'import os; print("설정됨" if os.getenv("ICSEE_PASSWORD") else "없음")'
```

GUI 앱 실행 후 다른 터미널의 export는 이미 실행된 앱에 소급 전달되지 않습니다. 이번 환경에서는 일반 실행 셸에 변수가 없고 대화형 `zsh -ic`에서 확인됐습니다. 값 자체를 출력하지 마세요.

## CLI

```sh
source .venv/bin/activate
python -m icsee_ptz probe
python -m icsee_ptz probe --output research/probe-new.json
python -m icsee_ptz status
python -m icsee_ptz --protocol dvrip status
python -m icsee_ptz move left --duration 1 --speed 0.25
python -m icsee_ptz move right --duration 1 --speed 0.25
python -m icsee_ptz move up --duration 1 --speed 0.25
python -m icsee_ptz move down --duration 1 --speed 0.25
python -m icsee_ptz stop
python -m icsee_ptz presets
```

- `probe`는 ONVIF 8899/80을 먼저 읽기 전용으로 탐지하고, DVRIP 34567의 인증·SystemInfo·SystemFunction을 확인합니다. 모터는 움직이지 않습니다.
- 열린 TCP 포트만으로 프로토콜 지원을 판정하지 않습니다. ONVIF 서비스 주소/기능은 SOAP 실제 응답으로 확인합니다.
- `camera-profile.json`은 비밀정보가 없는 사용자 관찰 검증 기록입니다. 현재 장비의 host, hardware, firmware가 일치하면 자동 이동/STOP은 검증된 DVRIP 방식으로 실행합니다. 장비가 다르면 자동 제어를 거부합니다.
- 기본 이동 시간은 **0.5초**, 일반 CLI 최대는 **2초**, 기본 속도는 **0.25 → Step 2/8**입니다. Step 1 동작은 확인되지 않아 기본값으로 사용하지 않습니다.
- 속도는 0 초과 1 이하이며 장비 Step 1~8로 변환됩니다. NaN/무한대와 범위 밖 값을 거부합니다.
- 이동 전 STOP 응답을 확인하고, 이동 후 `finally`에서 STOP을 보냅니다. 별도 인증 연결의 watchdog도 제한 시간에 STOP을 보냅니다. 예외/일반 Ctrl-C도 STOP을 시도합니다.
- 응답 수락(`move_response: accepted`)은 해당 호출의 물리적 이동을 자동 검증했다는 뜻이 아닙니다. 실제 모터 관찰과 구분합니다.
- `status`의 기본 ONVIF 좌표는 이 장비에서 항상 `(0, 0)`, `IDLE`로 보고돼 물리적 위치 증거로 쓰지 않습니다. 실제 제어 경로는 DVRIP입니다.
- CLI `presets`는 ONVIF 목록 조회만 수행합니다. 웹의 **방향 제어 → 추가 기능 → 위치 프리셋**은 admin 전용으로 조회·현재 위치 저장·덮어쓰기·이동·삭제를 제공합니다. 가까운 위치 왕복·중간 정지·원위치 복귀를 영상과 사용자 관찰로 검수해 이동을 활성화했습니다. 이동은 최대 2초 후 정지하며 먼 위치의 도달은 보장하지 않습니다.
- ONVIF 실험 이동은 `--protocol onvif move right --duration 0.5 --speed 0.2 --experimental`로 명시할 수 있지만, 현재 장비에서 해당 설정의 실제 이동은 확인하지 못했습니다. 자동 제어 경로로 선택하지 않습니다.
- 카메라 IP 변경은 `python -m icsee_ptz --host 새_IP probe`로 진단합니다. 다른 장비는 별도 실제 검증 기록이 필요합니다.

사용자 요청에 따른 **단일 왼쪽 3초 테스트**에서만 내부 `max_duration=3`을 명시적으로 사용했습니다. 일반 CLI와 웹의 2초 제한을 변경하지 않았습니다. 자동 반복 이동 테스트는 하지 않습니다.

## 웹 UI

```sh
cd ~/DEV/icsee-ptz
.venv/bin/python -m icsee_ptz serve --port 8000
```

[로컬 UI 열기](http://127.0.0.1:8000)

- 실제 RTSP 영상을 서버 측 ffmpeg로 MJPEG(960px, 5fps, 오디오 제외)로 변환해 전달합니다. 브라우저는 로컬 `/video`만 요청합니다.
- 상/하/좌/우 버튼을 누르면 이동, 손을 떼면 STOP입니다. STOP 버튼과 속도 슬라이더도 제공합니다.
- 한 번 누를 때 최대 2초입니다. 길게 이동하려면 손을 떼고 다시 누르세요. 자동 반복이나 자동 재시작은 없습니다.
- pointer cancel, 포커스 상실, 탭 숨김/닫힘에서 STOP 요청을 보냅니다. 연결 중 손을 놓은 요청은 취소해 나중에 갑자기 이동하지 않도록 했습니다.
- 브라우저 STOP 요청이 유실돼도 서버 측 제한 시간과 별도 DVRIP 연결의 watchdog이 STOP을 시도합니다.
- 서버는 **127.0.0.1에만 바인딩**합니다. 루프백 클라이언트, Host, Origin과 제어 헤더를 검사해 다른 웹사이트의 요청을 거부합니다. 외부 바인딩 옵션은 제공하지 않습니다.
- 시작 시 인증·정보 조회와 영상 수신만 수행하며 자동으로 모터를 움직이지 않습니다.
- 카메라 연결 확인 상태와 현재 DVRIP 경로를 표시합니다. 연결 상태는 마지막 연결 검사 결과입니다.
- 프리셋 조회·저장·삭제는 실제 장비 재조회로 확인했습니다. 가까운 위치 이동·중간 정지·원위치 복귀도 실제 영상과 사용자 관찰로 확인해 admin 이동을 활성화했습니다.

**실제 한계:** 이 DVRIP 방식에서 장비 자체의 이동 timeout은 확인되지 않았습니다. 물리적 네트워크 단절이나 서버 강제 종료로 STOP이 전달되지 않는 경우까지 정지를 보장할 수는 없습니다. watchdog은 브라우저 입력 중단과 일반 요청 지연을 처리합니다. UI는 로컬 직접 관찰 제어 용도이며 무인 자동 운전으로 사용하지 마세요.

## 실제 결과 — 2026-10-25 KST

| 항목 | 결과 |
| --- | --- |
| Python/venv/API import | arm64 Python 3.14.3, onvif-python 0.4.7 정상 |
| 비밀번호 | 대화형 셸에서 존재 확인, 값/해시 미출력·미저장 |
| 초기 네트워크 | 한때 errno 65/ARP incomplete, 이후 복구; 최초 원인 미확정 |
| 현재 네트워크 | ping 3/3 응답, TCP 80/554/8899/34567 OPEN |
| ONVIF | **8899에서 실제 SOAP 서비스 확인**; 80은 HTML 응답 |
| 실제 모델/펌웨어 | XM530_RH50X20_8M / V5.00.R02.000306bf.10010.343917.0000000 |
| ONVIF 주소 제약 | 실제 카메라가 `192.168.1.10`을 광고; 서비스 경로/포트는 유지하고 호스트만 실제 대상 IP로 보정 |
| ONVIF PTZ | profiles/nodes/options/ContinuousMove 지원 광고; 0.2 속도·0.5초 이동은 정상 응답이나 사용자 실제 이동 미확인 |
| ONVIF 좌표 | 실제 위치와 무관하게 `(0,0)`/IDLE 보고; 위치 증거로 사용하지 않음 |
| DVRIP 인증/정보 | Ret=100, SystemInfo/SystemFunction 정상; 버전1 헤더+일반 JSON 응답 확인 |
| DVRIP 최신 시작 값 | Preset=65535, Step1, 0.5초: 수락 응답이나 사용자 이동 미확인 |
| DVRIP 구형 시작 값 | **Preset=0, Step2, 왼쪽 3초: 사용자 이동 및 정지 확인** |
| 위쪽 | 구형 모드 1초 테스트 후 사용자 실제 이동 확인 |
| 오른쪽/아래쪽 | 구형 모드 1초 요청·STOP 정상 응답; 사용자 관찰 불확실 |
| 프리셋 | ONVIF 최대 100개 광고, 저장·재조회·삭제 확인. 가까운 위치 이동·DVRIP 정지 검수 완료, admin 이동 활성 |
| RTSP | ffprobe 실제 확인: H.264 1920×1080, pcm_alaw 8000Hz |
| 웹 영상 | 실제 ffmpeg MJPEG 프레임 생성 및 브라우저 영상 출력 확인 |
| 웹 버튼 | 모의 API/취소/2초 제한 테스트 통과; 웹 UI에서 실제 이동을 사용자 확인; 방향별 세부 확인 및 버튼 해제 즉시 정지의 별도 관찰은 미기록 |

세부 모터 결과는 [research/MOTOR_TEST_RESULTS.md](research/MOTOR_TEST_RESULTS.md), 프로토콜 근거는 [research/SOURCES.md](research/SOURCES.md), 문제 해결은 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)를 참고하세요.

## 검증 명령과 파일

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python -c 'import sys; from onvif import ONVIFClient; print(sys.executable)'
```

`icsee_ptz/`는 진단·제어·웹·영상 코드, `tests/`는 카메라 없이 실행되는 모의 테스트입니다. 실제 장비 테스트는 `research/`에 분리했으며 일반 테스트 실행에서 모터를 움직이지 않습니다. 진단 JSON과 실제 카메라 이미지는 `.gitignore`로 제외합니다. 카메라 설정 쓰기는 아래 자동 추적 켜기·끄기와 명시적으로 실행하는 시간 동기화에 한정합니다. 인증 우회, SSH 활성화, 초기화, 펌웨어 변경은 수행하지 않았습니다.

### Local Camera UI

실시간 화면은 영상 중심의 Dark UI, 원형 PTZ 패드, 개별 장치/스트림 상태, 재연결, 스냅샷, 전체화면과 설정 패널을 제공합니다. 프리셋 관리·이동 기능은 admin에게만 제공합니다. 가까운 위치의 이동·정지 검수를 완료했으며 최대 2초 후 정지합니다. 줌·홈은 지원 미확인 상태로 비활성화합니다. [UI 설계·검증 결과와 변경 전후 화면](docs/UI-UX-REDESIGN.md)을 참고하세요.

### 자동 추적 켜기 / 끄기

실시간 영상 아래에 **자동 추적 · 켜짐 / 꺼짐**을 표시합니다. 실시간 화면을 열면 현재 상태를 읽고, 화면을 보는 동안 30초마다 갱신합니다. 화면을 떠나거나 탭을 숨기면 자동 조회를 멈춥니다. 연결 오류는 **확인 불가**로 표시합니다. 방향 제어는 처음 접힌 상태이며 제목을 누르면 펼쳐집니다.

영상 아래 **자동 추적 설정** 또는 웹 상단 **설정 → 자동 추적**에서 카메라 자체의 움직임 추적을 켜고 끕니다. 설정 창을 열 때 현재 값을 읽으며, 토글을 누를 때만 변경합니다. 변경 후 카메라 설정을 다시 읽어 적용 여부를 확인하고 실시간 화면의 상태도 함께 갱신합니다. 앱에서 바꾼 상태는 주기적 조회 또는 **상태 새로고침**으로 확인할 수 있습니다.

기존 감도·복귀 시간은 보존하고 `Detect.DetectTrack.Enable`만 변경합니다. 움직임 감지·녹화 설정은 별도로 유지됩니다. 카메라에 저장되는 설정이므로 웹을 닫아도 자동 추적 상태가 유지됩니다. 통신 오류로 결과가 불확실하면 토글을 잠그고 현재 상태를 다시 조회하도록 안내합니다. 설정 쓰기는 자동 재시도하지 않습니다. 조회·변경 API(`/api/settings/tracking`)는 기존 로그인·Host·Origin·제어 헤더 검사를 적용합니다.

2026-10-26 운영 웹에서 실제 브라우저로 꺼짐 → 켜짐 → 꺼짐을 확인했습니다. 시험 후 `Enable=0`, `Sensitivity=2`, `ReturnTime=300`을 카메라에서 재조회했습니다. 데스크톱·375px 모바일 설정 화면과 자동 테스트 182개를 확인했습니다. 이 검증은 설정 적용 여부이며, 움직이는 대상을 실제로 따라가는 물리적 추적 성능은 별도 관찰이 필요합니다.

### 카메라 시간 동기화

영상에 표시되는 카메라 시계가 틀리면 서버의 현재 한국 시간(`Asia/Seoul`)으로 한 번 맞출 수 있습니다. `clock`은 조회만 수행하며 `--sync`를 붙일 때만 시간을 변경합니다. 변경 후 카메라 시간을 다시 읽어 서버와의 오차가 2초 이내인지 확인합니다. 서버 시계가 기준이며, 브라우저 시간이나 컨테이너의 기본 시간대에 의존하지 않습니다.

```sh
.venv/bin/python -m icsee_ptz clock
.venv/bin/python -m icsee_ptz clock --sync
# Docker에서 조회 / 동기화
docker compose exec -T web python -m icsee_ptz clock
docker compose exec -T web python -m icsee_ptz clock --sync
```

2026-10-26 07:41 KST 실제 동기화에서 약 111.8초 늦었던 시계를 보정하고, 재조회 오차 약 1.2초를 확인했습니다. 카메라의 네트워크·녹화 정책과 기존 저장 영상은 변경하지 않습니다.

Docker 웹 서비스에는 `ICSEE_DAILY_CLOCK_SYNC=1`로 **매일 04:00 한국 시간** 자동 동기화를 활성화했습니다. 브라우저가 닫혀 있어도 동작합니다. 하루 실행 여부를 60초마다 확인하며, Docker가 꺼져 있었다면 04:00 이후 시작할 때 당일분을 한 번 처리합니다. 지난 날짜의 작업을 몰아서 실행하지 않습니다. `data/clock-sync.sqlite3`에 카메라·날짜별 시도와 결과를 저장해 컨테이너 재시작이나 중복 프로세스에서 재실행을 막습니다.

응답 유실 후 반복 쓰기를 방지하기 위해 시도 기록을 먼저 저장합니다. 실패·중단된 당일 시도는 자동 재시도하지 않으며 다음 날 다시 시도합니다. 실제 실행에는 Docker와 서버가 실행 중이어야 합니다. 자동 동기화를 끄려면 Compose 웹 환경의 `ICSEE_DAILY_CLOCK_SYNC`를 `0`으로 변경하고 웹 컨테이너를 재생성합니다.

```sh
# 카메라 접속 없이 활성화 여부·마지막 결과·다음 실행 시간 조회
docker compose exec -T web python -m icsee_ptz clock --schedule-status
```
