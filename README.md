# 🔓 Telegram Password Finder Bot

A Telegram bot that extracts APIs from APK files and finds passwords from JSON responses.

## 🚀 Features

- 📦 Extract APIs from APK `resources.arsc` files
- 🔍 Test APIs for valid JSON responses
- 🔓 Find passwords in JSON data
- 👑 Admin commands for channel management
- 📊 User statistics tracking
- 📢 Broadcast messages to all users

## 📋 Commands

| Command | Description | Access |
|---------|-------------|--------|
| `/start` | Start the bot and create session | All users |
| `/done` | Process uploaded APK files | All users |
| `/cancel` | Cancel current session | All users |
| `/stats` | View bot statistics | Admin only |
| `/broadcast` | Send message to all users | Admin only |
| `/addchannel` | Add required channel | Admin only |
| `/removechannel` | Remove required channel | Admin only |
| `/listchannels` | List required channels | Admin only |

## 🛠️ How It Works

1. User sends `/start` to the bot
2. Clicks **"FIND PASSWORD"** button
3. Uploads one or more APK files
4. Types `/done` to process
5. Bot extracts APIs from `resources.arsc`
6. Tests each API for JSON response
7. Searches for passwords in JSON data
8. Returns found password or summary

## 💻 Deployment

### Deploy on Render (Free 24/7)

1. Fork this repository
2. Go to [render.com](https://render.com)
3. Create new Web Service
4. Connect your GitHub repository
5. Set:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
6. Click **"Deploy"**

### Deploy on Pydroid (Android)

1. Install Pydroid 3 from Play Store
2. Install dependencies:
   ```bash
   pip install python-telegram-bot aiohttp
