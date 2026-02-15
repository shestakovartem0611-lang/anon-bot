import telebot
import sqlite3
import logging
from datetime import datetime, timedelta
from telebot import types
import time
import os
import math

# ===== НАСТРОЙКИ =====
TOKEN = '8494465153:AAGhNsVnNmDE0LTtSSh2A5GE013Wptw0tvw'  # твой токен
ADMIN_ID = 1760627021     # твой ID

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
    conn = sqlite3.connect('dating_bot.db', check_same_thread=False)
    cur = conn.cursor()
    # Таблица пользователей
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            age INTEGER,
            gender TEXT,
            search_gender TEXT,
            bio TEXT,
            photo_file_id TEXT,
            reg_date TIMESTAMP,
            last_active TIMESTAMP,
            is_banned INTEGER DEFAULT 0,
            ban_reason TEXT,
            is_active INTEGER DEFAULT 1,
            rating INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            total_conversations INTEGER DEFAULT 0,
            total_likes INTEGER DEFAULT 0,
            total_dislikes INTEGER DEFAULT 0
        )
    ''')
    # Таблица диалогов
    cur.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER,
            user2_id INTEGER,
            start_time TIMESTAMP,
            last_message_time TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            rated_by_user1 INTEGER DEFAULT 0,
            rated_by_user2 INTEGER DEFAULT 0
        )
    ''')
    # Таблица оценок
    cur.execute('''
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            value INTEGER,
            timestamp TIMESTAMP,
            conversation_id INTEGER
        )
    ''')
    # Таблица очереди
    cur.execute('''
        CREATE TABLE IF NOT EXISTS waiting_queue (
            user_id INTEGER PRIMARY KEY,
            joined_time TIMESTAMP,
            search_gender TEXT
        )
    ''')
    # Таблица жалоб
    cur.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER,
            reported_id INTEGER,
            reason TEXT,
            timestamp TIMESTAMP,
            status TEXT DEFAULT 'new',
            admin_comment TEXT,
            conversation_id INTEGER,
            last_messages TEXT
        )
    ''')
    # Таблица логов сообщений
    cur.execute('''
        CREATE TABLE IF NOT EXISTS message_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            user_id INTEGER,
            message TEXT,
            timestamp TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

init_db()

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def get_user(user_id):
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cur.fetchone()
    conn.close()
    return user

def save_user(user_id, username, first_name):
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, reg_date, last_active)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, datetime.now(), datetime.now()))
    conn.commit()
    conn.close()

def update_user_profile(user_id, age, gender, search_gender, bio, photo_file_id=None):
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
    user = get_user(user_id)
    if not user:
        return False
    # Проверяем, что возраст, пол, поиск и био не пустые
    return all([user[2], user[3], user[4], user[5]])

def get_active_conversation(user_id):
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
    return conv['user2_id'] if conv['user1_id'] == user_id else conv['user1_id']

def end_conversation(conv_id, user_id=None):
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT user1_id, user2_id, rated_by_user1, rated_by_user2 FROM conversations WHERE id = ?', (conv_id,))
    row = cur.fetchone()
    if row:
        user1, user2, rated1, rated2 = row
        cur.execute('UPDATE conversations SET is_active = 0, last_message_time = ? WHERE id = ?', (datetime.now(), conv_id))
        conn.commit()
        if user_id:
            partner = user2 if user_id == user1 else user1
            if (user_id == user1 and not rated1) or (user_id == user2 and not rated2):
                markup = types.InlineKeyboardMarkup(row_width=2)
                btn_like = types.InlineKeyboardButton("👍", callback_data=f"rate_{conv_id}_{partner}_1")
                btn_dislike = types.InlineKeyboardButton("👎", callback_data=f"rate_{conv_id}_{partner}_-1")
                markup.add(btn_like, btn_dislike)
                bot.send_message(user_id, "Диалог завершён. Оцени собеседника:", reply_markup=markup)
    conn.close()

def add_to_waiting(user_id, search_gender):
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('''
        INSERT OR REPLACE INTO waiting_queue (user_id, joined_time, search_gender)
        VALUES (?, ?, ?)
    ''', (user_id, datetime.now(), search_gender))
    conn.commit()
    conn.close()

def remove_from_waiting(user_id):
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('DELETE FROM waiting_queue WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def find_partner(user_id):
    user = get_user(user_id)
    if not user:
        return None, None

    search_for = user[4]
    my_gender = user[3]

    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    query = '''
        SELECT w.user_id FROM waiting_queue w
        JOIN users u ON w.user_id = u.user_id
        WHERE w.user_id != ?
        AND u.is_banned = 0
        AND u.is_active = 1
        AND (? = 'both' OR u.gender = ?)
        AND (u.search_gender = 'both' OR u.search_gender = ?)
        LIMIT 1
    '''
    cur.execute(query, (user_id, search_for, search_for, my_gender))
    row = cur.fetchone()

    if row:
        partner_id = row[0]
        cur.execute('DELETE FROM waiting_queue WHERE user_id IN (?, ?)', (user_id, partner_id))
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

def ban_user(user_id, reason='Нарушение правил', admin_id=None):
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET is_banned = 1, ban_reason = ? WHERE user_id = ?', (reason, user_id))
    cur.execute('DELETE FROM waiting_queue WHERE user_id = ?', (user_id,))
    cur.execute('UPDATE conversations SET is_active = 0 WHERE user1_id = ? OR user2_id = ?', (user_id, user_id))
    conn.commit()
    conn.close()
    if admin_id:
        bot.send_message(admin_id, f"✅ Пользователь {user_id} забанен. Причина: {reason}")
    try:
        bot.send_message(user_id, f"🚫 Вы были забанены. Причина: {reason}")
    except:
        pass

def unban_user(user_id):
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET is_banned = 0, ban_reason = NULL WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id, username, first_name, age, gender, is_banned, last_active FROM users ORDER BY last_active DESC')
    users = cur.fetchall()
    conn.close()
    return users

def get_stats():
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
            time.sleep(0.05)
        except:
            fail += 1
    return success, fail

def update_user_level(user_id):
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT total_conversations, total_likes FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    if row:
        convs, likes = row
        level = 1 + int(math.sqrt(convs + likes // 2))
        cur.execute('UPDATE users SET level = ? WHERE user_id = ?', (level, user_id))
        conn.commit()
    conn.close()

# ===== ДЕКОРАТОР ПРОВЕРКИ АДМИНА =====
def admin_only(func):
    def wrapper(message):
        if message.from_user.id != ADMIN_ID:
            bot.reply_to(message, "🚫 Эта команда только для администратора.")
            return
        return func(message)
    return wrapper

# ===== РЕГИСТРАЦИЯ АНКЕТЫ =====
user_data = {}

@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = message.from_user.id
    save_user(user_id, message.from_user.username, message.from_user.first_name)

    if is_user_banned(user_id):
        bot.reply_to(message, "🚫 Вы забанены и не можете пользоваться ботом.")
        return

    if is_profile_complete(user_id):
        show_main_menu(message.chat.id)
    else:
        bot.send_message(user_id, "👋 Привет! Давай создадим твою анкету.\n\nСколько тебе лет? (введи число)")
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
    bot.send_message(user_id, "Кого ты хочешь искать?", reply_markup=markup)

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
    bot.send_message(user_id, "Напиши немного о себе (до 300 символов).", reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda m: m.from_user.id in user_data and user_data[m.from_user.id]['step'] == 'bio')
def process_bio(message):
    user_id = message.from_user.id
    bio = message.text[:300]
    user_data[user_id]['bio'] = bio
    user_data[user_id]['step'] = 'photo'
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add('Пропустить')
    bot.send_message(user_id, "Отправь своё фото (необязательно) или нажми 'Пропустить'.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.from_user.id in user_data and user_data[m.from_user.id]['step'] == 'photo', content_types=['photo', 'text'])
def process_photo(message):
    user_id = message.from_user.id
    photo_id = None
    if message.content_type == 'photo':
        photo_id = message.photo[-1].file_id
    data = user_data.pop(user_id)
    update_user_profile(user_id, data['age'], data['gender'], data['search_gender'], data['bio'], photo_id)
    bot.send_message(user_id, "✅ Анкета сохранена!", reply_markup=types.ReplyKeyboardRemove())
    show_main_menu(user_id)

def show_main_menu(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('🔍 Найти собеседника', '⏹ Завершить диалог')
    markup.add('👤 Моя анкета', '✏ Редактировать анкету')
    markup.add('📊 Статистика', '📈 Моя статистика')
    markup.add('🏆 Топ пользователей')
    bot.send_message(chat_id, "Главное меню:", reply_markup=markup)

# ===== ПРОВЕРКА БАНА =====
def is_user_banned(user_id):
    user = get_user(user_id)
    return user and user[10] == 1

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
Уровень: {user[12]}
Рейтинг: {user[11]}
    """
    # Отправляем фото, если есть, но с защитой от ошибок
    if user[6]:
        try:
            bot.send_photo(user_id, user[6], caption=profile_text)
        except Exception as e:
            logger.error(f"Ошибка отправки фото в профиле: {e}")
            bot.send_message(user_id, profile_text + "\n\n(Фото не может быть отображено, возможно, оно устарело.)")
    else:
        bot.send_message(user_id, profile_text)

@bot.message_handler(func=lambda m: m.text == '✏ Редактировать анкету')
def edit_profile(message):
    user_id = message.from_user.id
    bot.send_message(user_id, "Давай создадим новую анкету.")
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
    conv = get_active_conversation(user_id)
    if conv:
        bot.send_message(user_id, "⚠️ У тебя уже есть активный диалог. Заверши его командой /stop")
        return
    bot.send_message(user_id, "🔍 Ищу собеседника...")
    partner_id, conv_id = find_partner(user_id)
    if partner_id:
        bot.send_message(user_id, "✅ Собеседник найден! Можете общаться.")
        bot.send_message(partner_id, "✅ Собеседник найден! Можете общаться.")
    else:
        user = get_user(user_id)
        add_to_waiting(user_id, user[4])
        bot.send_message(user_id, "🕒 Пока никого нет. Ты в очереди.")

@bot.message_handler(func=lambda m: m.text == '⏹ Завершить диалог' or m.text == '/stop')
def cmd_stop(message):
    user_id = message.from_user.id
    conv = get_active_conversation(user_id)
    if not conv:
        bot.send_message(user_id, "❌ У тебя нет активного диалога.")
        return
    end_conversation(conv['id'], user_id)
    bot.send_message(user_id, "⏹ Диалог завершён.")

@bot.message_handler(func=lambda m: m.text == '/next')
def cmd_next(message):
    cmd_stop(message)
    cmd_search(message)

@bot.message_handler(func=lambda m: m.text == '📊 Статистика' or m.text == '/stats')
def cmd_stats(message):
    total, banned, active, waiting = get_stats()
    stats_text = f"""
📊 Статистика бота:
👥 Всего: {total}
🚫 Забанено: {banned}
💬 Активных диалогов: {active}
🕒 В очереди: {waiting}
    """
    bot.send_message(message.chat.id, stats_text)

@bot.message_handler(func=lambda m: m.text == '📈 Моя статистика' or m.text == '/mystats')
def cmd_mystats(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        return
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM conversations WHERE (user1_id = ? OR user2_id = ?) AND is_active = 0', (user_id, user_id))
    total_dialogs = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM ratings WHERE to_user = ? AND value = 1', (user_id,))
    likes = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM ratings WHERE to_user = ? AND value = -1', (user_id,))
    dislikes = cur.fetchone()[0]
    conn.close()
    text = f"""
📈 Твоя статистика:
👥 Диалогов: {total_dialogs}
👍 Лайков: {likes}
👎 Дизлайков: {dislikes}
⭐ Рейтинг: {user[11]}
📊 Уровень: {user[12]}
    """
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == '🏆 Топ пользователей' or m.text == '/top')
def cmd_top(message):
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT user_id, first_name, rating, level FROM users
        WHERE is_banned = 0 AND rating > 0
        ORDER BY rating DESC, level DESC
        LIMIT 10
    ''')
    top = cur.fetchall()
    conn.close()
    if not top:
        bot.send_message(message.chat.id, "Пока нет пользователей с рейтингом.")
        return
    text = "🏆 Топ-10 пользователей:\n\n"
    for i, (uid, name, rating, level) in enumerate(top, 1):
        text += f"{i}. {name or 'Без имени'} (ID: {uid}) — рейтинг {rating}, уровень {level}\n"
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=['report'])
def cmd_report(message):
    user_id = message.from_user.id
    conv = get_active_conversation(user_id)
    if not conv:
        bot.reply_to(message, "❌ У тебя нет активного диалога, чтобы на кого-то пожаловаться.")
        return
    partner_id = get_partner_id(user_id, conv)
    msg = bot.reply_to(message, "Опиши причину жалобы (можно одним сообщением):")
    bot.register_next_step_handler(msg, process_report, conv['id'], user_id, partner_id)

def process_report(message, conv_id, reporter_id, reported_id):
    reason = message.text[:500]
    # Сохраняем последние 10 сообщений диалога
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT user_id, message FROM message_logs
        WHERE conversation_id = ?
        ORDER BY timestamp DESC LIMIT 10
    ''', (conv_id,))
    logs = cur.fetchall()
    log_text = "\n".join([f"{'Собеседник' if uid == reported_id else 'Вы'}: {msg}" for uid, msg in reversed(logs)])
    cur.execute('''
        INSERT INTO reports (reporter_id, reported_id, reason, timestamp, conversation_id, last_messages, status)
        VALUES (?, ?, ?, ?, ?, ?, 'new')
    ''', (reporter_id, reported_id, reason, datetime.now(), conv_id, log_text))
    conn.commit()
    report_id = cur.lastrowid
    conn.close()
    admin_msg = f"""
🚨 Новая жалоба #{report_id}
От: {reporter_id}
На: {reported_id}
Причина: {reason}

Последние сообщения:
{log_text}
    """
    bot.send_message(ADMIN_ID, admin_msg)
    bot.reply_to(message, "✅ Жалоба отправлена администратору. Спасибо!")

# ===== ОБРАБОТКА РЕЙТИНГА =====
@bot.callback_query_handler(func=lambda call: call.data.startswith('rate_'))
def callback_rate(call):
    _, conv_id, partner_id, value = call.data.split('_')
    conv_id = int(conv_id)
    partner_id = int(partner_id)
    value = int(value)
    user_id = call.from_user.id

    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT rated_by_user1, rated_by_user2 FROM conversations WHERE id = ?', (conv_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return
    rated1, rated2 = row
    if (user_id == row[0] and not rated1) or (user_id == row[1] and not rated2):
        cur.execute('''
            INSERT INTO ratings (from_user, to_user, value, timestamp, conversation_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, partner_id, value, datetime.now(), conv_id))
        if value == 1:
            cur.execute('UPDATE users SET rating = rating + 1, total_likes = total_likes + 1 WHERE user_id = ?', (partner_id,))
        else:
            cur.execute('UPDATE users SET rating = rating - 1, total_dislikes = total_dislikes + 1 WHERE user_id = ?', (partner_id,))
        if user_id == row[0]:
            cur.execute('UPDATE conversations SET rated_by_user1 = 1 WHERE id = ?', (conv_id,))
        else:
            cur.execute('UPDATE conversations SET rated_by_user2 = 1 WHERE id = ?', (conv_id,))
        conn.commit()
        update_user_level(partner_id)
        bot.answer_callback_query(call.id, "Спасибо за оценку!")
        bot.edit_message_text("Оценка учтена. Спасибо!", call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, "Вы уже оценили этого собеседника.")
    conn.close()

# ===== АДМИН-ПАНЕЛЬ =====
@bot.message_handler(commands=['admin'])
@admin_only
def admin_panel(message):
    text = """
👑 **Админ-панель**

Доступные команды:
/admin_stats - Детальная статистика
/users_list - Список пользователей
/ban [id] [причина] - Забанить
/unban [id] - Разбанить
/broadcast [текст] - Рассылка
/demographics - Статистика по полу и возрасту
/reports - Список новых жалоб
/report [id] - Просмотр жалобы
    """
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(commands=['admin_stats'])
@admin_only
def admin_stats(message):
    total, banned, active, waiting = get_stats()
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE date(last_active) = date('now')")
    active_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM conversations WHERE date(start_time) = date('now')")
    new_chats_today = cur.fetchone()[0]
    conn.close()
    text = f"""
📈 **Детальная статистика**
👥 Всего: {total}
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
    text = "**Список пользователей (первые 50):**\n\n"
    for user in users[:50]:
        user_id, username, first_name, age, gender, is_banned, last_active = user
        status = "🔴 Забанен" if is_banned else "🟢 Активен"
        name = username or first_name or "Без имени"
        line = f"ID: {user_id} | {name} | Возраст: {age} | Пол: {gender} | {status} | Последний вход: {last_active[:16]}\n"
        text += line
    bot.send_message(message.chat.id, text[:4000])

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
        ban_user(user_id, reason, message.from_user.id)
        bot.reply_to(message, f"✅ Пользователь {user_id} забанен.")
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

@bot.message_handler(commands=['demographics'])
@admin_only
def demographics(message):
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute("SELECT gender, COUNT(*) FROM users WHERE is_banned = 0 GROUP BY gender")
    gender_stats = cur.fetchall()
    cur.execute("SELECT AVG(age) FROM users WHERE is_banned = 0 AND age IS NOT NULL")
    avg_age = cur.fetchone()[0] or 0
    cur.execute("SELECT age, COUNT(*) FROM users WHERE is_banned = 0 AND age IS NOT NULL GROUP BY age ORDER BY age")
    age_dist = cur.fetchall()
    conn.close()
    text = "📊 **Демография**\n\n"
    for gender, count in gender_stats:
        gender_name = {'male': 'Парни', 'female': 'Девушки', 'other': 'Другое'}.get(gender, gender)
        text += f"{gender_name}: {count}\n"
    text += f"\nСредний возраст: {avg_age:.1f}\n\nРаспределение по возрастам:\n"
    for age, cnt in age_dist[:20]:
        text += f"{age} лет: {cnt}\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(commands=['reports'])
@admin_only
def list_reports(message):
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute("SELECT id, reporter_id, reported_id, reason, timestamp FROM reports WHERE status = 'new' ORDER BY timestamp DESC LIMIT 10")
    reports = cur.fetchall()
    conn.close()
    if not reports:
        bot.send_message(message.chat.id, "Новых жалоб нет.")
        return
    text = "📋 **Новые жалобы:**\n\n"
    for r in reports:
        text += f"#{r[0]} от {r[4][:16]}: {r[1]} -> {r[2]}\nПричина: {r[3][:50]}...\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(commands=['report'])
@admin_only
def view_report(message):
    try:
        report_id = int(message.text.split()[1])
    except:
        bot.reply_to(message, "❌ Использование: /report [id]")
        return
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT reporter_id, reported_id, reason, timestamp, last_messages, status
        FROM reports WHERE id = ?
    ''', (report_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        bot.reply_to(message, "❌ Жалоба не найдена.")
        return
    reporter, reported, reason, ts, logs, status = row
    text = f"""
📋 **Жалоба #{report_id}**
Статус: {status}
От: {reporter}
На: {reported}
Время: {ts}
Причина: {reason}

**Последние сообщения:**
{logs}

Чтобы забанить нарушителя, ответь на это сообщение командой /ban {reported} [причина]
    """
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

# Обработка ответов админа на жалобы
@bot.message_handler(func=lambda m: m.reply_to_message and m.text.startswith('/ban'))
@admin_only
def admin_ban_from_report(message):
    try:
        parts = message.text.split()
        if len(parts) >= 2:
            user_id = int(parts[1])
            reason = ' '.join(parts[2:]) if len(parts) > 2 else "Нарушение правил (по жалобе)"
        else:
            text = message.reply_to_message.text
            import re
            match = re.search(r'На:\s*(\d+)', text)
            if match:
                user_id = int(match.group(1))
                reason = "Нарушение по жалобе"
            else:
                bot.reply_to(message, "❌ Не удалось определить ID пользователя.")
                return
        ban_user(user_id, reason, message.from_user.id)
        bot.reply_to(message, f"✅ Пользователь {user_id} забанен.")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")

# ===== ОБРАБОТКА СООБЩЕНИЙ В ДИАЛОГЕ =====
@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'sticker', 'voice', 'document'])
def handle_chat_message(message):
    user_id = message.from_user.id
    if user_id in user_data:
        return
    if is_user_banned(user_id):
        bot.reply_to(message, "🚫 Вы забанены.")
        return
    conv = get_active_conversation(user_id)
    if not conv:
        bot.send_message(user_id, "❌ У тебя нет активного диалога. Найди собеседника в меню.")
        return
    partner_id = get_partner_id(user_id, conv)

    # Логируем сообщение
    try:
        msg_text = message.text or message.caption or f"[{message.content_type}]"
        conn = sqlite3.connect('dating_bot.db')
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO message_logs (conversation_id, user_id, message, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (conv['id'], user_id, msg_text, datetime.now()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка логирования: {e}")

    # Пересылаем
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
        logger.error(f"Ошибка отправки: {e}")
        bot.send_message(user_id, "❌ Не удалось отправить сообщение. Возможно, собеседник покинул чат.")
        end_conversation(conv['id'])

# ===== ЗАПУСК =====
if __name__ == '__main__':
    print("=" * 50)
    print("🤖 Анонимный чат-бот с новыми функциями")
    print("=" * 50)
    print(f"👑 Админ ID: {ADMIN_ID}")
    print("🟢 Запуск...")

    # Удаляем вебхук и запускаем polling
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling()