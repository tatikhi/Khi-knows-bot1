import asyncio
import logging
import os
import sqlite3
import psycopg2
from datetime import datetime, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO)

# --- РАБОТА С БАЗОЙ ДАННЫХ (PostgreSQL / SQLite) ---
def get_db_connection():
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL, sslmode='require')
    return sqlite3.connect("bot_users.db")

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    if DATABASE_URL:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                child_name TEXT,
                child_age TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                donation_sent BOOLEAN DEFAULT FALSE
            )
        """)
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS donation_sent BOOLEAN DEFAULT FALSE;")
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                child_name TEXT,
                child_age TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                donation_sent BOOLEAN DEFAULT FALSE
            )
        """)
    conn.commit()
    cursor.close()
    conn.close()

def add_user(user_id, child_name, child_age):
    conn = get_db_connection()
    cursor = conn.cursor()
    if DATABASE_URL:
        cursor.execute("""
            INSERT INTO users (user_id, child_name, child_age, created_at, donation_sent) 
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP, FALSE)
            ON CONFLICT (user_id) DO UPDATE 
            SET child_name = EXCLUDED.child_name, child_age = EXCLUDED.child_age
        """, (user_id, child_name, child_age))
    else:
        cursor.execute("""
            INSERT OR REPLACE INTO users (user_id, child_name, child_age, created_at, donation_sent) 
            VALUES (?, ?, ?, COALESCE((SELECT created_at FROM users WHERE user_id = ?), CURRENT_TIMESTAMP), FALSE)
        """, (user_id, child_name, child_age, user_id))
    conn.commit()
    cursor.close()
    conn.close()

def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    return [user[0] for user in users]

def get_users_count():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return count

def get_users_for_donation_reminder():
    conn = get_db_connection()
    cursor = conn.cursor()
    target_date = datetime.now() - timedelta(days=5)
    
    if DATABASE_URL:
        cursor.execute("""
            SELECT user_id FROM users 
            WHERE created_at <= %s AND (donation_sent IS NULL OR donation_sent = FALSE)
        """, (target_date,))
    else:
        cursor.execute("""
            SELECT user_id FROM users 
            WHERE created_at <= ? AND (donation_sent IS NULL OR donation_sent = 0)
        """, (target_date.strftime('%Y-%m-%d %H:%M:%S'),))
        
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    return [user[0] for user in users]

def mark_donation_sent(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if DATABASE_URL:
        cursor.execute("UPDATE users SET donation_sent = TRUE WHERE user_id = %s", (user_id,))
    else:
        cursor.execute("UPDATE users SET donation_sent = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()

# --- ФОНОВЫЕ ЗАДАЧИ ---
async def keep_db_alive():
    while True:
        await asyncio.sleep(86400)  # Пауза 24 часа
        try:
            get_users_count()
            logging.info("Auto-ping: База данных Supabase активна 🟢")
        except Exception as e:
            logging.error(f"Auto-ping error: {e}")

async def check_donations_reminder():
    """Фоновая задача: проверяет раз в час, кому пора отправить напоминание о донате (через 5 дней)"""
    while True:
        await asyncio.sleep(3600)  # Проверка каждый час
        try:
            users_to_notify = get_users_for_donation_reminder()
            for user_id in users_to_notify:
                try:
                    donation_text = (
                        "Привет! 🤍 Прошло 5 дней с начала наших занятий. "
                        "Надеюсь, аудио-минутки помогают вам легко и без слез знакомить малыша с английским.\n\n"
                        "Этот проект задумывался как полностью бесплатный и доступный для каждой мамы. "
                        "Если он оказался вам полезен и хочется сказать мне спасибо — вы можете поддержать выход новых материалов по ссылке ниже. Спасибо, что вы со мной! 🧸👇"
                    )
                    keyboard = InlineKeyboardBuilder()
                    keyboard.button(text="поддержать выход нового материала🤍", url="https://pay.cloudtips.ru/p/18ecc58e")
                    
                    await bot.send_message(user_id, donation_text, reply_markup=keyboard.as_markup())
                    mark_donation_sent(user_id)
                    await asyncio.sleep(0.05)
                except Exception as e:
                    logging.error(f"Не удалось отправить напоминание о донате пользователю {user_id}: {e}")
        except Exception as e:
            logging.error(f"Ошибка в фоновой задаче донатов: {e}")

# --- СОСТОЯНИЯ ДИАЛОГОВ ---
class UserRegistration(StatesGroup):
    waiting_for_consent = State()
    waiting_for_name = State()
    waiting_for_age = State()
    waiting_for_confirmation = State()

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
    await message.answer("Очень приятно! Укажите, пожалуйста, возраст малыша.")
    await state.set_state(UserRegistration.waiting_for_age)

@dp.message(UserRegistration.waiting_for_age)
async def process_child_age(message: types.Message, state: FSMContext):
    raw_age = message.text.strip()
    
    if not (len(raw_age) == 1 and raw_age.isdigit()):
        await message.answer("Пожалуйста, введите корректное значение — только цифру (например: 3) 🤍")
        return

    await state.update_data(child_age=raw_age)
    data = await state.get_data()
    child_name = data.get("child_name")
    
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
    
    add_user(user_id, child_name, child_age)
    await state.clear()
    
    personalized_link = f"https://module1.khi-knows.ru/?name={child_name}&age={child_age}"
    
    await callback.message.edit_text("Готово! Создаю персональный доступ... 🤍")

    try:
        pdf_file = FSInputFile("instruction.pdf")
        await bot.send_document(
            chat_id=user_id, 
            document=pdf_file, 
            caption="📄 <b>Инструкция для родителей</b>\nПожалуйста, ознакомьтесь с ней перед началом занятий, чтобы всё прошло максимально комфортно! 🤍",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить файл инструкции: {e}")

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🧸 Открыть аудио-трекер", url=personalized_link)
    
    success_text = (
        f"А это персональная ссылка на трекер для вашего малыша! 🤍\n\n"
        "Нажимайте на кнопку ниже, включайте аудио-минутки и веселитесь с удовольствием! 🫂"
    )
    await callback.message.answer(success_text, reply_markup=keyboard.as_markup())
    
    channel_invite_text = (
        "А еще я знаю, как важно в этом деле иметь поддержку и единомышленников, "
        "чтобы не бросить через три дня. В моем Telegram-канале мамы делятся успехами и задают вопросы. "
        "Присоединяйтесь к нашему уютному пространству! 🫂👇"
    )
    channel_keyboard = InlineKeyboardBuilder()
    channel_keyboard.button(text="🤍 Перейти в Telegram-канал", url="https://t.me/khi_knows")
    await callback.message.answer(channel_invite_text, reply_markup=channel_keyboard.as_markup())
    await callback.answer()

# --- КОМАНДЫ АДМИНИСТРАТОРА ---
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для выполнения этой команды.")
        return

    total_users = get_users_count()
    await message.answer(
        f"📊 <b>Статистика бота:</b>\n\n"
        f"👤 Зарегистрировано пользователей в базе: <b>{total_users}</b>",
        parse_mode="HTML"
    )

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

# --- Веб-сервер для поддержания работы бота ---
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

# --- Параллельный запуск веб-сервера, авто-пинга, проверки донатов и бота ---
async def main():
    init_db()
    asyncio.create_task(keep_db_alive())         # Авто-пинг базы раз в 24 часа
    asyncio.create_task(check_donations_reminder()) # Фоновая проверка отправки доната раз в час
    
    # Удаление Webhook перед стартом (если он завис)
    await bot.delete_webhook(drop_pending_updates=True)
    
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
