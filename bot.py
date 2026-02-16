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
DONATION_AMOUNTS = [5, 10, 20, 50, 100, 200]  # суммы для донатов

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

# ===== БАЗА ДАННЫХ С ИМЕНОВАННЫМИ ПОЛЯМИ =====
def get_db_connection():
    conn = sqlite3.connect('dating_bot.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # Таблица пользователей
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
    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_achievements (
            user_id INTEGER,
            achievement_id INTEGER,
            unlocked_at TIMESTAMP,
            PRIMARY KEY (user_id, achievement_id)
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS referral_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            reward_coins INTEGER,
            rewarded_at TIMESTAMP
        )
    ''')
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
    cur.execute('''
        CREATE TABLE IF NOT EXISTS waiting_queue (
            user_id INTEGER PRIMARY KEY,
            joined_time TIMESTAMP,
            search_gender TEXT
        )
    ''')
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
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cur.fetchone()
    conn.close()
    return dict(user) if user else None

def save_user(user_id, username, first_name, referrer_id=None):
    conn = get_db_connection()
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
    conn = get_db_connection()
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
    return (user['age'] and user['age'] != 0 and
            user['gender'] and user['gender'] != 'не указан' and
            user['search_gender'] is not None and user['search_gender'] != '' and
            user['bio'] is not None and user['bio'] != '')

def get_active_conversation(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT id, user1_id, user2_id FROM conversations
        WHERE (user1_id = ? OR user2_id = ?) AND is_active = 1
    ''', (user_id, user_id))
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_partner_id(user_id, conv):
    return conv['user2_id'] if conv['user1_id'] == user_id else conv['user1_id']

def end_conversation(conv_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT user1_id, user2_id, rated_by_user1, rated_by_user2 FROM conversations WHERE id = ?', (conv_id,))
    row = cur.fetchone()
    if row:
        conv = dict(row)
        user1, user2, rated1, rated2 = conv['user1_id'], conv['user2_id'], conv['rated_by_user1'], conv['rated_by_user2']
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
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT OR REPLACE INTO waiting_queue (user_id, joined_time, search_gender)
        VALUES (?, ?, ?)
    ''', (user_id, datetime.now(), search_gender))
    conn.commit()
    conn.close()

def remove_from_waiting(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM waiting_queue WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def find_partner(user_id):
    user = get_user(user_id)
    if not user:
        return None, None
    search_for = user['search_gender']
    my_gender = user['gender']
    conn = get_db_connection()
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
        partner_id = row['user_id']
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
    conn = get_db_connection()
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
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('UPDATE users SET is_banned = 0, ban_reason = NULL WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT user_id, username, first_name, age, gender, is_banned, last_active FROM users ORDER BY last_active DESC')
    users = [dict(row) for row in cur.fetchall()]
    conn.close()
    return users

def get_stats():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM users')
    total = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM users WHERE is_banned = 1')
    banned = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM conversations WHERE is_active = 1')
    active = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM waiting_queue')
    waiting = cur.fetchone()[0]
    conn.close()
    return total, banned, active, waiting

def broadcast_message(text):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT user_id FROM users WHERE is_banned = 0')
    users = cur.fetchall()
    conn.close()
    success = 0
    fail = 0
    for user in users:
        try:
            bot.send_message(user['user_id'], text)
            success += 1
            time.sleep(0.05)
        except:
            fail += 1
    return success, fail

def update_user_level(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT total_conversations, total_likes FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    if row:
        convs, likes = row['total_conversations'], row['total_likes']
        level = 1 + int(math.sqrt(convs + likes // 2))
        cur.execute('UPDATE users SET level = ? WHERE user_id = ?', (level, user_id))
        conn.commit()
    conn.close()

def check_achievements(user_id):
    conn = get_db_connection()
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
    convs, likes, _, _, photo = user_data['total_conversations'], user_data['total_likes'], user_data['coins'], user_data['last_bonus'], user_data['photo_file_id']
    for ach in achievements:
        ach_id, cond_type, cond_val, reward = ach['id'], ach['condition_type'], ach['condition_value'], ach['reward_coins']
        cur.execute('SELECT 1 FROM user_achievements WHERE user_id = ? AND achievement_id = ?', (user_id, ach_id))
        if cur.fetchone():
            continue
        unlocked = False
        if cond_type == 'conversations' and convs >= cond_val:
            unlocked = True
        elif cond_type == 'likes' and likes >= cond_val:
            unlocked = True
        elif cond_type == 'photo' and photo:
            unlocked = True
        if unlocked:
            cur.execute('UPDATE users SET coins = coins + ? WHERE user_id = ?', (reward, user_id))
            cur.execute('INSERT INTO user_achievements (user_id, achievement_id, unlocked_at) VALUES (?, ?, ?)',
                        (user_id, ach_id, datetime.now()))
            cur.execute('SELECT name, description FROM achievements WHERE id = ?', (ach_id,))
            name_row = cur.fetchone()
            bot.send_message(user_id, f"🏆 Достижение разблокировано: {name_row['name']}\n{name_row['description']}\n+{reward} монет!")
    conn.commit()
    conn.close()
# ===== ДОНАТЫ =====
@bot.message_handler(func=lambda m: m.text == '💰 Донат' or m.text == '/donate')
def cmd_donate(message):
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = [types.InlineKeyboardButton(f"{amt} ⭐️", callback_data=f"donate_{amt}") for amt in DONATION_AMOUNTS]
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
    bot.send_invoice(
        call.message.chat.id,
        title='Поддержка бота',
        description=f'Донат {amount} Telegram Stars',
        invoice_payload='donation_payload',
        provider_token='',
        currency='XTR',
        prices=[types.LabeledPrice(label="XTR", amount=amount)],
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
    bonus_coins = total_amount * 2
    conn = get_db_connection()
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
    conn = get_db_connection()
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
    conv_id, partner_id, value = int(conv_id), int(partner_id), int(value)
    user_id = call.from_user.id
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT rated_by_user1, rated_by_user2, user1_id, user2_id FROM conversations WHERE id = ?', (conv_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return
    rated1, rated2, u1, u2 = row['rated_by_user1'], row['rated_by_user2'], row['user1_id'], row['user2_id']
    if user_id == u1:
        already_rated = rated1
    elif user_id == u2:
        already_rated = rated2
    else:
        already_rated = True
    if not already_rated:
        cur.execute('INSERT INTO ratings (from_user, to_user, value, timestamp, conversation_id) VALUES (?, ?, ?, ?, ?)',
                    (user_id, partner_id, value, datetime.now(), conv_id))
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
    conv_id, reported_id = int(conv_id), int(reported_id)
    reporter_id = call.from_user.id
    user_data[reporter_id] = {'step': 'report_reason', 'conv_id': conv_id, 'reported_id': reported_id}
    bot.send_message(reporter_id, "Опиши причину жалобы (одним сообщением):")
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: m.from_user.id in user_data and user_data[m.from_user.id].get('step') == 'report_reason')
def process_report_reason(message):
    user_id = message.from_user.id
    reason = message.text[:500]
    data = user_data.pop(user_id)
    conv_id, reported_id = data['conv_id'], data['reported_id']
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        SELECT user_id, message FROM message_logs
        WHERE conversation_id = ?
        ORDER BY timestamp DESC LIMIT 10
    ''', (conv_id,))
    logs = cur.fetchall()
    log_text = "\n".join([f"{'Собеседник' if row['user_id'] == reported_id else 'Вы'}: {row['message']}" for row in reversed(logs)])
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

# ===== НОВАЯ КОМАНДА: ОТПРАВКА СООБЩЕНИЯ КОНКРЕТНОМУ ПОЛЬЗОВАТЕЛЮ =====
@bot.message_handler(commands=['sendto'])
@admin_only
def cmd_sendto(message):
    # Формат: /sendto user_id текст
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(message, "❌ Использование: /sendto [user_id] [текст]")
        return
    try:
        target_id = int(parts[1])
        text = parts[2]
    except ValueError:
        bot.reply_to(message, "❌ ID должен быть числом.")
        return

    # Проверяем, существует ли пользователь (необязательно)
    user = get_user(target_id)
    if not user:
        bot.reply_to(message, f"❌ Пользователь с ID {target_id} не найден в базе.")
        return

    try:
        bot.send_message(target_id, f"📨 Сообщение от администратора:\n\n{text}")
        bot.reply_to(message, f"✅ Сообщение отправлено пользователю {target_id}.")
    except Exception as e:
        bot.reply_to(message, f"❌ Не удалось отправить сообщение: {e}")

# ===== АДМИН-ПАНЕЛЬ (обновлена с упоминанием sendto) =====
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
/sendto [id] [текст] - Личное сообщение пользователю
    """
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

# ===== ОСТАЛЬНЫЕ АДМИН-КОМАНДЫ (без изменений) =====
@bot.message_handler(commands=['admin_stats'])
@admin_only
def admin_stats(message):
    total, banned, active, waiting = get_stats()
    conn = get_db_connection()
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
        status = "🔴 Забанен" if user['is_banned'] else "🟢 Активен"
        name = user['username'] or user['first_name'] or "Без имени"
        line = f"ID: {user['user_id']} | {name} | Возраст: {user['age']} | Пол: {user['gender']} | {status} | Последний вход: {user['last_active'][:16]}\n"
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
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT gender, COUNT(*) FROM users WHERE is_banned = 0 GROUP BY gender")
    gender_stats = cur.fetchall()
    cur.execute("SELECT AVG(age) FROM users WHERE is_banned = 0 AND age IS NOT NULL")
    avg_age = cur.fetchone()[0] or 0
    cur.execute("SELECT age, COUNT(*) FROM users WHERE is_banned = 0 AND age IS NOT NULL GROUP BY age ORDER BY age")
    age_dist = cur.fetchall()
    conn.close()
    text = "📊 **Демография**\n\n"
    for row in gender_stats:
        gender_name = {'male': 'Парни', 'female': 'Девушки', 'other': 'Другое'}.get(row['gender'], row['gender'])
        text += f"{gender_name}: {row['COUNT(*)']}\n"
    text += f"\nСредний возраст: {avg_age:.1f}\n\nРаспределение по возрастам:\n"
    for row in age_dist[:20]:
        text += f"{row['age']} лет: {row['COUNT(*)']}\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(commands=['reports'])
@admin_only
def list_reports(message):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, reporter_id, reported_id, reason, timestamp FROM reports WHERE status = 'new' ORDER BY timestamp DESC LIMIT 10")
    reports = cur.fetchall()
    conn.close()
    if not reports:
        bot.send_message(message.chat.id, "Новых жалоб нет.")
        return
    text = "📋 **Новые жалобы:**\n\n"
    for r in reports:
        text += f"#{r['id']} от {r['timestamp'][:16]}: {r['reporter_id']} -> {r['reported_id']}\nПричина: {r['reason'][:50]}...\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.message_handler(commands=['report'])
@admin_only
def view_report(message):
    try:
        report_id = int(message.text.split()[1])
    except:
        bot.reply_to(message, "❌ Использование: /report [id]")
        return
    conn = get_db_connection()
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
    text = f"""
📋 **Жалоба #{report_id}**
Статус: {row['status']}
От: {row['reporter_id']}
На: {row['reported_id']}
Время: {row['timestamp']}
Причина: {row['reason']}

**Последние сообщения:**
{row['last_messages']}

Чтобы забанить нарушителя, ответь на это сообщение командой /ban {row['reported_id']} [причина]
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
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('INSERT INTO message_logs (conversation_id, user_id, message, timestamp) VALUES (?, ?, ?, ?)',
                    (conv['id'], user_id, msg_text, datetime.now()))
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
    print("🤖 Анонимный чат-бот с командой /sendto")
    print("=" * 50)
    print(f"👑 Админ ID: {ADMIN_ID}")
    print("🟢 Запуск...")
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling()    