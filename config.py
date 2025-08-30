import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден в переменных окружения")

# Payment Configuration
TICKET_PRICE = int(os.getenv('TICKET_PRICE', 1111))

# Google Sheets Configuration
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
GOOGLE_SERVICE_ACCOUNT = os.getenv('GOOGLE_SERVICE_ACCOUNT')

# User States
USER_STATE_WAITING_FOR_NAME = 'waiting_for_name'
USER_STATE_WAITING_FOR_PHONE = 'waiting_for_phone'
USER_STATE_WAITING_FOR_TICKET_COUNT = 'waiting_for_ticket_count'

# Google Sheets Scopes
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']