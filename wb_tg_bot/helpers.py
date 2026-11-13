import asyncio
import base64
import json
from datetime import datetime, timezone

from logger import log, describe_error


def token_expires_at(token):
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("token is not a JWT (expected 3 parts)")
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    if not isinstance(payload, dict):
        raise ValueError("token payload is not a JSON object")
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise ValueError("token payload has no numeric exp")
    try:
        return datetime.fromtimestamp(exp, timezone.utc)
    except (OverflowError, OSError):
        raise ValueError("token exp is out of range")


def log_token_expiry(token):
    try:
        expires = token_expires_at(token)
    except ValueError as exc:
        log.warning("WB token expiry unknown: %s", exc)
        return
    delta = expires - datetime.now(timezone.utc)
    when = expires.strftime("%Y-%m-%d %H:%M UTC")
    if delta.total_seconds() > 0:
        log.info("WB token expires at %s (in %d days)", when, delta.days)
    else:
        log.error("WB token expired at %s (%d days ago)", when, (-delta).days)


async def send_fire_and_forget(bot, chat_id, text):
    try:
        await bot.send_message(chat_id, text)
    except Exception as exc:
        log.error("Failed to send message to chat %s: %s", chat_id, describe_error(exc))


def log_task_end(task):
    try:
        task.result()
    except asyncio.CancelledError as cancel:
        # args[0] is the msg passed to task.cancel() by /stop; asyncio.run cancels with no msg on shutdown
        reason = cancel.args[0] if cancel.args else "bot shutdown"
        log.info("%s is stopped. Reason: %s", task.get_name(), reason)
    except Exception as exc:
        log.error("%s crashed: %s", task.get_name(), describe_error(exc), exc_info=exc)
