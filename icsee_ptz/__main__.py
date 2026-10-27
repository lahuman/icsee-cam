import argparse
import json
import logging
import os
import math
from pathlib import Path
import sys

from .config import CameraError, Config, redact, safe_error


def duration(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 2:
        raise argparse.ArgumentTypeError("이동 시간은 0초 초과, 최대 2초입니다.")
    return number


def main(argv=None):
    from .paths import config_root
    parser = argparse.ArgumentParser(description="iCSee 로컬 ONVIF 제어 및 프로토콜 진단")
    parser.add_argument("--host", help="카메라 IP (환경 변수·저장된 설정보다 우선)")
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--protocol", choices=("auto", "onvif", "dvrip"), default="auto")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="카메라 주소와 웹 관리자 계정 초기 설정 (카메라 접속 없음)")
    setup.add_argument("--host", dest="setup_host", help="카메라의 사설 IPv4 주소")
    setup.add_argument("--username", help="카메라 로그인 ID (웹 계정과 별도)")
    sub.add_parser("doctor", help="설정·웹 계정·ffmpeg 설치 점검 (카메라 접속 없음)")
    sub.add_parser("register", help="DVRIP 장비 식별 정보 등록 (읽기 전용, 모터 이동 없음)")
    verify = sub.add_parser("verify-motion", help="관찰하면서 네 방향 이동·정지를 확인하고 PTZ 활성화")
    verify.add_argument("--start-mode", choices=("legacy", "modern"), required=True)
    probe_parser = sub.add_parser("probe", help="TCP 및 ONVIF/DVRIP 읽기 전용 탐지")
    probe_parser.add_argument("--output", type=Path, help="비밀정보 제거 후 프로젝트 내부 JSON 저장")
    sub.add_parser("status", help="실제 연결 상태 재조회 (이동 없음)")
    clock = sub.add_parser("clock", help="카메라 시간과 한국 시간의 오차 조회")
    clock_action = clock.add_mutually_exclusive_group()
    clock_action.add_argument("--sync", action="store_true", help="서버의 현재 한국 시간으로 한 번 동기화")
    clock_action.add_argument("--schedule-status", action="store_true", help="일일 자동 동기화 실행 기록 조회 (카메라 접속 없음)")
    move = sub.add_parser("move", help="ONVIF 기능 확인 후 제한 시간 동안 이동하고 STOP")
    move.add_argument("direction", choices=("left", "right", "up", "down"))
    move.add_argument("--duration", type=duration, default=0.5)
    move.add_argument("--speed", type=float, default=0.25)
    move.add_argument("--experimental", action="store_true",
                      help="실제 동작 미검증 프로토콜을 명시적으로 시험")
    sub.add_parser("stop", help="ONVIF STOP")
    sub.add_parser("presets", help="ONVIF 프리셋 목록 조회 (이동 없음)")
    serve = sub.add_parser("serve", help="127.0.0.1에서 로컬 웹 UI 실행")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--auth-config", type=Path, default=config_root() / 'web-auth.json',
                       help="웹 로그인 ID/비밀번호 JSON 설정 파일 (필수)")
    serve.add_argument("--container", action="store_true",
                       help="컨테이너 내부 0.0.0.0 바인딩; 호스트에서는 반드시 루프백에만 포트 공개")
    timelapse = sub.add_parser("timelapse", help="로컬 스냅샷 타임랩스 촬영·조회·영상 생성")
    actions = timelapse.add_subparsers(dest="timelapse_action", required=True)
    for action in ("run", "status", "stop", "render", "reconcile", "recover"):
        command = actions.add_parser(action)
        command.add_argument("--directory", type=Path, default=None)
        if action == "run":
            command.add_argument('--retention', action='store_true', help='사진 3일·MP4 30일 보관 및 만료 파일 자동 삭제')
            command.add_argument("--interval", type=float, default=30)
            command.add_argument("--max-frames", type=int)
            limit = command.add_mutually_exclusive_group()
            limit.add_argument("--duration", type=float, default=3600,
                                 help="시험 실행의 최대 초 수 (기본 3600초)")
            limit.add_argument("--continuous", action="store_true",
                               help="사용자가 중지하거나 용량 기준에 도달할 때까지 자동 촬영")
            command.add_argument("--max-storage-mb", type=float, default=1024,
                                 help="저장소 촬영 중단 기준 MiB (기본 1024)")
            command.add_argument("--min-free-mb", type=float, default=512,
                                 help="디스크 최소 여유 MiB (기본 512)")
        if action == "render":
            command.add_argument("--date", help="한국 시간 기준 YYYY-MM-DD, 기본 오늘")
            command.add_argument("--fps", type=int, choices=[30], default=30)
    from .paths import config_root
    accounts = sub.add_parser("accounts", help="웹 계정 조회·이관·권한 관리 (카메라 접속 없음)")
    accounts.add_argument("--auth-config", type=Path, default=config_root() / "web-auth.json")
    accounts.add_argument("account_action", choices=("list", "migrate", "set", "disable"))
    accounts.add_argument("--username")
    accounts.add_argument("--role", choices=("viewer", "operator", "admin"), default="viewer")
    accounts.add_argument("--reset-password", action="store_true")
    args = parser.parse_args(argv)
    # Third-party SOAP logs can contain request URLs, response bodies and credentials.
    from .audit import configure
    configure()
    try:
        if args.command == "setup":
            from .onboarding import setup_installation
            print(json.dumps(setup_installation(args.setup_host or args.host, args.username), ensure_ascii=False, indent=2))
            return 0
        if args.command == "doctor":
            from .onboarding import doctor
            result = doctor(args.host, args.timeout)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ready"] else 2
        if args.command == "accounts":
            from .accounts import manage_accounts
            print(json.dumps(manage_accounts(args), ensure_ascii=False, indent=2))
            return 0
        config = Config.from_env(args.host, args.timeout)
        if args.command in ("register", "verify-motion"):
            from .onboarding import register_device, verify_motion
            result = register_device(config) if args.command == "register" else verify_motion(config, args.start_mode)
            print(json.dumps(redact(result, config.password), ensure_ascii=False, indent=2))
            return 0
        if args.command == "clock":
            from .camera_clock import camera_clock
            if args.schedule_status:
                from .daily_clock import DailyClockSync
                result = DailyClockSync(config).status()
                result["enabled"] = os.environ.get("ICSEE_DAILY_CLOCK_SYNC") == "1"
                if not result["enabled"]:
                    result["next_run"] = None
            else:
                result = camera_clock(config, sync=args.sync)
            print(json.dumps(redact(result, config.password),
                             ensure_ascii=False, indent=2))
            return 0
        if args.command == "timelapse":
            from .timelapse import Archive, archive_root, KST
            from datetime import datetime
            archive = Archive(archive_root(args.directory), config.host)
            def output(value):
                print(json.dumps(redact(value, config.password), ensure_ascii=False, indent=2), flush=True)
            if args.timelapse_action == "status":
                output(archive.status())
            elif args.timelapse_action == "stop":
                if archive.running():
                    (archive.root / ".stop").touch()
                    output({"state": "stop_requested"})
                else:
                    output({"state": "not_running"})
            elif args.timelapse_action == "reconcile":
                output(archive.reconcile())
            elif args.timelapse_action == "recover":
                from .archive_index import recover
                output(recover(archive))
            elif args.timelapse_action == "render":
                output(archive.render(args.date or datetime.now(KST).date().isoformat(), args.fps))
            else:
                config.require_password()
                from .snapshot import SnapshotClient
                from .jobs import JobQueue, executor
                queue = JobQueue(executor=executor(config), host=config.host)
                queue.start()
                def maintenance(current, retention):
                    queue.submit('maintenance', {'root': str(current.root), 'retention': retention,
                                                 'day': datetime.now(KST).date().isoformat()})
                try:
                    result = archive.collect(lambda: SnapshotClient(config), interval=args.interval,
                        max_frames=args.max_frames, duration=None if args.continuous else args.duration, emit=output,
                        max_storage_mb=args.max_storage_mb, min_free_mb=args.min_free_mb, retention=args.retention,
                        maintenance=maintenance)
                finally:
                    queue.close()
                output(result)
                if result["captured"]:
                    output(archive.render(archive.days()[-1]))
                return 0 if result["captured"] else 2
            return 0
        if args.command == "serve":
            if not 1024 <= args.port <= 65535:
                raise CameraError("웹 포트는 1024~65535여야 합니다.")
            from .web import create_app
            import uvicorn
            from .web_auth import WebAuth
            from .daily_clock import DailyClockSync
            app = create_app(config, port=args.port, container_mode=args.container,
                             lan_ip=os.environ.get('ICSEE_WEB_LAN_IP') or None,
                             public_origin=os.environ.get('ICSEE_WEB_PUBLIC_ORIGIN') or None,
                             web_auth=WebAuth.from_file(args.auth_config),
                             clock_scheduler=DailyClockSync(config)
                                if os.environ.get("ICSEE_DAILY_CLOCK_SYNC") == "1" else None)
            print(f"로컬 카메라 UI: http://127.0.0.1:{args.port}", flush=True)
            uvicorn.run(app, host="0.0.0.0" if args.container else "127.0.0.1", port=args.port, access_log=False,
                        log_level="critical", proxy_headers=False, ws_max_size=16384,
                        timeout_graceful_shutdown=5)
            return 0
        if args.command in ("move", "stop", "presets", "status"):
            config.require_password()
            from .verification import verified_profile
            verified = verified_profile(config)
            protocol = args.protocol
            if protocol == "auto":
                protocol = "dvrip" if verified and args.command in ("move", "stop") else "onvif"
            verified_move = protocol == "dvrip" and verified
            if args.command == "move" and not verified_move and not args.experimental:
                raise CameraError("실제 모터 동작이 검증된 프로토콜이 없습니다. "
                    "시험하려면 --protocol onvif 또는 dvrip와 move --experimental을 명시하세요.")
            from .control import ONVIFControl
            from .dvrip_control import DVRIPControl
            if protocol == "dvrip" and args.command == "presets":
                raise CameraError("DVRIP status/presets는 미구현입니다. probe 또는 ONVIF 조회를 사용하세요.")
            controller = (DVRIPControl(config, legacy_start=verified.get("legacy_start", True) if verified else False, verified=verified)
                          if protocol == "dvrip" else ONVIFControl(config))
            try:
                if args.command == "move":
                    result = controller.move(args.direction, args.duration, args.speed)
                elif args.command == "stop":
                    result = controller.stop()
                elif args.command == "presets":
                    result = {"protocol": "onvif", "presets": controller.presets()}
                else:
                    result = ({"protocol": "dvrip", "device_information": controller.device_information}
                              if protocol == "dvrip" else
                              {"protocol": "onvif", "status": controller.status(),
                               "verified_control_protocol": "dvrip" if verified else None})
            finally:
                controller.close()
            print(json.dumps(redact(result, config.password), ensure_ascii=False, indent=2, default=str))
            if args.command == "move":
                return 0 if result["move_response"] == "accepted" and result["stop_acknowledged_by"] else 2
            return 0
        if args.command == "probe" and args.output:
            target = args.output.resolve()
            from .paths import project_root
            root = project_root()
            if root not in target.parents or target.suffix != ".json":
                raise CameraError("진단 결과는 프로젝트 내부 .json 파일에만 저장할 수 있습니다.")
            if target.exists() or target.is_symlink():
                raise CameraError("기존 파일을 덮어쓰지 않습니다. 새 출력 경로를 지정하세요.")
        from .probe import probe
        report = redact(probe(config), config.password)
        payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        if args.command == "probe" and args.output:
            with target.open("x", encoding="utf-8") as output:
                output.write(payload + "\n")
        print(payload)
        confirmed = (any(entry.get("endpoint_confirmed") for entry in report["onvif"])
                     or report["dvrip"].get("state") == "authenticated")
        return 0 if confirmed and config.password else 2
    except CameraError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("명령이 중단되었습니다.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps(safe_error(exc), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
