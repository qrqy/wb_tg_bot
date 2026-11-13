import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "level": record.levelname,
            "message": record.getMessage(),
            "time": datetime.fromtimestamp(record.created, timezone.utc).strftime("%d.%m.%Y %H:%M:%S"),
            "logger": record.name,
        }
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


_log_handler = logging.StreamHandler()
_log_handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_log_handler])
logging.getLogger("aiogram.event").setLevel(logging.ERROR)
log = logging.getLogger("wb_tg_bot")


def describe_error(exc):
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
