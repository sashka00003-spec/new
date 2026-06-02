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
    ContextTypes, CallbackQueryHandler, ConversationHandler
)

# ---------- НАСТРОЙКА ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8812317225:AAE-cOCndbJkbRysfm-Ed8iLGMk_APZ18Jg")
ADMIN_ID = int(os.getenv("ADMIN_ID", "2064971302"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

DB_PATH = "store.db"

# ---------- БАЗА ДАННЫХ ----------
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("store_name", "Мой магазин"))
        cur.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("delivery_fee", "5"))
        cur.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("instagram", ""))
        cur.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", ("manager_link", ""))
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

# ---------- ФУНКЦИИ ДЛЯ РАБОТЫ С БАЗОЙ (те же, что были) ----------
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

def rename_category(cat_id, new_name):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE categories SET name=? WHERE id=?", (new_name, cat_id))
        return True
    except:
        return False

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
        [KeyboardButton("🔧 Админ панель")]   # новая кнопка для админов
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def admin_main_keyboard():
    """Инлайн-клавиатура админ-панели"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить категорию", callback_data="admin_add_category")],
        [InlineKeyboardButton("📂 Управление категориями", callback_data="admin_manage_categories")],
        [InlineKeyboardButton("👟 Управление товарами", callback_data="admin_manage_products")],
        [InlineKeyboardButton("📋 Все заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("⚙️ Настройки магазина", callback_data="admin_settings")],
        [InlineKeyboardButton("🔙 Закрыть", callback_data="admin_close")]
    ])

def categories_list_keyboard(categories):
    """Список категорий с кнопками редактирования/удаления"""
    kb = []
    for cat in categories:
        kb.append([InlineKeyboardButton(f"📁 {cat['name']}", callback_data=f"cat_edit_{cat['id']}")])
    kb.append([InlineKeyboardButton("➕ Добавить категорию", callback_data="admin_add_category")])
    kb.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back_main")])
    return InlineKeyboardMarkup(kb)

def category_edit_keyboard(cat_id, cat_name):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Переименовать", callback_data=f"cat_rename_{cat_id}")],
        [InlineKeyboardButton("🗑 Удалить категорию", callback_data=f"cat_delete_{cat_id}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_manage_categories")]
    ])

def categories_for_product_keyboard(categories):
    """Для выбора категории при добавлении товара"""
    kb = []
    for cat in categories:
        kb.append([InlineKeyboardButton(cat['name'], callback_data=f"prod_add_cat_{cat['id']}")])
    kb.append([InlineKeyboardButton("◀️ Отмена", callback_data="admin_back_main")])
    return InlineKeyboardMarkup(kb)

def products_list_keyboard(products, cat_id):
    kb = []
    for p in products:
        kb.append([InlineKeyboardButton(f"{p['name']} ({p['price']} BYN)", callback_data=f"prod_edit_{p['id']}")])
    kb.append([InlineKeyboardButton("➕ Добавить товар", callback_data=f"prod_add_new_{cat_id}")])
    kb.append([InlineKeyboardButton("◀️ Назад к категориям", callback_data="admin_manage_categories")])
    return InlineKeyboardMarkup(kb)

def product_edit_keyboard(prod_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Название", callback_data=f"prod_field_name_{prod_id}"),
         InlineKeyboardButton("💰 Цена", callback_data=f"prod_field_price_{prod_id}")],
        [InlineKeyboardButton("🏷 Старая цена", callback_data=f"prod_field_oldprice_{prod_id}"),
         InlineKeyboardButton("📝 Описание", callback_data=f"prod_field_desc_{prod_id}")],
        [InlineKeyboardButton("📏 Размеры", callback_data=f"prod_field_sizes_{prod_id}"),
         InlineKeyboardButton("🖼 Фото", callback_data=f"prod_field_photo_{prod_id}")],
        [InlineKeyboardButton("🗑 Удалить товар", callback_data=f"prod_delete_{prod_id}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"prod_back_to_list_{prod_id}")]
    ])

def settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏪 Название магазина", callback_data="set_store_name")],
        [InlineKeyboardButton("💰 Стоимость доставки", callback_data="set_delivery_fee")],
        [InlineKeyboardButton("📸 Instagram", callback_data="set_instagram")],
        [InlineKeyboardButton("👤 Ссылка на менеджера", callback_data="set_manager")],
        [InlineKeyboardButton("◀️ Назад", callback_data="admin_back_main")]
    ])

# ---------- ОБРАБОТЧИКИ БОТА ДЛЯ ПОКУПАТЕЛЕЙ ----------
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
            await update.message.reply_text("🌐 Ссылка на магазин пока не настроена. Сообщите администратору.")
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
        store_name = get_config("store_name")
        reply = f"🏪 {store_name}\nДоставка: {delivery_fee} BYN\n"
        if inst: reply += f"📸 Instagram: {inst}\n"
        if manager: reply += f"👤 Менеджер: {manager}\n"
        await update.message.reply_text(reply)
    elif text == "🔧 Админ панель":
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Доступ запрещён.")
            return
        await update.message.reply_text("🔧 Панель администратора:", reply_markup=admin_main_keyboard())

# ---------- ОБРАБОТЧИКИ АДМИН-ПАНЕЛИ (инлайн) ----------
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_close":
        await query.message.delete()
        return
    elif data == "admin_back_main":
        await query.message.edit_text("🔧 Панель администратора:", reply_markup=admin_main_keyboard())
        return

    # Управление категориями
    if data == "admin_manage_categories":
        cats = get_categories()
        if not cats:
            await query.message.edit_text("Категории отсутствуют. Добавьте первую.", reply_markup=categories_list_keyboard([]))
        else:
            await query.message.edit_text("📂 Список категорий (нажмите для редактирования):", reply_markup=categories_list_keyboard(cats))
        return

    if data == "admin_add_category":
        context.user_data['awaiting_cat_name'] = True
        await query.message.edit_text("Введите название новой категории (или /cancel для отмены):")
        return

    if data.startswith("cat_edit_"):
        cat_id = int(data.split("_")[-1])
        cat_name = get_category_name(cat_id)
        await query.message.edit_text(f"Редактирование категории «{cat_name}»:", reply_markup=category_edit_keyboard(cat_id, cat_name))
        return

    if data.startswith("cat_rename_"):
        cat_id = int(data.split("_")[-1])
        context.user_data['rename_cat_id'] = cat_id
        context.user_data['awaiting_rename'] = True
        await query.message.edit_text("Введите новое название категории:")
        return

    if data.startswith("cat_delete_"):
        cat_id = int(data.split("_")[-1])
        delete_category(cat_id)
        await query.answer("Категория удалена")
        # Обновить список
        cats = get_categories()
        await query.message.edit_text("📂 Список категорий:", reply_markup=categories_list_keyboard(cats))
        return

    # Управление товарами
    if data == "admin_manage_products":
        cats = get_categories()
        if not cats:
            await query.message.edit_text("Сначала создайте категории.", reply_markup=admin_main_keyboard())
        else:
            await query.message.edit_text("Выберите категорию для управления товарами:", reply_markup=categories_for_product_keyboard(cats))
        return

    if data.startswith("prod_add_cat_"):
        cat_id = int(data.split("_")[-1])
        context.user_data['add_product_cat_id'] = cat_id
        await query.message.edit_text("Введите название товара (или /cancel для отмены):")
        context.user_data['awaiting_product_name'] = True
        return

    if data.startswith("prod_add_new_"):
        cat_id = int(data.split("_")[-1])
        context.user_data['add_product_cat_id'] = cat_id
        await query.message.edit_text("Введите название товара:")
        context.user_data['awaiting_product_name'] = True
        return

    if data.startswith("prod_edit_"):
        prod_id = int(data.split("_")[-1])
        context.user_data['edit_prod_id'] = prod_id
        prod = get_product(prod_id)
        if not prod:
            await query.message.edit_text("Товар не найден.")
            return
        text = f"Редактирование товара: {prod['name']}\nВыберите, что изменить:"
        await query.message.edit_text(text, reply_markup=product_edit_keyboard(prod_id))
        return

    if data.startswith("prod_field_"):
        parts = data.split("_")
        field = parts[2]
        prod_id = int(parts[3])
        context.user_data['edit_prod_id'] = prod_id
        context.user_data['edit_field'] = field
        field_names = {"name": "название", "price": "цену (число)", "oldprice": "старую цену (число или 0)", "desc": "описание", "sizes": "размеры через запятую (или '-')", "photo": "фото"}
        await query.message.edit_text(f"Введите новое значение для поля '{field_names.get(field, field)}':")
        context.user_data['awaiting_edit_value'] = True
        return

    if data.startswith("prod_delete_"):
        prod_id = int(data.split("_")[-1])
        delete_product(prod_id)
        await query.answer("Товар удалён")
        # Вернуться к списку категорий
        cats = get_categories()
        await query.message.edit_text("Выберите категорию для управления товарами:", reply_markup=categories_for_product_keyboard(cats))
        return

    if data.startswith("prod_back_to_list_"):
        prod_id = int(data.split("_")[-1])
        prod = get_product(prod_id)
        if prod:
            cat_id = prod['category_id']
            products = get_products_by_category(cat_id)
            await query.message.edit_text(f"Товары в категории:", reply_markup=products_list_keyboard(products, cat_id))
        else:
            await query.message.edit_text("Ошибка.", reply_markup=admin_main_keyboard())
        return

    # Настройки
    if data == "admin_settings":
        await query.message.edit_text("⚙️ Настройки магазина:", reply_markup=settings_keyboard())
        return

    if data == "set_store_name":
        context.user_data['awaiting_setting'] = 'store_name'
        await query.message.edit_text("Введите новое название магазина:")
        return
    if data == "set_delivery_fee":
        context.user_data['awaiting_setting'] = 'delivery_fee'
        await query.message.edit_text("Введите стоимость доставки (число, BYN):")
        return
    if data == "set_instagram":
        context.user_data['awaiting_setting'] = 'instagram'
        await query.message.edit_text("Введите ссылку на Instagram (или username):")
        return
    if data == "set_manager":
        context.user_data['awaiting_setting'] = 'manager_link'
        await query.message.edit_text("Введите ссылку на менеджера (например, t.me/username):")
        return

    # Заказы
    if data == "admin_orders":
        orders = get_all_orders()
        if not orders:
            await query.message.edit_text("Заказов нет.", reply_markup=admin_main_keyboard())
            return
        for o in orders:
            text = f"🆔 Заказ #{o['id']}\n{o['fio']}\n{o['phone']}\n{o['email']}\nДоставка: {o['delivery_method']}, {o['post_office']}\nСтатус: {o['status']}\nТовары: {o['items_json']}\nИтого: {o['total']} BYN"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Принять", callback_data=f"order_status_{o['id']}_принят")],
                [InlineKeyboardButton("Отправить", callback_data=f"order_status_{o['id']}_отправлен")],
                [InlineKeyboardButton("Завершить", callback_data=f"order_status_{o['id']}_завершён")],
                [InlineKeyboardButton("◀️ Назад", callback_data="admin_orders_back")]
            ])
            await query.message.reply_text(text, reply_markup=kb)
            await asyncio.sleep(0.3)
        await query.message.delete()
        return

    if data.startswith("order_status_"):
        _, order_id, new_status = data.split("_")
        update_order_status(int(order_id), new_status)
        await query.answer(f"Статус изменён на {new_status}")
        await query.message.edit_text(query.message.text + f"\n✅ Статус: {new_status}")
        return

    if data == "admin_orders_back":
        await query.message.edit_text("🔧 Панель администратора:", reply_markup=admin_main_keyboard())
        return

def get_category_name(cat_id):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM categories WHERE id=?", (cat_id,))
        row = cur.fetchone()
        return row[0] if row else ""

# ---------- ОБРАБОТЧИКИ ТЕКСТОВЫХ СООБЩЕНИЙ (диалоги) ----------
async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        # Не админ, просто игнорируем или обрабатываем как обычное сообщение
        return
    text = update.message.text.strip()
    if text == "/cancel":
        # Очищаем все ожидания
        for key in list(context.user_data.keys()):
            if key.startswith('awaiting'):
                del context.user_data[key]
        await update.message.reply_text("Действие отменено.")
        return

    # Ожидание названия новой категории
    if context.user_data.get('awaiting_cat_name'):
        if add_category(text):
            await update.message.reply_text(f"✅ Категория «{text}» добавлена.")
        else:
            await update.message.reply_text("❌ Такая категория уже существует.")
        del context.user_data['awaiting_cat_name']
        await update.message.reply_text("🔧 Панель администратора:", reply_markup=admin_main_keyboard())
        return

    # Переименование категории
    if context.user_data.get('awaiting_rename'):
        cat_id = context.user_data.get('rename_cat_id')
        if cat_id and rename_category(cat_id, text):
            await update.message.reply_text(f"✅ Категория переименована в «{text}».")
        else:
            await update.message.reply_text("❌ Ошибка или такое имя уже существует.")
        del context.user_data['awaiting_rename']
        del context.user_data['rename_cat_id']
        await update.message.reply_text("🔧 Панель администратора:", reply_markup=admin_main_keyboard())
        return

    # Добавление товара (многоэтапный диалог)
    if context.user_data.get('awaiting_product_name'):
        context.user_data['prod_name'] = text
        context.user_data['awaiting_product_name'] = False
        context.user_data['awaiting_product_price'] = True
        await update.message.reply_text("Введите цену (только число):")
        return

    if context.user_data.get('awaiting_product_price'):
        if not text.isdigit():
            await update.message.reply_text("Ошибка! Введите число.")
            return
        context.user_data['prod_price'] = int(text)
        context.user_data['awaiting_product_price'] = False
        context.user_data['awaiting_product_oldprice'] = True
        await update.message.reply_text("Введите старую цену (для скидки) или 0, чтобы пропустить:")
        return

    if context.user_data.get('awaiting_product_oldprice'):
        old = int(text) if text.isdigit() else 0
        context.user_data['prod_old'] = old if old > 0 else None
        context.user_data['awaiting_product_oldprice'] = False
        context.user_data['awaiting_product_desc'] = True
        await update.message.reply_text("Введите описание товара:")
        return

    if context.user_data.get('awaiting_product_desc'):
        context.user_data['prod_desc'] = text
        context.user_data['awaiting_product_desc'] = False
        context.user_data['awaiting_product_photo'] = True
        await update.message.reply_text("Отправьте фотографию товара:")
        return

    if context.user_data.get('awaiting_product_sizes'):
        sizes = text if text != "-" else ""
        add_product(
            cat_id=context.user_data['add_product_cat_id'],
            name=context.user_data['prod_name'],
            price=context.user_data['prod_price'],
            old_price=context.user_data['prod_old'],
            desc=context.user_data['prod_desc'],
            photo_id=context.user_data['prod_photo'],
            sizes=sizes
        )
        await update.message.reply_text("✅ Товар успешно добавлен!")
        for k in ['awaiting_product_sizes', 'add_product_cat_id', 'prod_name', 'prod_price', 'prod_old', 'prod_desc', 'prod_photo']:
            if k in context.user_data:
                del context.user_data[k]
        await update.message.reply_text("🔧 Панель администратора:", reply_markup=admin_main_keyboard())
        return

    # Редактирование поля товара
    if context.user_data.get('awaiting_edit_value'):
        field = context.user_data.get('edit_field')
        prod_id = context.user_data.get('edit_prod_id')
        if not prod_id or not field:
            await update.message.reply_text("Ошибка. Попробуйте заново.")
            return
        if field == "price" or field == "oldprice":
            if not text.isdigit():
                await update.message.reply_text("Нужно число. Попробуйте снова:")
                return
            value = int(text)
            if field == "oldprice" and value == 0:
                value = None
            db_field = "old_price" if field == "oldprice" else "price"
        elif field == "photo":
            await update.message.reply_text("Пожалуйста, отправьте фото (изображение).")
            return
        elif field == "sizes" and text == "-":
            value = ""
            db_field = "sizes"
        else:
            value = text
            db_field = field if field != "oldprice" else "old_price"
        if field == "photo":
            # Фото обрабатывается отдельно в handle_photo
            context.user_data['awaiting_photo_for_edit'] = prod_id
            await update.message.reply_text("Отправьте новое фото товара:")
            return
        update_product(prod_id, **{db_field: value})
        await update.message.reply_text(f"✅ Поле обновлено.")
        del context.user_data['awaiting_edit_value']
        del context.user_data['edit_field']
        # Показать снова меню редактирования товара
        prod = get_product(prod_id)
        if prod:
            text = f"Редактирование товара: {prod['name']}\nВыберите, что изменить:"
            await update.message.reply_text(text, reply_markup=product_edit_keyboard(prod_id))
        return

    # Настройки
    if context.user_data.get('awaiting_setting'):
        setting_key = context.user_data['awaiting_setting']
        set_config(setting_key, text)
        await update.message.reply_text(f"✅ Настройка {setting_key} обновлена.")
        del context.user_data['awaiting_setting']
        await update.message.reply_text("⚙️ Настройки магазина:", reply_markup=settings_keyboard())
        return

async def handle_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    # Если ожидаем фото для товара
    if context.user_data.get('awaiting_product_photo'):
        photo_id = update.message.photo[-1].file_id
        context.user_data['prod_photo'] = photo_id
        context.user_data['awaiting_product_photo'] = False
        context.user_data['awaiting_product_sizes'] = True
        await update.message.reply_text("Введите размеры через запятую (например: 36,37,38) или '-' если размеров нет:")
        return
    # Если ожидаем фото для редактирования товара
    if context.user_data.get('awaiting_photo_for_edit'):
        prod_id = context.user_data['awaiting_photo_for_edit']
        photo_id = update.message.photo[-1].file_id
        update_product(prod_id, photo_file_id=photo_id)
        await update.message.reply_text("✅ Фото обновлено.")
        del context.user_data['awaiting_photo_for_edit']
        # Показать меню редактирования
        prod = get_product(prod_id)
        if prod:
            text = f"Редактирование товара: {prod['name']}\nВыберите, что изменить:"
            await update.message.reply_text(text, reply_markup=product_edit_keyboard(prod_id))
        return

# ---------- ВЕБ-ИНТЕРФЕЙС (HTML) ----------
# Здесь должен быть полный HTML-код из предыдущего ответа (длинный).
# Для краткости оставлю заглушку, но вы вставьте свой HTML.
HTML_PAGE = """<!DOCTYPE html><html>... (вставьте сюда полный HTML из предыдущего ответа) ...</html>"""

# ---------- ВЕБ-ОБРАБОТЧИКИ (API) ----------
async def api_categories(request):
    return web.json_response(get_categories())

async def api_store_name(request):
    return web.Response(text=get_config("store_name"))

async def api_products(request):
    cat_id = int(request.match_info['cat_id'])
    prods = get_products_by_category(cat_id)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        for p in prods:
            cur.execute("SELECT sizes FROM products WHERE id=?", (p['id'],))
            row = cur.fetchone()
            p['sizes'] = row['sizes'] if row else ""
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
    """Принимает обновления от Telegram и передаёт их боту"""
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.update_queue.put(update)
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(text="OK")

# ---------- ЗАПУСК ----------
application = None

def main():
    global application
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Все обработчики (команды, диалоги и т.д.) – они у вас уже есть,
    # убедитесь, что они добавлены (я не стал переписывать все, оставьте свои)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("addcategory", add_category_start))
    application.add_handler(CommandHandler("addproduct", add_product_start))
    application.add_handler(CommandHandler("editproduct", edit_product_start))
    application.add_handler(CallbackQueryHandler(admin_orders, pattern="^admin_orders$"))
    application.add_handler(CallbackQueryHandler(change_order_status, pattern="^order_status_"))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_text))

    # Диалоги (они тоже уже есть, оставьте как есть)
    # ... conv_addcat, conv_addprod, conv_editprod ...

    # Установка вебхука (вместо polling)
    webhook_url = f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.initialize())
    loop.run_until_complete(application.bot.set_webhook(webhook_url))
    loop.run_until_complete(application.start())
    # НЕ вызываем polling: убираем application.updater.start_polling()

    # Веб-сервер (aiohttp)
    web_app = web.Application()
    web_app.router.add_get("/", index)
    web_app.router.add_get("/api/categories", api_categories)
    web_app.router.add_get("/api/store_name", api_store_name)
    web_app.router.add_get("/api/products/{cat_id}", api_products)
    web_app.router.add_post("/api/create_order", api_create_order)
    web_app.router.add_post(f"/{TELEGRAM_TOKEN}", telegram_webhook)   # эндпоинт для вебхука

    port = int(os.environ.get("PORT", 8000))
    web.run_app(web_app, host="0.0.0.0", port=port, loop=loop)
if __name__ == "__main__":
    main()
