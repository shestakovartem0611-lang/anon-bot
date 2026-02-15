import telebot
import sqlite3
import logging
from datetime import datetime
from telebot import types
import time
import os

# ===== НАСТРОЙКИ =====
TOKEN = '8494465153:AAGhNsVnNmDE0LTtSSh2A5GE013Wptw0tvw'  # Вставь сюда новый токен от BotFather
ADMIN_ID = 1760627021     # Вставь свой Telegram ID (число)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TOKEN)

# ===== БАЗА ДАННЫХ =====
def init_db():
    """Создание всех необходимых таблиц"""
    conn = sqlite3.connect('dating_bot.db', check_same_thread=False)
    cur = conn.cursor()
    # Таблица пользователей с анкетами
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            age INTEGER,
            gender TEXT,          -- 'male', 'female', 'other'
            search_gender TEXT,    -- 'male', 'female', 'both'
            bio TEXT,
            photo_file_id TEXT,
            reg_date TIMESTAMP,
            last_active TIMESTAMP,
            is_banned INTEGER DEFAULT 0,
            ban_reason TEXT,
            is_active INTEGER DEFAULT 1
        )
    ''')
    # Таблица текущих диалогов
    cur.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER,
            user2_id INTEGER,
            start_time TIMESTAMP,
            last_message_time TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    ''')
    # Таблица очереди ожидания (с фильтром)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS waiting_queue (
            user_id INTEGER PRIMARY KEY,
            joined_time TIMESTAMP,
            search_gender TEXT
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

init_db()

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def get_user(user_id):
    """Получить данные пользователя по ID"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cur.fetchone()
    conn.close()
    return user

def save_user(user_id, username, first_name):
    """Сохранить нового пользователя при первом входе"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, reg_date, last_active)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, datetime.now(), datetime.now()))
    conn.commit()
    conn.close()

def update_user_profile(user_id, age, gender, search_gender, bio, photo_file_id=None):
    """Обновить анкету пользователя"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    if photo_file_id:
        cur.execute('''
            UPDATE users SET age = ?, gender = ?, search_gender = ?, bio = ?, photo_file_id = ?, last_active = ?
            WHERE user_id = ?
        ''', (age, gender, search_gender, bio, photo_file_id, datetime.now(), user_id))
    else:
        cur.execute('''
            UPDATE users SET age = ?, gender = ?, search_gender = ?, bio = ?, last_active = ?
            WHERE user_id = ?
        ''', (age, gender, search_gender, bio, datetime.now(), user_id))
    conn.commit()
    conn.close()

def is_profile_complete(user_id):
    """Проверяет, заполнена ли анкета"""
    user = get_user(user_id)
    if not user:
        return False
    # Индексы: 2-age, 3-gender, 4-search_gender, 5-bio
    return all([user[2], user[3], user[4], user[5]])

def get_active_conversation(user_id):
    """Возвращает активный диалог пользователя, если есть"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT id, user1_id, user2_id FROM conversations
        WHERE (user1_id = ? OR user2_id = ?) AND is_active = 1
    ''', (user_id, user_id))
    row = cur.fetchone()
    conn.close()
    if row:
        return {'id': row[0], 'user1_id': row[1], 'user2_id': row[2]}
    return None

def get_partner_id(user_id, conv):
    """Возвращает ID собеседника в диалоге"""
    return conv['user2_id'] if conv['user1_id'] == user_id else conv['user1_id']

def end_conversation(conv_id):
    """Завершает диалог"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('UPDATE conversations SET is_active = 0 WHERE id = ?', (conv_id,))
    conn.commit()
    conn.close()

def add_to_waiting(user_id, search_gender):
    """Добавить пользователя в очередь ожидания"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('''
        INSERT OR REPLACE INTO waiting_queue (user_id, joined_time, search_gender)
        VALUES (?, ?, ?)
    ''', (user_id, datetime.now(), search_gender))
    conn.commit()
    conn.close()

def remove_from_waiting(user_id):
    """Удалить пользователя из очереди"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('DELETE FROM waiting_queue WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def find_partner(user_id):
    """
    Ищет подходящего партнёра для user_id.
    Возвращает (partner_id, conv_id) или (None, None) если никого нет.
    """
    user = get_user(user_id)
    if not user:
        return None, None

    search_for = user[4]  # search_gender пользователя
    my_gender = user[3]   # пол пользователя

    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()

    # Ищем в очереди подходящего кандидата
    # Кандидат должен:
    # 1. Быть в очереди
    # 2. Не быть самим собой
    # 3. Соответствовать search_gender пользователя
    # 4. Пользователь должен соответствовать search_gender кандидата
    query = '''
        SELECT w.user_id FROM waiting_queue w
        JOIN users u ON w.user_id = u.user_id
        WHERE w.user_id != ?
        AND u.is_banned = 0
        AND u.is_active = 1
        AND (
            ? = 'both' 
            OR u.gender = ?
        )
        AND (
            u.search_gender = 'both' 
            OR u.search_gender = ?
        )
        LIMIT 1
    '''
    cur.execute(query, (user_id, search_for, search_for, my_gender))
    row = cur.fetchone()

    if row:
        partner_id = row[0]
        # Удаляем обоих из очереди
        cur.execute('DELETE FROM waiting_queue WHERE user_id IN (?, ?)', (user_id, partner_id))
        # Создаём диалог
        cur.execute('''
            INSERT INTO conversations (user1_id, user2_id, start_time, last_message_time)
            VALUES (?, ?, ?, ?)
        ''', (user_id, partner_id, datetime.now(), datetime.now()))
        conn.commit()
        conv_id = cur.lastrowid
        conn.close()
        return partner_id, conv_id
    else:
        conn.close()
        return None, None

def ban_user(user_id, reason='Нарушение правил'):
    """Забанить пользователя"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET is_banned = 1, ban_reason = ? WHERE user_id = ?', (reason, user_id))
    # Также удаляем из очереди и завершаем активные диалоги
    cur.execute('DELETE FROM waiting_queue WHERE user_id = ?', (user_id,))
    cur.execute('UPDATE conversations SET is_active = 0 WHERE user1_id = ? OR user2_id = ?', (user_id, user_id))
    conn.commit()
    conn.close()

def unban_user(user_id):
    """Разбанить пользователя"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET is_banned = 0, ban_reason = NULL WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    """Получить список всех пользователей (для админа)"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id, username, first_name, age, gender, is_banned, last_active FROM users ORDER BY last_active DESC')
    users = cur.fetchall()
    conn.close()
    return users

def get_stats():
    """Получить общую статистику"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM users')
    total_users = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM users WHERE is_banned = 1')
    banned_users = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM conversations WHERE is_active = 1')
    active_chats = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM waiting_queue')
    waiting = cur.fetchone()[0]
    conn.close()
    return total_users, banned_users, active_chats, waiting

def broadcast_message(text):
    """Отправить сообщение всем пользователям (кроме забаненных)"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id FROM users WHERE is_banned = 0')
    users = cur.fetchall()
    conn.close()
    success = 0
    fail = 0
    for (user_id,) in users:
        try:
            bot.send_message(user_id, text)
            success += 1
            time.sleep(0.05)  # чтобы не спамить
        except:
            fail += 1
    return success, fail

# ===== ДЕКОРАТОР ПРОВЕРКИ АДМИНА =====
def admin_only(func):
    def wrapper(message):
        if message.from_user.id != ADMIN_ID:
            bot.reply_to(message, "🚫 Эта команда только для администратора.")
            return
        return func(message)
    return wrapper

# ===== РЕГИСТРАЦИЯ АНКЕТЫ =====
# Хранилище временных данных регистрации (в реальном проекте лучше использовать Redis или БД)
user_data = {}

@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = message.from_user.id
    save_user(user_id, message.from_user.username, message.from_user.first_name)

    if is_user_banned(user_id):
        bot.reply_to(message, "🚫 Вы забанены и не можете пользоваться ботом.")
        return

    if is_profile_complete(user_id):
        # Анкета уже заполнена, показываем главное меню
        show_main_menu(message.chat.id)
    else:
        # Начинаем регистрацию
        bot.send_message(user_id, "👋 Привет! Давай создадим твою анкету для анонимного общения.\n\nСколько тебе лет? (введи число)")
        user_data[user_id] = {'step': 'age'}

@bot.message_handler(func=lambda m: m.from_user.id in user_data and user_data[m.from_user.id]['step'] == 'age')
def process_age(message):
    user_id = message.from_user.id
    try:
        age = int(message.text)
        if age < 12 or age > 100:
            raise ValueError
        user_data[user_id]['age'] = age
        user_data[user_id]['step'] = 'gender'
        # Клавиатура выбора пола
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add('Парень', 'Девушка', 'Другое')
        bot.send_message(user_id, "Выбери свой пол:", reply_markup=markup)
    except:
        bot.send_message(user_id, "❌ Пожалуйста, введи число от 12 до 100.")

@bot.message_handler(func=lambda m: m.from_user.id in user_data and user_data[m.from_user.id]['step'] == 'gender')
def process_gender(message):
    user_id = message.from_user.id
    text = message.text.lower()
    if 'парень' in text:
        gender = 'male'
    elif 'девушк' in text:
        gender = 'female'
    else:
        gender = 'other'
    user_data[user_id]['gender'] = gender
    user_data[user_id]['step'] = 'search_gender'
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add('Парней', 'Девушек', 'Всех')
    bot.send_message(user_id, "Кого ты хочешь искать для общения?", reply_markup=markup)

@bot.message_handler(func=lambda m: m.from_user.id in user_data and user_data[m.from_user.id]['step'] == 'search_gender')
def process_search_gender(message):
    user_id = message.from_user.id
    text = message.text.lower()
    if 'парн' in text:
        search = 'male'
    elif 'девуш' in text:
        search = 'female'
    else:
        search = 'both'
    user_data[user_id]['search_gender'] = search
    user_data[user_id]['step'] = 'bio'
    bot.send_message(user_id, "Напиши немного о себе (чем увлекаешься, что ищешь). Можно не более 300 символов.", reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda m: m.from_user.id in user_data and user_data[m.from_user.id]['step'] == 'bio')
def process_bio(message):
    user_id = message.from_user.id
    bio = message.text[:300]
    user_data[user_id]['bio'] = bio
    user_data[user_id]['step'] = 'photo'
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add('Пропустить')
    bot.send_message(user_id, "Отправь своё фото (необязательно, но повысит шансы на общение). Или нажми 'Пропустить'.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.from_user.id in user_data and user_data[m.from_user.id]['step'] == 'photo', content_types=['photo', 'text'])
def process_photo(message):
    user_id = message.from_user.id
    photo_id = None
    if message.content_type == 'photo':
        photo_id = message.photo[-1].file_id
    # Сохраняем анкету
    data = user_data.pop(user_id)
    update_user_profile(
        user_id,
        data['age'],
        data['gender'],
        data['search_gender'],
        data['bio'],
        photo_id
    )
    bot.send_message(user_id, "✅ Анкета сохранена! Теперь ты можешь искать собеседников.", reply_markup=types.ReplyKeyboardRemove())
    show_main_menu(user_id)

def show_main_menu(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('🔍 Найти собеседника', '⏹ Завершить диалог')
    markup.add('👤 Моя анкета', '✏ Редактировать анкету')
    markup.add('📊 Статистика')
    bot.send_message(chat_id, "Главное меню:", reply_markup=markup)

# ===== ПРОВЕРКА БАНА =====
def is_user_banned(user_id):
    user = get_user(user_id)
    return user and user[10] == 1  # is_banned индекс 10

# ===== КОМАНДЫ ПОЛЬЗОВАТЕЛЯ =====
@bot.message_handler(func=lambda m: m.text == '👤 Моя анкета')
def show_profile(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        return
    gender_str = {'male': 'Парень', 'female': 'Девушка', 'other': 'Другое'}.get(user[3], 'Не указан')
    search_str = {'male': 'Парней', 'female': 'Девушек', 'both': 'Всех'}.get(user[4], 'Не указано')
    profile_text = f"""
👤 Твоя анкета:
Возраст: {user[2]}
Пол: {gender_str}
Ищу: {search_str}
О себе: {user[5]}
"""
    if user[6]:  # photo_file_id
        bot.send_photo(user_id, user[6], caption=profile_text)
    else:
        bot.send_message(user_id, profile_text)

@bot.message_handler(func=lambda m: m.text == '✏ Редактировать анкету')
def edit_profile(message):
    user_id = message.from_user.id
    # Очищаем старую анкету и запускаем регистрацию заново
    # Можно просто начать процесс заново
    bot.send_message(user_id, "Давай создадим новую анкету.")
    # Сбросим данные в БД (можно просто начать регистрацию)
    # Для простоты перезапустим процесс
    user_data[user_id] = {'step': 'age'}
    bot.send_message(user_id, "Сколько тебе лет?")

@bot.message_handler(func=lambda m: m.text == '🔍 Найти собеседника' or m.text == '/search')
def cmd_search(message):
    user_id = message.from_user.id
    if is_user_banned(user_id):
        bot.reply_to(message, "🚫 Вы забанены.")
        return

    if not is_profile_complete(user_id):
        bot.send_message(user_id, "Сначала заполни анкету: /start")
        return

    # Проверяем, нет ли активного диалога
    conv = get_active_conversation(user_id)
    if conv:
        bot.send_message(user_id, "⚠️ У тебя уже есть активный диалог. Заверши его командой /stop")
        return

    # Пытаемся найти партнёра
    bot.send_message(user_id, "🔍 Ищу собеседника...")
    partner_id, conv_id = find_partner(user_id)

    if partner_id:
        # Уведомляем обоих
        bot.send_message(user_id, "✅ Собеседник найден! Можете общаться анонимно.\nОтправляй текст, фото, видео, стикеры.")
        bot.send_message(partner_id, "✅ Собеседник найден! Можете общаться анонимно.\nОтправляй текст, фото, видео, стикеры.")
    else:
        # Добавляем в очередь
        user = get_user(user_id)
        add_to_waiting(user_id, user[4])
        bot.send_message(user_id, "🕒 Пока никого нет. Ты добавлен в очередь. Как только появится подходящий собеседник, я сообщу.")

@bot.message_handler(func=lambda m: m.text == '⏹ Завершить диалог' or m.text == '/stop')
def cmd_stop(message):
    user_id = message.from_user.id
    conv = get_active_conversation(user_id)
    if not conv:
        bot.send_message(user_id, "❌ У тебя нет активного диалога.")
        return

    partner_id = get_partner_id(user_id, conv)
    end_conversation(conv['id'])

    bot.send_message(user_id, "⏹ Диалог завершён.")
    try:
        bot.send_message(partner_id, "👋 Собеседник покинул чат. Диалог завершён.")
    except:
        pass

@bot.message_handler(func=lambda m: m.text == '/next')
def cmd_next(message):
    cmd_stop(message)
    cmd_search(message)

@bot.message_handler(func=lambda m: m.text == '📊 Статистика' or m.text == '/stats')
def cmd_stats(message):
    total, banned, active, waiting = get_stats()
    stats_text = f"""
📊 Статистика бота:
👥 Всего пользователей: {total}
🚫 Забанено: {banned}
💬 Активных диалогов: {active}
🕒 В очереди: {waiting}
    """
    bot.send_message(message.chat.id, stats_text)

# ===== АДМИН-ПАНЕЛЬ =====
@bot.message_handler(commands=['admin'])
@admin_only
def admin_panel(message):
    text = """
👑 **Админ-панель**

Доступные команды:
/admin_stats - Детальная статистика
/users_list - Список пользователей
/ban [id] [причина] - Забанить пользователя
/unban [id] - Разбанить
/broadcast [текст] - Рассылка всем
    """
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(commands=['admin_stats'])
@admin_only
def admin_stats(message):
    total, banned, active, waiting = get_stats()
    # Дополнительные данные
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE date(last_active) = date('now')")
    active_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM conversations WHERE date(start_time) = date('now')")
    new_chats_today = cur.fetchone()[0]
    conn.close()
    text = f"""
📈 **Детальная статистика**
👥 Всего пользователей: {total}
🚫 Забанено: {banned}
💬 Активных диалогов: {active}
🕒 В очереди: {waiting}
📅 Активных сегодня: {active_today}
🆕 Новых чатов сегодня: {new_chats_today}
    """
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(commands=['users_list'])
@admin_only
def users_list(message):
    users = get_all_users()
    if not users:
        bot.send_message(message.chat.id, "Пользователей нет.")
        return
    # Выведем первых 50, чтобы не спамить
    text = "**Список пользователей (первые 50):**\n\n"
    for user in users[:50]:
        user_id, username, first_name, age, gender, is_banned, last_active = user
        status = "🔴 Забанен" if is_banned else "🟢 Активен"
        name = username or first_name or "Без имени"
        line = f"ID: {user_id} | {name} | Возраст: {age} | Пол: {gender} | {status} | Последний вход: {last_active[:16]}\n"
        text += line
    bot.send_message(message.chat.id, text[:4000])  # Telegram ограничение

@bot.message_handler(commands=['ban'])
@admin_only
def ban_command(message):
    try:
        parts = message.text.split(maxsplit=2)
        if len(parts) < 2:
            bot.reply_to(message, "❌ Использование: /ban [user_id] [причина]")
            return
        user_id = int(parts[1])
        reason = parts[2] if len(parts) > 2 else "Нарушение правил"
        ban_user(user_id, reason)
        # Уведомить пользователя
        try:
            bot.send_message(user_id, f"🚫 Вы были забанены. Причина: {reason}")
        except:
            pass
        bot.reply_to(message, f"✅ Пользователь {user_id} забанен. Причина: {reason}")
    except ValueError:
        bot.reply_to(message, "❌ ID должен быть числом.")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['unban'])
@admin_only
def unban_command(message):
    try:
        user_id = int(message.text.split()[1])
        unban_user(user_id)
        bot.reply_to(message, f"✅ Пользователь {user_id} разбанен.")
    except (IndexError, ValueError):
        bot.reply_to(message, "❌ Использование: /unban [user_id]")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['broadcast'])
@admin_only
def broadcast_command(message):
    text = message.text.replace('/broadcast', '', 1).strip()
    if not text:
        bot.reply_to(message, "❌ Введи текст рассылки: /broadcast [текст]")
        return
    bot.reply_to(message, "⏳ Начинаю рассылку...")
    success, fail = broadcast_message(text)
    bot.send_message(message.chat.id, f"✅ Рассылка завершена.\nУспешно: {success}\nНе доставлено: {fail}")

# ===== ОБРАБОТКА СООБЩЕНИЙ В ДИАЛОГЕ =====
@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'sticker', 'voice', 'document'])
def handle_chat_message(message):
    user_id = message.from_user.id

    # Если пользователь в процессе регистрации — пропускаем, обработчики выше уже сработают
    if user_id in user_data:
        return

    if is_user_banned(user_id):
        bot.reply_to(message, "🚫 Вы забанены.")
        return

    # Проверяем, есть ли активный диалог
    conv = get_active_conversation(user_id)
    if not conv:
        # Если нет диалога, предлагаем найти
        bot.send_message(user_id, "❌ У тебя нет активного диалога. Найди собеседника в меню.")
        return

    partner_id = get_partner_id(user_id, conv)

    # Пересылаем сообщение партнёру
    try:
        if message.content_type == 'text':
            bot.send_message(partner_id, f"💬 {message.text}")
        elif message.content_type == 'photo':
            bot.send_photo(partner_id, message.photo[-1].file_id, caption=message.caption)
        elif message.content_type == 'video':
            bot.send_video(partner_id, message.video.file_id, caption=message.caption)
        elif message.content_type == 'sticker':
            bot.send_sticker(partner_id, message.sticker.file_id)
        elif message.content_type == 'voice':
            bot.send_voice(partner_id, message.voice.file_id)
        elif message.content_type == 'document':
            bot.send_document(partner_id, message.document.file_id, caption=message.caption)
        else:
            bot.send_message(partner_id, f"📦 Сообщение (тип: {message.content_type})")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения от {user_id} к {partner_id}: {e}")
        bot.send_message(user_id, "❌ Не удалось отправить сообщение. Возможно, собеседник покинул чат.")
        # Завершаем диалог
        end_conversation(conv['id'])

# ===== ЗАПУСК ЧЕРЕЗ ВЕБХУКИ (ДЛЯ БОТХОСТА) =====
if __name__ == '__main__':
    import time
    import logging
    from flask import Flask, request
    import os

    logger = logging.getLogger(__name__)
    
    # Создаём простенькое Flask-приложение
    app = Flask(__name__)

    @app.route('/', methods=['POST'])
    def webhook():
        """Здесь Telegram будет присылать обновления"""
        json_str = request.get_data().decode('UTF-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return 'OK', 200

    @app.route('/')
    def index():
        return 'Бот работает!', 200

    # Удаляем старый вебхук на всякий случай
    bot.remove_webhook()
    time.sleep(1)

    # ВНИМАНИЕ! СЮДА НУЖНО ВСТАВИТЬ СВОЙ URL, КОТОРЫЙ ДАЁТ BOTHOST
    WEBHOOK_URL = ''  # ЗАМЕНИ ЭТУ СТРОКУ!
    bot.set_webhook(url=WEBHOOK_URL)

    print("=" * 50)
    print("🤖 Анонимный чат-бот запущен в режиме вебхуков")
    print("=" * 50)
    print(f"👑 Админ ID: {ADMIN_ID}")
    print(f"🌐 Вебхук установлен на: {WEBHOOK_URL}")
    print("=" * 50)

    # Запускаем Flask-сервер
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)