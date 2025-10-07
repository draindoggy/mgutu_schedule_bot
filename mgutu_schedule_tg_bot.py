import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import aiohttp
import os
from dotenv import load_dotenv


load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SCHEDULE_URL = 'https://dec.mgutm.ru/api/Rasp?idGroup=30948'

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class ScheduleStates(StatesGroup):
    select_subgroup = State()
    select_date = State()

async def get_schedule(date: datetime.date, subgroup: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(SCHEDULE_URL) as response:
            if response.status != 200:
                return f"ошибка при загрузке расписания: статус {response.status}"
            data = await response.json()

    if not data.get('data', {}).get('rasp'):
        return f"расписание для {date.strftime('%d.%m.%Y')} не найдено."

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
        return f"расписание для {date.strftime('%d.%m.%Y')} ({subgroup_text}) не найдено."

    schedule_text = (
        f"{day_of_week}\n\n"
        f"расписание на {date.strftime('%d.%m.%Y')} ({subgroup_text})\n\n"
        + schedule_text.split('\n', 2)[2]
    )

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

@dp.callback_query(
    lambda c: c.data.startswith("subgroup_"), StateFilter(ScheduleStates.select_subgroup)
)
async def process_subgroup_callback(callback_query: CallbackQuery, state: FSMContext):
    subgroup = callback_query.data.split("_")[1]
    today = datetime.now().date()
    schedule = await get_schedule(today, subgroup)

    data = await state.get_data()
    user_command_id = data.get("user_command_id")
    start_prompt_id = data.get("start_prompt_id")
    last_message_id = data.get("last_message_id")

    for msg_id in [user_command_id, start_prompt_id, last_message_id]:
        if msg_id:
            try:
                await bot.delete_message(
                    chat_id=callback_query.message.chat.id, message_id=msg_id
                )
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

@dp.callback_query(
    lambda c: c.data.startswith(("prev_", "next_")), StateFilter(ScheduleStates.select_date)
)
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

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())