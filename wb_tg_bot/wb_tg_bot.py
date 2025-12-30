import asyncio
from aiogram.enums import ParseMode
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from config import API_TOKEN, WB_API, interval

# Глобальные переменные для хранения состояния
processed_orders = set()
first_run = True
check_wb_task = None
check_balance_task = None

current_balance = None
for_withdraw_balance = None

# Маппинг валют (числовые коды для заказов)
currency_mapping = {
    643: ('RUB', '₽', 'Российский рубль'),
    840: ('USD', '$', 'Доллар США'),
    978: ('EUR', '€', 'Евро'),
    398: ('KZT', '₸', 'Казахстанский тенге'),
    156: ('CNY', '¥', 'Китайский юань'),
    826: ('GBP', '£', 'Британский фунт'),
    392: ('JPY', '¥', 'Японская иена'),
}

# Символы валют (строковые коды для баланса)
currency_symbols = {
    'RUB': '₽',
    'USD': '$',
    'EUR': '€',
    'KZT': '₸',
    'CNY': '¥',
    'GBP': '£',
    'JPY': '¥'
}

def format_address(full_address):
    parts = [part.strip() for part in full_address.split(',')]
    
    if len(parts) >= 2:
        # Берем область/край/республику (первая часть) и город (вторая часть)
        result = f"{parts[0]}, {parts[1]}"
    elif len(parts) == 1:
        # Если нет запятых - берем как есть, но обрезаем если длинно
        result = parts[0]
        if len(result) > 30:
            result = result[:27] + "..."
    else:
        result = full_address[:30]
    
    return result

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    global check_wb_task, check_balance_task
    await message.answer("Привет! Выполняю проверку API...")
    
    if check():
        await message.answer("WB API подключено")
        
        # Запуск проверки заказов
        if check_wb_task is None or check_wb_task.done():
            check_wb_task = asyncio.create_task(check_new_orders(message))
            await message.answer("Начинаю выполнение функции проверки новых заказов")
        else:
            await message.answer("Функция проверки заказов уже выполняется")
        
        # Запуск проверки баланса
        if check_balance_task is None or check_balance_task.done():
            check_balance_task = asyncio.create_task(check_balance(message))
            await message.answer("Начинаю выполнение функции проверки баланса")
        else:
            await message.answer("Функция проверки баланса уже выполняется")
    else:
        await message.answer("WB API не подключено(((")
        await message.answer("Выполнение функции прервано")

@dp.message(Command("chatid"))
async def chatid_handler(message: types.Message):
    chat_id = message.chat.id
    await message.answer(f"Chat ID этого чата: {chat_id}")

@dp.message(Command("check"))
async def check_handler(message: types.Message):
    if check():
        await message.answer("WB API подключено")
    else:
        await message.answer("WB API не подключено(((")

@dp.message(Command("status"))
async def status_handler(message: types.Message):
    global check_wb_task, check_balance_task
    
    # Статус проверки заказов
    if check_wb_task is None:
        order_status = "Функция проверки заказов не запущена"
    elif check_wb_task.done():
        order_status = "Функция проверки заказов завершена"
    else:
        order_status = "Функция проверки заказов выполняется"
    
    # Статус проверки баланса
    if check_balance_task is None:
        balance_status = "Функция проверки баланса не запущена"
    elif check_balance_task.done():
        balance_status = "Функция проверки баланса завершена"
    else:
        balance_status = "Функция проверки баланса выполняется"
    
    await message.answer(f"{order_status}\n{balance_status}")

@dp.message(Command("stop"))
async def stop_handler(message: types.Message):
    global check_wb_task, check_balance_task
    stopped_tasks = []
    
    # Остановка проверки заказов
    if check_wb_task is not None and not check_wb_task.done():
        check_wb_task.cancel()
        try:
            await check_wb_task
        except asyncio.CancelledError:
            pass
        check_wb_task = None
        stopped_tasks.append("проверки заказов")
    
    # Остановка проверки баланса
    if check_balance_task is not None and not check_balance_task.done():
        check_balance_task.cancel()
        try:
            await check_balance_task
        except asyncio.CancelledError:
            pass
        check_balance_task = None
        stopped_tasks.append("проверки баланса")
    
    if stopped_tasks:
        await message.answer(f"Функции {', '.join(stopped_tasks)} остановлены")
    else:
        await message.answer("Нет активных функций для остановки")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот запущен и ждет входящие сообщения...")
    await dp.start_polling(bot)

def check():
    """Проверка подключения к WB API"""
    url = 'https://marketplace-api.wildberries.ru/ping'
    headers = {'Authorization': WB_API}
    try:
        response = requests.get(url, headers=headers)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False

async def check_new_orders(msg):
    """Проверка новых заказов"""
    global first_run
    
    while True:
        try:
            url = 'https://marketplace-api.wildberries.ru/api/v3/orders/new'
            headers = {'Authorization': WB_API}
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                orders = data.get('orders', [])
                
                if not orders:
                    first_run = False
                    await asyncio.sleep(interval)
                    continue
                
                # Фильтруем новые заказы
                new_orders = [order for order in orders if order['id'] not in processed_orders]
                
                if not new_orders:
                    await asyncio.sleep(interval)
                    continue
                
                # Добавляем новые заказы в обработанные
                processed_orders.update(order['id'] for order in new_orders)
                
                # Подсчет количества заказов и общей суммы
                total_orders = len(new_orders)
                total_sum = sum(order['salePrice'] for order in new_orders)
                
                # Формирование деталей по каждому заказу
                order_details = "\n".join(
                    [
                        f"📦 Код товара: {order['article']}\n"
                        f"💵 Сумма: {format_number(order['salePrice']/100)} {get_currency_info(order['currencyCode'])}\n"
                        f"🆔 ID: <code>{order['id']}</code>\n"
                        f"📅 Дата: {order['createdAt']}\n"
                        f"🗺️ Место: {format_address(order['address']['fullAddress'])}\n"
                        for order in new_orders
                    ]
                )
                
                # Формирование итогового сообщения
                message_text = (
                    f"🎉 Новые заказы!\n\n"
                    f"📊 Общая информация:\n"
                    f"• Количество заказов: {total_orders}\n"
                    f"• Общая сумма: {format_number(total_sum/100)} {get_currency_info(new_orders[0]['currencyCode'])}\n\n"
                    f"📋 Детали заказов:\n{order_details}"
                )
                await msg.answer(message_text, parse_mode=ParseMode.HTML)
            else:
                await msg.answer(f"❌ Ошибка при запросе к API Wildberries: {response.status_code} - {response.text}")
        
        except Exception as e:
            await msg.answer(f"❌ Ошибка в функции проверки заказов: {str(e)}")
        
        await asyncio.sleep(interval)

async def check_balance(msg):
    """Проверка изменений баланса"""
    global current_balance, for_withdraw_balance
    
    while True:
        try:
            url = 'https://finance-api.wildberries.ru/api/v1/account/balance'
            headers = {'Authorization': WB_API}
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                # Получаем данные напрямую из ответа
                current = data.get('current', 0)
                for_withdraw = data.get('for_withdraw', 0)
                currency = data.get('currency', 'RUB')
                
                # Получаем символ валюты
                currency_symbol = get_currency_symbol(currency)
                
                # Первый запуск - инициализируем значения без уведомления
                if current_balance is None:
                    current_balance = current
                    await msg.answer(f"💰 Первоначальный баланс установлен: {format_number(current/100)} {currency_symbol}")
                
                if for_withdraw_balance is None:
                    for_withdraw_balance = for_withdraw
                    await msg.answer(f"💳 Первоначальный баланс для вывода установлен: {format_number(for_withdraw/100)} {currency_symbol}")
                
                # Проверяем изменения текущего баланса (только после инициализации)
                if current_balance is not None and current != current_balance:
                    difference = current - current_balance
                    trend = "📈" if difference > 0 else "📉"
                    
                    message_text = (
                        f"💰 Изменения в балансе! {trend}\n\n"
                        f"Текущий баланс:\n"
                        f"• Было: {format_number(current_balance)} {currency_symbol}\n"
                        f"• Стало: {format_number(current)} {currency_symbol}\n"
                        f"• Разница: {format_number(difference)} {currency_symbol}"
                    )
                    await msg.answer(message_text, parse_mode=ParseMode.HTML)
                    current_balance = current  # Обновляем только после отправки уведомления
                
                # Проверяем изменения баланса для вывода (только после инициализации)
                if for_withdraw_balance is not None and for_withdraw != for_withdraw_balance:
                    difference = for_withdraw - for_withdraw_balance
                    trend = "📈" if difference > 0 else "📉"
                    
                    message_text = (
                        f"💳 Изменения в балансе для вывода! {trend}\n\n"
                        f"Доступно для вывода:\n"
                        f"• Было: {format_number(for_withdraw_balance)} {currency_symbol}\n"
                        f"• Стало: {format_number(for_withdraw)} {currency_symbol}\n"
                        f"• Разница: {format_number(difference)} {currency_symbol}"
                    )
                    await msg.answer(message_text, parse_mode=ParseMode.HTML)
                    for_withdraw_balance = for_withdraw  # Обновляем только после отправки уведомления
                
            else:
                await msg.answer(f"❌ Ошибка при запросе баланса: {response.status_code} - {response.text}")
        
        except Exception as e:
            await msg.answer(f"❌ Ошибка в функции проверки баланса: {str(e)}")
        
        await asyncio.sleep(120)

def get_currency_info(currency_code):
    """Получает информацию о валюте по числовому или строковому коду"""
    # Если код числовой (из заказов)
    if isinstance(currency_code, int):
        currency_info = currency_mapping.get(currency_code, (None, None, f"Неизвестная валюта ({currency_code})"))
        return currency_info[1] if currency_info[1] else currency_info[2]
    # Если код строковый (из баланса)
    else:
        return currency_symbols.get(currency_code, currency_code)

def get_currency_symbol(currency_code):
    """Получает символ валюты по строковому коду"""
    return currency_symbols.get(currency_code, currency_code)

def format_number(number):
    """Форматирует число с разделителями тысяч"""
    return "{:,.2f}".format(number).replace(",", "X").replace(".", ",").replace("X", ".")

if __name__ == '__main__':
    asyncio.run(main())