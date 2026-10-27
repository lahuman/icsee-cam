"""One read-only storage/recording inspection; never formats or starts recording."""
import json
from datetime import datetime, timezone
from pathlib import Path

from icsee_ptz.config import Config, AuthenticationError, redact, safe_error
from icsee_ptz.dvrip import DVRIPClient


def main():
    config = Config.from_env()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "host": config.host, "read_only": True, "results": {}}
    try:
        with DVRIPClient(config) as client:
            for name in ("StorageInfo", "Record", "Detect.MotionDetect", "Simplify.Encode", "WorkState"):
                try:
                    report["results"][name] = {"ok": True, "data": client.read(name)}
                except AuthenticationError:
                    raise
                except Exception as exc:
                    report["results"][name] = {"ok": False, "error": safe_error(exc)}
                    # A timeout can desynchronise the connection; stop rather than
                    # interpreting a late packet as the next query's reply.
                    break
    except Exception as exc:
        report["error"] = safe_error(exc)
    cleaned = redact(report, config.password)
    Path("research/storage-check.json").write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(cleaned, ensure_ascii=False, indent=2))
    return 0 if "error" not in report and all(r["ok"] for r in report["results"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
