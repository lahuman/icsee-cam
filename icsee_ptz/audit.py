"""Structured operational events containing only explicitly selected fields."""
from datetime import datetime, timezone
import json
import logging

LOGGER = logging.getLogger('icsee.audit')
ALLOWED = {'role', 'kind', 'state', 'count', 'category', 'status', 'job_id', 'client'}


def configure():
    logging.disable(logging.NOTSET)
    for name in ('zeep', 'onvif', 'urllib3', 'requests', 'httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
        logging.getLogger(name).disabled = True
    if not LOGGER.handlers:
        LOGGER.addHandler(logging.StreamHandler())
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def event(name, **fields):
    payload = {'event': name, 'at': datetime.now(timezone.utc).isoformat()}
    payload.update({key: value for key, value in fields.items() if key in ALLOWED and isinstance(value, (str, int, bool))})
    LOGGER.info(json.dumps(payload, ensure_ascii=False))
