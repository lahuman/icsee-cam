# 처음 설치하고 카메라 등록하기

목표는 자신의 카메라 영상을 먼저 확인하고, 필요한 경우 방향 제어를 검증해 활성화하는 것입니다. 한 설치에 카메라 한 대를 등록합니다. 기존 운영 환경은 그대로 두고 별도 설정·데이터 공간에서 시작할 수 있습니다.

## 준비

- 카메라와 실행할 컴퓨터가 같은 로컬 네트워크에서 통신할 수 있어야 합니다.
- 카메라의 사설 IPv4 주소와 카메라 로그인 ID·비밀번호가 필요합니다.
- 현재 장비 등록은 일반 JSON DVRIP 인증을 지원하는 iCSee/Xiongmai 카메라용입니다. 암호화 DVRIP 전용 장비나 모든 ONVIF 카메라를 지원하는 것은 아닙니다.
- 카메라 로그인과 이 프로그램의 웹 로그인은 별개입니다.
- 영상은 카메라 RTSP 지원이 필요합니다. 오디오·스냅샷·SD 녹화는 장비별로 다를 수 있습니다.

실제 검증 장비는 `XM530_RH50X20_8M`, 펌웨어 `V5.00.R02.000306bf.10010.343917.0000000` 조합 1대입니다. 판매 제품명은 확인되지 않았습니다. 기능별 검증 범위와 다른 장비의 제한은 [지원 장비](../README.md#지원-장비)를 참고하세요.

## 방법 A: Docker Compose

Docker와 Compose를 설치하고 실행한 뒤, 내려받은 프로젝트 폴더에서 아래 명령을 실행하세요. 이 구성은 독립된 `icsee-starter` 프로젝트와 전용 설정·데이터 볼륨을 사용합니다. 기존 `compose.yaml`에는 영향을 주지 않습니다.

```sh
docker compose -f compose.quickstart.yaml build
docker compose -f compose.quickstart.yaml run --rm web setup
```

화면에서 카메라 IP, 카메라 로그인 ID, 새 웹 관리자 ID와 비밀번호를 입력합니다. 웹 비밀번호는 해시로 저장합니다. `setup`은 카메라에 접속하지 않으며, 다시 실행해도 기존 설정이나 계정을 덮어쓰지 않습니다.

카메라 비밀번호는 실행할 터미널에서 숨김 입력으로 설정합니다.

```sh
# macOS 기본 zsh
read -rs 'ICSEE_PASSWORD?Camera password: '; echo
export ICSEE_PASSWORD
```

```bash
# Linux bash
read -rsp 'Camera password: ' ICSEE_PASSWORD; echo
export ICSEE_PASSWORD
```

설치 점검과 읽기 전용 등록 후 웹을 실행합니다.

```sh
docker compose -f compose.quickstart.yaml run --rm web doctor
docker compose -f compose.quickstart.yaml run --rm web register
docker compose -f compose.quickstart.yaml up -d web
```

[http://127.0.0.1:8000](http://127.0.0.1:8000)을 열고 **setup에서 만든 웹 계정**으로 로그인하세요. 영상 확인 모드에서는 방향 제어가 잠겨 있습니다. 장비 등록은 네트워크와 인증·식별 정보를 확인하며 영상 수신까지 보장하지 않습니다.

이미 8000 포트를 사용 중이면 `up` 전에 `export ICSEE_PORT=8001`을 실행하고 [http://127.0.0.1:8001](http://127.0.0.1:8001)을 여세요. 이후 Compose 명령도 같은 터미널의 환경 변수로 실행합니다. 다른 터미널에서 컨테이너를 재생성할 때는 카메라 비밀번호와 변경한 포트를 다시 설정하세요.

카메라 비밀번호는 소스나 생성된 설정 파일에 저장하지 않습니다. 실행 중에는 기존 방식과 동일하게 서버/컨테이너 환경 변수로 전달됩니다. Docker 설정을 공유할 때 `docker compose config` 전체 출력에는 이 값이 포함될 수 있으므로 공유하지 마세요.

설정과 영상은 Docker 볼륨에 보존됩니다. `down`은 볼륨을 보존하지만 `down -v`는 삭제하므로 사용하지 마세요. 새 설치 구성은 로컬 컴퓨터에서만 접근하도록 포트를 공개하며, 자동 시간 동기화·자동 타임랩스는 시작하지 않습니다.

## 방법 B: Python으로 실행

Python 3.10 이상과 `ffmpeg`, `ffprobe`가 필요합니다. 실제 기존 운영 검증은 macOS ARM64 / Python 3.14에서 진행했습니다. 다른 OS·Python 버전은 별도 검증이 필요합니다.

프로젝트 폴더에서 실행합니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .

# 현재 운영 환경과 분리된 새 설치 공간
export ICSEE_PROJECT_DIR="$PWD/starter"
.venv/bin/python -m icsee_ptz setup
```

방법 A의 zsh 또는 bash 명령으로 `ICSEE_PASSWORD`를 설정한 뒤:

```sh
.venv/bin/python -m icsee_ptz doctor
.venv/bin/python -m icsee_ptz register
.venv/bin/python -m icsee_ptz serve
```

이후에도 프로젝트 폴더에서 같은 `ICSEE_PROJECT_DIR`과 비밀번호 환경 변수로 실행하세요. 생성되는 파일은 다음과 같습니다.

| 파일 | 내용 |
| --- | --- |
| `starter/config/camera.json` | 카메라 IP, 사용자명, 연결 제한 시간 |
| `starter/config/web-auth.json` | 웹 계정, 역할, 비밀번호 해시 |
| `starter/config/camera-profile.json` | 등록된 장비·펌웨어와 선택적 이동 검증 기록 |
| `starter/data/` | 영상, 작업 기록, 실행 상태 |

CLI `--host` → `ICSEE_HOST` → 저장된 카메라 주소 순으로 우선 적용합니다. 카메라 사용자명은 `ICSEE_USERNAME` → 저장된 사용자명 순입니다. 카메라 비밀번호는 `ICSEE_PASSWORD`에서만 읽습니다. 기존 설정 파일이 없는 설치는 기존 기본값·루트 검증 프로필을 유지합니다. 새 사용자는 `setup`으로 자신의 주소를 명시하세요.

## 방향 제어 활성화: 선택 사항

먼저 영상이 나오는지 확인하세요. 다음 검증은 **카메라를 실제로 움직입니다.** 카메라를 직접 볼 수 있는 상태에서 웹 서버를 중지하고 실행합니다. 검증 명령과 웹 서버는 같은 실행 잠금을 사용하므로 동시에 실행할 수 없습니다. 별도 앱이나 다른 설치에서도 카메라 제어를 중지하세요.

Docker:

```sh
docker compose -f compose.quickstart.yaml stop web
docker compose -f compose.quickstart.yaml run --rm web verify-motion --start-mode legacy
docker compose -f compose.quickstart.yaml up -d web
```

Python은 실행 중인 웹 서버를 Ctrl-C로 종료한 후:

```sh
.venv/bin/python -m icsee_ptz verify-motion --start-mode legacy
.venv/bin/python -m icsee_ptz serve
```

각 방향마다 `MOVE`를 입력한 경우에만 속도 Step 2로 0.5초 이동합니다. 실제 움직인 방향을 입력하고 실제 정지를 확인한 경우에만 `STOP`을 입력합니다. 네 방향이 모두 확인되어야 검증 프로필을 저장합니다. 입력을 취소하거나 움직임·정지가 불확실하면 기존 프로필을 변경하지 않습니다. 카메라의 방향 반전은 관찰한 방향을 기준으로 웹 버튼에 반영합니다.

`legacy`는 시작 Preset 0, `modern`은 Preset 65535 방식입니다. 기존 검증 장비는 `legacy`를 사용합니다. 움직이지 않았다면 성공으로 확인하지 말고 중단하세요. 필요할 때 `modern`을 별도로 선택해 시험할 수 있으며 자동 반복 시험은 없습니다.

일반 웹 제어는 기존처럼 최대 2초 이동 후 STOP을 시도합니다. 카메라 자체의 이동 timeout은 확인되지 않았으므로 물리적 네트워크 단절이나 서버 강제 종료 시 정지를 보장하지 않습니다. 관찰 가능한 환경에서 사용하세요. 프리셋 이동은 이 검증으로 활성화되지 않으며 별도의 기존 검증 조건을 유지합니다.

## 문제가 생기면

| 증상 | 다음 확인 |
| --- | --- |
| `doctor`에서 비밀번호 누락 | 같은 터미널에서 `ICSEE_PASSWORD`를 설정했는지 확인 |
| 웹 계정 오류 | `setup` 완료 여부 확인; 기존 계정은 `accounts` 명령으로 관리 |
| ffmpeg/ffprobe 없음 | PATH에 설치하거나 ffmpeg가 포함된 Docker 이미지 사용 |
| 등록 실패 | 주소·카메라 비밀번호·로컬 통신 및 DVRIP 지원 여부 확인; `probe`로 진단 |
| 등록 성공, 영상 안 나옴 | 카메라 RTSP 지원과 포트 554, ffmpeg 설치 확인 |
| 장비/펌웨어 불일치 | 이전 검증을 재사용하지 말고 별도 설치 공간에서 새로 등록·검증 |
| 기존 운영 프로필 때문에 setup 중단 | Python은 별도 `ICSEE_PROJECT_DIR`, Docker는 새 설치용 Compose 사용 |

`doctor`는 로컬 설정만 점검합니다. `register`와 `probe`는 읽기 전용으로 카메라에 접속하며 모터를 움직이지 않습니다. 보고서를 공유하기 전 장비 주소·식별 정보 등을 검토하세요. 등록 결과에는 원본 진단 응답, 장비 일련번호, 세션 토큰을 저장하지 않습니다.

현재 타임랩스 날짜와 카메라 시간 기능은 한국 시간(`Asia/Seoul`)을 사용합니다. 새 설치에서 자동 시간 동기화는 꺼져 있으며, 다른 시간대 지원은 이번 변경에 포함하지 않았습니다. 기존 기능은 [README](../README.md), [오디오](../AUDIO.md), [SD 녹화](../SD-RECORDINGS.md), [타임랩스](../TIMELAPSE.md)를 참고하세요.
