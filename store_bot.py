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

# Переменные окружения (замените на свои или используйте .env)
TELEGRAM_TOKEN = "8812317225:AAE-cOCndbJkbRysfm-Ed8iLGMk_APZ18Jg"
ADMIN_ID = 2064971302  # ваш Telegram ID
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

# ---------- БАЗА ДАННЫХ ----------
DB_PATH = "store.db"

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

# ---------- РАБОТА С БАЗОЙ ----------
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

# ---------- КЛАВИАТУРЫ БОТА ----------
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🛍 Открыть магазин")],
        [KeyboardButton("📦 Мои заказы"), KeyboardButton("ℹ️ О магазине")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить категорию", callback_data="admin_add_cat")],
        [InlineKeyboardButton("📂 Управление товарами", callback_data="admin_manage_products")],
        [InlineKeyboardButton("📋 Все заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")],
    ])

# ---------- ОБРАБОТЧИКИ БОТА ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store_name = get_config("store_name")
    await update.message.reply_text(
        f"👋 Добро пожаловать в {store_name}!\nИспользуйте кнопки ниже.",
        reply_markup=get_main_keyboard()
    )

# Вместо жёсткой строки
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

# И в main_menu_text:
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
    # ... остальные условия без изменений
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
        text = f"🏪 {get_config('store_name')}\nДоставка: {delivery_fee} BYN\n"
        if inst: text += f"📸 Instagram: {inst}\n"
        if manager: text += f"👤 Менеджер: {manager}\n"
        await update.message.reply_text(text)

# ---------- АДМИН-КОМАНДЫ ----------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет прав.")
        return
    await update.message.reply_text("🔧 Панель администратора:", reply_markup=admin_keyboard())

# --- Добавление категории (диалог) ---
async def add_category_start(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("Введите название новой категории (или /cancel для отмены):")
    return 1

async def add_category_receive(update: Update, context):
    name = update.message.text.strip()
    if add_category(name):
        await update.message.reply_text(f"✅ Категория «{name}» добавлена.")
    else:
        await update.message.reply_text("❌ Такая категория уже существует.")
    return ConversationHandler.END

# --- Состояния для добавления товара ---
ADD_CATEGORY_STATE, ADD_NAME_STATE, ADD_PRICE_STATE, ADD_OLD_PRICE_STATE, ADD_DESC_STATE, ADD_PHOTO_STATE, ADD_SIZES_STATE = range(7)

async def add_product_start(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    cats = get_categories()
    if not cats:
        await update.message.reply_text("Сначала добавьте категорию через /addcategory")
        return ConversationHandler.END
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(cat['name'], callback_data=f"addprod_cat_{cat['id']}")] for cat in cats
    ])
    await update.message.reply_text("Выберите категорию для нового товара:", reply_markup=kb)
    return ADD_CATEGORY_STATE

async def add_product_category(update: Update, context):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[-1])
    context.user_data['prod_cat_id'] = cat_id
    await query.message.reply_text("Введите название товара:")
    return ADD_NAME_STATE

async def add_product_name(update: Update, context):
    context.user_data['prod_name'] = update.message.text
    await update.message.reply_text("Введите цену (только число):")
    return ADD_PRICE_STATE

async def add_product_price(update: Update, context):
    if not update.message.text.isdigit():
        await update.message.reply_text("Ошибка! Введите число.")
        return ADD_PRICE_STATE
    context.user_data['prod_price'] = int(update.message.text)
    await update.message.reply_text("Введите старую цену (для скидки) или 0, чтобы пропустить:")
    return ADD_OLD_PRICE_STATE

async def add_product_old_price(update: Update, context):
    text = update.message.text.strip()
    old = 0
    if text.isdigit():
        old = int(text)
    context.user_data['prod_old'] = old if old > 0 else None
    await update.message.reply_text("Введите описание товара:")
    return ADD_DESC_STATE

async def add_product_desc(update: Update, context):
    context.user_data['prod_desc'] = update.message.text
    await update.message.reply_text("Отправьте фотографию товара:")
    return ADD_PHOTO_STATE

async def add_product_photo(update: Update, context):
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправьте фото.")
        return ADD_PHOTO_STATE
    photo_id = update.message.photo[-1].file_id
    context.user_data['prod_photo'] = photo_id
    await update.message.reply_text("Введите размеры через запятую (например: 36,37,38) или '-' если размеров нет:")
    return ADD_SIZES_STATE

async def add_product_sizes(update: Update, context):
    sizes = update.message.text.strip()
    if sizes == "-":
        sizes = ""
    add_product(
        cat_id=context.user_data['prod_cat_id'],
        name=context.user_data['prod_name'],
        price=context.user_data['prod_price'],
        old_price=context.user_data['prod_old'],
        desc=context.user_data['prod_desc'],
        photo_id=context.user_data['prod_photo'],
        sizes=sizes
    )
    await update.message.reply_text("✅ Товар успешно добавлен!")
    return ConversationHandler.END

# --- Редактирование товара ---
async def edit_product_start(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    # Показываем список товаров для выбора
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, name, category_id FROM products ORDER BY id")
        products = cur.fetchall()
    if not products:
        await update.message.reply_text("Нет товаров для редактирования.")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{p['name']} (ID:{p['id']})", callback_data=f"edit_prod_{p['id']}")] for p in products
    ])
    await update.message.reply_text("Выберите товар для редактирования:", reply_markup=kb)

async def edit_product_select(update: Update, context):
    query = update.callback_query
    await query.answer()
    prod_id = int(query.data.split("_")[-1])
    context.user_data['edit_prod_id'] = prod_id
    prod = get_product(prod_id)
    if not prod:
        await query.message.reply_text("Товар не найден.")
        return
    text = f"Редактирование: {prod['name']}\nЧто хотите изменить?"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Название", callback_data="edit_field_name")],
        [InlineKeyboardButton("Цену", callback_data="edit_field_price")],
        [InlineKeyboardButton("Старую цену", callback_data="edit_field_old_price")],
        [InlineKeyboardButton("Описание", callback_data="edit_field_desc")],
        [InlineKeyboardButton("Размеры", callback_data="edit_field_sizes")],
        [InlineKeyboardButton("Удалить товар", callback_data="edit_field_delete")],
        [InlineKeyboardButton("Отмена", callback_data="edit_cancel")]
    ])
    await query.message.reply_text(text, reply_markup=kb)

async def edit_product_field(update: Update, context):
    query = update.callback_query
    await query.answer()
    field = query.data.split("_")[-1]
    if field == "cancel":
        await query.message.reply_text("Редактирование отменено.")
        return
    if field == "delete":
        prod_id = context.user_data.get('edit_prod_id')
        if prod_id:
            delete_product(prod_id)
            await query.message.reply_text("🗑 Товар удалён.")
        else:
            await query.message.reply_text("Ошибка.")
        return
    context.user_data['edit_field'] = field
    field_names = {"name": "название", "price": "цену (число)", "old_price": "старую цену (число или 0)", "desc": "описание", "sizes": "размеры через запятую (или '-')"}
    await query.message.reply_text(f"Введите новое значение для поля '{field_names.get(field, field)}':")
    return 1  # состояние ожидания ввода

async def edit_product_value(update: Update, context):
    new_value = update.message.text.strip()
    field = context.user_data.get('edit_field')
    prod_id = context.user_data.get('edit_prod_id')
    if not prod_id or not field:
        await update.message.reply_text("Ошибка. Попробуйте заново.")
        return ConversationHandler.END
    if field == "price" or field == "old_price":
        if not new_value.isdigit():
            await update.message.reply_text("Нужно число. Попробуйте снова:")
            return 1
        new_value = int(new_value)
        if field == "old_price" and new_value == 0:
            new_value = None
    elif field == "sizes" and new_value == "-":
        new_value = ""
    update_product(prod_id, **{field: new_value})
    await update.message.reply_text(f"✅ Поле обновлено.")
    return ConversationHandler.END

# --- Просмотр заказов (админ) ---
async def admin_orders(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    orders = get_all_orders()
    if not orders:
        await update.message.reply_text("Заказов нет.")
        return
    for o in orders:
        text = f"🆔 Заказ #{o['id']}\n{o['fio']}\n{o['phone']}\n{o['email']}\nДоставка: {o['delivery_method']}, {o['post_office']}\nСтатус: {o['status']}\nТовары: {o['items_json']}\nИтого: {o['total']} BYN"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Принять", callback_data=f"order_status_{o['id']}_принят")],
            [InlineKeyboardButton("Отправить", callback_data=f"order_status_{o['id']}_отправлен")],
            [InlineKeyboardButton("Завершить", callback_data=f"order_status_{o['id']}_завершён")]
        ])
        await update.message.reply_text(text, reply_markup=kb)
        await asyncio.sleep(0.3)

async def change_order_status(update: Update, context):
    query = update.callback_query
    await query.answer()
    _, order_id, new_status = query.data.split("_")
    update_order_status(int(order_id), new_status)
    await query.edit_message_text(query.message.text + f"\n✅ Статус изменён на {new_status}")

# ---------- ВЕБ-ИНТЕРФЕЙС (HTML) ----------
HTML_PAGE = """... (здесь ваш HTML из предыдущего ответа, он же без изменений) ..."""

# В целях экономии места HTML-код не повторяю, но он должен быть таким же, как в предыдущем ответе.

# ---------- ВЕБ-ОБРАБОТЧИКИ (API) ----------
async def api_categories(request):
    return web.json_response(get_categories())

async def api_store_name(request):
    return web.Response(text=get_config("store_name"))

async def api_products(request):
    cat_id = int(request.match_info['cat_id'])
    prods = get_products_by_category(cat_id)
    # Добавляем sizes для каждого товара
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
    # Здесь в реальном проекте можно получить telegram_id из initData
    tg_id = 0  # или из данных пользователя
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

# ---------- ЗАПУСК ----------
application = None

def main():
    global application
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Команды и обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("addcategory", add_category_start))
    application.add_handler(CommandHandler("addproduct", add_product_start))
    application.add_handler(CommandHandler("editproduct", edit_product_start))
    application.add_handler(CallbackQueryHandler(admin_orders, pattern="^admin_orders$"))
    application.add_handler(CallbackQueryHandler(change_order_status, pattern="^order_status_"))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_text))

    # Диалог добавления категории
    conv_addcat = ConversationHandler(
        entry_points=[CommandHandler("addcategory", add_category_start)],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_category_receive)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
    )
    application.add_handler(conv_addcat)

    # Диалог добавления товара
    conv_addprod = ConversationHandler(
        entry_points=[CommandHandler("addproduct", add_product_start)],
        states={
            ADD_CATEGORY_STATE: [CallbackQueryHandler(add_product_category, pattern="^addprod_cat_")],
            ADD_NAME_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_name)],
            ADD_PRICE_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_price)],
            ADD_OLD_PRICE_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_old_price)],
            ADD_DESC_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_desc)],
            ADD_PHOTO_STATE: [MessageHandler(filters.PHOTO, add_product_photo)],
            ADD_SIZES_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_sizes)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
    )
    application.add_handler(conv_addprod)

    # Диалог редактирования товара
    conv_editprod = ConversationHandler(
        entry_points=[CommandHandler("editproduct", edit_product_start)],
        states={
            0: [CallbackQueryHandler(edit_product_select, pattern="^edit_prod_")],
            1: [CallbackQueryHandler(edit_product_field, pattern="^edit_field_")],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_value)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
    )
    application.add_handler(conv_editprod)

    # Запуск бота в режиме long polling
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.initialize())
    loop.run_until_complete(application.start())
    loop.create_task(application.updater.start_polling())

    # Веб-сервер
    web_app = web.Application()
    web_app.router.add_get("/", index)
    web_app.router.add_get("/api/categories", api_categories)
    web_app.router.add_get("/api/store_name", api_store_name)
    web_app.router.add_get("/api/products/{cat_id}", api_products)
    web_app.router.add_post("/api/create_order", api_create_order)

    port = int(os.environ.get("PORT", 8000))
    web.run_app(web_app, host="0.0.0.0", port=port, loop=loop)

if __name__ == "__main__":
    main()
