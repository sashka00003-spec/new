import os
import asyncio
import sqlite3
import json
import logging
from datetime import datetime
from aiohttp import web
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = "8812317225:AAE-cOCndbJkbRysfm-Ed8iLGMk_APZ18Jg"
ADMIN_ID = 2064971302
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://new-qudb.onrender.com/")

DB_PATH = "store.db"

# ---------- БАЗА ДАННЫХ ----------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("store_name", "Мой магазин"))
        cur.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("delivery_fee", "5"))
        cur.execute("CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                name TEXT,
                price INTEGER,
                old_price INTEGER,
                description TEXT,
                photo_file_id TEXT,
                sizes TEXT,
                FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_telegram_id INTEGER,
                fio TEXT,
                phone TEXT,
                email TEXT,
                delivery_method TEXT,
                post_office TEXT,
                payment_method TEXT,
                total INTEGER,
                items_json TEXT,
                status TEXT DEFAULT 'новый',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

init_db()

def get_config(key, default=""):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM config WHERE key=?", (key,))
        row = cur.fetchone()
        return row[0] if row else default

def set_config(key, value):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))

def get_categories():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM categories ORDER BY id")
        return [dict(row) for row in cur.fetchall()]

def add_category(name):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        return True
    except:
        return False

def delete_category(cat_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))

def get_products_by_category(cat_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, name, price, old_price, photo_file_id, sizes FROM products WHERE category_id=?", (cat_id,))
        return [dict(row) for row in cur.fetchall()]

def get_product(product_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM products WHERE id=?", (product_id,))
        row = cur.fetchone()
        return dict(row) if row else None

def add_product(cat_id, name, price, old_price, desc, photo_id, sizes):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO products (category_id, name, price, old_price, description, photo_file_id, sizes)
            VALUES (?,?,?,?,?,?,?)
        """, (cat_id, name, price, old_price or None, desc, photo_id, sizes))

def update_product(product_id, **kwargs):
    allowed = ["name", "price", "old_price", "description", "photo_file_id", "sizes"]
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return
    set_clause = ", ".join([f"{k}=?" for k in updates])
    values = list(updates.values()) + [product_id]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(f"UPDATE products SET {set_clause} WHERE id=?", values)

def delete_product(product_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM products WHERE id=?", (product_id,))

def create_order(telegram_id, fio, phone, email, delivery, post_office, payment, total, items):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO orders (user_telegram_id, fio, phone, email, delivery_method, post_office, payment_method, total, items_json)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (telegram_id, fio, phone, email, delivery, post_office, payment, total, json.dumps(items)))
        return cur.lastrowid

def get_all_orders():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders ORDER BY created_at DESC")
        return [dict(row) for row in cur.fetchall()]

def update_order_status(order_id, status):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))

# ---------- КЛАВИАТУРЫ ----------
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🛍 Открыть магазин")],
        [KeyboardButton("📦 Мои заказы"), KeyboardButton("ℹ️ О магазине")],
        [KeyboardButton("🔧 Админ панель")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ---------- ОБРАБОТЧИКИ БОТА ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store_name = get_config("store_name")
    await update.message.reply_text(
        f"👋 Добро пожаловать в {store_name}!\nИспользуйте кнопки ниже.",
        reply_markup=get_main_keyboard()
    )

async def main_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🛍 Открыть магазин":
        url = WEBHOOK_URL
        if not url:
            await update.message.reply_text("🌐 Ссылка на магазин не настроена.")
            return
        await update.message.reply_text(
            "Нажмите кнопку ниже, чтобы открыть магазин:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Открыть магазин", web_app=WebAppInfo(url=url))]
            ])
        )
    elif text == "📦 Мои заказы":
        uid = update.effective_user.id
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM orders WHERE user_telegram_id=? ORDER BY created_at DESC", (uid,))
            rows = cur.fetchall()
        if not rows:
            await update.message.reply_text("У вас пока нет заказов.")
            return
        msg = "📋 Ваши заказы:\n\n"
        for row in rows:
            msg += f"#{row['id']} — {row['created_at'][:10]} — {row['total']} BYN — {row['status']}\n"
        await update.message.reply_text(msg)
    elif text == "ℹ️ О магазине":
        inst = get_config("instagram", "")
        manager = get_config("manager_link", "")
        delivery_fee = get_config("delivery_fee")
        info = f"🏪 {get_config('store_name')}\nДоставка: {delivery_fee} BYN\n"
        if inst: info += f"📸 Instagram: {inst}\n"
        if manager: info += f"👤 Менеджер: {manager}\n"
        await update.message.reply_text(info)
    elif text == "🔧 Админ панель":
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Нет прав.")
            return
        await admin_panel(update, context)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Категории", callback_data="admin_cats")],
        [InlineKeyboardButton("👟 Товары", callback_data="admin_products")],
        [InlineKeyboardButton("📦 Заказы", callback_data="admin_orders_list")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")],
    ])
    await update.message.reply_text("🔧 Админ панель:", reply_markup=kb)

# ---------- Управление категориями ----------
async def admin_categories(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None):
    cats = get_categories()
    text = "📂 Категории:\n" + "\n".join([f"{c['id']}. {c['name']}" for c in cats]) if cats else "Нет категорий."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить", callback_data="add_category")],
        [InlineKeyboardButton("✏️ Переименовать", callback_data="rename_category")],
        [InlineKeyboardButton("❌ Удалить", callback_data="del_category")],
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]
    ])
    if query:
        await query.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)

async def add_category_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['admin_action'] = 'add_category'
    await update.callback_query.message.reply_text("Введите название новой категории:")
    return

async def rename_category_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cats = get_categories()
    if not cats:
        await update.callback_query.message.reply_text("Нет категорий для переименования.")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(c['name'], callback_data=f"rename_cat_{c['id']}")] for c in cats
    ])
    await update.callback_query.message.reply_text("Выберите категорию:", reply_markup=kb)
    return

async def rename_category_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[-1])
    context.user_data['rename_cat_id'] = cat_id
    context.user_data['admin_action'] = 'rename_category'
    await query.message.reply_text("Введите новое название категории:")
    return

async def delete_category_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cats = get_categories()
    if not cats:
        await update.callback_query.message.reply_text("Нет категорий для удаления.")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(c['name'], callback_data=f"del_cat_{c['id']}")] for c in cats
    ])
    await update.callback_query.message.reply_text("Выберите категорию для удаления:", reply_markup=kb)
    return

async def delete_category_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[-1])
    delete_category(cat_id)
    await query.message.reply_text("✅ Категория удалена.")
    await admin_categories(update, context)

async def handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    action = context.user_data.get('admin_action')
    if action == 'add_category':
        if add_category(text):
            await update.message.reply_text(f"✅ Категория «{text}» добавлена.")
        else:
            await update.message.reply_text("❌ Такая категория уже существует.")
        context.user_data.pop('admin_action', None)
        await admin_categories(update, context)
    elif action == 'rename_category':
        cat_id = context.user_data.get('rename_cat_id')
        if cat_id:
            with sqlite3.connect(DB_PATH) as conn:
                try:
                    conn.execute("UPDATE categories SET name=? WHERE id=?", (text, cat_id))
                    conn.commit()
                    await update.message.reply_text(f"✅ Категория переименована в «{text}».")
                except:
                    await update.message.reply_text("❌ Ошибка или такое имя уже существует.")
        context.user_data.pop('admin_action', None)
        context.user_data.pop('rename_cat_id', None)
        await admin_categories(update, context)
    else:
        await update.message.reply_text("Неизвестная команда.")

# ---------- Управление товарами ----------
async def admin_products(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None):
    cats = get_categories()
    if not cats:
        text = "Нет категорий. Сначала добавьте категорию."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]])
        if query:
            await query.edit_message_text(text, reply_markup=kb)
        else:
            await update.message.reply_text(text, reply_markup=kb)
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(c['name'], callback_data=f"show_products_{c['id']}")] for c in cats
    ] + [[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]])
    if query:
        await query.edit_message_text("Выберите категорию для управления товарами:", reply_markup=kb)
    else:
        await update.message.reply_text("Выберите категорию:", reply_markup=kb)

async def show_products_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[-1])
    context.user_data['current_cat_id'] = cat_id
    prods = get_products_by_category(cat_id)
    if not prods:
        text = "В этой категории нет товаров."
    else:
        text = "Товары в категории:\n"
        for p in prods:
            text += f"📦 {p['name']} — {p['price']} BYN\n"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить товар", callback_data="add_product")],
        [InlineKeyboardButton("✏️ Редактировать товар", callback_data="edit_product")],
        [InlineKeyboardButton("❌ Удалить товар", callback_data="del_product")],
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_products")]
    ])
    await query.edit_message_text(text, reply_markup=kb)

async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['admin_action'] = 'add_product'
    context.user_data['prod_step'] = 1
    await update.callback_query.message.reply_text("Введите название товара:")
    return

async def edit_product_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cat_id = context.user_data.get('current_cat_id')
    if not cat_id:
        await update.callback_query.message.reply_text("Ошибка, выберите категорию сначала.")
        return
    prods = get_products_by_category(cat_id)
    if not prods:
        await update.callback_query.message.reply_text("Нет товаров для редактирования.")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(p['name'], callback_data=f"edit_prod_{p['id']}")] for p in prods
    ] + [[InlineKeyboardButton("◀️ Назад", callback_data=f"show_products_{cat_id}")]])
    await update.callback_query.message.reply_text("Выберите товар для редактирования:", reply_markup=kb)
    return

async def edit_product_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    prod_id = int(query.data.split("_")[-1])
    context.user_data['edit_prod_id'] = prod_id
    context.user_data['admin_action'] = 'edit_product'
    context.user_data['edit_step'] = 1
    await query.message.reply_text("Что хотите изменить? Отправьте:\n1 - название\n2 - цену\n3 - старую цену\n4 - описание\n5 - размеры\n0 - отмена")
    return

async def delete_product_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cat_id = context.user_data.get('current_cat_id')
    if not cat_id:
        await update.callback_query.message.reply_text("Ошибка.")
        return
    prods = get_products_by_category(cat_id)
    if not prods:
        await update.callback_query.message.reply_text("Нет товаров для удаления.")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(p['name'], callback_data=f"del_prod_{p['id']}")] for p in prods
    ] + [[InlineKeyboardButton("◀️ Назад", callback_data=f"show_products_{cat_id}")]])
    await update.callback_query.message.reply_text("Выберите товар для удаления:", reply_markup=kb)
    return

async def delete_product_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    prod_id = int(query.data.split("_")[-1])
    delete_product(prod_id)
    await query.message.reply_text("✅ Товар удалён.")
    cat_id = context.user_data.get('current_cat_id')
    if cat_id:
        await show_products_list(update, context)

async def handle_product_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    action = context.user_data.get('admin_action')
    if action == 'add_product':
        step = context.user_data.get('prod_step', 1)
        if step == 1:
            context.user_data['prod_name'] = text
            context.user_data['prod_step'] = 2
            await update.message.reply_text("Введите цену (число):")
        elif step == 2:
            if not text.isdigit():
                await update.message.reply_text("Цена должна быть числом. Попробуйте снова:")
                return
            context.user_data['prod_price'] = int(text)
            context.user_data['prod_step'] = 3
            await update.message.reply_text("Введите старую цену (0 если нет):")
        elif step == 3:
            old = int(text) if text.isdigit() else 0
            context.user_data['prod_old'] = old if old > 0 else None
            context.user_data['prod_step'] = 4
            await update.message.reply_text("Введите описание товара:")
        elif step == 4:
            context.user_data['prod_desc'] = text
            context.user_data['prod_step'] = 5
            await update.message.reply_text("Отправьте фото товара:")
        elif step == 5:
            if not update.message.photo:
                await update.message.reply_text("Пожалуйста, отправьте фото.")
                return
            photo_id = update.message.photo[-1].file_id
            context.user_data['prod_photo'] = photo_id
            context.user_data['prod_step'] = 6
            await update.message.reply_text("Введите размеры через запятую (или '-' если нет):")
        elif step == 6:
            sizes = text if text != "-" else ""
            add_product(
                cat_id=context.user_data['current_cat_id'],
                name=context.user_data['prod_name'],
                price=context.user_data['prod_price'],
                old_price=context.user_data['prod_old'],
                desc=context.user_data['prod_desc'],
                photo_id=context.user_data['prod_photo'],
                sizes=sizes
            )
            await update.message.reply_text("✅ Товар добавлен!")
            context.user_data.pop('admin_action', None)
            context.user_data.pop('prod_step', None)
            await show_products_list(update, context)
    elif action == 'edit_product':
        step = context.user_data.get('edit_step', 1)
        prod_id = context.user_data.get('edit_prod_id')
        if step == 1:
            choice = text
            if choice == '1':
                context.user_data['edit_field'] = 'name'
                await update.message.reply_text("Введите новое название:")
                context.user_data['edit_step'] = 2
            elif choice == '2':
                context.user_data['edit_field'] = 'price'
                await update.message.reply_text("Введите новую цену (число):")
                context.user_data['edit_step'] = 2
            elif choice == '3':
                context.user_data['edit_field'] = 'old_price'
                await update.message.reply_text("Введите новую старую цену (число или 0):")
                context.user_data['edit_step'] = 2
            elif choice == '4':
                context.user_data['edit_field'] = 'description'
                await update.message.reply_text("Введите новое описание:")
                context.user_data['edit_step'] = 2
            elif choice == '5':
                context.user_data['edit_field'] = 'sizes'
                await update.message.reply_text("Введите размеры через запятую (или '-'):")
                context.user_data['edit_step'] = 2
            elif choice == '0':
                context.user_data.pop('admin_action', None)
                context.user_data.pop('edit_step', None)
                await update.message.reply_text("Отменено.")
                await show_products_list(update, context)
            else:
                await update.message.reply_text("Неверный выбор. Попробуйте снова.")
        elif step == 2:
            field = context.user_data['edit_field']
            value = text
            if field == 'price' or field == 'old_price':
                if not value.isdigit():
                    await update.message.reply_text("Нужно число. Попробуйте снова:")
                    return
                value = int(value)
                if field == 'old_price' and value == 0:
                    value = None
            elif field == 'sizes' and value == '-':
                value = ''
            update_product(prod_id, **{field: value})
            await update.message.reply_text("✅ Поле обновлено.")
            context.user_data.pop('admin_action', None)
            context.user_data.pop('edit_step', None)
            await show_products_list(update, context)

# ---------- Заказы для админа ----------
async def admin_orders_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = get_all_orders()
    if not orders:
        await update.callback_query.answer("Заказов нет.", show_alert=True)
        return
    for o in orders[:5]:  # показываем последние 5, чтобы не заспамить
        text = f"🆔 Заказ #{o['id']}\n{o['fio']}\n{o['phone']}\n{o['email']}\nДоставка: {o['delivery_method']}, {o['post_office']}\nСтатус: {o['status']}\nТовары: {o['items_json']}\nИтого: {o['total']} BYN"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Принять", callback_data=f"order_status_{o['id']}_принят")],
            [InlineKeyboardButton("Отправить", callback_data=f"order_status_{o['id']}_отправлен")],
            [InlineKeyboardButton("Завершить", callback_data=f"order_status_{o['id']}_завершён")]
        ])
        await update.callback_query.message.reply_text(text, reply_markup=kb)
        await asyncio.sleep(0.3)
    await update.callback_query.answer()

async def change_order_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, order_id, new_status = query.data.split("_")
    update_order_status(int(order_id), new_status)
    await query.message.reply_text(f"✅ Статус заказа #{order_id} изменён на {new_status}")

# ---------- Настройки магазина ----------
async def admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏪 Название магазина", callback_data="set_storename")],
        [InlineKeyboardButton("💰 Стоимость доставки", callback_data="set_delivery")],
        [InlineKeyboardButton("📸 Instagram", callback_data="set_instagram")],
        [InlineKeyboardButton("👤 Менеджер", callback_data="set_manager")],
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]
    ])
    await update.callback_query.edit_message_text("⚙️ Настройки магазина:", reply_markup=kb)

async def set_config_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data
    context.user_data['config_key'] = key
    await query.message.reply_text("Введите новое значение:")
    # следующий шаг обрабатывается в handle_admin_text_input
    context.user_data['admin_action'] = 'set_config'

async def handle_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get('config_key')
    value = update.message.text.strip()
    if key:
        if key == "set_storename":
            set_config("store_name", value)
            await update.message.reply_text(f"✅ Название магазина изменено на {value}")
        elif key == "set_delivery":
            if value.isdigit():
                set_config("delivery_fee", value)
                await update.message.reply_text(f"✅ Стоимость доставки установлена {value} BYN")
            else:
                await update.message.reply_text("❌ Введите число.")
        elif key == "set_instagram":
            set_config("instagram", value)
            await update.message.reply_text(f"✅ Instagram обновлён: {value}")
        elif key == "set_manager":
            set_config("manager_link", value)
            await update.message.reply_text(f"✅ Ссылка на менеджера обновлена")
    context.user_data.pop('config_key', None)
    context.user_data.pop('admin_action', None)

# ---------- Веб-интерфейс (HTML) ----------
HTML_PAGE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>Магазин</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family: system-ui; background: var(--tg-theme-bg-color, #fff); color: var(--tg-theme-text-color, #000); padding-bottom: 70px; }
        .header { display: flex; justify-content: space-between; padding: 15px; background: var(--tg-theme-secondary-bg-color, #f0f0f0); position: sticky; top:0; }
        .cart-icon { position: relative; cursor: pointer; font-size: 28px; }
        .cart-count { position: absolute; top:-5px; right:-10px; background:red; color:white; border-radius:50%; padding:2px 6px; font-size:12px; }
        .categories { display: flex; gap: 10px; overflow-x: auto; padding: 10px; background: var(--tg-theme-bg-color); border-bottom:1px solid #ddd; }
        .category-btn { padding: 8px 16px; border: none; border-radius: 20px; background: var(--tg-theme-button-color, #3390ec); color: white; white-space: nowrap; cursor: pointer; }
        .products { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px,1fr)); gap: 15px; padding: 15px; }
        .product-card { border:1px solid #ddd; border-radius:12px; padding:10px; text-align:center; background: var(--tg-theme-secondary-bg-color, #f9f9f9); }
        .product-price { font-weight: bold; margin:8px 0; }
        .old-price { text-decoration: line-through; color: gray; font-size:0.8em; margin-right:8px; }
        .size-select { display: flex; flex-wrap: wrap; gap:5px; justify-content: center; margin:8px 0; }
        .size-btn { padding:4px 8px; border:1px solid #ccc; border-radius:16px; background:#fff; cursor: pointer; }
        .size-btn.selected { background:#3390ec; color:white; }
        .add-to-cart { background: var(--tg-theme-button-color, #3390ec); color:white; border:none; padding:8px; border-radius:20px; width:100%; cursor: pointer; margin-top:8px; }
        .modal { display: none; position: fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); z-index:1000; overflow-y:auto; }
        .modal-content { background: var(--tg-theme-bg-color); margin:50px auto; width:90%; max-width:400px; border-radius:20px; padding:20px; position:relative; }
        .close { position: absolute; right:20px; top:10px; font-size:28px; cursor:pointer; }
        .cart-item { display: flex; justify-content: space-between; margin-bottom:10px; padding:5px; border-bottom:1px solid #eee; }
        .form-group { margin-bottom:12px; }
        input, select { width:100%; padding:10px; border-radius:8px; border:1px solid #ccc; background: var(--tg-theme-bg-color); color: var(--tg-theme-text-color); }
        .btn-primary { background: var(--tg-theme-button-color, #3390ec); color:white; border:none; padding:12px; border-radius:30px; width:100%; font-size:16px; margin-top:10px; cursor:pointer; }
        .loading { text-align:center; padding:40px; }
    </style>
</head>
<body>
<div class="header">
    <h1 id="store-name">Загрузка...</h1>
    <div class="cart-icon" id="cart-btn">🛒<span id="cart-count" class="cart-count">0</span></div>
</div>
<div class="categories" id="categories-list"></div>
<div class="products" id="products-list"><div class="loading">Загрузка...</div></div>

<div id="cart-modal" class="modal">
    <div class="modal-content">
        <span class="close" data-modal="cart-modal">&times;</span>
        <h2>🛒 Корзина</h2>
        <div id="cart-items"></div>
        <div id="cart-total" style="font-weight:bold; margin-top:15px;"></div>
        <button id="checkout-btn" class="btn-primary">Оформить заказ</button>
    </div>
</div>

<div id="checkout-modal" class="modal">
    <div class="modal-content">
        <span class="close" data-modal="checkout-modal">&times;</span>
        <h2>📝 Оформление заказа</h2>
        <form id="order-form">
            <div class="form-group"><input type="text" id="fio" placeholder="ФИО" required></div>
            <div class="form-group"><input type="tel" id="phone" placeholder="Телефон" required></div>
            <div class="form-group"><input type="email" id="email" placeholder="Email" required></div>
            <div class="form-group"><select id="delivery" required><option value="">Способ доставки</option><option value="Белпочта">Белпочта</option><option value="Европочта">Европочта</option></select></div>
            <div class="form-group"><input type="text" id="post_office" placeholder="Адрес отделения" required></div>
            <button type="submit" class="btn-primary">Подтвердить заказ</button>
        </form>
    </div>
</div>

<script>
    let tg = window.Telegram.WebApp;
    tg.expand();
    let categories = [];
    let currentCategoryId = null;
    let productsData = {};
    let cart = [];

    async function loadData() {
        const resp = await fetch('/api/categories');
        categories = await resp.json();
        const nameResp = await fetch('/api/store_name');
        const storeName = await nameResp.text();
        document.getElementById('store-name').innerText = storeName || "Магазин";
        renderCategories();
        if(categories.length) selectCategory(categories[0].id);
    }

    function renderCategories() {
        const container = document.getElementById('categories-list');
        container.innerHTML = '';
        categories.forEach(cat => {
            const btn = document.createElement('button');
            btn.innerText = cat.name;
            btn.classList.add('category-btn');
            btn.onclick = () => selectCategory(cat.id);
            container.appendChild(btn);
        });
    }

    async function selectCategory(catId) {
        currentCategoryId = catId;
        const resp = await fetch(`/api/products/${catId}`);
        const prods = await resp.json();
        productsData[catId] = prods;
        renderProducts(prods);
    }

    function renderProducts(prods) {
        const container = document.getElementById('products-list');
        if(!prods.length) { container.innerHTML = '<p>Нет товаров</p>'; return; }
        container.innerHTML = prods.map(prod => `
            <div class="product-card" data-id="${prod.id}">
                <h3>${prod.name}</h3>
                <div class="product-price">
                    ${prod.old_price ? `<span class="old-price">${prod.old_price} BYN</span>` : ''}
                    ${prod.price} BYN
                </div>
                <div class="size-select" data-prodid="${prod.id}">
                    ${prod.sizes ? prod.sizes.split(',').map(s => `<button class="size-btn" data-size="${s.trim()}">${s.trim()}</button>`).join('') : '<span>нет размеров</span>'}
                </div>
                <button class="add-to-cart">📥 В корзину</button>
            </div>
        `).join('');
        document.querySelectorAll('.size-btn').forEach(btn => {
            btn.onclick = (e) => {
                e.stopPropagation();
                const parent = btn.closest('.size-select');
                parent.querySelectorAll('.size-btn').forEach(b => b.classList.remove('selected'));
                btn.classList.add('selected');
            };
        });
        document.querySelectorAll('.add-to-cart').forEach(btn => {
            btn.onclick = (e) => {
                const card = btn.closest('.product-card');
                const prodId = parseInt(card.dataset.id);
                const prod = prods.find(p => p.id === prodId);
                const selectedSize = card.querySelector('.size-btn.selected')?.dataset.size;
                if(!selectedSize && card.querySelector('.size-select span')===null) {
                    tg.showAlert('Выберите размер');
                    return;
                }
                addToCart(prodId, prod.name, selectedSize, prod.price);
            };
        });
    }

    function addToCart(id, name, size, price) {
        const existing = cart.find(i => i.id === id && i.size === size);
        if(existing) existing.quantity++;
        else cart.push({ id, name, size, price, quantity: 1 });
        updateCartUI();
        tg.showPopup({ title: "Добавлено", message: `${name} (${size}) в корзине`, buttons: [{type:"ok"}] });
    }

    function updateCartUI() {
        const totalQty = cart.reduce((s,i) => s + i.quantity, 0);
        document.getElementById('cart-count').innerText = totalQty;
        const cartDiv = document.getElementById('cart-items');
        if(!cartDiv) return;
        if(cart.length===0) { cartDiv.innerHTML = '<p>Корзина пуста</p>'; document.getElementById('cart-total').innerHTML = ''; return; }
        cartDiv.innerHTML = cart.map(item => `
            <div class="cart-item">
                <div><b>${item.name}</b> (${item.size})<br>${item.price} BYN × ${item.quantity}</div>
                <div>
                    <button onclick="changeQty(${item.id}, '${item.size}', -1)">-</button>
                    <span>${item.quantity}</span>
                    <button onclick="changeQty(${item.id}, '${item.size}', 1)">+</button>
                    <button onclick="removeItem(${item.id}, '${item.size}')">🗑</button>
                </div>
            </div>
        `).join('');
        const total = cart.reduce((s,i) => s + i.price * i.quantity, 0);
        document.getElementById('cart-total').innerHTML = `Итого: ${total} BYN`;
    }

    function changeQty(id, size, delta) {
        const item = cart.find(i => i.id === id && i.size === size);
        if(item) {
            item.quantity += delta;
            if(item.quantity <= 0) cart = cart.filter(i => !(i.id === id && i.size === size));
        }
        updateCartUI();
    }
    function removeItem(id, size) { cart = cart.filter(i => !(i.id === id && i.size === size)); updateCartUI(); }

    function openModal(id) { document.getElementById(id).style.display = 'block'; }
    function closeModal(id) { document.getElementById(id).style.display = 'none'; }
    document.getElementById('cart-btn').onclick = () => { if(cart.length) openModal('cart-modal'); else tg.showAlert('Корзина пуста'); };
    document.querySelectorAll('.close').forEach(el => { el.onclick = () => closeModal(el.dataset.modal); });
    window.onclick = (e) => { if(e.target.classList.contains('modal')) e.target.style.display = 'none'; };
    document.getElementById('checkout-btn').onclick = () => { closeModal('cart-modal'); openModal('checkout-modal'); };

    document.getElementById('order-form').onsubmit = async (e) => {
        e.preventDefault();
        const fio = document.getElementById('fio').value;
        const phone = document.getElementById('phone').value;
        const email = document.getElementById('email').value;
        const delivery = document.getElementById('delivery').value;
        const post_office = document.getElementById('post_office').value;
        if(!fio || !phone || !email || !delivery || !post_office) { tg.showAlert('Заполните все поля'); return; }
        const total = cart.reduce((s,i) => s + i.price * i.quantity, 0);
        const orderData = { fio, phone, email, delivery_method: delivery, post_office, payment_method: "Наличные при получении", items: cart, total };
        const resp = await fetch('/api/create_order', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(orderData) });
        if(resp.ok) {
            tg.showAlert('Заказ оформлен! С вами свяжутся.');
            cart = []; updateCartUI(); closeModal('checkout-modal');
        } else { tg.showAlert('Ошибка оформления'); }
    };
    loadData();
</script>
</body>
</html>
"""

# ---------- ВЕБ-ОБРАБОТЧИКИ ----------
async def api_categories(request):
    return web.json_response(get_categories())

async def api_store_name(request):
    return web.Response(text=get_config("store_name"))

async def api_products(request):
    cat_id = int(request.match_info['cat_id'])
    prods = get_products_by_category(cat_id)
    for p in prods:
        p['sizes'] = p.get('sizes', '')
    return web.json_response(prods)

async def api_create_order(request):
    data = await request.json()
    tg_id = 0
    order_id = create_order(
        telegram_id=tg_id,
        fio=data['fio'],
        phone=data['phone'],
        email=data['email'],
        delivery=data['delivery_method'],
        post_office=data['post_office'],
        payment=data['payment_method'],
        total=data['total'],
        items=data['items']
    )
    try:
        await application.bot.send_message(ADMIN_ID, f"🆕 Новый заказ #{order_id} на сумму {data['total']} BYN\n{data['fio']}\n{data['phone']}")
    except:
        pass
    return web.json_response({"order_id": order_id})

async def index(request):
    return web.Response(text=HTML_PAGE, content_type="text/html")

async def telegram_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.update_queue.put(update)
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(text="OK")

# ---------- Callback обработчики ----------
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "admin_cats":
        await admin_categories(update, context, query=query)
    elif data == "admin_products":
        await admin_products(update, context, query=query)
    elif data == "admin_orders_list":
        await admin_orders_list(update, context)
    elif data == "admin_settings":
        await admin_settings(update, context)
    elif data == "admin_back":
        await admin_panel(update, context)
    elif data == "add_category":
        await add_category_dialog(update, context)
    elif data == "rename_category":
        await rename_category_dialog(update, context)
    elif data == "del_category":
        await delete_category_dialog(update, context)
    elif data.startswith("rename_cat_"):
        await rename_category_select(update, context)
    elif data.startswith("del_cat_"):
        await delete_category_confirm(update, context)
    elif data.startswith("show_products_"):
        await show_products_list(update, context)
    elif data == "add_product":
        await add_product_start(update, context)
    elif data == "edit_product":
        await edit_product_list(update, context)
    elif data == "del_product":
        await delete_product_list(update, context)
    elif data.startswith("edit_prod_"):
        await edit_product_select(update, context)
    elif data.startswith("del_prod_"):
        await delete_product_confirm(update, context)
    elif data in ["set_storename", "set_delivery", "set_instagram", "set_manager"]:
        await set_config_value(update, context)
    elif data.startswith("order_status_"):
        await change_order_status(update, context)
    else:
        await query.answer("Неизвестная команда")

# ---------- ЗАПУСК ----------
application = None

def main():
    global application
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_text))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^(?!\/)'), handle_admin_text_input))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^(?!\/)'), handle_product_input))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^(?!\/)'), handle_config_input))
    application.add_handler(MessageHandler(filters.PHOTO, handle_product_input))

    # Установка вебхука
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.initialize())
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
        loop.run_until_complete(application.bot.set_webhook(webhook_url))
        logger.info(f"Webhook set to {webhook_url}")
    else:
        logger.warning("WEBHOOK_URL not set")
    loop.run_until_complete(application.start())

    # Веб-сервер
    web_app = web.Application()
    web_app.router.add_get("/", index)
    web_app.router.add_get("/api/categories", api_categories)
    web_app.router.add_get("/api/store_name", api_store_name)
    web_app.router.add_get("/api/products/{cat_id}", api_products)
    web_app.router.add_post("/api/create_order", api_create_order)
    web_app.router.add_post(f"/{TELEGRAM_TOKEN}", telegram_webhook)

    port = int(os.environ.get("PORT", 10000))
    web.run_app(web_app, host="0.0.0.0", port=port, loop=loop)

if __name__ == "__main__":
    main()
