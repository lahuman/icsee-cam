# 동일 네트워크 웹 접속

현재 Mac의 카메라 네트워크 주소는 en0의 192.168.1.159/24입니다. 같은 LAN의 기기는 `http://192.168.1.159:8000/`으로 접속합니다. 기존 `http://127.0.0.1:8000/`도 유지합니다. 별도 무선 인터페이스 192.168.219.106에는 웹 포트를 공개하지 않습니다.

## 적용 및 검증 (2026-10-25)

- Colima를 `--network-host-addresses`로 재시작하고 기존 컨테이너(cokacremote 포함)를 복구했습니다.
- 실제 Mac LISTEN 주소는 127.0.0.1:8000과 192.168.1.159:8000 두 개입니다.
- 두 주소의 웹 페이지와 타임랩스 날짜 목록은 HTTP 200, 잘못된 Host와 외부 Origin은 403을 확인했습니다. 다른 물리 기기에서의 접속은 별도 확인이 필요합니다.
- 웹 healthcheck 정상, 자동 타임랩스 재연결 및 새 JPEG 저장을 확인했습니다.
- Mac 자동 테스트 139개 통과. LAN HTTP·WebSocket 정책은 모의 검증했으며 실제 휴대폰 음성 송출은 미검증입니다.
- 컨테이너에서는 포워더가 원래 클라이언트 주소를 가릴 수 있습니다. Host/Origin 검사는 유지하지만 클라이언트별 서브넷 방화벽을 대체하지 않습니다.

## Colima 재시작과 경로 보정

Colima 0.10.1의 hostAddresses 옵션은 호스트 IP뿐 아니라 `192.168.1.0/24` 전체를 루프백 로컬 경로로 복제했습니다. 이 상태에서는 카메라 192.168.1.168 접속이 실패합니다. `scripts/fix-colima-lan-route.py`는 정확히 일치하는 잘못된 서브넷 경로만 제거하고 웹 공개용 단일 호스트 주소는 유지합니다.

현재는 보정이 적용됐습니다. Colima를 새로 시작하면 아래 순서를 사용해야 합니다. 보정은 VM 재시작 후 다시 필요하며, 이 스크립트는 상주 서비스가 아닙니다.

```sh
# ICSEE_PASSWORD가 설정된 터미널에서
colima start --network-host-addresses
.venv/bin/python scripts/fix-colima-lan-route.py
docker compose up -d web timelapse
```

모든 기존 Colima 컨테이너를 잠시 중단했다 복원하는 전체 전환 명령:

```sh
zsh scripts/enable-lan.zsh --restart-colima
```

IP가 변경되면 `ICSEE_WEB_LAN_IP`를 현재 en0 주소로 설정하고 경로 보정 및 `docker compose up -d web`으로 재생성합니다. 공유기에서 Mac 주소를 고정 할당하면 운영하기 편합니다. 로컬 접속만 필요하면 `docker compose -f compose.yaml -f compose.local.yaml up -d web`을 사용합니다.

일반 사설 HTTP 주소에서는 휴대폰 브라우저의 마이크 송출이 제한될 수 있으므로 HTTPS는 별도 단계입니다. 공유기 WAN 공개·포트 포워딩은 변경하지 않았습니다.

근거: [Colima 공식 hostAddresses 설정](https://github.com/abiosoft/colima/blob/main/embedded/defaults/colima.yaml).
