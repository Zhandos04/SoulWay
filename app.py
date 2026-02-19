from flask import Flask, render_template, request, jsonify, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from google import genai
import os
from datetime import datetime
import json
from flasgger import Swagger

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-to-random-string'

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
GEMINI_API_KEY = 'AIzaSyBq1H9EawUD5ZH-cvOn7OnrQCzGx90qpMo'
client = genai.Client(api_key=GEMINI_API_KEY)

# Хранилище данных (в продакшене использовать БД)
users_data = {}
registered_users = {}  # {username: {password_hash, user_id}}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


def get_user_id():
    return session.get('user_id', 'anonymous')


def get_user_data():
    """Получить данные пользователя"""
    user_id = get_user_id()
    if user_id not in users_data:
        users_data[user_id] = {
            'journal_entries': [],
            'preferences': {},
            'profile': {},
            'history': []
        }
    return users_data[user_id]


def analyze_with_gemini(prompt):
    """Анализ через Gemini API"""
    try:
        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"Ошибка API: {str(e)}"

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
        schema:
          type: object
          properties:
            success:
              type: boolean
      400:
        description: Ошибка регистрации
        schema:
          type: object
          properties:
            error:
              type: string
    """
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'error': 'Заполните все поля'})
    if username in registered_users:
        return jsonify({'error': 'Пользователь уже существует'})
    if len(password) < 6:
        return jsonify({'error': 'Пароль минимум 6 символов'})
    
    user_id = f"user_{username}"
    registered_users[username] = {
        'password_hash': generate_password_hash(password),
        'user_id': user_id
    }
    session['username'] = username
    session['user_id'] = user_id
    return jsonify({'success': True})

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
    
    user = registered_users.get(username)
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'Неверный логин или пароль'})
    
    session['username'] = username
    session['user_id'] = user['user_id']
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
def journal():
    """Страница дневника"""
    user_data = get_user_data()
    return render_template('journal.html', entries=user_data['journal_entries'])


@app.route('/journal/add', methods=['POST'])
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
    """Добавить запись в дневник"""
    data = request.json
    user_data = get_user_data()
    
    entry = {
        'id': len(user_data['journal_entries']) + 1,
        'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'text': data.get('text', ''),
        'mood': data.get('mood', 5),
        'tags': data.get('tags', [])
    }
    
    user_data['journal_entries'].append(entry)
    
    # Анализ эмоционального состояния
    analysis_prompt = f"""
    Проанализируй следующую дневниковую запись и определи:
    1. Эмоциональное состояние (0-10)
    2. Ключевые темы
    3. Потребности пользователя
    
    Запись: {entry['text']}
    Самооценка настроения: {entry['mood']}/10
    
    Ответ дай в формате JSON:
    {{
        "emotional_state": число,
        "themes": [список тем],
        "needs": [список потребностей],
        "insight": "краткий инсайт"
    }}
    """
    
    try:
        analysis = analyze_with_gemini(analysis_prompt)
        entry['analysis'] = analysis
    except:
        entry['analysis'] = "Анализ недоступен"
    
    return jsonify({'success': True, 'entry': entry})


@app.route('/preferences')
def preferences():
    """Страница настройки предпочтений"""
    user_data = get_user_data()
    return render_template('preferences.html', preferences=user_data['preferences'])


@app.route('/preferences/save', methods=['POST'])
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
    """Сохранить предпочтения"""
    data = request.json
    user_data = get_user_data()
    
    user_data['preferences'] = {
        'interests': data.get('interests', []),
        'budget': data.get('budget', 'medium'),
        'activity_level': data.get('activity_level', 'moderate'),
        'social_preference': data.get('social_preference', 'mixed'),
        'favorite_genres': data.get('favorite_genres', []),
        'avoid': data.get('avoid', [])
    }
    
    return jsonify({'success': True})


@app.route('/recommendations')
def recommendations():
    """Страница рекомендаций"""
    return render_template('recommendations.html')


@app.route('/recommendations/get', methods=['POST'])
def get_recommendations():
    """
    Получить персональные рекомендации
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
    responses:
      200:
        description: Список рекомендаций от AI
    """
    """Получить персональные рекомендации"""
    data = request.json
    user_data = get_user_data()
    
    # Собираем контекст пользователя
    recent_entries = user_data['journal_entries'][-5:] if user_data['journal_entries'] else []
    preferences = user_data['preferences']
    
    context = f"""
    КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:
    
    Последние записи в дневнике:
    {json.dumps([{'text': e['text'], 'mood': e['mood']} for e in recent_entries], ensure_ascii=False, indent=2)}
    
    Предпочтения:
    {json.dumps(preferences, ensure_ascii=False, indent=2)}
    
    Текущий запрос: {data.get('context', 'общие рекомендации')}
    Тип активности: {data.get('type', 'любой')}
    Бюджет: {data.get('budget', preferences.get('budget', 'medium'))}
    """
    
    prompt = f"""
    Ты - SoulWay, интеллектуальный ассистент для подбора осмысленного досуга.
    
    {context}
    
    На основе эмоционального состояния, предпочтений и контекста пользователя,
    подбери 5-7 персонализированных рекомендаций для досуга.
    
    Включи:
    - Домашние активности (фильмы, книги, игры, творчество)
    - Городские мероприятия (если актуально)
    - Краткосрочные поездки (если настроение и контекст подходят)
    
    Для каждой рекомендации укажи:
    1. Название
    2. Тип активности
    3. Почему это подходит ИМЕННО СЕЙЧАС
    4. Как это поможет эмоциональному балансу
    5. Примерный бюджет
    6. Продолжительность
    
    Ответ дай в формате JSON:
    {{
        "recommendations": [
            {{
                "title": "название",
                "type": "тип",
                "why_now": "почему подходит",
                "benefit": "польза для эмоций",
                "budget": "бюджет",
                "duration": "время",
                "details": "детали"
            }}
        ],
        "overall_insight": "общий инсайт о текущем состоянии и потребностях"
    }}
    """
    
    result = analyze_with_gemini(prompt)
    
    # Сохраняем в историю
    user_data['history'].append({
        'timestamp': datetime.now().isoformat(),
        'request': data,
        'response': result
    })
    
    return jsonify({'success': True, 'recommendations': result})


@app.route('/profile')
def profile():
    """Страница профиля и аналитики"""
    user_data = get_user_data()
    
    # Преобразуем данные в JSON-безопасный формат для шаблона
    safe_user_data = {
        'journal_entries': user_data.get('journal_entries', []),
        'preferences': user_data.get('preferences', {}),
        'history': user_data.get('history', [])
    }
    
    return render_template('profile.html', user_data=safe_user_data)


@app.route('/analyze/emotional-dynamics')
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
    """Анализ эмоциональной динамики"""
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
    
    Ответ дай в формате JSON:
    {{
        "trend": "описание тренда",
        "average_mood": число,
        "positive_triggers": ["список"],
        "negative_triggers": ["список"],
        "patterns": ["список паттернов"],
        "recommendations": ["рекомендации"],
        "summary": "общий вывод"
    }}
    """
    
    analysis = analyze_with_gemini(prompt)
    
    try:
        # Пытаемся распарсить JSON
        parsed = json.loads(analysis)
        return jsonify({'success': True, 'analysis': parsed})
    except:
        # Если не JSON, возвращаем как есть
        return jsonify({'success': True, 'analysis': {'raw': analysis}})


@app.route('/travel/suggest', methods=['POST'])
def suggest_travel():
    """
    Подобрать направление для путешествия
    ---
    tags:
      - Travel
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            mood:
              type: string
              example: спокойное
            budget:
              type: string
              example: средний
            duration:
              type: string
              example: 3-5 дней
    responses:
      200:
        description: Список направлений
    """
    """Предложить направления для путешествий"""
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
    
    Подбери 3-5 направлений, которые:
    1. Соответствуют текущему эмоциональному состоянию
    2. Помогут достичь нужного баланса
    3. Будут способствовать самопознанию
    
    Для каждого направления дай:
    - Название и страну
    - Почему это подходит психологически
    - Ключевые активности
    - Маршрут на 3-5 дней
    - Примерный бюджет
    - Сезонность
    
    Ответ в формате JSON:
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
    
    suggestions = analyze_with_gemini(prompt)
    return jsonify({'success': True, 'suggestions': suggestions})


# Обработчик ошибок
@app.errorhandler(404)
def not_found(e):
    return render_template('index.html'), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Внутренняя ошибка сервера'}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)