import logging
import re
import uuid
import json
import asyncio
import hashlib
import hmac
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import gspread
from google.oauth2.service_account import Credentials
import aiohttp
import yookassa
from yookassa import Payment
from config import *

# Безопасная инициализация ЮKassa
yookassa.Configuration.account_id = YOOKASSA_SHOP_ID
yookassa.Configuration.secret_key = YOOKASSA_SECRET_KEY
# Logging configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

class MatrixBot:
    def __init__(self):
        self.sheet = None
        self.initialize_google_sheets()
    
    def initialize_google_sheets(self):
        """Инициализация подключения к Google Sheets"""
        try:
            if GOOGLE_SERVICE_ACCOUNT:
                credentials_info = json.loads(GOOGLE_SERVICE_ACCOUNT)
                credentials = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
                gc = gspread.authorize(credentials)
                self.sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
                
                # Проверяем, есть ли заголовки, если нет - добавляем
                if not self.sheet.cell(1, 1).value:
                    self.sheet.append_row([
                        'User ID', 'Username', 'Имя покупателя', 'Телефон', 
                        'Количество билетов', 'Сумма', 'Дата регистрации', 'Статус оплаты', 'Payment ID'
                    ])
                logger.info("Google Sheets инициализирован успешно")
            else:
                logger.warning("GOOGLE_SERVICE_ACCOUNT не найден")
        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {e}")
            self.sheet = None
    
    def is_valid_phone(self, phone):
        """
        Проверяет, является ли строка валидным телефоном.
        Поддерживает форматы: +79001234567, 89001234567, (900) 123-45-67 и т.п.
        """
        # Убираем все символы, кроме цифр и знаков + и -
        clean_phone = re.sub(r'[^\d+\-]', '', phone)
        
        # Убираем лишние пробелы и дефисы
        clean_phone = re.sub(r'[\s\-]+', '', clean_phone)
        
        # Если начинается с 8, заменяем на +7
        if clean_phone.startswith('8'):
            clean_phone = '+7' + clean_phone[1:]
        
        # Если начинается с +7, проверяем, что дальше только цифры
        if clean_phone.startswith('+7'):
            if len(clean_phone) != 12:  # +7 + 10 цифр
                return False
            if not clean_phone[2:].isdigit():
                return False
            return True
        
        # Если начинается с +9, допускаем международные номера (до 15 цифр)
        if clean_phone.startswith('+9'):
            if len(clean_phone) < 12 or len(clean_phone) > 17:
                return False
            if not clean_phone[2:].isdigit():
                return False
            return True
        
        # Другие форматы не принимаются
        return False
    
    def user_already_registered(self, user_id):
        """Проверяет, зарегистрирован ли пользователь"""
        if not self.sheet:
            return False
        try:
            records = self.sheet.get_all_records()
            for record in records:
                if str(record.get('User ID', '')) == str(user_id) and str(record.get('Статус оплаты', '')) == 'Оплачено':
                    return True
            return False
        except Exception as e:
            logger.error(f"Ошибка проверки регистрации: {e}")
            return False
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        try:
            user_id = update.effective_user.id
            
            # Проверяем, не зарегистрирован ли уже пользователь
            if self.user_already_registered(user_id):
                await update.message.reply_text("✅ Вы уже зарегистрированы на мероприятии!")
                return
            
            # Новое приветственное сообщение
            welcome_text = (
                "🎲 <b>Дорогой друг!</b>\n\n"
                "🔮 Приглашаю тебя на Трансформационную игру\n"
                "✨ <b>«Выход из Матрицы»</b> ✨\n\n"
                "📅 <b>Дата проведения:</b> 25 сентября \n"
                "🎟️ <b>Стоимость билета:</b> <code>{price} руб.</code>\n\n"
                "Здесь ты откроешь новые горизонты своего сознания, найдешь путь к внутренним ресурсам и сделаешь первые шаги к осознанному изменению своей жизни🎯\n\n"
                "Пусть игра станет началом твоего вдохновляющего путешествия к мечте и счастью!💌"
            ).format(price=TICKET_PRICE)
            
            keyboard = [[InlineKeyboardButton("🎟️ Зарегистрироваться", callback_data='register')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(welcome_text, parse_mode='HTML', reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Ошибка в start handler: {e}")
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")
    
    async def button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик callback кнопок"""
        try:
            query = update.callback_query
            await query.answer()
            user_id = query.from_user.id
            
            if query.data == 'register':
                # Проверяем, не зарегистрирован ли уже пользователь
                if self.user_already_registered(user_id):
                    await query.edit_message_text("✅ Вы уже зарегистрированы на мероприятии!")
                    return
                    
                # Начинаем регистрацию - спрашиваем количество билетов
                context.user_data['state'] = USER_STATE_WAITING_FOR_TICKET_COUNT
                await query.edit_message_text(
                    "🎟️ <b>Сколько билетов вы хотите приобрести?</b>\n\n"
                    "Введите число от 1 до 10:",
                    parse_mode='HTML'
                )
            
            elif query.data.startswith('pay_'):
                # Обработка оплаты
                payment_id = query.data.split('_')[1]
                await self.process_payment(update, context, payment_id)
            
            elif query.data == 'confirm_payment':
                # Подтверждение оплаты
                await self.confirm_payment(update, context)
            
            elif query.data == 'cancel_payment':
                # Отмена оплаты
                await self.cancel_payment(update, context)
        except Exception as e:
            logger.error(f"Ошибка в button handler: {e}")
            try:
                await update.callback_query.answer("⚠️ Произошла ошибка. Попробуйте позже.", show_alert=True)
            except:
                pass
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстовых сообщений"""
        try:
            user_id = update.effective_user.id
            text = update.message.text.strip()
            state = context.user_data.get('state')
            
            # Проверяем, не зарегистрирован ли уже пользователь
            if self.user_already_registered(user_id) and state != USER_STATE_WAITING_FOR_TICKET_COUNT:
                await update.message.reply_text("✅ Вы уже зарегистрированы на мероприятии!")
                return
            
            if state == USER_STATE_WAITING_FOR_TICKET_COUNT:
                try:
                    ticket_count = int(text)
                    if 1 <= ticket_count <= 10:
                        context.user_data['ticket_count'] = ticket_count
                        context.user_data['state'] = USER_STATE_WAITING_FOR_NAME
                        await update.message.reply_text("👤 <b>Введите ваше имя:</b>", parse_mode='HTML')
                    else:
                        await update.message.reply_text("⚠️ Пожалуйста, введите число от 1 до 10.")
                except ValueError:
                    await update.message.reply_text("⚠️ Пожалуйста, введите корректное число.")
                    
            elif state == USER_STATE_WAITING_FOR_NAME:
                if len(text) < 2:
                    await update.message.reply_text("⚠️ Имя должно содержать минимум 2 символа.")
                    return
                context.user_data['name'] = text
                context.user_data['state'] = USER_STATE_WAITING_FOR_PHONE
                await update.message.reply_text("📱 <b>Введите ваш номер телефона:</b>\nПример: +79001234567", parse_mode='HTML')
                
            elif state == USER_STATE_WAITING_FOR_PHONE:
                # Отладочный вывод
                logger.info(f"Введённый номер: {text}")
                clean_phone = re.sub(r'[^\d+\-]', '', text)
                clean_phone = re.sub(r'[\s\-]+', '', clean_phone)
                logger.info(f"Чистый номер: {clean_phone}")
                logger.info(f"Длина: {len(clean_phone)}")
                logger.info(f"Валидный: {self.is_valid_phone(text)}")
                
                if not self.is_valid_phone(text):
                    await update.message.reply_text(
                        "⚠️ Пожалуйста, введите корректный номер телефона.\n"
                        "Пример: +79001234567 или 89001234567"
                    )
                    return
                context.user_data['phone'] = text
                # Регистрация завершена, показываем кнопку оплаты
                await self.show_payment_button(update, context)
        except Exception as e:
            logger.error(f"Ошибка в handle_message: {e}")
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")
    
    async def show_payment_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показ кнопки оплаты"""
        try:
            user_data = {
                'name': context.user_data.get('name', ''),
                'phone': context.user_data.get('phone', ''),
                'ticket_count': context.user_data.get('ticket_count', 1)
            }
            
            total_amount = user_data['ticket_count'] * TICKET_PRICE
            
            # Генерируем уникальный ID для платежа
            payment_id = str(uuid.uuid4())[:8]
            context.user_data['payment_id'] = payment_id
            context.user_data['total_amount'] = total_amount
            
            # Сообщение с деталями заказа
            order_text = (
                "📄 <b>Подтверждение заказа:</b>\n\n"
                f"👤 Имя: <code>{user_data['name']}</code>\n"
                f"📱 Телефон: <code>{user_data['phone']}</code>\n"
                f"🎟️ Количество билетов: <b>{user_data['ticket_count']}</b>\n"
                f"💰 Сумма к оплате: <b>{total_amount} руб.</b>\n\n"
                "Для оплаты нажмите кнопку ниже:"
            )
            
            keyboard = [[InlineKeyboardButton("💳 Оплатить через ЮKassa", callback_data=f'pay_{payment_id}')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(order_text, parse_mode='HTML', reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Ошибка в show_payment_button: {e}")
            await update.message.reply_text("⚠️ Произошла ошибка при создании заказа.")
    
    async def process_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id):
        """Обработка оплаты через ЮKassa"""
        try:
            query = update.callback_query
            
            total_amount = context.user_data.get('total_amount', 0)
            user_data = {
                'name': context.user_data.get('name', ''),
                'phone': context.user_data.get('phone', ''),
                'ticket_count': context.user_data.get('ticket_count', 1)
            }
            
            # Создаем платеж через ЮKassa
            payment = Payment.create({
                "amount": {
                    "value": str(total_amount),
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": "https://t.me/your_bot_username"  # Замените на ваш URL
                },
                "capture": True,
                "description": f"Оплата за участие в игре 'Выход из Матрицы'. Билетов: {user_data['ticket_count']}",
                "metadata": {
                    "payment_id": payment_id,
                    "user_id": str(query.from_user.id),
                    "name": user_data['name'],
                    "phone": user_data['phone'],
                    "ticket_count": str(user_data['ticket_count'])
                }
            })
            
            # Сохраняем ID платежа ЮKassa
            context.user_data['yookassa_payment_id'] = payment.id
            
            # Сохраняем предварительные данные в Google Sheets со статусом "Ожидание оплаты"
            if self.sheet:
                self.sheet.append_row([
                    query.from_user.id,
                    query.from_user.username or '',
                    user_data['name'],
                    user_data['phone'],
                    user_data['ticket_count'],
                    f"{total_amount} руб.",
                    datetime.now().strftime("%d.%m.%Y %H:%M"),
                    "Ожидание оплаты",
                    payment_id
                ])
            
            # Создаем кнопку для перехода к оплате
            payment_text = (
                "💳 <b>Оплата через ЮKassa</b>\n\n"
                f"🛒 Сумма к оплате: <b>{total_amount} руб.</b>\n"
                f"🆔 Номер заказа: <code>{payment_id}</code>\n"
                f"🏪 Магазин: Большая Трансформационная Игра\n\n"
                "Нажмите кнопку ниже для перехода к оплате:"
            )
            
            keyboard = [
                [InlineKeyboardButton("💳 Перейти к оплате", url=payment.confirmation.confirmation_url)],
                [InlineKeyboardButton("✅ Проверить оплату", callback_data=f'check_payment_{payment_id}')],
                [InlineKeyboardButton("❌ Отменить", callback_data='cancel_payment')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(payment_text, parse_mode='HTML', reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Ошибка в process_payment: {e}")
            try:
                await update.callback_query.answer("⚠️ Ошибка при создании платежа.", show_alert=True)
            except:
                pass
    
    async def check_payment_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id):
        """Проверка статуса платежа"""
        try:
            query = update.callback_query
            
            yookassa_payment_id = context.user_data.get('yookassa_payment_id')
            if not yookassa_payment_id:
                await query.answer("⚠️ Платеж не найден", show_alert=True)
                return
            
            # Получаем статус платежа из ЮKassa
            payment = Payment.find_one(yookassa_payment_id)
            
            if payment.status == 'succeeded':
                # Платеж успешен
                await self.confirm_payment_success(update, context, payment_id)
            elif payment.status == 'canceled':
                # Платеж отменен
                await self.cancel_payment(update, context)
            else:
                # Платеж в процессе
                await query.answer("⏳ Платеж еще не подтвержден. Пожалуйста, дождитесь подтверждения оплаты.", show_alert=True)
                
        except Exception as e:
            logger.error(f"Ошибка при проверке статуса платежа: {e}")
            await query.answer("⚠️ Ошибка при проверке статуса платежа", show_alert=True)
    
    async def confirm_payment_success(self, update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id):
        """Подтверждение успешной оплаты"""
        try:
            query = update.callback_query
            user_data = {
                'user_id': query.from_user.id,
                'username': query.from_user.username or '',
                'name': context.user_data.get('name', ''),
                'phone': context.user_data.get('phone', ''),
                'ticket_count': context.user_data.get('ticket_count', 1),
                'total_amount': context.user_data.get('total_amount', 0),
                'payment_id': payment_id
            }
            
            # Обновляем статус в Google Sheets
            if self.sheet:
                try:
                    records = self.sheet.get_all_records()
                    for i, record in enumerate(records, start=2):  # начинаем с 2, так как первая строка - заголовки
                        if str(record.get('Payment ID', '')) == str(payment_id):
                            # Обновляем статус оплаты
                            self.sheet.update_cell(i, 8, "Оплачено")  # Столбец "Статус оплаты"
                            break
                except Exception as e:
                    logger.error(f"Ошибка обновления статуса в Google Sheets: {e}")
                
                # Сообщение об успешной оплате
                success_text = (
                    "🎉 <b>Поздравляем!</b>\n\n"
                    "✅ <b>Оплата успешно выполнена!</b>\n\n"
                    "🔮 Вы успешно зарегистрировались на Трансформационную игру\n"
                    "✨ <b>«Выход из Матрицы»</b> ✨\n\n"
                    "📄 <b>Детали заказа:</b>\n"
                    f"👤 Имя: <code>{user_data['name']}</code>\n"
                    f"📱 Телефон: <code>{user_data['phone']}</code>\n"
                    f"🎟️ Количество билетов: <b>{user_data['ticket_count']}</b>\n"
                    f"💰 Сумма: <b>{user_data['total_amount']} руб.</b>\n"
                    f"🆔 Номер платежа: <code>{user_data['payment_id']}</code>\n"
                    f"📅 Дата: <code>{datetime.now().strftime('%d.%m.%Y %H:%M')}</code>\n\n"
                    "💳 <b>Статус:</b> <code>Оплачено</code>\n\n"
                    "Спасибо за регистрацию! До встречи на игре! 🎊"
                )
                
                await query.edit_message_text(success_text, parse_mode='HTML')
            else:
                await query.edit_message_text("⚠️ Ошибка подключения к базе данных!")
        except Exception as e:
            logger.error(f"Ошибка в confirm_payment_success: {e}")
            try:
                await update.callback_query.edit_message_text("⚠️ Ошибка при подтверждении оплаты.")
            except:
                pass
        finally:
            # Сбрасываем состояние
            context.user_data.clear()
    
    async def confirm_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Подтверждение оплаты (для обратной совместимости)"""
        await self.confirm_payment_success(update, context, context.user_data.get('payment_id', ''))
    
    async def cancel_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отмена оплаты"""
        try:
            query = update.callback_query
            await query.edit_message_text("❌ Оплата отменена.\n\nВведите /start для новой регистрации")
        except Exception as e:
            logger.error(f"Ошибка в cancel_payment: {e}")
        finally:
            context.user_data.clear()
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /cancel"""
        try:
            context.user_data.clear()
            await update.message.reply_text("❌ Регистрация отменена.\n\nВведите /start для новой регистрации")
        except Exception as e:
            logger.error(f"Ошибка в cancel handler: {e}")

# Создаем экземпляр бота
matrix_bot = MatrixBot()

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await matrix_bot.start(update, context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_data = update.callback_query.data
    
    if query_data.startswith('check_payment_'):
        payment_id = query_data.split('_')[2]
        await matrix_bot.check_payment_status(update, context, payment_id)
    else:
        await matrix_bot.button(update, context)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await matrix_bot.handle_message(update, context)

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await matrix_bot.cancel(update, context)

def main():
    """Основная функция для запуска бота"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("cancel", cancel_handler))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    # Запускаем бота
    application.run_polling()

if __name__ == '__main__':
    main()