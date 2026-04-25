from flask import Flask, render_template, request, jsonify, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from google import genai
import os
from datetime import datetime
import json
import re
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from flasgger import Swagger
import requests as http_requests

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-change-this-to-random-string')

swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec",
            "route": "/apispec.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/swagger/"
}

swagger_template = {
    "info": {
        "title": "SoulWay API",
        "description": "API для приложения осмысленного досуга",
        "version": "1.0.0"
    }
}

swagger = Swagger(app, config=swagger_config, template=swagger_template)

# Инициализация Gemini API
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
client = genai.Client(api_key=GEMINI_API_KEY)

DATABASE_URL = os.getenv('DATABASE_URL')

EVENT_FETCHER_URL = os.getenv('EVENT_FETCHER_URL', 'https://event-fetcher-d058.onrender.com')


CITY_NAME_MAP = {
    'Алматы': 'Almaty',
    'Астана': 'Astana',
    'Шымкент': 'Shymkent',
    'Актобе': 'Aktobe',
    'Атырау': 'Atyrau',
    'Павлодар': 'Pavlodar',
    'Усть-Каменогорск': 'Ust-Kamenogorsk',
    'Семей': 'Semey',
    'Тараз': 'Taraz',
    'Костанай': 'Kostanay',
}


def translate_city(city: str) -> str:
    """Переводит название города с русского на английский для API запросов."""
    return CITY_NAME_MAP.get(city, city)


def fetch_city_events(city: str) -> dict:
    """
    Получить реальные события и фильмы по городу через event-fetcher API.
    Возвращает словарь с ключами 'events' и 'movies'.
    """
    result = {'events': [], 'movies': []}
    try:
        events_resp = http_requests.get(
            f'{EVENT_FETCHER_URL}/events/filter',
            params={'city': city, 'type': 'EVENT'},
            timeout=10
        )
        if events_resp.ok:
            result['events'] = events_resp.json()
    except Exception as e:
        print(f'[event-fetcher] events error: {e}')

    try:
        movies_resp = http_requests.get(
            f'{EVENT_FETCHER_URL}/events/filter',
            params={'city': city, 'type': 'MOVIE'},
            timeout=10
        )
        if movies_resp.ok:
            result['movies'] = movies_resp.json()
    except Exception as e:
        print(f'[event-fetcher] movies error: {e}')

    return result


def get_db():
    """Получить соединение с PostgreSQL"""
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def init_db():
    """Инициализация базы данных — создать таблицы если не существуют"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS preferences (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS journal_entries (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            date TEXT NOT NULL,
            text TEXT NOT NULL,
            mood INTEGER NOT NULL,
            tags TEXT NOT NULL,
            analysis TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recommendations_history (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            timestamp TEXT NOT NULL,
            request_data TEXT NOT NULL,
            response_data TEXT NOT NULL
        )
    ''')

    conn.commit()
    cursor.close()
    conn.close()


# Инициализируем БД при старте
init_db()


def login_required(f):
    """Для API-эндпоинтов — возвращает JSON ошибку"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


def login_required_page(f):
    """Для страниц — редирект на логин"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated


def get_user_id():
    return session.get('user_id')


def get_user_data():
    """Получить данные пользователя из БД"""
    user_id = get_user_id()
    if not user_id:
        return {'journal_entries': [], 'preferences': {}, 'history': []}

    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute(
        'SELECT * FROM journal_entries WHERE user_id = %s ORDER BY id ASC',
        (user_id,)
    )
    rows = cursor.fetchall()
    journal_entries = []
    for row in rows:
        journal_entries.append({
            'id': row['id'],
            'date': row['date'],
            'text': row['text'],
            'mood': row['mood'],
            'tags': json.loads(row['tags']),
            'analysis': row['analysis']
        })

    cursor.execute(
        'SELECT data FROM preferences WHERE user_id = %s ORDER BY id DESC LIMIT 1',
        (user_id,)
    )
    pref_row = cursor.fetchone()
    preferences = json.loads(pref_row['data']) if pref_row else {}

    cursor.execute(
        'SELECT * FROM recommendations_history WHERE user_id = %s ORDER BY id ASC',
        (user_id,)
    )
    history_rows = cursor.fetchall()
    history = []
    for row in history_rows:
        history.append({
            'timestamp': row['timestamp'],
            'request': json.loads(row['request_data']),
            'response': row['response_data']
        })

    cursor.close()
    conn.close()

    return {
        'journal_entries': journal_entries,
        'preferences': preferences,
        'history': history
    }


def analyze_with_gemini(prompt):
    """Анализ через Gemini API"""
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"Ошибка API: {str(e)}"


def parse_gemini_json(text):
    """
    Парсит JSON из ответа Gemini.
    Gemini часто оборачивает ответ в ```json ... ```, эта функция это обрабатывает.
    """
    text = text.strip()
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


@app.route('/login')
def login_page():
    return render_template('login.html')


@app.route('/register')
def register_page():
    return render_template('register.html')


@app.route('/auth/register', methods=['POST'])
def register():
    """
    Регистрация пользователя
    ---
    tags:
      - Auth
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required:
            - username
            - password
          properties:
            username:
              type: string
              example: john_doe
            password:
              type: string
              example: secret123
    responses:
      200:
        description: Успешная регистрация
      400:
        description: Ошибка регистрации
    """
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': 'Заполните все поля'})
    if len(password) < 6:
        return jsonify({'error': 'Пароль минимум 6 символов'})

    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cursor.execute(
            'INSERT INTO users (username, password_hash, created_at) VALUES (%s, %s, %s) RETURNING id',
            (username, generate_password_hash(password), datetime.now().isoformat())
        )
        user = cursor.fetchone()
        conn.commit()
        session['username'] = username
        session['user_id'] = user['id']
        cursor.close()
        conn.close()
        return jsonify({'success': True})
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        cursor.close()
        conn.close()
        return jsonify({'error': 'Пользователь уже существует'})


@app.route('/auth/login', methods=['POST'])
def login():
    """
    Вход в аккаунт
    ---
    tags:
      - Auth
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required:
            - username
            - password
          properties:
            username:
              type: string
              example: john_doe
            password:
              type: string
              example: secret123
    responses:
      200:
        description: Успешный вход
      401:
        description: Неверный логин или пароль
    """
    data = request.json
    username = data.get('username', '')
    password = data.get('password', '')

    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'Неверный логин или пароль'})

    session['username'] = username
    session['user_id'] = user['id']
    return jsonify({'success': True})


@app.route('/auth/logout')
def logout():
    session.clear()
    return redirect('/')


@app.route('/')
def index():
    """Главная страница"""
    return render_template('index.html')


@app.route('/journal')
@login_required_page
def journal():
    """Страница дневника"""
    user_data = get_user_data()
    return render_template('journal.html', entries=user_data['journal_entries'])


@app.route('/journal/add', methods=['POST'])
@login_required
def add_journal_entry():
    """
    Добавить запись в дневник
    ---
    tags:
      - Journal
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            text:
              type: string
              example: Сегодня был хороший день
            mood:
              type: integer
              example: 7
            tags:
              type: array
              items:
                type: string
              example: ["работа", "семья"]
    responses:
      200:
        description: Запись добавлена
    """
    data = request.json
    user_id = get_user_id()

    text = data.get('text', '')
    mood = data.get('mood', 5)
    tags = data.get('tags', [])
    date = datetime.now().strftime('%Y-%m-%d %H:%M')

    analysis_prompt = f"""
    Проанализируй следующую дневниковую запись и определи:
    1. Эмоциональное состояние (0-10)
    2. Ключевые темы
    3. Потребности пользователя
    
    Запись: {text}
    Самооценка настроения: {mood}/10
    
    Ответ дай ТОЛЬКО в формате JSON без markdown-обёртки:
    {{
        "emotional_state": 7,
        "themes": ["список тем"],
        "needs": ["список потребностей"],
        "insight": "краткий инсайт"
    }}
    """

    try:
        analysis_raw = analyze_with_gemini(analysis_prompt)
        parsed = parse_gemini_json(analysis_raw)
        analysis = json.dumps(parsed, ensure_ascii=False)
    except Exception:
        analysis = json.dumps({"insight": "Анализ недоступен"}, ensure_ascii=False)

    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute(
        'INSERT INTO journal_entries (user_id, date, text, mood, tags, analysis) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id',
        (user_id, date, text, mood, json.dumps(tags, ensure_ascii=False), analysis)
    )
    row = cursor.fetchone()
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'success': True, 'entry': {
        'id': row['id'], 'date': date, 'text': text,
        'mood': mood, 'tags': tags, 'analysis': analysis
    }})


@app.route('/preferences')
@login_required_page
def preferences():
    """Страница настройки предпочтений"""
    user_data = get_user_data()
    return render_template('preferences.html', preferences=user_data['preferences'])


@app.route('/preferences/save', methods=['POST'])
@login_required
def save_preferences():
    """
    Сохранить предпочтения пользователя
    ---
    tags:
      - Preferences
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            interests:
              type: array
              items:
                type: string
              example: ["кино", "книги"]
            budget:
              type: string
              example: medium
            activity_level:
              type: string
              example: moderate
            social_preference:
              type: string
              example: mixed
    responses:
      200:
        description: Предпочтения сохранены
    """
    data = request.json
    user_id = get_user_id()

    prefs = {
        'interests': data.get('interests', []),
        'budget': data.get('budget', 'medium'),
        'activity_level': data.get('activity_level', 'moderate'),
        'social_preference': data.get('social_preference', 'mixed'),
        'favorite_genres': data.get('favorite_genres', []),
        'avoid': data.get('avoid', [])
    }

    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute('SELECT id FROM preferences WHERE user_id = %s', (user_id,))
    existing = cursor.fetchone()

    if existing:
        cursor.execute(
            'UPDATE preferences SET data = %s, updated_at = %s WHERE user_id = %s',
            (json.dumps(prefs, ensure_ascii=False), datetime.now().isoformat(), user_id)
        )
    else:
        cursor.execute(
            'INSERT INTO preferences (user_id, data, updated_at) VALUES (%s, %s, %s)',
            (user_id, json.dumps(prefs, ensure_ascii=False), datetime.now().isoformat())
        )

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'success': True})


@app.route('/preferences/save-address', methods=['POST'])
@login_required
def save_address():
    """Сохранить домашний адрес пользователя (merge с текущими preferences)"""
    data = request.json
    user_id = get_user_id()

    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Читаем текущие preferences
    cursor.execute('SELECT data FROM preferences WHERE user_id = %s ORDER BY id DESC LIMIT 1', (user_id,))
    row = cursor.fetchone()
    current_prefs = json.loads(row['data']) if row else {}

    # Обновляем только home_address
    current_prefs['home_address'] = {
        'name': data.get('name', ''),
        'lat': data.get('lat'),
        'lng': data.get('lng')
    }

    if row:
        cursor.execute(
            'UPDATE preferences SET data = %s, updated_at = %s WHERE user_id = %s',
            (json.dumps(current_prefs, ensure_ascii=False), datetime.now().isoformat(), user_id)
        )
    else:
        cursor.execute(
            'INSERT INTO preferences (user_id, data, updated_at) VALUES (%s, %s, %s)',
            (user_id, json.dumps(current_prefs, ensure_ascii=False), datetime.now().isoformat())
        )

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/recommendations')
@login_required_page
def recommendations():
    """Страница рекомендаций"""
    return render_template('recommendations.html')


def build_recommendations_prompt(data, user_data, mode='full'):
    """
    Строит промпт для рекомендаций.
    mode='quick'  — короткий промпт, только названия + why_now (быстрый первый ответ)
    mode='full'   — полный промпт с деталями, кино, картой
    """
    recent_entries = user_data['journal_entries'][-5:] if user_data['journal_entries'] else []
    prefs = user_data['preferences']
    activity_type = data.get('type', 'any')
    home_address = prefs.get('home_address') or {}
    home_addr_name = home_address.get('name', '')
    city_from_address = home_addr_name.split(',')[0].strip() if home_addr_name else ''
    city = data.get('city') or city_from_address or 'Алматы'

    recent_entries_short = [{'text': e['text'], 'mood': e['mood']} for e in recent_entries]

    travel_instruction = ""
    if activity_type == 'travel':
        budget_val = data.get('budget', prefs.get('budget', 'medium'))
        travel_instruction = f"""
ВАЖНО — РЕЖИМ ПУТЕШЕСТВИЙ:
Пользователь ищет направления для поездки ЗА РУБЕЖ или по СНГ. Город проживания: {city}.
Бюджет: {budget_val}. Предлагай конкретные страны и города назначения (НЕ активности внутри {city}).
Для каждого направления укажи: страну, город, почему подходит, что делать, примерный бюджет перелёта+отеля.
"""

    context = f"""
КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:
Последние записи в дневнике:
{json.dumps(recent_entries_short, ensure_ascii=False)}
Предпочтения: {json.dumps(prefs, ensure_ascii=False)}
Текущий запрос: {data.get('context', 'общие рекомендации')}
Тип активности: {data.get('type', 'любой')}
Бюджет: {data.get('budget', prefs.get('budget', 'medium'))}
Город проживания: {city}
{travel_instruction}"""

    # FITNESS инструкция — всегда добавляем если тип any/fitness
    fitness_instruction = ""
    if activity_type in ['any', 'fitness']:
        fitness_instruction = """
ФИТНЕС-АКТИВНОСТИ: Обязательно включи 1-2 фитнес-рекомендации с полем "fitness_info":
{
    "activity": "название активности (йога/бег/плавание/зал/танцы/велосипед/etc)",
    "level": "уровень (начинающий/средний/продвинутый)",
    "duration_min": 45,
    "calories": "примерный расход калорий",
    "equipment": "необходимое оборудование или место",
    "why_fits_mood": "почему эта активность подходит текущему настроению"
}
"""

    if mode == 'quick':
        quick_role = (
            "Ты - SoulWay, ассистент для подбора путешествий. "
            "Пользователь из Казахстана ищет международные направления — предлагай страны и города по всему миру."
            if activity_type == 'travel'
            else "Ты - SoulWay, ассистент для подбора досуга в Казахстане."
        )
        return f"""
{quick_role}
{context}

Быстро подбери 5-6 идей для досуга. Ответ ТОЛЬКО в JSON без markdown:
{{
    "recommendations": [
        {{
            "title": "название",
            "type": "тип",
            "why_now": "одно предложение — почему сейчас",
            "benefit": "польза",
            "budget": "бюджет",
            "duration": "время"
        }}
    ],
    "overall_insight": "одно предложение об общем состоянии"
}}
"""

    # FULL mode — собираем полный промпт
    real_events_instruction = ""
    if activity_type in ['any', 'city']:
        city_data = fetch_city_events(translate_city(city))
        events = city_data['events'][:10]
        movies = city_data['movies'][:10]

        if events:
            events_json = json.dumps(
                [{'title': e.get('title'), 'category': e.get('category'),
                  'description': e.get('description'), 'link': e.get('link')}
                 for e in events],
                ensure_ascii=False, indent=2
            )
            real_events_instruction += f"""
РЕАЛЬНЫЕ СОБЫТИЯ В ГОРОДЕ {city} (данные от sxodim/kino.kz):
{events_json}
При рекомендации события — используй реальное название, описание и ссылку. Добавь "event_link".
"""
        if movies:
            movies_json = json.dumps(
                [{'title': e.get('title'), 'genre': e.get('genre'),
                  'rating': e.get('rating'), 'description': e.get('description'),
                  'link': e.get('link')}
                 for e in movies],
                ensure_ascii=False, indent=2
            )
            real_events_instruction += f"""
РЕАЛЬНЫЕ ФИЛЬМЫ В ПРОКАТЕ В {city}:
{movies_json}
При рекомендации кино — используй реальные данные. Добавь "cinema_info" с "shodim_url".
"""

    home_addr_instruction = ""
    if home_address and home_address.get('lat'):
        home_addr_instruction = f"""
ДОМАШНИЙ АДРЕС (начальная точка маршрута):
Название: {home_address.get('name', '')}
Координаты: lat={home_address.get('lat')}, lng={home_address.get('lng')}
"""

    cinema_instruction = ""
    if activity_type in ['any', 'city']:
        cinema_instruction = """
Если рекомендуешь кино/сериал, добавь "cinema_info":
{
    "title_ru": "название на русском", "title_en": "английское название",
    "year": 2024, "genre": "жанр", "rating_imdb": "7.5", "duration_min": 120,
    "kinopoisk_url": "https://www.kinopoisk.ru/film/XXXXX/",
    "shodim_url": "https://shodim.kz/films/XXXXX/",
    "description": "краткое описание", "why_fits": "почему подходит"
}
"""

    map_instruction = ""
    if activity_type in ['city', 'travel']:
        map_instruction = """
Для каждой рекомендации с конкретным местом добавь "map_points" с реальными координатами:
[{"name":"место","address":"адрес","lat":0.0,"lng":0.0,"order":1,"tip":"совет"}]
Для международных направлений используй координаты страны/города назначения.
Нумерацию order начинай с 1 — стартовая точка будет добавлена автоматически.
"""

    assistant_role = (
        "Ты - SoulWay, интеллектуальный ассистент для подбора осмысленного досуга. "
        "Пользователь находится в Казахстане и ищет международные направления для путешествий — "
        "предлагай страны и города по всему миру, соответствующие бюджету и настроению."
        if activity_type == 'travel'
        else "Ты - SoulWay, интеллектуальный ассистент для подбора осмысленного досуга в Казахстане."
    )

    return f"""
{assistant_role}
{context}
{home_addr_instruction}
{real_events_instruction}
{fitness_instruction}

Подбери 5-7 персонализированных рекомендаций. Включи реальные события/фильмы если есть.
{cinema_instruction}
{map_instruction}

Ответ ТОЛЬКО в JSON без markdown:
{{
    "recommendations": [
        {{
            "title": "название",
            "type": "тип (кино/прогулка/путешествие/книга/музыка/фитнес/etc)",
            "why_now": "почему подходит именно сейчас",
            "benefit": "польза для эмоций",
            "budget": "бюджет",
            "duration": "продолжительность",
            "details": "детали и конкретные советы",
            "event_link": null,
            "cinema_info": null,
            "fitness_info": null,
            "map_points": null
        }}
    ],
    "overall_insight": "общий инсайт о текущем состоянии и потребностях"
}}
"""


def inject_home_point(parsed, home_address):
    """
    Программно вставляет домашнюю точку (order:0) в map_points каждой рекомендации.
    Не доверяем Gemini координаты дома — берём из сохранённого адреса пользователя.
    """
    if not home_address or not home_address.get('lat') or not home_address.get('lng'):
        return parsed
    if not isinstance(parsed, dict) or 'recommendations' not in parsed:
        return parsed

    home_point = {
        'name': '🏠 Мой дом',
        'address': home_address.get('name', ''),
        'lat': float(home_address.get('lat')),
        'lng': float(home_address.get('lng')),
        'order': 0,
        'tip': 'Начало маршрута'
    }

    for rec in parsed.get('recommendations', []):
        points = rec.get('map_points')
        if not points or not isinstance(points, list) or len(points) == 0:
            continue
        # Удаляем любые точки которые Gemini добавил с order:0 или именем дома
        points = [p for p in points if not (
            p.get('order') == 0 or
            ('дом' in str(p.get('name', '')).lower()) or
            ('home' in str(p.get('name', '')).lower())
        )]
        # Пересчитываем order
        for i, p in enumerate(points):
            p['order'] = i + 1
        # Вставляем правильную домашнюю точку в начало
        rec['map_points'] = [home_point] + points

    return parsed


@app.route('/recommendations/get', methods=['POST'])
@login_required
def get_recommendations():
    """
    Получить персональные рекомендации (быстрый первый ответ)
    ---
    tags:
      - Recommendations
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            context:
              type: string
              example: устал от работы, хочу отдохнуть
            type:
              type: string
              example: home
            budget:
              type: string
              example: medium
            city:
              type: string
              example: Алматы
    responses:
      200:
        description: Быстрый список рекомендаций от AI
    """
    data = request.json
    user_data = get_user_data()
    user_id = get_user_id()

    # Быстрый запрос — только названия и краткие описания
    quick_prompt = build_recommendations_prompt(data, user_data, mode='quick')
    result_raw = analyze_with_gemini(quick_prompt)

    try:
        parsed = parse_gemini_json(result_raw)
        parsed = inject_home_point(parsed, user_data.get('preferences', {}).get('home_address'))
        result_str = json.dumps(parsed, ensure_ascii=False)
    except Exception:
        result_str = result_raw

    # Сохраняем быстрый результат в историю
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO recommendations_history (user_id, timestamp, request_data, response_data) VALUES (%s, %s, %s, %s)',
        (user_id, datetime.now().isoformat(), json.dumps(data, ensure_ascii=False), result_str)
    )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'success': True, 'recommendations': result_str})


@app.route('/recommendations/details', methods=['POST'])
@login_required
def get_recommendations_details():
    """
    Получить детальные рекомендации (второй фоновый запрос)
    ---
    tags:
      - Recommendations
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            context:
              type: string
            type:
              type: string
            budget:
              type: string
            city:
              type: string
    responses:
      200:
        description: Детальные рекомендации с картой, кино, фитнесом
    """
    data = request.json
    user_data = get_user_data()
    user_id = get_user_id()

    full_prompt = build_recommendations_prompt(data, user_data, mode='full')
    result_raw = analyze_with_gemini(full_prompt)

    try:
        parsed = parse_gemini_json(result_raw)
        parsed = inject_home_point(parsed, user_data.get('preferences', {}).get('home_address'))
        result_str = json.dumps(parsed, ensure_ascii=False)
    except Exception:
        result_str = result_raw

    return jsonify({'success': True, 'recommendations': result_str})


@app.route('/profile')
@login_required_page
def profile():
    """Страница профиля и аналитики"""
    user_data = get_user_data()

    safe_user_data = {
        'journal_entries': user_data.get('journal_entries', []),
        'preferences': user_data.get('preferences', {}),
        'history': user_data.get('history', [])
    }

    return render_template('profile.html', user_data=safe_user_data)


@app.route('/analyze/emotional-dynamics')
@login_required
def analyze_emotional_dynamics():
    """
    Анализ эмоциональной динамики
    ---
    tags:
      - Analytics
    responses:
      200:
        description: Результат анализа эмоций
      400:
        description: Недостаточно данных
    """
    user_data = get_user_data()
    entries = user_data['journal_entries']

    if not entries:
        return jsonify({'error': 'Недостаточно данных для анализа. Начните вести дневник!'})

    prompt = f"""
    Проанализируй эмоциональную динамику пользователя за период.
    
    Записи ({len(entries)} записей):
    {json.dumps([{'date': e['date'], 'mood': e['mood'], 'text': e['text'][:100]} for e in entries], ensure_ascii=False, indent=2)}
    
    Определи:
    1. Тренды настроения (улучшается, ухудшается, стабильно)
    2. Триггеры позитивных и негативных состояний
    3. Паттерны поведения
    4. Рекомендации для улучшения баланса
    
    Ответ дай ТОЛЬКО в формате JSON без markdown-обёртки:
    {{
        "trend": "описание тренда",
        "average_mood": 7.5,
        "positive_triggers": ["список"],
        "negative_triggers": ["список"],
        "patterns": ["список паттернов"],
        "recommendations": ["рекомендации"],
        "summary": "общий вывод"
    }}
    """

    analysis_raw = analyze_with_gemini(prompt)

    try:
        parsed = parse_gemini_json(analysis_raw)
        return jsonify({'success': True, 'analysis': parsed})
    except Exception:
        return jsonify({'success': True, 'analysis': {'raw': analysis_raw}})


@app.route('/travel/suggest', methods=['POST'])
@login_required
def suggest_travel():
    """
    Подобрать направление для путешествия
    ---
    tags:
      - Travel
    """
    data = request.json
    user_data = get_user_data()

    prompt = f"""
    Пользователь ищет направление для путешествия.
    
    Контекст:
    - Эмоциональное состояние: {data.get('mood', 'нейтральное')}
    - Предпочтения: {json.dumps(user_data['preferences'], ensure_ascii=False)}
    - Бюджет: {data.get('budget', 'средний')}
    - Длительность: {data.get('duration', '3-5 дней')}
    - Особые запросы: {data.get('special_requests', 'нет')}
    
    Подбери 3-5 направлений. Ответ ТОЛЬКО в формате JSON без markdown-обёртки:
    {{
        "destinations": [
            {{
                "name": "название",
                "country": "страна",
                "why": "психологическое обоснование",
                "activities": ["активности"],
                "route": ["день 1: ...", "день 2: ..."],
                "budget": "бюджет",
                "best_season": "сезон"
            }}
        ]
    }}
    """

    suggestions_raw = analyze_with_gemini(prompt)
    return jsonify({'success': True, 'suggestions': suggestions_raw})


@app.route('/chat')
@login_required_page
def chat_page():
    """Страница чата с ИИ-ассистентом"""
    user_data = get_user_data()
    safe_user_data = {
        'journal_entries': user_data.get('journal_entries', []),
        'preferences': user_data.get('preferences', {}),
        'history': user_data.get('history', [])
    }
    return render_template('chat.html', user_data=safe_user_data)


@app.route('/chat/send', methods=['POST'])
@login_required
def chat_send():
    """
    Отправить сообщение ИИ-ассистенту
    ---
    tags:
      - Chat
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            message:
              type: string
              example: Мне грустно, что посоветуешь?
            history:
              type: array
              items:
                type: object
    responses:
      200:
        description: Ответ ассистента
    """
    data = request.json
    user_message = data.get('message', '').strip()
    history = data.get('history', [])

    if not user_message:
        return jsonify({'error': 'Пустое сообщение'}), 400

    user_data = get_user_data()
    entries = user_data.get('journal_entries', [])
    prefs = user_data.get('preferences', {})

    # Контекст о пользователе
    recent_entries_summary = ''
    if entries:
        recent = entries[-5:]
        recent_entries_summary = '\n'.join([
            f"- {e['date'][:10]}: настроение {e['mood']}/10, {e['text'][:120]}..."
            for e in recent
        ])

    prefs_summary = ''
    if prefs:
        parts = []
        if prefs.get('interests'):
            parts.append(f"интересы: {', '.join(prefs['interests'])}")
        if prefs.get('budget'):
            budget_map = {'low': 'ограниченный', 'medium': 'средний', 'high': 'комфортный'}
            parts.append(f"бюджет: {budget_map.get(prefs['budget'], prefs['budget'])}")
        if prefs.get('activity_level'):
            act_map = {'low': 'спокойный', 'moderate': 'умеренный', 'high': 'активный'}
            parts.append(f"активность: {act_map.get(prefs['activity_level'], prefs['activity_level'])}")
        if prefs.get('favorite_genres'):
            parts.append(f"жанры: {', '.join(prefs['favorite_genres'])}")
        if prefs.get('avoid'):
            parts.append(f"избегает: {', '.join(prefs['avoid'])}")
        prefs_summary = '; '.join(parts)

    city = ''
    if prefs.get('home_address', {}).get('name'):
        city = prefs['home_address']['name'].split(',')[0].strip()

    system_prompt = f"""Ты — SoulWay, дружелюбный и эмпатичный ИИ-ассистент для осмысленного досуга.
Ты помогаешь пользователю найти баланс, подобрать активности и понять своё эмоциональное состояние.

ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:
Город: {city or 'не указан'}
Предпочтения: {prefs_summary or 'не заданы'}

ПОСЛЕДНИЕ ЗАПИСИ В ДНЕВНИКЕ:
{recent_entries_summary or 'Дневник пуст.'}

ПРАВИЛА:
- Отвечай на русском языке
- Будь конкретным и полезным, не общим
- Учитывай эмоциональное состояние из дневника
- Предлагай реальные активности с учётом города и бюджета пользователя
- Если пользователь грустит или тревожится — сначала прояви эмпатию
- Используй emoji умеренно для тепла
- Отвечай кратко (2-4 абзаца), но содержательно
- Не нужно каждый раз упоминать что ты ИИ"""

    # Строим историю для Gemini
    conversation_parts = []
    for msg in history[-8:]:
        role = msg.get('role', 'user')
        content = msg.get('content', '')
        if role == 'user':
            conversation_parts.append(f"Пользователь: {content}")
        elif role == 'assistant':
            conversation_parts.append(f"Ассистент: {content}")

    full_prompt = system_prompt + "\n\n" + "\n".join(conversation_parts) + f"\nПользователь: {user_message}\nАссистент:"

    try:
        reply = analyze_with_gemini(full_prompt)
        # Убираем префикс "Ассистент:" если Gemini его добавил
        reply = reply.strip()
        if reply.startswith('Ассистент:'):
            reply = reply[len('Ассистент:'):].strip()
        return jsonify({'success': True, 'reply': reply})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.errorhandler(404)
def not_found(e):
    return render_template('index.html'), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Внутренняя ошибка сервера'}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)