import telebot
import sqlite3
import logging
from datetime import datetime, timedelta
from telebot import types
import time
import os
import math
import re
import json

# ===== НАСТРОЙКИ =====
TOKEN = '8494465153:AAGhNsVnNmDE0LTtSSh2A5GE013Wptw0tvw'  # твой токен
ADMIN_ID = 1760627021     # твой ID
REFERRAL_BONUS = 20       # бонус за приглашённого друга (монет)
REFERRAL_BONUS_FOR_NEW = 10  # бонус новому пользователю за регистрацию по рефералке

# Список доступных сумм для донатов (можешь менять)
DONATION_AMOUNTS = [5, 10, 20, 50, 100, 200]

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
    # Таблица пользователей с полями по умолчанию
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            age INTEGER DEFAULT 0,
            gender TEXT DEFAULT 'не указан',
            search_gender TEXT DEFAULT 'both',
            bio TEXT DEFAULT '',
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
            total_dislikes INTEGER DEFAULT 0,
            coins INTEGER DEFAULT 0,
            last_bonus TIMESTAMP,
            referrer_id INTEGER DEFAULT NULL
        )
    ''')
    # Таблица достижений
    cur.execute('''
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            description TEXT,
            condition_type TEXT,
            condition_value INTEGER,
            reward_coins INTEGER
        )
    ''')
    # Таблица полученных достижений
    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_achievements (
            user_id INTEGER,
            achievement_id INTEGER,
            unlocked_at TIMESTAMP,
            PRIMARY KEY (user_id, achievement_id)
        )
    ''')
    # Таблица для учёта наград за рефералов
    cur.execute('''
        CREATE TABLE IF NOT EXISTS referral_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            reward_coins INTEGER,
            rewarded_at TIMESTAMP
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
    # Заполняем достижения, если пусто
    cur.execute('SELECT COUNT(*) FROM achievements')
    if cur.fetchone()[0] == 0:
        achievements = [
            ('Первый шаг', 'Провести первый диалог', 'conversations', 1, 5),
            ('Болтун', 'Провести 10 диалогов', 'conversations', 10, 20),
            ('Звезда', 'Получить 10 лайков', 'likes', 10, 15),
            ('Популярный', 'Получить 50 лайков', 'likes', 50, 50),
            ('Джентльмен', 'Получить 5 комплиментов', 'consecutive_likes', 5, 10),
            ('Завсегдатай', 'Заходить 7 дней подряд', 'streak_days', 7, 30),
            ('Фотогеничный', 'Загрузить фото', 'photo', 1, 5),
            ('Душа компании', 'Провести 5 диалогов за день', 'daily_conversations', 5, 25),
            ('Модератор', 'Отправить 3 жалобы', 'reports', 3, 10),
            ('Благодетель', 'Сделать донат', 'donation', 1, 0)
        ]
        for a in achievements:
            cur.execute('''
                INSERT INTO achievements (name, description, condition_type, condition_value, reward_coins)
                VALUES (?, ?, ?, ?, ?)
            ''', a)
        conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

init_db()

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def get_user(user_id):
    """Возвращает пользователя или None, с обработкой ошибок"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cur.fetchone()
    conn.close()
    return user

def save_user(user_id, username, first_name, referrer_id=None):
    """Сохраняет нового пользователя или обновляет время активности"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    if cur.fetchone():
        cur.execute('UPDATE users SET last_active = ? WHERE user_id = ?', (datetime.now(), user_id))
    else:
        cur.execute('''
            INSERT INTO users (user_id, username, first_name, reg_date, last_active, referrer_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, datetime.now(), datetime.now(), referrer_id))
        if referrer_id:
            cur.execute('SELECT 1 FROM referral_rewards WHERE referred_id = ?', (user_id,))
            if not cur.fetchone():
                cur.execute('UPDATE users SET coins = coins + ? WHERE user_id = ?', (REFERRAL_BONUS, referrer_id))
                cur.execute('UPDATE users SET coins = coins + ? WHERE user_id = ?', (REFERRAL_BONUS_FOR_NEW, user_id))
                cur.execute('''
                    INSERT INTO referral_rewards (referrer_id, referred_id, reward_coins, rewarded_at)
                    VALUES (?, ?, ?, ?)
                ''', (referrer_id, user_id, REFERRAL_BONUS, datetime.now()))
                try:
                    bot.send_message(referrer_id, f"🎉 По вашей реферальной ссылке зарегистрировался новый пользователь! Вам начислено {REFERRAL_BONUS} монет.")
                except:
                    pass
    conn.commit()
    conn.close()

def update_user_profile(user_id, age, gender, search_gender, bio, photo_file_id=None):
    """Обновляет анкету пользователя"""
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
    """Проверяет, заполнены ли основные поля анкеты"""
    user = get_user(user_id)
    if not user:
        return False
    # Индексы полей: age (2), gender (3), search_gender (4), bio (5)
    # Проверяем, что они не пустые и не равны значениям по умолчанию
    return (user[2] and user[2] != 0 and 
            user[3] and user[3] != 'не указан' and 
            user[4] and user[4] != 'both' and 
            user[5] and user[5] != '')

def get_active_conversation(user_id):
    """Возвращает активный диалог пользователя"""
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

def end_conversation(conv_id):
    """Завершает диалог и отправляет оценку/жалобу обоим"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT user1_id, user2_id, rated_by_user1, rated_by_user2 FROM conversations WHERE id = ?', (conv_id,))
    row = cur.fetchone()
    if row:
        user1, user2, rated1, rated2 = row
        cur.execute('UPDATE conversations SET is_active = 0, last_message_time = ? WHERE id = ?', (datetime.now(), conv_id))
        cur.execute('UPDATE users SET total_conversations = total_conversations + 1 WHERE user_id IN (?, ?)', (user1, user2))
        conn.commit()
        conn.close()

        check_achievements(user1)
        check_achievements(user2)

        def get_feedback_keyboard(conv_id, partner_id):
            markup = types.InlineKeyboardMarkup(row_width=3)
            btn_like = types.InlineKeyboardButton("👍", callback_data=f"rate_{conv_id}_{partner_id}_1")
            btn_dislike = types.InlineKeyboardButton("👎", callback_data=f"rate_{conv_id}_{partner_id}_-1")
            btn_report = types.InlineKeyboardButton("🚩 Пожаловаться", callback_data=f"report_{conv_id}_{partner_id}")
            markup.add(btn_like, btn_dislike, btn_report)
            return markup

        if not rated1:
            try:
                bot.send_message(user1, "Диалог завершён. Оцени собеседника или отправь жалобу:", reply_markup=get_feedback_keyboard(conv_id, user2))
            except Exception as e:
                logger.error(f"Не удалось отправить feedback пользователю {user1}: {e}")

        if not rated2:
            try:
                bot.send_message(user2, "Диалог завершён. Оцени собеседника или отправь жалобу:", reply_markup=get_feedback_keyboard(conv_id, user1))
            except Exception as e:
                logger.error(f"Не удалось отправить feedback пользователю {user2}: {e}")
    else:
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
    """Поиск подходящего собеседника"""
    user = get_user(user_id)
    if not user:
        return None, None

    search_for = user[4]  # search_gender
    my_gender = user[3]   # gender

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

def check_achievements(user_id):
    """Проверка и выдача достижений"""
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT id, condition_type, condition_value, reward_coins FROM achievements')
    achievements = cur.fetchall()
    cur.execute('''
        SELECT total_conversations, total_likes, coins, last_bonus, photo_file_id
        FROM users WHERE user_id = ?
    ''', (user_id,))
    user_data = cur.fetchone()
    if not user_data:
        conn.close()
        return
    convs, likes, coins, last_bonus, photo = user_data

    for ach_id, cond_type, cond_val, reward in achievements:
        cur.execute('SELECT 1 FROM user_achievements WHERE user_id = ? AND achievement_id = ?', (user_id, ach_id))
        if cur.fetchone():
            continue
        unlocked = False
        if cond_type == 'conversations':
            if convs >= cond_val:
                unlocked = True
        elif cond_type == 'likes':
            if likes >= cond_val:
                unlocked = True
        elif cond_type == 'photo':
            if photo:
                unlocked = True
        # Другие условия можно добавить по аналогии

        if unlocked:
            cur.execute('UPDATE users SET coins = coins + ? WHERE user_id = ?', (reward, user_id))
            cur.execute('INSERT INTO user_achievements (user_id, achievement_id, unlocked_at) VALUES (?, ?, ?)',
                        (user_id, ach_id, datetime.now()))
            cur.execute('SELECT name, description FROM achievements WHERE id = ?', (ach_id,))
            name, desc = cur.fetchone()
            bot.send_message(user_id, f"🏆 Достижение разблокировано: {name}\n{desc}\n+{reward} монет!")
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
    args = message.text.split()
    referrer_id = None
    if len(args) > 1:
        ref_param = args[1]
        if ref_param.startswith('ref_'):
            try:
                referrer_id = int(ref_param.split('_')[1])
            except:
                pass
    save_user(user_id, message.from_user.username, message.from_user.first_name, referrer_id)

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
    markup.add('🏆 Топ', '🎁 Бонус', '✨ Сгенерировать описание')
    markup.add('💰 Донат', '🤝 Рефералы')
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
        bot.send_message(user_id, "❌ Профиль не найден. Напиши /start для регистрации.")
        return
    # Безопасное получение значений с подстановкой значений по умолчанию
    age = user[2] if user[2] else 'не указан'
    gender = user[3] if user[3] else 'не указан'
    search_gender = user[4] if user[4] else 'не указано'
    bio = user[5] if user[5] else 'не заполнено'
    level = user[12] if user[12] is not None else 1
    rating = user[11] if user[11] is not None else 0
    coins = user[18] if user[18] is not None else 0

    # Преобразование кодов пола в читаемый вид
    gender_str = {'male': 'Парень', 'female': 'Девушка', 'other': 'Другое'}.get(gender, str(gender))
    search_str = {'male': 'Парней', 'female': 'Девушек', 'both': 'Всех'}.get(search_gender, str(search_gender))

    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM users WHERE referrer_id = ?', (user_id,))
    referrals_count = cur.fetchone()[0]
    conn.close()

    profile_text = f"""
👤 Твоя анкета:
Возраст: {age}
Пол: {gender_str}
Ищу: {search_str}
О себе: {bio}
Уровень: {level}
Рейтинг: {rating}
Монеты: {coins}
Приглашено друзей: {referrals_count}
    """
    if user[6]:
        try:
            bot.send_photo(user_id, user[6], caption=profile_text)
        except Exception as e:
            logger.error(f"Ошибка отправки фото: {e}")
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
    end_conversation(conv['id'])
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
👥 Всего пользователей: {total}
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
    cur.execute('SELECT COUNT(*) FROM user_achievements WHERE user_id = ?', (user_id,))
    achievements_count = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM users WHERE referrer_id = ?', (user_id,))
    referrals = cur.fetchone()[0]
    conn.close()
    text = f"""
📈 Твоя статистика:
👥 Диалогов: {total_dialogs}
👍 Лайков: {likes}
👎 Дизлайков: {dislikes}
⭐ Рейтинг: {user[11] if user[11] is not None else 0}
📊 Уровень: {user[12] if user[12] is not None else 1}
🪙 Монеты: {user[18] if user[18] is not None else 0}
🏆 Достижений: {achievements_count}
👥 Приглашено: {referrals}
    """
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == '🏆 Топ' or m.text == '/top')
def cmd_top_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_rating = types.InlineKeyboardButton("По рейтингу", callback_data="top_rating")
    btn_coins = types.InlineKeyboardButton("По монетам", callback_data="top_coins")
    btn_level = types.InlineKeyboardButton("По уровню", callback_data="top_level")
    markup.add(btn_rating, btn_coins, btn_level)
    bot.send_message(message.chat.id, "Выбери категорию топа:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('top_'))
def callback_top(call):
    category = call.data.split('_')[1]
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    if category == 'rating':
        cur.execute('''
            SELECT user_id, first_name, rating, level FROM users
            WHERE is_banned = 0 AND rating > 0
            ORDER BY rating DESC, level DESC
            LIMIT 10
        ''')
    elif category == 'coins':
        cur.execute('''
            SELECT user_id, first_name, coins, level FROM users
            WHERE is_banned = 0 AND coins > 0
            ORDER BY coins DESC, level DESC
            LIMIT 10
        ''')
    elif category == 'level':
        cur.execute('''
            SELECT user_id, first_name, level, rating FROM users
            WHERE is_banned = 0
            ORDER BY level DESC, rating DESC
            LIMIT 10
        ''')
    top = cur.fetchall()
    conn.close()
    if not top:
        bot.send_message(call.message.chat.id, "В этой категории пока нет данных.")
        return
    text = f"🏆 Топ-10 по {category}:\n\n"
    for i, (uid, name, val1, val2) in enumerate(top, 1):
        name = name or "Без имени"
        if category == 'rating':
            text += f"{i}. {name} — рейтинг {val1}, уровень {val2}\n"
        elif category == 'coins':
            text += f"{i}. {name} — монет {val1}, уровень {val2}\n"
        elif category == 'level':
            text += f"{i}. {name} — уровень {val1}, рейтинг {val2}\n"
    bot.send_message(call.message.chat.id, text)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.text == '🎁 Бонус' or m.text == '/bonus')
def cmd_bonus(message):
    user_id = message.from_user.id
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT last_bonus FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    last_bonus = row[0]
    today = datetime.now().date()
    if last_bonus:
        try:
            last_date = datetime.fromisoformat(last_bonus).date()
        except:
            last_date = None
        if last_date == today:
            bot.send_message(user_id, "❌ Ты уже получал бонус сегодня. Приходи завтра!")
            conn.close()
            return
    cur.execute('UPDATE users SET coins = coins + 10, last_bonus = ? WHERE user_id = ?', (datetime.now(), user_id))
    conn.commit()
    conn.close()
    bot.send_message(user_id, "🎉 Ты получил ежедневный бонус: +10 монет!")

@bot.message_handler(func=lambda m: m.text == '✨ Сгенерировать описание' or m.text == '/generate_bio')
def cmd_generate_bio(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        return
    gender_str = {'male': 'парень', 'female': 'девушка', 'other': 'человек'}.get(user[3], 'человек')
    age = user[2] if user[2] else '?'
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add('Музыка', 'Спорт', 'Кино', 'Книги', 'Путешествия', 'Игры', 'Другое')
    msg = bot.send_message(user_id, "Выбери свои интересы (можно несколько, через запятую):", reply_markup=markup)
    bot.register_next_step_handler(msg, process_interests_for_bio, user_id, gender_str, age)

def process_interests_for_bio(message, user_id, gender_str, age):
    interests = message.text
    bio = f"Привет! Я {gender_str}, мне {age} лет. Интересуюсь: {interests}. Буду рад(а) пообщаться!"
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET bio = ? WHERE user_id = ?', (bio, user_id))
    conn.commit()
    conn.close()
    bot.send_message(user_id, f"✅ Описание сгенерировано и сохранено:\n{bio}", reply_markup=types.ReplyKeyboardRemove())
    show_main_menu(user_id)

# ===== ДОНАТЫ (Telegram Stars) =====
@bot.message_handler(func=lambda m: m.text == '💰 Донат' or m.text == '/donate')
def cmd_donate(message):
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = []
    for amount in DONATION_AMOUNTS:
        buttons.append(types.InlineKeyboardButton(f"{amount} ⭐️", callback_data=f"donate_{amount}"))
    markup.add(*buttons)
    bot.send_message(
        message.chat.id,
        "💰 Выбери сумму доната в Telegram Stars:\n\n"
        "Звёзды можно потратить внутри Telegram или вывести на свой кошелёк.\n"
        f"Доступные суммы: {', '.join(map(str, DONATION_AMOUNTS))} ⭐️",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('donate_'))
def process_donate(call):
    amount = int(call.data.split('_')[1])
    prices = [types.LabeledPrice(label="XTR", amount=amount)]
    bot.send_invoice(
        call.message.chat.id,
        title='Поддержка бота',
        description=f'Донат {amount} Telegram Stars',
        invoice_payload='donation_payload',
        provider_token='',  # Для Stars оставляем пустым!
        currency='XTR',
        prices=prices,
        start_parameter='donate',
        need_name=False,
        need_phone_number=False,
        need_email=False,
        need_shipping_address=False,
        is_flexible=False
    )
    bot.answer_callback_query(call.id)

@bot.pre_checkout_query_handler(func=lambda query: True)
def pre_checkout_query(query):
    bot.answer_pre_checkout_query(query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def successful_payment(message):
    user_id = message.from_user.id
    total_amount = message.successful_payment.total_amount
    bonus_coins = total_amount * 2  # например, 2 монеты за 1 звезду
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET coins = coins + ? WHERE user_id = ?', (bonus_coins, user_id))
    conn.commit()
    conn.close()
    bot.send_message(user_id, f"✅ Спасибо за поддержку! Тебе начислено {bonus_coins} монет.")
    check_achievements(user_id)

# ===== РЕФЕРАЛЫ =====
@bot.message_handler(func=lambda m: m.text == '🤝 Рефералы' or m.text == '/referral')
def cmd_referral(message):
    user_id = message.from_user.id
    conn = sqlite3.connect('dating_bot.db')
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM users WHERE referrer_id = ?', (user_id,))
    referrals_count = cur.fetchone()[0]
    cur.execute('SELECT SUM(reward_coins) FROM referral_rewards WHERE referrer_id = ?', (user_id,))
    earned = cur.fetchone()[0] or 0
    conn.close()
    bot_username = bot.get_me().username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    text = f"""
🤝 **Реферальная программа**

Приглашай друзей и получай бонусы!
За каждого друга, зарегистрировавшегося по твоей ссылке, ты получишь **{REFERRAL_BONUS} монет**, а друг — **{REFERRAL_BONUS_FOR_NEW} монет** сразу после регистрации.

📊 Твоя статистика:
• Приглашено друзей: {referrals_count}
• Заработано монет: {earned}

🔗 Твоя реферальная ссылка:
`{ref_link}`
    """
    bot.send_message(user_id, text, parse_mode='Markdown')

# ===== ОБРАБОТКА РЕЙТИНГА И ЖАЛОБ =====
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
    cur.execute('SELECT user1_id, user2_id FROM conversations WHERE id = ?', (conv_id,))
    u1, u2 = cur.fetchone()
    if user_id == u1:
        already_rated = rated1
    elif user_id == u2:
        already_rated = rated2
    else:
        already_rated = True

    if not already_rated:
        cur.execute('''
            INSERT INTO ratings (from_user, to_user, value, timestamp, conversation_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, partner_id, value, datetime.now(), conv_id))
        if value == 1:
            cur.execute('UPDATE users SET rating = rating + 1, total_likes = total_likes + 1 WHERE user_id = ?', (partner_id,))
        else:
            cur.execute('UPDATE users SET rating = rating - 1, total_dislikes = total_dislikes + 1 WHERE user_id = ?', (partner_id,))
        if user_id == u1:
            cur.execute('UPDATE conversations SET rated_by_user1 = 1 WHERE id = ?', (conv_id,))
        else:
            cur.execute('UPDATE conversations SET rated_by_user2 = 1 WHERE id = ?', (conv_id,))
        conn.commit()
        update_user_level(partner_id)
        check_achievements(partner_id)
        bot.answer_callback_query(call.id, "Спасибо за оценку!")
        bot.edit_message_text("Оценка учтена. Спасибо!", call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, "Вы уже оценили этого собеседника.")
    conn.close()

@bot.callback_query_handler(func=lambda call: call.data.startswith('report_'))
def callback_report_start(call):
    _, conv_id, reported_id = call.data.split('_')
    conv_id = int(conv_id)
    reported_id = int(reported_id)
    reporter_id = call.from_user.id
    user_data[reporter_id] = {
        'step': 'report_reason',
        'conv_id': conv_id,
        'reported_id': reported_id
    }
    bot.send_message(reporter_id, "Опиши причину жалобы (одним сообщением):")
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.from_user.id in user_data and user_data[m.from_user.id].get('step') == 'report_reason')
def process_report_reason(message):
    user_id = message.from_user.id
    reason = message.text[:500]
    data = user_data.pop(user_id)
    conv_id = data['conv_id']
    reported_id = data['reported_id']

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
    ''', (user_id, reported_id, reason, datetime.now(), conv_id, log_text))
    conn.commit()
    report_id = cur.lastrowid
    conn.close()

    admin_msg = f"""
🚨 **Новая жалоба #{report_id}**
От: {user_id}
На: {reported_id}
Причина: {reason}

**Последние сообщения:**
{log_text}

Чтобы забанить, ответь на это сообщение командой: `/ban {reported_id} [причина]`
    """
    bot.send_message(ADMIN_ID, admin_msg, parse_mode='Markdown')
    bot.send_message(user_id, "✅ Жалоба отправлена администратору. Спасибо!")
    check_achievements(user_id)

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
    cur.execute("SELECT SUM(coins) FROM users")
    total_coins = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM referral_rewards")
    total_referrals = cur.fetchone()[0] or 0
    conn.close()
    text = f"""
📈 **Детальная статистика**
👥 Всего пользователей: {total}
🚫 Забанено: {banned}
💬 Активных диалогов: {active}
🕒 В очереди: {waiting}
📅 Активных сегодня: {active_today}
🆕 Новых чатов сегодня: {new_chats_today}
🪙 Всего монет: {total_coins}
👥 Всего рефералов: {total_referrals}
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
    admin_text = "📢 Сообщение от администратора:\n\n" + text
    bot.reply_to(message, "⏳ Начинаю рассылку...")
    success, fail = broadcast_message(admin_text)
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
    print("🤖 Анонимный чат-бот с донатами через Stars")
    print("=" * 50)
    print(f"👑 Админ ID: {ADMIN_ID}")
    print("🟢 Запуск...")

    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling()