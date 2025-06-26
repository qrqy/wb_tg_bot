import asyncio
from email import message_from_binary_file
from aiogram.enums import ParseMode
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from config import API_TOKEN, WB_API, interval

processed_orders = set()
first_run = True
check_wb_task = None

currency_mapping = {
    643: ('RUB', '₽', 'Российский рубль'),
    840: ('USD', '$', 'Доллар США'),
    978: ('EUR', '€', 'Евро'),
    398: ('KZT', '₸', 'Казахстанский тенге'),
    156: ('CNY', '¥', 'Китайский юань'),
    826: ('GBP', '£', 'Британский фунт'),
    392: ('JPY', '¥', 'Японская иена'),
    # Добавьте другие коды валют по необходимости
}

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    global check_wb_task
    await message.answer("Привет! Выполняю проверку апи...")
    if check():
        await message.answer("WB API подключено");
        if check_wb_task is None or check_wb_task.done():
            check_wb_task = asyncio.create_task(check_new_orders(message))
            await message.answer("Начинаю выполнение функции проверки новых заказов")
        else:
            await message.answer("Функция проверки уже выполняется")

    else:
        await message.answer("WB API не подключено(((");
        await message.answer("Выполнение функции прервано");
@dp.message(Command("chatid"))
async def start_handler(message: types.Message):
    chat_id = message.chat.id  # Получаем chat ID из объекта сообщения
    await message.answer(f"Chat ID этого чата: {chat_id}")

@dp.message(Command("check"))
async def start_handler(message: types.Message):
    if check():
        await message.answer("WB API подключено");
    else:
        await message.answer("WB API не подключено(((");
@dp.message(Command("status"))
async def status_handler(message: types.Message):
    global check_wb_task
    if check_wb_task is None:
        await message.answer("Функция проверки не запущена")
    elif check_wb_task.done():
        await message.answer("Функция проверки завершена")
    else:
        await message.answer("Функция проверки выполняется")
@dp.message(Command("stop"))
async def stop_handler(message: types.Message):
    global check_wb_task
    if check_wb_task is not None and not check_wb_task.done():
        check_wb_task.cancel()
        try:
            await check_wb_task  # Дождемся отмены задачи
        except asyncio.CancelledError:
            pass  # Задача отменена
        check_wb_task = None
        await message.answer("Функция проверки остановлена")
    else:
        await message.answer("Функция проверки не запущена")
    
# @dp.message()
# async def get_chat_id(message: types.Message):
#     chat_id = message.chat.id  # Получаем chat ID из объекта сообщения
#     await message.answer(f"Chat ID этого чата: {chat_id}")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот запущен и ждет входящие сообщения...")
    await dp.start_polling(bot)

def check():
    url = 'https://marketplace-api.wildberries.ru/ping';
    headers = {'Authorization': WB_API}
    response = requests.get(url, headers=headers)
    if(response.status_code==200):
        return True;
    else:
        return False;

async def check_new_orders(msg):
    global first_run
    while True:
        # Здесь ваш код для проверки новых заказов на WB
        url = 'https://marketplace-api.wildberries.ru/api/v3/orders/new';
        headers = {'Authorization': WB_API}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            orders = data.get('orders', [])
            
            if not orders:
                first_run = False
                await asyncio.sleep(interval)
                continue
                
            # if first_run:
            #     processed_orders.update(order['id'] for order in orders)
            #     first_run = False  # Устанавливаем флаг в False после первого запуска
            #     await msg.answer( "Первый запуск завершен. Все текущие заказы добавлены.")
            #     await asyncio.sleep(interval)
            #     continue
            new_orders = [order for order in orders if order['id'] not in processed_orders]
            if not new_orders:
                await asyncio.sleep(interval)
                continue
            processed_orders.update(order['id'] for order in new_orders)
            # Подсчет количества заказов и общей суммы
            total_orders = len(new_orders)
            total_sum = sum(order['salePrice'] for order in new_orders)  # Общая сумма всех заказов

            # Формирование деталей по каждому заказу
            order_details = "\n".join(
                [
                    f"Код товара: {order['article']}, Сумма: {format_number(order['salePrice']/100)}, ID: <code>{order['id']}</code> , {get_currency_info(order['currencyCode'])}, "
                    f"Дата: {order['createdAt']}"
                    for order in new_orders
                ]
            )
            # Формирование итогового сообщения
            message_text = (
                f"Новые заказы:\n"
                f"Количество заказов: {total_orders}\n"
                f"Общая сумма: {format_number(total_sum/100)} {get_currency_info(new_orders[0]['currencyCode'])}\n\n"
                f"Детали заказов:\n{order_details}"
            )
            await msg.answer(message_text, parse_mode=ParseMode.HTML)
        else:
            await msg.answer(f"Ошибка при запросе к API Wildberries: {response.status_code} - {response.text}")
        # await bot.send_message(chat_id, "Новый заказ!")  # Пример отправки сообщения о новом заказе
        await asyncio.sleep(interval)
        
def get_currency_info(numeric_code):
    return currency_mapping.get(numeric_code, (None, None, f"Неизвестная валюта ({numeric_code})"))[1]
def format_number(number):
    return "{:,.2f}".format(number).replace(",", "X").replace(".", ",").replace("X", ".")
if __name__ == '__main__':
    asyncio.run(main())
