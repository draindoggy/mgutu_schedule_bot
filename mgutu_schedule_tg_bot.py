import asyncio
import json
import os
from datetime import datetime, timedelta
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SCHEDULE_URL = 'https://dec.mgutm.ru/api/Rasp?idGroup=30948'

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

CACHE_FILE = "schedule_cache.json"
CACHE_DAYS = 7

class ScheduleStates(StatesGroup):
    select_subgroup = State()
    select_date = State()

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def clean_cache(cache):
    cutoff_date = datetime.now().date() - timedelta(days=CACHE_DAYS)
    keys_to_delete = []
    for key in cache.keys():
        date_str = key.split("_")[0]  # ключ формата "YYYY-MM-DD_subgroup"
        try:
            key_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if key_date < cutoff_date:
                keys_to_delete.append(key)
        except Exception:
            keys_to_delete.append(key)
    for key in keys_to_delete:
        del cache[key]
    return cache

async def fetch_schedule_from_api():
    async with aiohttp.ClientSession() as session:
        async with session.get(SCHEDULE_URL) as response:
            if response.status != 200:
                return None
            return await response.json()

async def get_schedule(date: datetime.date, subgroup: str) -> str:
    cache = load_cache()
    cache = clean_cache(cache)
    key = f"{date}_{subgroup}"

    if key in cache:
        save_cache(cache)
        return cache[key]

    data = await fetch_schedule_from_api()
    if not data or not data.get('data', {}).get('rasp'):
        schedule_text = f"расписание для {date.strftime('%d.%m.%Y')} не найдено."
        cache[key] = schedule_text
        save_cache(cache)
        return schedule_text

    subgroup_text = f"{subgroup} п/г" if subgroup != "all" else "все подгруппы"
    day_of_week = ""
    schedule_text = f"расписание на {date.strftime('%d.%m.%Y')} ({subgroup_text})\n\n"
    found = False

    for lesson in data['data']['rasp']:
        lesson_date = lesson['дата'][:10]
        if lesson_date == date.strftime('%Y-%m-%d'):
            if not day_of_week:
                day_of_week = lesson.get('день_недели', 'Не указан').lower()
            if ("п/г" not in lesson['дисциплина'] or
                (subgroup == "all" and "п/г" in lesson['дисциплина']) or
                (subgroup != "all" and f"п/г {subgroup}" in lesson['дисциплина'])):
                found = True
                room_raw = str(lesson.get('аудитория', 'Не указана')).strip().capitalize()
                room = room_raw.split('-', 1)[1] if '-' in room_raw else room_raw
                schedule_text += (
                    f"🕓 {lesson['начало']} - {lesson['конец']}\n"
                    f"📚 {lesson['дисциплина']}\n"
                    f"👨‍🏫 {lesson['фиоПреподавателя']}\n"
                    f"🏫 {room}\n\n"
                )

    if not found:
        schedule_text = f"расписание для {date.strftime('%d.%m.%Y')} ({subgroup_text}) не найдено."
    else:
        schedule_text = (
            f"{day_of_week}\n\n"
            f"расписание на {date.strftime('%d.%m.%Y')} ({subgroup_text})\n\n"
            + schedule_text.split('\n', 2)[2]
        )

    cache[key] = schedule_text
    save_cache(cache)
    return schedule_text

def create_subgroup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="п/г 1", callback_data="subgroup_1"),
                InlineKeyboardButton(text="п/г 2", callback_data="subgroup_2"),
                InlineKeyboardButton(text="все подгруппы", callback_data="subgroup_all"),
            ]
        ]
    )

def create_date_keyboard(current_date: datetime.date) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="предыдущий день",
                    callback_data=f"prev_{current_date.strftime('%Y-%m-%d')}",
                ),
                InlineKeyboardButton(
                    text="следующий день",
                    callback_data=f"next_{current_date.strftime('%Y-%m-%d')}",
                ),
            ]
        ]
    )

@dp.message(Command(commands=["start", "schedule"]))
async def start_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    last_message_id = data.get("last_message_id")
    start_prompt_id = data.get("start_prompt_id")
    user_command_id = data.get("user_command_id")

    for msg_id in [last_message_id, start_prompt_id, user_command_id]:
        if msg_id:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
            except Exception:
                pass

    await state.clear()
    await state.update_data(user_command_id=message.message_id)

    sent_message = await message.answer(
        "привет! выбери свою подгруппу:", reply_markup=create_subgroup_keyboard()
    )
    await state.update_data(start_prompt_id=sent_message.message_id)
    await state.set_state(ScheduleStates.select_subgroup)

@dp.callback_query(lambda c: c.data.startswith("subgroup_"), StateFilter(ScheduleStates.select_subgroup))
async def process_subgroup_callback(callback_query: CallbackQuery, state: FSMContext):
    subgroup = callback_query.data.split("_")[1]
    today = datetime.now().date()
    schedule = await get_schedule(today, subgroup)

    data = await state.get_data()
    for msg_id in [data.get("user_command_id"), data.get("start_prompt_id"), data.get("last_message_id")]:
        if msg_id:
            try:
                await bot.delete_message(chat_id=callback_query.message.chat.id, message_id=msg_id)
            except Exception:
                pass

    sent_message = await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=schedule,
        reply_markup=create_date_keyboard(today),
    )

    await state.update_data(
        current_date=today.strftime("%Y-%m-%d"),
        subgroup=subgroup,
        last_message_id=sent_message.message_id,
    )
    await state.set_state(ScheduleStates.select_date)
    await callback_query.answer()

@dp.callback_query(lambda c: c.data.startswith(("prev_", "next_")), StateFilter(ScheduleStates.select_date))
async def process_day_callback(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    subgroup = data.get("subgroup", "all")
    action, date_str = callback_query.data.split("_", 1)
    current_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    new_date = current_date - timedelta(days=1) if action == "prev" else current_date + timedelta(days=1)

    schedule = await get_schedule(new_date, subgroup)
    edited_message = await callback_query.message.edit_text(
        text=schedule, reply_markup=create_date_keyboard(new_date)
    )

    await state.update_data(
        current_date=new_date.strftime("%Y-%m-%d"),
        last_message_id=edited_message.message_id,
    )
    await callback_query.answer()

async def background_updater():
    while True:
        cache = load_cache()
        cache = clean_cache(cache)
        await fetch_schedule_from_api()
        save_cache(cache)
        await asyncio.sleep(3600)

async def main():
    asyncio.create_task(background_updater())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
