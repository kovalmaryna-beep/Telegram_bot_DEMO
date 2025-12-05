import asyncio, json, os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import requests
import concurrent.futures
import re
import traceback

# ------------------ Налаштування ------------------

BOT_TOKEN = "YOUR_BOT_TOKEN"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Фіксуємо робочу директорію на папку скрипта
os.chdir(BASE_DIR)

ADDRESS_FILE = os.path.join(BASE_DIR, "addresses.json")
TRACKING_FILE = os.path.join(BASE_DIR, "tracking.json")
SCREENSHOT_FILE = os.path.join(BASE_DIR, "schedule.png")
LOG_FILE = os.path.join(BASE_DIR, "tracking.log")
STARTUP_LOG = os.path.join(BASE_DIR, "startup.log")

executor = concurrent.futures.ThreadPoolExecutor()

user_data = {}
tracking_data = {}
previous_html = {}
tracking_tasks = {}  # активні asyncio.Task

# ------------------ Утиліти ------------------

def safe_read_json(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Помилка читання {path}: {e}. Відновлюю файл дефолтним вмістом.")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        return default

def load_addresses():
    return safe_read_json(ADDRESS_FILE, {})

def save_addresses(data):
    with open(ADDRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_tracking():
    return safe_read_json(TRACKING_FILE, {})

def save_tracking(data):
    with open(TRACKING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def log_change(message: str):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception as e:
        print(f"⚠️ Не вдалося записати в лог: {e}")

def log_startup_line(line: str):
    try:
        with open(STARTUP_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

# ------------------ Відправка повідомлень ------------------

def send_text_to_telegram(message, bot_token, chat_id):
    if not message.strip():
        return
    if len(message) > 4000:
        message = message[:4000]
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {"chat_id": chat_id, "text": message}
    try:
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print(f"⚠️ Помилка відправки тексту: {e}")

def send_image_to_telegram(image_path, bot_token, chat_id):
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    try:
        with open(image_path, "rb") as image:
            files = {"photo": image}
            data = {"chat_id": chat_id}
            requests.post(url, files=files, data=data, timeout=30)
    except FileNotFoundError:
        print("⚠️ Зображення не знайдено для відправки")
    except Exception as e:
        print(f"⚠️ Помилка відправки зображення: {e}")

# ------------------ Витяг даних зі сторінки ------------------

def extract_status_text(html):
    soup = BeautifulSoup(html, "html.parser")

    block = (soup.select_one("div#discon-fact.active p")
             or soup.select_one("div#showCurOutage.active p")
             or soup.select_one("div#discon-fact p"))
    if not block:
        return ""

    content = block.decode_contents()

    content = re.sub(
        r'(<span\s+class="_update_info"[^>]*>.*?</span>).*$', r'\1', content, flags=re.S
    )
    content = re.sub(
        r'<span\s+class="_update_info"[^>]*>.*?</span>', '', content, flags=re.S
    )
    content = re.sub(
        r'[\s\-–—]*\d{2}:\d{2}\s+\d{2}\.\d{2}\.\d{4}\s*$', '', content
    )

    clean = BeautifulSoup(content, "html.parser").get_text(separator="\n", strip=True)
    return clean

# --- нова функція для парсингу активного рядка таблиці ---
ALLOWED_CELL_CLASSES = {
    "cell-non-scheduled",
    "cell-scheduled",
    "cell-first-half",
    "cell-second-half",
}

def extract_active_row_cells(html):
    """
    Повертає список класів клітинок активної таблиці (тільки <tbody><tr>),
    пропускаючи перші дві комірки (colspan). Повертає тільки 4 дозволені класи,
    інші значення — як None (ігноруємо при порівнянні).
    """
    soup = BeautifulSoup(html, "html.parser")
    active_table = soup.select_one("div.discon-fact-tables div.discon-fact-table.active table")
    if not active_table:
        return []

    row = active_table.select_one("tbody tr")
    if not row:
        return []

    cells = []
    tds = row.find_all("td")
    # пропускаємо перші дві colspan
    for td in tds[2:]:
        classes = td.get("class", [])
        cls = classes[0] if classes else None
        cells.append(cls if cls in ALLOWED_CELL_CLASSES else None)
    return cells

def get_html_for_address(city, street, house):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.dtek-dnem.com.ua/ua/shutdowns")
        try:
            page.wait_for_selector(".modal__close", timeout=5000)
            page.click(".modal__close")
        except:
            pass

        page.click("#city")
        page.fill("#city", city)
        page.wait_for_selector("#cityautocomplete-list > div", timeout=5000)
        page.click("#cityautocomplete-list > div")

        page.wait_for_function("!document.querySelector('#street').disabled")
        page.click("#street")
        page.fill("#street", street)
        page.wait_for_selector("#streetautocomplete-list > div", timeout=5000)
        page.click("#streetautocomplete-list > div")

        page.wait_for_function("!document.querySelector('#house_num').disabled")
        page.click("#house_num")
        page.fill("#house_num", house)
        page.wait_for_selector("#house_numautocomplete-list > div", timeout=5000)
        page.click("#house_numautocomplete-list > div")

        page.wait_for_selector("div#discon-fact.active", timeout=10000)
        page.wait_for_timeout(2000)

        html = page.content()

        try:
            element = page.query_selector("#discon-fact.active")
            if element:
                element.screenshot(path=SCREENSHOT_FILE)
            else:
                page.screenshot(path=SCREENSHOT_FILE, full_page=True)
        except Exception:
            page.screenshot(path=SCREENSHOT_FILE, full_page=True)

        browser.close()
        return html

# ------------------ Команди ------------------

async def add_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("❗ Формат: /addaddress <місто> <вулиця> <будинок>")
        return
    city, street, house = context.args[0], context.args[1], context.args[2]
    chat_id = str(update.effective_chat.id)
    user_data.setdefault(chat_id, []).append({"city": city, "street": street, "house": house})
    save_addresses(user_data)
    await update.message.reply_text(f"✅ Додано адресу: {city}, {street}, {house}")

async def list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    addresses = load_addresses().get(chat_id, [])
    if not addresses:
        await update.message.reply_text("ℹ️ Немає збережених адрес")
        return
    text = "\n".join([f"{i+1}. {a['city']}, {a['street']}, {a['house']}" for i, a in enumerate(addresses)])
    await update.message.reply_text(f"📋 Збережені адреси:\n{text}")

async def delete_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    addresses = load_addresses().get(chat_id, [])
    if not addresses:
        await update.message.reply_text("ℹ️ Немає адрес для видалення")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❗ Формат: /deleteaddress <номер>")
        return
    index = int(context.args[0]) - 1
    if 0 <= index < len(addresses):
        removed = addresses.pop(index)
        user_data[chat_id] = addresses
        save_addresses(user_data)
        await update.message.reply_text(f"🗑️ Видалено: {removed['city']}, {removed['street']}, {removed['house']}")
    else:
        await update.message.reply_text("❗ Невірний номер адреси")

async def get_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    addresses = load_addresses().get(chat_id, [])
    if not addresses:
        await update.message.reply_text("❗ Спочатку додайте адресу командою /addaddress")
        return

    async def run_check(addr):
        html = await asyncio.get_event_loop().run_in_executor(
            executor,
            get_html_for_address,
            addr["city"], addr["street"], addr["house"]
        )
        text = extract_status_text(html)
        message = text if text else "ℹ️ Статус електропостачання не знайдено"
        send_text_to_telegram(message, BOT_TOKEN, chat_id)
        send_image_to_telegram(SCREENSHOT_FILE, BOT_TOKEN, chat_id)

    if context.args and context.args[0] == "all":
        for addr in addresses:
            await run_check(addr)
    elif context.args and context.args[0].isdigit():
        index = int(context.args[0]) - 1
        if 0 <= index < len(addresses):
            await run_check(addresses[index])
        else:
            await update.message.reply_text("❗ Невірний номер адреси")
    else:
        await update.message.reply_text("❗ Формат: /status <номер> або /status all")

async def track_changes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    addresses = load_addresses().get(chat_id, [])
    if not addresses:
        await update.message.reply_text("❗ Спочатку додайте адресу командою /addaddress")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❗ Формат: /track <номер адреси>")
        return
    index = int(context.args[0]) - 1
    if 0 <= index < len(addresses):
        addr = addresses[index]
        await update.message.reply_text(f"🔄 Відстеження змін для адреси {index+1} активовано")
        task = asyncio.create_task(start_tracking(chat_id, index, addr))
        tracking_tasks[f"{chat_id}_{index}"] = task
        tracking_data.setdefault(chat_id, [])
        if index not in tracking_data[chat_id]:
            tracking_data[chat_id].append(index)
            save_tracking(tracking_data)
    else:
        await update.message.reply_text("❗ Невірний номер адреси")

async def stop_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    addresses = load_addresses().get(chat_id, [])
    if not addresses:
        await update.message.reply_text("ℹ️ Немає збережених адрес")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❗ Формат: /stoptrack <номер>")
        return
    index = int(context.args[0]) - 1
    key = f"{chat_id}_{index}"
    if chat_id in tracking_data and index in tracking_data[chat_id]:
        tracking_data[chat_id].remove(index)
        save_tracking(tracking_data)
        if key in tracking_tasks:
            tracking_tasks[key].cancel()
            del tracking_tasks[key]
        await update.message.reply_text(
            f"🛑 Відстеження для адреси {index+1} "
            f"({addresses[index]['city']}, {addresses[index]['street']} {addresses[index]['house']}) зупинено"
        )
    else:
        await update.message.reply_text("❗ Для цієї адреси відстеження не було активовано")

async def start_tracking(chat_id, index, addr):
    key = f"{chat_id}_{index}"
    while True:
        try:
            html = await asyncio.get_event_loop().run_in_executor(
                executor,
                get_html_for_address,
                addr["city"], addr["street"], addr["house"]
            )
            text = extract_status_text(html)
            cells = extract_active_row_cells(html)

            current_state = (text, cells)

            if key not in previous_html:
                previous_html[key] = current_state
            elif current_state != previous_html[key]:
                previous_html[key] = current_state
                message = (
                    f"🔔 Зміни для адреси {index+1} "
                    f"({addr['city']}, {addr['street']} {addr['house']}):\n\n{text}"
                )
                send_text_to_telegram(message, BOT_TOKEN, chat_id)
                send_image_to_telegram(SCREENSHOT_FILE, BOT_TOKEN, chat_id)
                log_change(message)
        except Exception as e:
            # Лог і пауза, щоб уникнути частих падінь при тимчасових збоях
            log_change(f"⚠️ Помилка в start_tracking для {addr['city']}, {addr['street']} {addr['house']}: {e}")

        await asyncio.sleep(600)

async def restore_tracking():
    for chat_id, indices in tracking_data.items():
        addresses = load_addresses().get(chat_id, [])
        for index in indices:
            if 0 <= index < len(addresses):
                addr = addresses[index]
                task = asyncio.create_task(start_tracking(chat_id, index, addr))
                tracking_tasks[f"{chat_id}_{index}"] = task

# Хук, який виконується у правильному event loop після ініціалізації application
async def on_post_init(app):
    try:
        await restore_tracking()
        log_startup_line("✅ restore_tracking виконано в post_init")
    except Exception as e:
        log_startup_line(f"❌ Помилка restore_tracking: {e}")

# ------------------ Запуск бота ------------------

if __name__ == "__main__":
    try:
        user_data = load_addresses()
        tracking_data = load_tracking()

        app = ApplicationBuilder().token(BOT_TOKEN).build()

        app.add_handler(CommandHandler("addaddress", add_address))
        app.add_handler(CommandHandler("listaddresses", list_addresses))
        app.add_handler(CommandHandler("deleteaddress", delete_address))
        app.add_handler(CommandHandler("status", get_status))
        app.add_handler(CommandHandler("track", track_changes))
        app.add_handler(CommandHandler("stoptrack", stop_track))

        # ВАЖЛИВО: не створюємо задачі напряму через get_event_loop() тут.
        # Використовуємо офіційний хук post_init, який працює у внутрішньому loop бота.
        app.post_init = on_post_init

        print("🤖 Бот запущено. Очікує команди...")
        log_startup_line("🚀 Старт run_polling")
        app.run_polling()
    except Exception:
        traceback.print_exc()
        log_startup_line("❌ Критична помилка запуску, див. traceback вище.")

        input("Натисніть Enter, щоб закрити...")
