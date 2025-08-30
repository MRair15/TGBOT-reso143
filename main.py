import asyncio
import os
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from bot import start_handler, button_handler, message_handler, cancel_handler
from config import TELEGRAM_TOKEN

# Для Render
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def start_health_server():
    """Запуск легкого HTTP-сервера для Render"""
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    print(f"Health server running on port {port}")

def main():
    """Основная функция запуска"""
    # Запускаем "здоровый" сервер для Render
    start_health_server()
    
    # Создаем приложение бота
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("cancel", cancel_handler))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("Бот запущен...")
    
    # Запускаем polling
    application.run_polling()

if __name__ == '__main__':
    main()