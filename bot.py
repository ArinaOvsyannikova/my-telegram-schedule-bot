# bot.py
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import aiosqlite
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Токен не найден! Проверьте файл .env")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

# Основная клавиатура
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Добавить задачу")],
        [KeyboardButton(text="📅 Сегодня"), KeyboardButton(text="🗓 Неделя")],
        [KeyboardButton(text="🗑 Удалить задачу")]
    ],
    resize_keyboard=True
)

# FSM
class AddTask(StatesGroup):
    choosing_type = State()
    waiting_for_title = State()
    waiting_for_date = State()
    waiting_for_time_for_once = State()
    waiting_for_time_for_recurring = State()
    waiting_for_days = State()

DB_PATH = "tasks.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('once', 'recurring')),
                datetime TEXT,
                time TEXT,
                days_of_week TEXT
            )
        """)
        await db.commit()

# Вспомогательная функция: получить все задачи пользователя
async def get_all_tasks(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, title, type, datetime, time, days_of_week FROM tasks WHERE user_id = ? ORDER BY datetime, time",
            (user_id,)
        )
        return await cursor.fetchall()

# Вспомогательная функция: форматирование задачи для отображения
def format_task(task):
    id, title, type_, datetime_str, time_str, days_str = task
    if type_ == "once":
        dt = datetime.fromisoformat(datetime_str)
        return f"📅 {dt.strftime('%d.%m.%Y %H:%M')} — {title}"
    else:
        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        days = [day_names[int(d)] for d in days_str.split(",") if d.strip()]
        return f"🔁 {' '.join(days)} в {time_str} — {title}"

# --- Основное меню ---
@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("👋 Добро пожаловать в бот расписания!", reply_markup=main_kb)

# --- Удаление задачи ---
@router.message(F.text == "🗑 Удалить задачу")
async def delete_task_start(message: Message):
    tasks = await get_all_tasks(message.from_user.id)
    if not tasks:
        await message.answer("У вас нет задач для удаления.")
        return

    kb = []
    for task in tasks:
        kb.append([
            InlineKeyboardButton(text=f"❌ {format_task(task)}", callback_data=f"del_{task[0]}")
        ])
    await message.answer("Выберите задачу для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("del_"))
async def confirm_delete(callback_query: CallbackQuery):
    task_id = int(callback_query.data.split("_")[1])
    await callback_query.message.edit_text(
        "Вы уверены, что хотите удалить эту задачу?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_del_{task_id}")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="cancel_del")]
        ])
    )
    await callback_query.answer()

@router.callback_query(F.data.startswith("confirm_del_"))
async def execute_delete(callback_query: CallbackQuery):
    task_id = int(callback_query.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await db.commit()
    await callback_query.message.edit_text("✅ Задача удалена!", reply_markup=None)
    await callback_query.answer()

@router.callback_query(F.data == "cancel_del")
async def cancel_delete(callback_query: CallbackQuery):
    await callback_query.message.edit_text("Удаление отменено.", reply_markup=None)
    await callback_query.answer()

# --- (Остальной код: добавление, просмотр, рассылка — без изменений) ---

@router.message(F.text == "✅ Добавить задачу")
async def add_task_start(message: Message, state: FSMContext):
    type_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Разовая")],
            [KeyboardButton(text="Повторяющаяся")],
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer("Выберите тип задачи:", reply_markup=type_kb)
    await state.set_state(AddTask.choosing_type)

@router.message(AddTask.choosing_type, F.text.in_({"Разовая", "Повторяющаяся"}))
async def process_type(message: Message, state: FSMContext):
    task_type = "once" if message.text == "Разовая" else "recurring"
    await state.update_data(type=task_type)
    await message.answer("Введите название задачи:")
    await state.set_state(AddTask.waiting_for_title)

@router.message(AddTask.choosing_type, F.text == "⬅️ Назад")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выберите действие:", reply_markup=main_kb)

@router.message(AddTask.waiting_for_title)
async def process_title(message: Message, state: FSMContext):
    if message.text == "⬅️ Назад":
        await add_task_start(message, state)
        return
    await state.update_data(title=message.text)
    user_data = await state.get_data()
    if user_data["type"] == "once":
        await message.answer("Выберите дату:", reply_markup=await SimpleCalendar().start_calendar())
        await state.set_state(AddTask.waiting_for_date)
    else:
        time_kb = []
        for i in range(8, 21):
            time_kb.append([InlineKeyboardButton(text=f"{i:02d}:00", callback_data=f"time_{i:02d}:00")])
        await message.answer("Выберите время:", reply_markup=InlineKeyboardMarkup(inline_keyboard=time_kb))
        await state.set_state(AddTask.waiting_for_time_for_recurring)

async def get_tasks_for_date(user_id: int, target_date: datetime):
    day_of_week = target_date.weekday()
    date_str = target_date.strftime("%Y-%m-%d")
    tasks = []

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT title, datetime FROM tasks WHERE user_id = ? AND type = 'once' AND date(datetime) = ? ORDER BY datetime",
            (user_id, date_str)
        )
        once_tasks = await cursor.fetchall()
        for title, dt in once_tasks:
            tasks.append((title, dt))

        cursor = await db.execute(
            "SELECT title, time, days_of_week FROM tasks WHERE user_id = ? AND type = 'recurring'",
            (user_id,)
        )
        rec_tasks = await cursor.fetchall()
        for title, time_str, days_str in rec_tasks:
            if str(day_of_week) in days_str.split(","):
                full_dt = f"{date_str}T{time_str}"
                tasks.append((title, full_dt))

    tasks.sort(key=lambda x: x[1])
    return tasks

@router.callback_query(SimpleCalendarCallback.filter(), AddTask.waiting_for_date)
async def process_calendar(callback_query: CallbackQuery, callback_data: SimpleCalendarCallback, state: FSMContext):
    calendar = SimpleCalendar()
    selected, date = await calendar.process_selection(callback_query, callback_data)
    if selected:
        await state.update_data(selected_date=date)
        time_kb = []
        for i in range(8, 21):
            time_kb.append([InlineKeyboardButton(text=f"{i:02d}:00", callback_data=f"time_once_{i:02d}:00")])
        await callback_query.message.answer("Выберите время:", reply_markup=InlineKeyboardMarkup(inline_keyboard=time_kb))
        await state.set_state(AddTask.waiting_for_time_for_once)
    await callback_query.answer()

@router.callback_query(F.data.startswith("time_once_"), AddTask.waiting_for_time_for_once)
async def process_time_once(callback_query: CallbackQuery, state: FSMContext):
    time_str = callback_query.data.replace("time_once_", "")
    user_data = await state.get_data()
    dt = datetime.combine(user_data["selected_date"], datetime.strptime(time_str, "%H:%M").time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tasks (user_id, title, type, datetime) VALUES (?, ?, ?, ?)",
            (callback_query.from_user.id, user_data['title'], 'once', dt.isoformat())
        )
        await db.commit()
    await callback_query.message.answer("✅ Разовая задача добавлена!", reply_markup=main_kb)
    await state.clear()
    await callback_query.answer()

@router.callback_query(F.data.startswith("time_"), AddTask.waiting_for_time_for_recurring)
async def process_time_recurring(callback_query: CallbackQuery, state: FSMContext):
    time_str = callback_query.data.replace("time_", "")
    await state.update_data(time=time_str)
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    kb = []
    row = []
    for i, day in enumerate(days):
        row.append(InlineKeyboardButton(text=f"⬜ {day}", callback_data=f"day_{i}"))
        if len(row) == 4:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton(text="✅ Готово", callback_data="days_done")])
    await callback_query.message.answer(
        "Выберите дни недели (нажмите, чтобы отметить):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await state.update_data(selected_days=[])
    await state.set_state(AddTask.waiting_for_days)
    await callback_query.answer()

@router.callback_query(F.data.startswith("day_"), AddTask.waiting_for_days)
async def toggle_day(callback_query: CallbackQuery, state: FSMContext):
    day_index = int(callback_query.data.split("_")[1])
    user_data = await state.get_data()
    selected = user_data.get("selected_days", [])
    if day_index in selected:
        selected.remove(day_index)
    else:
        selected.append(day_index)
    await state.update_data(selected_days=selected)

    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    kb = []
    row = []
    for i, day in enumerate(days):
        mark = "✅" if i in selected else "⬜"
        row.append(InlineKeyboardButton(text=f"{mark} {day}", callback_data=f"day_{i}"))
        if len(row) == 4:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton(text="✅ Готово", callback_data="days_done")])

    await callback_query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback_query.answer()

@router.callback_query(F.data == "days_done", AddTask.waiting_for_days)
async def finish_recurring(callback_query: CallbackQuery, state: FSMContext):
    user_data = await state.get_data()
    selected_days = user_data.get("selected_days", [])
    if not selected_days:
        await callback_query.answer("⚠️ Выберите хотя бы один день!", show_alert=True)
        return
    days_str = ",".join(str(d) for d in sorted(selected_days))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tasks (user_id, title, type, time, days_of_week) VALUES (?, ?, ?, ?, ?)",
            (callback_query.from_user.id, user_data['title'], 'recurring', user_data['time'], days_str)
        )
        await db.commit()
    await callback_query.message.answer("✅ Повторяющаяся задача добавлена!", reply_markup=main_kb)
    await state.clear()
    await callback_query.answer()

@router.message(F.text == "📅 Сегодня")
async def show_today(message: Message):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = await get_tasks_for_date(message.from_user.id, today)
    if tasks:
        msg = "🗓 Ваше расписание на сегодня:\n\n"
        for title, dt_str in tasks:
            dt = datetime.fromisoformat(dt_str.replace("T", " "))
            msg += f"⏰ {dt.strftime('%H:%M')} — {title}\n"
        await message.answer(msg)
    else:
        await message.answer("На сегодня задач нет.")

@router.message(F.text == "🗓 Неделя")
async def show_week(message: Message):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    all_msgs = []
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    for i in range(7):
        day = today + timedelta(days=i)
        tasks = await get_tasks_for_date(message.from_user.id, day)
        if tasks:
            msg = f"📅 {day.strftime('%d.%m')} ({day_names[day.weekday()]}):\n"
            for title, dt_str in tasks:
                dt = datetime.fromisoformat(dt_str.replace("T", " "))
                msg += f"  ⏰ {dt.strftime('%H:%M')} — {title}\n"
            all_msgs.append(msg)
    if all_msgs:
        full_text = "\n".join(all_msgs)
        if len(full_text) > 4000:
            for i in range(0, len(full_text), 4000):
                await message.answer(full_text[i:i+4000])
        else:
            await message.answer(full_text)
    else:
        await message.answer("На этой неделе задач нет.")

async def send_daily_tasks(bot: Bot):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT DISTINCT user_id FROM tasks WHERE
            (type = 'once' AND date(datetime) = ?)
            OR (type = 'recurring')
        """, (today.strftime("%Y-%m-%d"),))
        user_ids = await cursor.fetchall()

    for (user_id,) in user_ids:
        tasks = await get_tasks_for_date(user_id, today)
        if tasks:
            msg = "🌅 Доброе утро! Вот ваше расписание на сегодня:\n\n"
            for title, dt_str in tasks:
                dt = datetime.fromisoformat(dt_str.replace("T", " "))
                msg += f"⏰ {dt.strftime('%H:%M')} — {title}\n"
            try:
                await bot.send_message(user_id, msg)
            except Exception as e:
                print(f"Не удалось отправить пользователю {user_id}: {e}")

async def main():
    await init_db()
    dp.include_router(router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_daily_tasks, CronTrigger(hour=0, minute=0), args=[bot])
    scheduler.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())