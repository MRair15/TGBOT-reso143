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
import yookassa
from yookassa import Payment
from config import *

# Безопасная инициализация ЮKassa
yookassa.Configuration.account_id = YOOKASSA_SHOP_ID
yookassa.Configuration.secret_key = YOOKASSA_SECRET_KEY

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO, # Установите на DEBUG для более подробного лога
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Определение констант состояний
USER_STATE_WAITING_FOR_TICKET_COUNT = 'waiting_for_ticket_count'
USER_STATE_WAITING_FOR_NAME = 'waiting_for_name'
USER_STATE_WAITING_FOR_PHONE = 'waiting_for_phone'
USER_STATE_WAITING_FOR_PAYMENT_CONFIRMATION = 'waiting_for_payment_confirmation'

GS_HEADERS = [
    'User ID', 'Username', 'Имя', 'Номер телефона', # Изменил
    'Количество билетов', 'Сумма', 'Когда куплено', 'Статус', 'Payment ID' # Добавил Payment ID
]
# И соответственно обновите индексы столбцов:
GS_COL_NAME = 3
GS_COL_PHONE = 4
GS_COL_DATE = 7
GS_COL_STATUS = 8
GS_COL_PAYMENT_ID = 9 # Новый столбец

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
                first_cell_value = self.sheet.cell(1, 1).value
                if not first_cell_value:
                    logger.info("Заголовки в Google Sheets не найдены, добавляем новые.")
                    self.sheet.append_row(GS_HEADERS)
                else:
                    # Проверяем, совпадают ли заголовки
                    existing_headers = self.sheet.row_values(1)
                    if existing_headers != GS_HEADERS:
                        logger.warning(f"Заголовки в таблице не совпадают. Ожидалось: {GS_HEADERS}, Получено: {existing_headers}")
                        # Можно либо выдать ошибку, либо попытаться адаптироваться
                        # Пока просто предупреждение
                    else:
                        logger.info("Заголовки в Google Sheets проверены и совпадают.")
                logger.info("Google Sheets инициализирован успешно")
            else:
                logger.error("GOOGLE_SERVICE_ACCOUNT не найден в конфигурации!")
                self.sheet = None
        except Exception as e:
            logger.error(f"Критическая ошибка подключения к Google Sheets: {e}", exc_info=True)
            self.sheet = None
    
    def is_valid_phone(self, phone):
        """
        Проверяет, является ли строка валидным телефоном.
        Поддерживает форматы: +79001234567, 89001234567
        """
        if not phone:
            return False
        # Убираем все символы, кроме цифр и +
        clean_phone = re.sub(r'[^\d+]', '', phone)
        
        # Если начинается с 8, заменяем на +7
        if clean_phone.startswith('8'):
            clean_phone = '+7' + clean_phone[1:]
        
        # Проверка формата +7 и длины
        if clean_phone.startswith('+7') and len(clean_phone) == 12 and clean_phone[1:].isdigit():
            return True
            
        # Простая проверка для международных номеров (пример)
        if clean_phone.startswith('+') and 10 <= len(clean_phone) <= 16 and clean_phone[1:].isdigit():
            return True
        
        return False

    def find_row_by_payment_id(self, payment_id):
        """Находит номер строки по Payment ID. Возвращает -1, если не найдено."""
        if not self.sheet:
            logger.error("Google Sheets не инициализирован для поиска строки.")
            return -1
        try:
            # Получаем все значения, включая заголовки
            all_values = self.sheet.get_all_values()
            if not all_values:
                logger.warning("Таблица пуста.")
                return -1
            
            headers = all_values[0] # Первая строка - заголовки
            try:
                payment_id_col_index = headers.index('Payment ID') + 1 # gspread использует 1-based индекс
            except ValueError:
                logger.error("Столбец 'Payment ID' не найден в заголовках таблицы.")
                return -1

            # Поиск строки с нужным payment_id
            for i, row in enumerate(all_values[1:], start=2): # Начинаем с 2, т.к. первая строка - заголовки
                if len(row) >= payment_id_col_index and row[payment_id_col_index - 1] == str(payment_id):
                    logger.info(f"Найдена строка с Payment ID {payment_id} в строке {i}.")
                    return i
            logger.info(f"Строка с Payment ID {payment_id} не найдена.")
            return -1
        except Exception as e:
            logger.error(f"Ошибка при поиске строки по Payment ID: {e}", exc_info=True)
            return -1

    def user_already_registered(self, user_id):
        """Проверяет, зарегистрирован ли пользователь с успешной оплатой"""
        if not self.sheet:
            logger.warning("Google Sheets не инициализирован для проверки регистрации.")
            return False
        try:
            records = self.sheet.get_all_records()
            logger.debug(f"Проверка регистрации для User ID: {user_id}. Всего записей: {len(records)}")
            for record in records:
                # Проверяем ID пользователя и статус оплаты
                if str(record.get('User ID', '')) == str(user_id) and str(record.get('Статус оплаты', '')) == 'Оплачено':
                    logger.info(f"Пользователь {user_id} уже зарегистрирован и оплатил.")
                    return True
            logger.info(f"Пользователь {user_id} не найден как оплативший.")
            return False
        except Exception as e:
            logger.error(f"Ошибка проверки регистрации для User ID {user_id}: {e}", exc_info=True)
            return False

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        try:
            user_id = update.effective_user.id
            logger.info(f"Пользователь {user_id} запустил бота (/start).")
            
            # Проверяем, не зарегистрирован ли уже пользователь
            if self.user_already_registered(user_id):
                await update.message.reply_text("✅ Вы уже зарегистрированы на мероприятии!")
                return
            
            # Новое приветственное сообщение
            welcome_text = (
                "🎲 <b>Дорогой друг!</b>\n\n"
                "🔮 Приглашаю тебя на Трансформационную игру\n"
                "✨ <b>«Выход из Матрицы»</b> ✨\n\n"
                "📅 <b>Дата проведения:</b> 27 сентября \n"
                "🎟️ <b>Стоимость билета:</b> <code>{price} руб.</code>\n\n"
                "Здесь ты откроешь новые горизонты своего сознания, найдешь путь к внутренним ресурсам и сделаешь первые шаги к осознанному изменению своей жизни🎯\n\n"
                "Пусть игра станет началом твоего вдохновляющего путешествия к мечте и счастью!💌"
            ).format(price=TICKET_PRICE)
            
            keyboard = [[InlineKeyboardButton("🎟️ Зарегистрироваться", callback_data='register')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(welcome_text, parse_mode='HTML', reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Ошибка в start handler для User ID {update.effective_user.id if update.effective_user else 'unknown'}: {e}", exc_info=True)
            await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")

    async def button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик callback кнопок"""
        try:
            query = update.callback_query
            await query.answer()
            user_id = query.from_user.id
            logger.info(f"Пользователь {user_id} нажал кнопку: {query.data}")
            
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
                payment_id = query.data.split('_', 1)[1] # Безопасное разделение
                await self.process_payment(update, context, payment_id)
            
            elif query.data.startswith('check_payment_'):
                # Проверка статуса оплаты
                payment_id = query.data.split('_', 2)[2] # Безопасное разделение
                await self.check_payment_status(update, context, payment_id)
            
            elif query.data == 'confirm_payment':
                # Подтверждение оплаты (устаревший вызов, но оставим для совместимости)
                payment_id = context.user_data.get('payment_id')
                if payment_id:
                     await self.check_payment_status(update, context, payment_id)
                else:
                     await query.answer("⚠️ Ошибка: ID платежа не найден.", show_alert=True)
            
            elif query.data == 'cancel_payment':
                # Отмена оплаты
                await self.cancel_payment(update, context)
                
        except Exception as e:
            logger.error(f"Ошибка в button handler для User ID {update.callback_query.from_user.id if update.callback_query and update.callback_query.from_user else 'unknown'}: {e}", exc_info=True)
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
            logger.info(f"Пользователь {user_id} отправил сообщение в состоянии {state}: '{text}'")

            # Проверяем, не зарегистрирован ли уже пользователь (кроме ввода количества билетов)
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
                logger.info(f"Проверка введенного номера: {text}")
                if not self.is_valid_phone(text):
                    await update.message.reply_text(
                        "⚠️ Пожалуйста, введите корректный номер телефона.\n"
                        "Пример: +79001234567 или 89001234567"
                    )
                    return
                context.user_data['phone'] = text
                # Регистрация завершена, показываем кнопку оплаты
                await self.show_payment_button(update, context)
            else:
                # Если состояние неизвестно, предлагаем начать сначала
                logger.warning(f"Неизвестное состояние для пользователя {user_id}: {state}")
                await update.message.reply_text("Что-то пошло не так. Введите /start, чтобы начать заново.")
                
        except Exception as e:
            logger.error(f"Ошибка в handle_message для User ID {update.effective_user.id if update.effective_user else 'unknown'}: {e}", exc_info=True)
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
            payment_id = str(uuid.uuid4())
            context.user_data['payment_id'] = payment_id
            context.user_data['total_amount'] = total_amount
            context.user_data['state'] = USER_STATE_WAITING_FOR_PAYMENT_CONFIRMATION
            
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
            logger.error(f"Ошибка в show_payment_button для User ID {update.effective_user.id if update.effective_user else 'unknown'}: {e}", exc_info=True)
            await update.message.reply_text("⚠️ Произошла ошибка при создании заказа.")

    async def process_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id):
        """Обработка оплаты через ЮKassa"""
        try:
            query = update.callback_query
            user_id = query.from_user.id
            
            # Проверка, что это тот же пользователь, который инициировал платеж
            if context.user_data.get('payment_id') != payment_id:
                logger.warning(f"Попытка обработки чужого платежа. User ID: {user_id}, Payment ID: {payment_id}")
                await query.answer("⚠️ Ошибка: неверный ID платежа.", show_alert=True)
                return

            total_amount = context.user_data.get('total_amount', 0)
            user_data = {
                'name': context.user_data.get('name', ''),
                'phone': context.user_data.get('phone', ''),
                'ticket_count': context.user_data.get('ticket_count', 1)
            }
            
            if total_amount <= 0:
                logger.error(f"Неверная сумма для оплаты: {total_amount}")
                await query.answer("⚠️ Ошибка: неверная сумма.", show_alert=True)
                return

            logger.info(f"Создание платежа в ЮKassa для User ID: {user_id}, Payment ID: {payment_id}, Сумма: {total_amount}")

            # Создаем платеж через ЮKassa
            payment = Payment.create({
                "amount": {
                    "value": f"{total_amount:.2f}", # Форматирование до 2 знаков после запятой
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": f"https://t.me/{context.bot.username}" # Используем имя бота из контекста
                },
                "capture": True,
                "description": f"Оплата за участие в игре 'Выход из Матрицы'. Билетов: {user_data['ticket_count']}",
                "metadata": {
                    "payment_id": payment_id,
                    "user_id": str(user_id),
                    "name": user_data['name'],
                    "phone": user_data['phone'],
                    "ticket_count": str(user_data['ticket_count'])
                }
            })
            
            # Сохраняем ID платежа ЮKassa
            context.user_data['yookassa_payment_id'] = payment.id
            logger.info(f"Платеж в ЮKassa создан. ЮKassa Payment ID: {payment.id}")
            
            # Сохраняем предварительные данные в Google Sheets со статусом "Ожидание оплаты"
            if self.sheet:
                try:
                    new_row_data = [
                        user_id,
                        query.from_user.username or '',
                        user_data['name'],
                        user_data['phone'],
                        user_data['ticket_count'],
                        f"{total_amount} руб.",
                        datetime.now().strftime("%d.%m.%Y %H:%M"),
                        "Ожидание оплаты",
                        payment_id
                    ]
                    logger.debug(f"Подготовленные данные для записи в Google Sheets: {new_row_data}")
                    append_result = self.sheet.append_row(new_row_data)
                    logger.debug(f"Результат добавления строки в Google Sheets: {append_result}")
                    logger.info(f"Данные 'Ожидание оплаты' добавлены в Google Sheets для Payment ID: {payment_id}")
                except Exception as e:
                    logger.error(f"КРИТИЧЕСКАЯ ОШИБКА записи 'Ожидание оплаты' в Google Sheets: {e}", exc_info=True)
                    # Можно также попробовать отправить сообщение пользователю об ошибке записи
            else:
                logger.error("Google Sheets не инициализирован для записи 'Ожидание оплаты'")
            
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
            
        except yookassa.Error as ye:
            logger.error(f"Ошибка API ЮKassa: {ye}", exc_info=True)
            try:
                await update.callback_query.answer("⚠️ Ошибка при создании платежа в ЮKassa.", show_alert=True)
            except:
                pass
        except Exception as e:
            logger.error(f"Ошибка в process_payment для User ID {update.callback_query.from_user.id if update.callback_query and update.callback_query.from_user else 'unknown'}: {e}", exc_info=True)
            try:
                await update.callback_query.answer("⚠️ Ошибка при создании платежа.", show_alert=True)
            except:
                pass

    async def check_payment_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id):
        """Проверка статуса платежа"""
        try:
            query = update.callback_query
            user_id = query.from_user.id
            logger.info(f"Проверка статуса платежа для User ID: {user_id}, Payment ID: {payment_id}")

            # Проверка, что это тот же пользователь, который инициировал платеж
            if context.user_data.get('payment_id') != payment_id:
                logger.warning(f"Попытка проверки чужого платежа. User ID: {user_id}, Payment ID: {payment_id}")
                await query.answer("⚠️ Ошибка: неверный ID платежа.", show_alert=True)
                return

            yookassa_payment_id = context.user_data.get('yookassa_payment_id')
            if not yookassa_payment_id:
                logger.error(f"ЮKassa Payment ID не найден для Payment ID: {payment_id}")
                await query.answer("⚠️ Платеж не найден", show_alert=True)
                return
            
            logger.info(f"Запрос статуса платежа у ЮKassa: {yookassa_payment_id}")
            # Получаем статус платежа из ЮKassa
            payment = Payment.find_one(yookassa_payment_id)
            logger.info(f"Статус платежа от ЮKassa: {payment.status}")
            
            if payment.status == 'succeeded':
                # Платеж успешен
                await self.confirm_payment_success(update, context, payment_id)
            elif payment.status == 'canceled':
                # Платеж отменен
                await self.cancel_payment(update, context)
            else:
                # Платеж в процессе
                status_message = {
                    'pending': '⏳ Платеж еще не подтвержден. Пожалуйста, дождитесь подтверждения оплаты.',
                    'waiting_for_capture': '⏳ Платеж ожидает захвата. Обычно это происходит автоматически.',
                    'canceled': '❌ Платеж был отменен.',
                }.get(payment.status, f'⏳ Статус платежа: {payment.status}. Пожалуйста, дождитесь подтверждения.')
                
                await query.answer(status_message, show_alert=True)
                
        except yookassa.Error as ye:
            logger.error(f"Ошибка API ЮKassa при проверке статуса: {ye}", exc_info=True)
            await query.answer("⚠️ Ошибка при проверке статуса платежа в ЮKassa", show_alert=True)
        except Exception as e:
            logger.error(f"Ошибка при проверке статуса платежа для Payment ID {payment_id}: {e}", exc_info=True)
            await query.answer("⚠️ Ошибка при проверке статуса платежа", show_alert=True)

    async def confirm_payment_success(self, update: Update, context: ContextTypes.DEFAULT_TYPE, payment_id):
        """Подтверждение успешной оплаты"""
        try:
            query = update.callback_query
            user_id = query.from_user.id
            user_data = {
                'user_id': user_id,
                'username': query.from_user.username or '',
                'name': context.user_data.get('name', ''),
                'phone': context.user_data.get('phone', ''),
                'ticket_count': context.user_data.get('ticket_count', 1),
                'total_amount': context.user_data.get('total_amount', 0),
                'payment_id': payment_id
            }
            
            logger.info(f"Подтверждение успешной оплаты для User ID: {user_id}, Payment ID: {payment_id}")
            
            # Обновляем статус в Google Sheets
            update_success = False
            if self.sheet:
                try:
                    row_index = self.find_row_by_payment_id(payment_id)
                    if row_index != -1:
                        self.sheet.update_cell(row_index, GS_COL_STATUS, "Оплачено")
                        logger.info(f"Статус в Google Sheets обновлен на 'Оплачено' для строки {row_index}, Payment ID: {payment_id}")
                        update_success = True
                    else:
                        logger.error(f"Строка с Payment ID {payment_id} не найдена в Google Sheets для обновления статуса.")
                except Exception as e:
                    logger.error(f"Ошибка обновления статуса 'Оплачено' в Google Sheets: {e}", exc_info=True)
            else:
                logger.error("Google Sheets не инициализирован для обновления статуса 'Оплачено'")
            
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
                f"💳 <b>Статус:</b> <code>{'Оплачено' if update_success else 'Ошибка обновления статуса'}</code>\n\n"
                "Спасибо за регистрацию! До встречи на игре! 🎊"
            )
            
            await query.edit_message_text(success_text, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"Ошибка в confirm_payment_success для User ID {update.callback_query.from_user.id if update.callback_query and update.callback_query.from_user else 'unknown'}, Payment ID {payment_id}: {e}", exc_info=True)
            try:
                error_text = "⚠️ Ошибка при подтверждении оплаты. Пожалуйста, свяжитесь с администратором."
                # Проверяем, можно ли редактировать сообщение
                if update.callback_query and update.callback_query.message:
                    await update.callback_query.edit_message_text(error_text)
                else:
                    # Если callback_query устарел, отправляем новое сообщение
                    if update.effective_chat:
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=error_text)
            except Exception as inner_e:
                logger.error(f"Ошибка при отправке сообщения об ошибке в confirm_payment_success: {inner_e}")
        finally:
            # Сбрасываем состояние
            context.user_data.clear()
            logger.info(f"Сессия пользователя {user_id} очищена после успешной оплаты.")

    async def confirm_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Подтверждение оплаты (для обратной совместимости)"""
        payment_id = context.user_data.get('payment_id', '')
        if payment_id:
            await self.check_payment_status(update, context, payment_id)
        else:
            try:
                await update.callback_query.answer("⚠️ Ошибка: ID платежа не найден.", show_alert=True)
            except:
                pass

    async def cancel_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отмена оплаты"""
        try:
            query = update.callback_query
            user_id = query.from_user.id
            payment_id = context.user_data.get('payment_id')
            logger.info(f"Отмена оплаты для User ID: {user_id}, Payment ID: {payment_id}")
            
            # Пытаемся обновить статус в таблице на "Отменено"
            if self.sheet and payment_id:
                try:
                    row_index = self.find_row_by_payment_id(payment_id)
                    if row_index != -1:
                        self.sheet.update_cell(row_index, GS_COL_STATUS, "Отменено")
                        logger.info(f"Статус в Google Sheets обновлен на 'Отменено' для строки {row_index}, Payment ID: {payment_id}")
                except Exception as e:
                    logger.error(f"Ошибка обновления статуса 'Отменено' в Google Sheets: {e}", exc_info=True)
            
            await query.edit_message_text("❌ Оплата отменена.\n\nВведите /start для новой регистрации")
        except Exception as e:
            logger.error(f"Ошибка в cancel_payment для User ID {update.callback_query.from_user.id if update.callback_query and update.callback_query.from_user else 'unknown'}: {e}", exc_info=True)
        finally:
            context.user_data.clear()
            logger.info("Сессия пользователя очищена после отмены оплаты.")

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /cancel"""
        try:
            user_id = update.effective_user.id if update.effective_user else 'unknown'
            logger.info(f"Пользователь {user_id} отменил регистрацию (/cancel).")
            context.user_data.clear()
            await update.message.reply_text("❌ Регистрация отменена.\n\nВведите /start для новой регистрации")
        except Exception as e:
            logger.error(f"Ошибка в cancel handler для User ID {update.effective_user.id if update.effective_user else 'unknown'}: {e}", exc_info=True)


# Создаем экземпляр бота
matrix_bot = MatrixBot()

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await matrix_bot.start(update, context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await matrix_bot.button(update, context)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await matrix_bot.handle_message(update, context)

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await matrix_bot.cancel(update, context)

def main():
    """Основная функция для запуска бота"""
    logger.info("Запуск бота...")
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("cancel", cancel_handler))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    logger.info("Обработчики добавлены. Запуск polling...")
    # Запускаем бота
    application.run_polling()

if __name__ == '__main__':
    main()