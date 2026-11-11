import asyncio
import html
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from config import API_TOKEN, WB_API, interval

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
DB_PATH = os.getenv("WB_BOT_DB", "/data/wb_tg_bot.sqlite3")
try:
    POLL_SECONDS = max(5, int(interval or 60))
except (TypeError, ValueError):
    POLL_SECONDS = 60
WB_BASE = "https://marketplace-api.wildberries.ru"
BALANCE_URL = "https://finance-api.wildberries.ru/api/v1/account/balance"
EVENTS = {
    (None, "complete"): "Продавец передал заказ в доставку Wildberries",
    ("ready_for_pickup", None): "Заказ прибыл в пункт выдачи заказов (ПВЗ)",
    ("sold", None): "Покупатель забрал заказ",
    ("canceled_by_client", None): "Покупатель отказался от заказа при получении",
    ("declined_by_client", None): "Покупатель отменил заказ в первый час",
    ("defect", None): "Заказ отменён из-за брака",
    ("canceled_by_carrier", None): "Заказ отменён перевозчиком",
    (None, "cancel_carrier"): "Заказ отменён перевозчиком",
    ("canceled", None): "Заказ отменён продавцом",
    (None, "cancel"): "Заказ отменён продавцом",
}

class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        with self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY, article TEXT, data TEXT NOT NULL,
                    supplier_status TEXT, wb_status TEXT, created_at TEXT,
                    new_notified INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    order_id INTEGER NOT NULL, event_key TEXT NOT NULL,
                    message TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(order_id, event_key)
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS balance (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)

    def initialized(self):
        row = self.db.execute("SELECT value FROM settings WHERE key='initialized'").fetchone()
        return bool(row and row[0] == "1")

    def set_initialized(self):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('initialized','1')")

    def tracked_ids(self):
        return [r[0] for r in self.db.execute("SELECT id FROM orders")]

    def upsert_order(self, order, supplier, wb, notify_new=False, initial=False):
        oid = int(order["id"])
        now = datetime.now(timezone.utc).isoformat()
        data = __import__("json").dumps(order, ensure_ascii=False)
        article = str(order.get("article", ""))
        with self.db:
            old = self.db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
            if old:
                supplier = supplier if supplier is not None else old["supplier_status"]
                wb = wb if wb is not None else old["wb_status"]
            self.db.execute("""INSERT INTO orders(id,article,data,supplier_status,wb_status,created_at,new_notified,updated_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET article=excluded.article,data=excluded.data,
                supplier_status=excluded.supplier_status,wb_status=excluded.wb_status,updated_at=excluded.updated_at""",
                (oid, article, data, supplier, wb, order.get("createdAt"), int(notify_new), now))
            if notify_new and (not old or not old["new_notified"]):
                text = format_order(order, "Новый FBS-заказ")
                self.db.execute("INSERT OR IGNORE INTO events(order_id,event_key,message) VALUES(?,?,?)", (oid, "new", text))
                self.db.execute("UPDATE orders SET new_notified=1 WHERE id=?", (oid,))
            if old and not initial:
                for status, key in ((wb, (wb, None)), (supplier, (None, supplier))):
                    previous = old["wb_status"] if key[0] else old["supplier_status"]
                    if status and status != previous and key in EVENTS:
                        self.db.execute("INSERT OR IGNORE INTO events(order_id,event_key,message) VALUES(?,?,?)", (oid, EVENTS[key], format_order(order, EVENTS[key])))

    def pending_events(self):
        return self.db.execute("SELECT order_id,event_key,message FROM events WHERE sent=0 ORDER BY rowid").fetchall()

    def mark_sent(self, oid, key):
        with self.db:
            self.db.execute("UPDATE events SET sent=1 WHERE order_id=? AND event_key=?", (oid, key))

    def get_balance(self, key):
        r = self.db.execute("SELECT value FROM balance WHERE key=?", (key,)).fetchone()
        return float(r[0]) if r else None

    def set_balance(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO balance VALUES(?,?)", (key, str(value)))

store = Store(DB_PATH)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
monitor_task = None
balance_task = None


def key_name(key):
    return EVENTS[key]


def format_order(order, title):
    article = html.escape(str(order.get("article") or "?"))
    oid = int(order["id"])
    created = html.escape(str(order.get("createdAt") or "?"))
    price = order.get("salePrice", order.get("price"))
    amount = f"\nЦена: {int(price) / 100:.2f}" if isinstance(price, (int, float)) else ""
    return f"{title}\nАртикул: {article}\nID заказа: <code>{oid}</code>\nСоздан: {created}{amount}"


async def request(session, method, path, **kwargs):
    for attempt in range(3):
        try:
            async with session.request(method, WB_BASE + path, timeout=aiohttp.ClientTimeout(total=20), **kwargs) as resp:
                if resp.status == 429 or resp.status >= 500:
                    body = await resp.text()
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise RuntimeError(f"WB API {resp.status}: {body[:500]}")
                if resp.status >= 400:
                    raise RuntimeError(f"WB API {resp.status}: {(await resp.text())[:500]}")
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == 2:
                raise RuntimeError(f"WB API connection failed: {exc}") from exc
            await asyncio.sleep(2 ** attempt)


async def fetch_orders(session):
    # The FBS orders endpoint is cursor-paginated; query the last 90 days so
    # active orders remain visible after restarts and long monitoring sessions.
    params = {"dateFrom": int(time.time()) - 90 * 86400, "limit": 1000}
    found = []
    while True:
        data = await request(session, "GET", "/api/v3/orders", params=params)
        batch = data if isinstance(data, list) else data.get("orders", [])
        found.extend(batch)
        next_cursor = data.get("next") if isinstance(data, dict) else None
        if not next_cursor or not batch:
            break
        params["next"] = next_cursor
    return found


async def fetch_statuses(session, ids):
    result = {}
    for offset in range(0, len(ids), 100):
        chunk = ids[offset:offset + 100]
        if not chunk:
            continue
        data = await request(session, "POST", "/api/v3/orders/status", json={"orders": chunk})
        statuses = data if isinstance(data, list) else data.get("orders", [])
        result.update({int(item["id"]): item for item in statuses if "id" in item})
    return result


async def deliver_outbox(chat_id):
    for row in store.pending_events():
        await bot.send_message(chat_id, row["message"], parse_mode=ParseMode.HTML)
        store.mark_sent(row["order_id"], row["event_key"])


async def monitor(chat_id):
    headers = {"Authorization": WB_API}
    started = datetime.now(timezone.utc)
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            while True:
                try:
                    orders = await fetch_orders(session)
                    ids = [int(o["id"]) for o in orders if "id" in o]
                    statuses = await fetch_statuses(session, ids)
                    bootstrapping = not store.initialized()
                    for order in orders:
                        oid = int(order["id"])
                        state = statuses.get(oid, {})
                        created = order.get("createdAt")
                        genuinely_new = False
                        if bootstrapping and created:
                            try:
                                genuinely_new = datetime.fromisoformat(created.replace("Z", "+00:00")) >= started
                            except ValueError:
                                pass
                        elif not bootstrapping:
                            genuinely_new = oid not in store.tracked_ids()
                        store.upsert_order(order, state.get("supplierStatus"), state.get("wbStatus"), genuinely_new, initial=bootstrapping)
                    if bootstrapping:
                        store.set_initialized()
                    await deliver_outbox(chat_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("FBS polling failed")
                    await bot.send_message(chat_id, "Ошибка опроса WB API; повторю позже. Подробности в журнале контейнера.")
                await asyncio.sleep(POLL_SECONDS)
    finally:
        log.info("Order monitoring остановлен")


async def check_api():
    async with aiohttp.ClientSession(headers={"Authorization": WB_API}) as session:
        await request(session, "GET", "/ping")


async def monitor_balance(chat_id):
    headers = {"Authorization": WB_API}
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                async with session.get(BALANCE_URL, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for key in ("current", "for_withdraw"):
                            value = data.get(key)
                            old = store.get_balance(key)
                            if value is not None and old is not None and float(value) != old:
                                await bot.send_message(chat_id, f"Баланс {key}: {old:,.2f} -> {float(value):,.2f}")
                            if value is not None:
                                store.set_balance(key, value)
                    else:
                        log.warning("Баланс API returned %s", resp.status)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Баланс polling failed")
            await asyncio.sleep(120)


@dp.message(Command("start"))
async def start_handler(message: types.Message):
    global monitor_task, balance_task
    try:
        await check_api()
    except Exception as exc:
        await message.answer(f"WB API недоступен: {html.escape(str(exc))}")
        return
    if monitor_task is None or monitor_task.done():
        monitor_task = asyncio.create_task(monitor(message.chat.id))
    if balance_task is None or balance_task.done():
        balance_task = asyncio.create_task(monitor_balance(message.chat.id))
    await message.answer("Мониторинг FBS-заказов и баланса запущен. При первом опросе текущие заказы сохраняются без уведомлений.")


@dp.message(Command("stop"))
async def stop_handler(message: types.Message):
    global monitor_task, balance_task
    tasks = [t for t in (monitor_task, balance_task) if t and not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    monitor_task = balance_task = None
    await message.answer("Мониторинг остановлен." if tasks else "Мониторинг не был запущен.")


@dp.message(Command("status"))
async def status_handler(message: types.Message):
    is_running = monitor_task is not None and not monitor_task.done()
    await message.answer(f"Мониторинг: {'запущен' if is_running else 'остановлен'}\nИнтервал опроса заказов: {POLL_SECONDS} сек.")


@dp.message(Command("check"))
async def check_handler(message: types.Message):
    try:
        await check_api()
        await message.answer("Подключение к WB API работает.")
    except Exception as exc:
        await message.answer(f"Ошибка WB API: {html.escape(str(exc))}")


@dp.message(Command("chatid"))
async def chatid_handler(message: types.Message):
    await message.answer(f"ID чата: <code>{message.chat.id}</code>", parse_mode=ParseMode.HTML)


@dp.message(Command("help"))
async def help_handler(message: types.Message):
    await message.answer("/start — запустить мониторинг\n/stop — остановить\n/status — показать состояние\n/check — проверить WB API\n/chatid — показать ID чата")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Bot started; monitoring starts only after /start")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
