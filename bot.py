import asyncio
import logging
import os
import sqlite3
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

logging.basicConfig(level=logging.INFO)

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("bot_users.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            child_name TEXT,
            child_age TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_user(user_id, child_name, child_age):
    conn = sqlite3.connect("bot_users.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, child_name, child_age) 
        VALUES (?, ?, ?)
    """, (user_id, child_name, child_age))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect("bot_users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    return [user[0] for user in users]

# --- СОСТОЯНИЯ ДИАЛОГОВ ---
class UserRegistration(StatesGroup):
    waiting_for_consent = State()
    waiting_for_name = State()
    waiting_for_age = State()
    waiting_for_confirmation = State() # Новое состояние для проверки перед сохранением

class BroadcastState(StatesGroup):
    waiting_for_message = State()

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- КОМАНДА СБРОСА ДЛЯ ТЕСТИРОВАНИЯ ---
@dp.message(Command("reset"))
async def cmd_reset(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Состояние сброшено! Теперь можете отправить /start и пройти тест заново.")

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    welcome_text = (
        "Приветствую вас! 🤍 Я Татьяна — автор проекта «Бережный английский».\n\n"
        "Здесь вы можете получить бесплатный доступ к интерактивному аудио-трекеру "
        "для занятий с малышом без слез и зубрежки.\n\n"
        "Прежде чем мы начнем, пожалуйста, подтвердите согласие на обработку персональных данных "
        "и ознакомление с политикой конфиденциальности."
    )
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="✅ Согласен(а), продолжить", callback_data="accept_consent")
    
    await message.answer(welcome_text, reply_markup=keyboard.as_markup())
    await state.set_state(UserRegistration.waiting_for_consent)

@dp.callback_query(UserRegistration.waiting_for_consent, F.data == "accept_consent")
async def process_consent(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Спасибо! 🤍 Давайте настроим трекер под вашего ребенка.\n\nКак зовут малыша?")
    await state.set_state(UserRegistration.waiting_for_name)
    await callback.answer()

@dp.message(UserRegistration.waiting_for_name)
async def process_child_name(message: types.Message, state: FSMContext):
    await state.update_data(child_name=message.text.strip())
    await message.answer("Очень приятно! Укажите, пожалуйста, возраст малыша. (только цифра)")
    await state.set_state(UserRegistration.waiting_for_age)

@dp.message(UserRegistration.waiting_for_age)
async def process_child_age(message: types.Message, state: FSMContext):
    raw_age = message.text.strip()
    
    # Строгая проверка: введена ровно одна цифра
    if not (len(raw_age) == 1 and raw_age.isdigit()):
        await message.answer("Пожалуйста, введите корректное значение — только цифру (например: 3) 🤍")
        return

    await state.update_data(child_age=raw_age)
    data = await state.get_data()
    child_name = data.get("child_name")
    
    # Шаг подтверждения: показываем сводку перед записью в базу
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="✅ Все верно, открыть трекер", callback_data="confirm_yes")
    keyboard.button(text="🔄 Ввести заново", callback_data="confirm_no")
    keyboard.adjust(1)
    
    confirm_text = (
        f"Давайте проверим данные:\n\n"
        f"👶 Малыш: <b>{child_name}</b>\n"
        f"🎂 Возраст: <b>{raw_age}</b>\n\n"
        f"Всё верно?"
    )
    
    await message.answer(confirm_text, reply_markup=keyboard.as_markup(), parse_mode="HTML")
    await state.set_state(UserRegistration.waiting_for_confirmation)

@dp.callback_query(UserRegistration.waiting_for_confirmation, F.data == "confirm_no")
async def process_confirmation_no(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(UserRegistration.waiting_for_name)
    await callback.message.edit_text("Хорошо, давайте начнем сначала. Как зовут малыша?")
    await callback.answer()

@dp.callback_query(UserRegistration.waiting_for_confirmation, F.data == "confirm_yes")
async def process_confirmation_yes(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    child_name = data.get("child_name")
    child_age = data.get("child_age")
    user_id = callback.from_user.id
    
    # Сохраняем в базу только после подтверждения
    add_user(user_id, child_name, child_age)
    await state.clear()
    
    personalized_link = f"https://module1.khi-knows.ru/?name={child_name}&age={child_age}"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🧸 Открыть аудио-трекер", url=personalized_link)
    
    success_text = (
        f"Готово! Мы создали персональное пространство для малыша.\n\n"
        "Нажимайте на кнопку ниже, включайте аудио-минутки и веселитесь с удовольствием! 🫂"
    )
    await callback.message.edit_text(success_text, reply_markup=keyboard.as_markup())
    
    channel_invite_text = (
        "А это наше пространство для мам — здесь вы первыми узнаете об обновлениях, "
        "найдете аудио-подкасты от меня, живое общение и поддержку. Подписывайтесь! 👇"
    )
    channel_keyboard = InlineKeyboardBuilder()
    channel_keyboard.button(text="🤍 Перейти в Telegram-канал", url="https://t.me/khi_knows")
    await callback.message.answer(channel_invite_text, reply_markup=channel_keyboard.as_markup())
    await callback.answer()

@dp.message(Command("broadcast"))
async def start_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для этой команды.")
        return
    
    await message.answer("Введите текст рассылки для всех пользователей бота:")
    await state.set_state(BroadcastState.waiting_for_message)

@dp.message(BroadcastState.waiting_for_message)
async def send_broadcast(message: types.Message, state: FSMContext):
    await state.clear()
    users = get_all_users()
    
    count = 0
    for user_id in users:
        try:
            await bot.send_message(user_id, message.text, parse_mode="HTML")
            count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            
    await message.answer(f"Рассылка завершена. Успешно отправлено: {count} пользователям.")

# --- Веб-сервер для поддержания работы бота на Render ---
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")

# --- Параллельный запуск веб-сервера и бота ---
async def main():
    init_db()
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
