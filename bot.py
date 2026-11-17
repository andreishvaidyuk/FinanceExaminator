import json
import random
import logging
import asyncio
import os
from html import escape
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- ЗАГРУЗКА ОКРУЖЕНИЯ ---
load_dotenv()  # Эта функция ищет файл .env и загружает переменные из него
TOKEN = os.environ.get("TOKEN")  # Достаем токен из окружения

# На всякий случай проверяем, загрузился ли токен
if not TOKEN:
    exit("Ошибка: TOKEN не найден в файле .env")

logging.basicConfig(level=logging.INFO)

QUESTIONS_PER_QUIZ = 20
LABELS = "ABCDEFGH"

# Предметные области: ключ -> (название, файл с вопросами)
DOMAINS = {
    "osnovy_finansov": ("Основы финансов", "osnovy_finansov_ru.json"),
    "korporativnye_finansy": ("Корпоративные финансы", "korporativnye_finansy.json"),
}


class QuizStates(StatesGroup):
    choosing_domain = State()
    in_progress = State()
    answered = State()


# --- ЗАГРУЗКА ДАННЫХ ---
def load_questions():
    data = {}
    for key, (_, filename) in DOMAINS.items():
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                data[key] = json.load(f)
        else:
            logging.error(f"Файл {filename} не найден!")
            data[key] = []
    return data


QUESTIONS = load_questions()

bot = Bot(token=TOKEN)
dp = Dispatcher()


def correct_answers(question) -> list:
    """Поле 'correct' может быть строкой (один ответ) или списком (несколько)."""
    correct = question['correct']
    return correct if isinstance(correct, list) else [correct]


def is_multi(question) -> bool:
    return isinstance(question['correct'], list)


def options_keyboard(options, multi: bool, selected=()):
    builder = InlineKeyboardBuilder()
    for i in range(len(options)):
        if multi:
            mark = "✅ " if i in selected else ""
            builder.add(types.InlineKeyboardButton(text=f"{mark}{LABELS[i]}", callback_data=f"tog_{i}"))
        else:
            builder.add(types.InlineKeyboardButton(text=LABELS[i], callback_data=f"ans_{i}"))
    builder.adjust(3 if len(options) > 4 else 2)
    if multi:
        builder.row(types.InlineKeyboardButton(text="Ответить ✔️", callback_data="submit"))
    return builder.as_markup()


# --- ХЕНДЛЕРЫ ---

WELCOME_TEXT = (
    "👋 <b>Добро пожаловать в FinanceExaminator!</b>\n\n"
    "Этот бот поможет подготовиться к экзамену по финансам.\n\n"
    "📚 <b>Доступные темы:</b>\n"
    + "".join(f"• {escape(title)}\n" for title, _ in DOMAINS.values())
    + "\n📝 <b>Как проходит тест:</b>\n"
    f"• до {QUESTIONS_PER_QUIZ} случайных вопросов из выбранной темы;\n"
    "• если верный ответ один — просто нажмите на букву;\n"
    "• если верных ответов несколько — отметьте все и нажмите «Ответить»;\n"
    "• после каждого ответа вы увидите правильный вариант и пояснение;\n"
    "• в конце — итоговый результат.\n\n"
    "<b>Важно знать</b>\n"
    "Это не официальные вопросы Национального центра тестирования.\n" 
    "Задания составлены по темам официальной спецификации и учебной теории,\n" 
    "часть из них сгенерирована искусственным интеллектом.\n\n"
    "В вопросах и ответах возможны неточности. Используйте тест для тренировки,\n" 
    "а в спорных случаях сверяйтесь с учебниками и актуальными нормативными документами РК.\n\n"
    "Чтобы начать заново в любой момент, отправьте /start.\n\n"
    "Нажмите «Начать», когда будете готовы 👇"
)


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="🚀 Начать", callback_data="begin"))
    await message.answer(WELCOME_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data == "begin")
async def show_domains(callback: types.CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    for key, (title, _) in DOMAINS.items():
        builder.row(types.InlineKeyboardButton(text=title, callback_data=f"dom_{key}"))
    await callback.message.edit_text("Выберите тему теста:", reply_markup=builder.as_markup())
    await state.set_state(QuizStates.choosing_domain)
    await callback.answer()


@dp.callback_query(F.data.startswith("dom_"))
async def select_domain(callback: types.CallbackQuery, state: FSMContext):
    domain = callback.data.removeprefix("dom_")
    if not QUESTIONS.get(domain):
        await callback.answer("Ошибка: вопросы не загружены.", show_alert=True)
        return

    # Выбираем случайные вопросы
    pool = random.sample(QUESTIONS[domain], min(len(QUESTIONS[domain]), QUESTIONS_PER_QUIZ))

    await state.update_data(
        domain=domain,
        questions=pool,
        current_idx=0,
        score=0
    )
    await callback.answer()
    await send_question(callback.message, state)


async def send_question(message: types.Message, state: FSMContext):
    data = await state.get_data()
    q_idx = data['current_idx']
    questions = data['questions']

    if q_idx < len(questions):
        question = questions[q_idx]
        multi = is_multi(question)

        # Перемешиваем варианты
        options = list(question['options'])
        random.shuffle(options)

        # Формируем текст вопроса и список вариантов A, B, C...
        options_text = "".join(f"<b>{LABELS[i]})</b> {escape(opt)}\n" for i, opt in enumerate(options))
        hint = "\n<i>Выберите все верные варианты и нажмите «Ответить»</i>" if multi else ""
        text = (
            f"❓ <b>Вопрос {q_idx + 1}/{len(questions)}</b>\n\n"
            f"{escape(question['text'])}\n\n{options_text}"
        )

        # Сохраняем перемешанные варианты и текст вопроса,
        # чтобы показать вопрос вместе с результатом после ответа
        await state.update_data(current_options=options, current_text=text, selected=[])

        await message.edit_text(text + hint, reply_markup=options_keyboard(options, multi), parse_mode="HTML")
        await state.set_state(QuizStates.in_progress)
    else:
        # Финал
        score, total = data['score'], len(questions)
        title = DOMAINS[data['domain']][0]
        builder = InlineKeyboardBuilder()
        builder.add(types.InlineKeyboardButton(text="🔄 Пройти ещё раз", callback_data="begin"))
        await message.edit_text(
            f"🏆 {escape(title)}\nРезультат: {score}/{total}",
            reply_markup=builder.as_markup()
        )
        await state.clear()


@dp.callback_query(F.data.startswith("tog_"), QuizStates.in_progress)
async def toggle_option(callback: types.CallbackQuery, state: FSMContext):
    """Выбор/снятие варианта в вопросе с несколькими ответами."""
    data = await state.get_data()
    idx = int(callback.data.removeprefix("tog_"))
    selected = set(data.get('selected', []))
    selected ^= {idx}
    await state.update_data(selected=sorted(selected))

    await callback.message.edit_reply_markup(
        reply_markup=options_keyboard(data['current_options'], True, selected)
    )
    await callback.answer()


@dp.callback_query(F.data == "submit", QuizStates.in_progress)
async def submit_multi(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get('selected'):
        await callback.answer("Выберите хотя бы один вариант.", show_alert=True)
        return
    await show_result(callback, state, data['selected'])


@dp.callback_query(F.data.startswith("ans_"), QuizStates.in_progress)
async def handle_answer(callback: types.CallbackQuery, state: FSMContext):
    idx = int(callback.data.removeprefix("ans_"))
    await show_result(callback, state, [idx])


async def show_result(callback: types.CallbackQuery, state: FSMContext, chosen_idx: list):
    data = await state.get_data()
    question = data['questions'][data['current_idx']]
    options = data['current_options']

    correct = correct_answers(question)
    chosen = {options[i] for i in chosen_idx}
    is_correct = chosen == set(correct)

    if is_correct:
        await state.update_data(score=data['score'] + 1)
        res_text = "✅ Верно!"
    else:
        expected = "\n".join(
            f"<b>{LABELS[options.index(c)]})</b> {escape(c)}" if c in options else escape(c)
            for c in correct
        )
        res_text = f"❌ Ошибка!\nОжидалось:\n{expected}"

    note_text = f"\n\n📖 {escape(question['note'])}" if question.get('note') else ""
    chosen_labels = ", ".join(LABELS[i] for i in sorted(chosen_idx))

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Далее ➡️", callback_data="next_q"))

    # Показываем вопрос с вариантами + выбранный ответ + результат
    full_text = (
        f"{data['current_text']}\n"
        f"👉 Ваш ответ: <b>{chosen_labels}</b>\n\n"
        f"{res_text}{note_text}"
    )

    # Сбрасываем состояние, чтобы повторные нажатия по старым кнопкам не засчитывались
    await state.set_state(QuizStates.answered)
    await callback.message.edit_text(full_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "next_q", QuizStates.answered)
async def next_handler(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(current_idx=data['current_idx'] + 1)
    await callback.answer()
    await send_question(callback.message, state)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
