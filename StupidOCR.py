"""
StupidOCR - 基于 DDDDOCR 的验证码识别服务
提供多种验证码识别接口，支持 Token 认证和管理
"""

import ddddocr
import uvicorn
import base64
import re
import json
import secrets
import sqlite3
import threading
import time
from io import BytesIO
from PIL import Image
from fastapi import FastAPI, HTTPException, Depends, Header, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field, validator
from fastapi.middleware.cors import CORSMiddleware
import os
from datetime import datetime
from typing import Optional, List, Dict

# ==================== 配置 ====================
APP_VERSION = "1.2.0"
APP_DESCRIPTION = """
* 增强版DDDDOCR

* 识别效果完全靠玄学，可能可以识别，可能不能识别。——DDDDOCR

  <img src="https://img.shields.io/badge/GitHub-ffffff"></a> 
  <a href="https://github.com/81NewArk/StupidOCR"> 
  <img src="https://img.shields.io/github/stars/81NewArk/StupidOCR?style=social"> 
  <img src="https://badges.pufler.dev/visits/81NewArk/StupidOCR">
"""

# 环境变量配置
MAX_IMAGE_SIZE = int(os.environ.get("MAX_IMAGE_SIZE", 5 * 1024 * 1024))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "yzm_admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "7jnyxx54")

# 文件路径
BASE_DIR = os.path.dirname(__file__)
TOKEN_DB_PATH = os.environ.get("TOKEN_DB_PATH", os.path.join(BASE_DIR, "tokens.db"))

# 全局对象
app = FastAPI(
    title="StupidOCR",
    description=APP_DESCRIPTION,
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)

# 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 会话管理
admin_sessions = set()

# ==================== OCR 模型初始化 ====================
ocr = ddddocr.DdddOcr(show_ad=False, beta=True)
number_ocr = ddddocr.DdddOcr(show_ad=False, beta=True)
number_ocr.set_ranges(0)
compute_ocr = ddddocr.DdddOcr(show_ad=False, beta=True)
compute_ocr.set_ranges("0123456789+-x÷=")
alphabet_ocr = ddddocr.DdddOcr(show_ad=False, beta=True)
alphabet_ocr.set_ranges(3)
det = ddddocr.DdddOcr(det=True, show_ad=False)
shadow_slide = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)

# ==================== 工具函数 ====================

def safe_eval_arithmetic(expression: str) -> float:
    """
    安全地计算算术表达式，只允许数字和基本运算符
    替换 eval() 以避免代码注入风险
    """
    expression = expression.replace(" ", "")
    
    # 验证字符
    if not re.match(r'^[0-9+\-*/().]+$', expression):
        raise ValueError("表达式包含非法字符")
    
    # 验证括号匹配
    if expression.count('(') != expression.count(')'):
        raise ValueError("括号不匹配")
    
    # 验证表达式格式
    if expression and expression[0] in '*/+':
        raise ValueError("表达式格式错误")
    if expression and expression[-1] in '+-*/':
        raise ValueError("表达式格式错误")
    
    # 使用受限的命名空间执行计算
    try:
        safe_dict = {"__builtins__": {}}
        code = compile(expression, "<string>", "eval")
        result = eval(code, safe_dict)
        
        if not isinstance(result, (int, float)):
            raise ValueError("计算结果不是数字")
        
        return float(result)
    except SyntaxError as e:
        raise ValueError(f"表达式语法错误: {str(e)}")
    except ZeroDivisionError:
        raise ValueError("除零错误")
    except Exception as e:
        raise ValueError(f"表达式计算错误: {str(e)}")


def validate_image_size(img_base64: str, max_size: int = MAX_IMAGE_SIZE) -> bytes:
    """
    验证 base64 图片大小并返回解码后的图片数据
    """
    try:
        img_data = base64.b64decode(img_base64)
        
        if len(img_data) > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"图片大小超过限制，最大允许 {max_size / 1024 / 1024:.2f}MB"
            )
        
        # 验证是否为有效图片
        try:
            img = Image.open(BytesIO(img_data))
            img.verify()
        except Exception:
            raise HTTPException(status_code=400, detail="无效的图片格式")
        
        return img_data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"图片解码失败: {str(e)}")


def extract_text_from_probability(result: Dict) -> str:
    """从概率结果中提取文本"""
    return "".join(result['charsets'][i.index(max(i))] for i in result['probability'])

# ==================== Token 管理 ====================

token_cache: List[Dict] = []
token_value_cache = set()
token_value_map: Dict[str, Dict] = {}
token_cache_lock = threading.Lock()
rate_limit_state: Dict[str, Dict] = {}
rate_limit_lock = threading.Lock()
usage_increment_queue: Dict[str, int] = {}
usage_queue_lock = threading.Lock()
USAGE_FLUSH_INTERVAL = 5  # 秒
usage_flush_thread: Optional[threading.Thread] = None


def get_db_connection() -> sqlite3.Connection:
    """获取 SQLite 连接"""
    conn = sqlite3.connect(TOKEN_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def load_tokens_from_db() -> List[Dict]:
    """从 SQLite 读取所有 Token"""
    conn = get_db_connection()
    cursor = conn.execute("""
        SELECT id, token, name, created_at, updated_at, minute_limit, hour_limit, usage_count
        FROM tokens
        ORDER BY id ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            'id': str(row['id']),
            'token': row['token'],
            'name': row['name'] or f"Token {row['id']}",
            'created_at': row['created_at'] or "",
            'updated_at': row['updated_at'] or row['created_at'] or "",
            'minute_limit': row['minute_limit'],
            'hour_limit': row['hour_limit'],
            'usage_count': row['usage_count'] or 0
        }
        for row in rows
    ]


def refresh_token_cache():
    """刷新 Token 缓存"""
    global token_cache, token_value_cache, token_value_map, rate_limit_state
    tokens = load_tokens_from_db()
    with token_cache_lock:
        token_cache = tokens
        token_value_cache = {t['token'] for t in tokens if t.get('token')}
        token_value_map = {t['token']: t for t in tokens if t.get('token')}
        # 清理已删除 token 的限流状态
        rate_limit_state = {k: v for k, v in rate_limit_state.items() if k in token_value_cache}


def init_db():
    """初始化 SQLite 数据库并加载缓存"""
    db_dir = os.path.dirname(TOKEN_DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            name TEXT,
            created_at TEXT,
            updated_at TEXT,
            minute_limit INTEGER,
            hour_limit INTEGER,
            usage_count INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
    
    refresh_token_cache()
    try:
        os.chmod(TOKEN_DB_PATH, 0o600)
    except Exception:
        pass


def load_tokens() -> List[Dict]:
    """返回缓存中的 Token 列表"""
    with token_cache_lock:
        return [t.copy() for t in token_cache]


def generate_token() -> str:
    """生成新的 token"""
    return secrets.token_urlsafe(32)


def verify_admin_credentials(username: str, password: str) -> bool:
    """验证管理员凭证"""
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD


def create_session() -> str:
    """创建会话"""
    session_id = secrets.token_urlsafe(32)
    admin_sessions.add(session_id)
    return session_id


def verify_session(session_id: Optional[str]) -> bool:
    """验证会话"""
    return session_id is not None and session_id in admin_sessions


def get_token_by_id(token_id: str) -> Optional[Dict]:
    """从缓存获取指定 Token"""
    token_id = str(token_id)
    with token_cache_lock:
        for token in token_cache:
            if token.get('id') == token_id:
                return token.copy()
    return None


def enforce_rate_limit(token_value: str, minute_limit: Optional[int], hour_limit: Optional[int]):
    """
    针对 Token 进行分钟与小时级限流
    - minute_limit: 每分钟最大请求数，None 表示不限
    - hour_limit: 每小时最大请求数，None 表示不限
    """
    now = time.time()
    minute_bucket = int(now // 60)
    hour_bucket = int(now // 3600)
    
    with rate_limit_lock:
        state = rate_limit_state.get(token_value, {
            'minute_bucket': minute_bucket,
            'minute_count': 0,
            'hour_bucket': hour_bucket,
            'hour_count': 0
        })
        
        if state['minute_bucket'] != minute_bucket:
            state['minute_bucket'] = minute_bucket
            state['minute_count'] = 0
        if state['hour_bucket'] != hour_bucket:
            state['hour_bucket'] = hour_bucket
            state['hour_count'] = 0
        
        if minute_limit is not None and state['minute_count'] >= minute_limit:
            raise HTTPException(status_code=429, detail=f"已超过每分钟 {minute_limit} 次的限流")
        if hour_limit is not None and state['hour_count'] >= hour_limit:
            raise HTTPException(status_code=429, detail=f"已超过每小时 {hour_limit} 次的限流")
        
        state['minute_count'] += 1
        state['hour_count'] += 1
        rate_limit_state[token_value] = state


def schedule_usage_increment(token_value: str):
    """记录 Token 调用次数，先更新内存，再批量异步落库"""
    with token_cache_lock:
        token_data = token_value_map.get(token_value)
        if token_data:
            token_data['usage_count'] = (token_data.get('usage_count') or 0) + 1
            for cached in token_cache:
                if cached.get('token') == token_value:
                    cached['usage_count'] = token_data['usage_count']
                    break
    
    with usage_queue_lock:
        usage_increment_queue[token_value] = usage_increment_queue.get(token_value, 0) + 1


def usage_flush_worker():
    """周期性将调用次数增量写入 SQLite"""
    while True:
        time.sleep(USAGE_FLUSH_INTERVAL)
        with usage_queue_lock:
            pending_updates = usage_increment_queue.copy()
            usage_increment_queue.clear()
        
        if not pending_updates:
            continue
        
        conn = get_db_connection()
        for token_value, inc in pending_updates.items():
            conn.execute(
                """
                UPDATE tokens
                SET usage_count = COALESCE(usage_count, 0) + ?
                WHERE token = ?
                """,
                (inc, token_value)
            )
        conn.commit()
        conn.close()
        refresh_token_cache()


def start_usage_flush_worker():
    """启动后台线程，用于异步持久化调用次数"""
    global usage_flush_thread
    if usage_flush_thread and usage_flush_thread.is_alive():
        return
    usage_flush_thread = threading.Thread(target=usage_flush_worker, daemon=True)
    usage_flush_thread.start()


def add_token_record(token_value: str, name: str, minute_limit: Optional[int] = None, hour_limit: Optional[int] = None) -> Dict:
    """新增 Token 记录"""
    now = datetime.now().isoformat()
    conn = get_db_connection()
    cursor = conn.execute(
        """
        INSERT INTO tokens (token, name, created_at, updated_at, minute_limit, hour_limit)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (token_value, name, now, now, minute_limit, hour_limit)
    )
    conn.commit()
    new_id = str(cursor.lastrowid)
    conn.close()
    refresh_token_cache()
    return get_token_by_id(new_id) or {
        'id': new_id,
        'token': token_value,
        'name': name,
        'created_at': now,
        'updated_at': now,
        'usage_count': 0
    }


def update_token_record(
    token_id: str,
    token_value: Optional[str] = None,
    name: Optional[str] = None,
    minute_limit: Optional[int] = None,
    hour_limit: Optional[int] = None
) -> Optional[Dict]:
    """更新 Token 记录"""
    now = datetime.now().isoformat()
    conn = get_db_connection()
    cursor = conn.execute(
        """
        UPDATE tokens
        SET token = ?,
            name = ?,
            minute_limit = ?,
            hour_limit = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (token_value, name, minute_limit, hour_limit, now, token_id)
    )
    conn.commit()
    conn.close()
    if cursor.rowcount == 0:
        return None
    refresh_token_cache()
    return get_token_by_id(str(token_id))


def delete_token_record(token_id: str) -> bool:
    """删除 Token 记录"""
    conn = get_db_connection()
    cursor = conn.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
    conn.commit()
    conn.close()
    if cursor.rowcount > 0:
        refresh_token_cache()
        return True
    return False


def reset_token_usage_count(token_id: str) -> bool:
    """将指定 Token 的使用次数清零"""
    token_data = get_token_by_id(token_id)
    if not token_data:
        return False
    
    with usage_queue_lock:
        usage_increment_queue.pop(token_data.get('token'), None)
    
    conn = get_db_connection()
    cursor = conn.execute("UPDATE tokens SET usage_count = 0 WHERE id = ?", (token_id,))
    conn.commit()
    conn.close()
    if cursor.rowcount > 0:
        refresh_token_cache()
        return True
    return False


async def verify_token(x_token: Optional[str] = Header(None, alias="X-Token")):
    """验证 token 的依赖函数"""
    if not x_token:
        raise HTTPException(status_code=403, detail="缺少 Token，请在请求头中添加 X-Token")
    
    with token_cache_lock:
        cached_tokens = list(token_cache)
        cached_token_values = set(token_value_cache)
        token_config = token_value_map.get(x_token)
    
    if not cached_tokens:
        raise HTTPException(status_code=403, detail="Token 未配置，请先访问管理界面配置 Token")
    
    if x_token not in cached_token_values or not token_config:
        raise HTTPException(status_code=403, detail="Token 验证失败")
    
    enforce_rate_limit(
        x_token,
        token_config.get('minute_limit'),
        token_config.get('hour_limit')
    )
    
    schedule_usage_increment(x_token)
    
    return x_token


# 初始化数据库与缓存
init_db()
start_usage_flush_worker()

# ==================== 数据模型 ====================

class ModelImageIn(BaseModel):
    """单图片输入模型"""
    img_base64: str = Field(..., description="Base64编码的图片数据")
    
    @validator('img_base64')
    def validate_base64(cls, v):
        if not v or len(v) == 0:
            raise ValueError("图片数据不能为空")
        try:
            base64.b64decode(v, validate=True)
        except Exception:
            raise ValueError("无效的 base64 编码")
        return v


class ModelSliderImageIn(BaseModel):
    """滑块图片输入模型"""
    gapimg_base64: str = Field(..., description="Base64编码的缺口图片数据")
    fullimg_base64: str = Field(..., description="Base64编码的完整图片数据")
    
    @validator('gapimg_base64', 'fullimg_base64')
    def validate_base64(cls, v):
        if not v or len(v) == 0:
            raise ValueError("图片数据不能为空")
        try:
            base64.b64decode(v, validate=True)
        except Exception:
            raise ValueError("无效的 base64 编码")
        return v


class LoginModel(BaseModel):
    """登录模型"""
    username: str
    password: str


class TokenConfigModel(BaseModel):
    """Token 配置模型"""
    token: Optional[str] = Field(None, description="Token 值，留空则自动生成")
    name: Optional[str] = Field(None, description="Token 名称")
    minute_limit: Optional[int] = Field(None, description="每分钟限流次数，空为不限")
    hour_limit: Optional[int] = Field(None, description="每小时限流次数，空为不限")
    
    @validator('minute_limit', 'hour_limit', pre=True)
    def validate_limit(cls, v):
        if v in (None, '', 'null'):
            return None
        try:
            v_int = int(v)
        except Exception:
            raise ValueError("限流值必须为整数或留空")
        if v_int <= 0:
            return None
        return v_int


class TokenUpdateModel(BaseModel):
    """Token 更新模型"""
    token_id: str
    token: Optional[str] = Field(None, description="Token 值")
    name: Optional[str] = Field(None, description="Token 名称")
    minute_limit: Optional[int] = Field(None, description="每分钟限流次数，空为不限")
    hour_limit: Optional[int] = Field(None, description="每小时限流次数，空为不限")
    
    @validator('minute_limit', 'hour_limit', pre=True)
    def validate_limit(cls, v):
        if v in (None, '', 'null'):
            return None
        try:
            v_int = int(v)
        except Exception:
            raise ValueError("限流值必须为整数或留空")
        if v_int <= 0:
            return None
        return v_int

# ==================== OCR API 路由 ====================

@app.post("/api/ocr/image", summary="通用", tags=["验证码识别"])
async def ocr_image(data: ModelImageIn, token: str = Depends(verify_token)):
    """通用验证码识别"""
    img = validate_image_size(data.img_base64)
    result = ocr.classification(img)
    return {"result": result}


@app.post("/api/ocr/number", summary="数字", tags=["验证码识别"])
async def ocr_image_number(data: ModelImageIn, token: str = Depends(verify_token)):
    """数字验证码识别"""
    img = validate_image_size(data.img_base64)
    result = number_ocr.classification(img, probability=True)
    string = extract_text_from_probability(result)
    return {"result": string}


@app.post("/api/ocr/compute", summary="算术", tags=["验证码识别"])
async def ocr_image_compute(data: ModelImageIn, token: str = Depends(verify_token)):
    """算术验证码识别"""
    img = validate_image_size(data.img_base64)
    result = compute_ocr.classification(img, probability=True)
    string = extract_text_from_probability(result)
    string = string.split("=")[0].replace("x", "*").replace("÷", "/")
    
    try:
        result = safe_eval_arithmetic(string)
        result = int(result) if result.is_integer() else result
    except ValueError as e:
        result = f"Error: {str(e)}"
    except Exception:
        result = "Error: 计算失败"
    
    return {"result": result}


@app.post("/api/ocr/alphabet", summary="字母", tags=["验证码识别"])
async def ocr_image_alphabet(data: ModelImageIn, token: str = Depends(verify_token)):
    """字母验证码识别"""
    img = validate_image_size(data.img_base64)
    result = alphabet_ocr.classification(img, probability=True)
    string = extract_text_from_probability(result)
    return {"result": string}


@app.post("/api/ocr/detection", summary="文字点选", tags=["验证码识别"])
async def ocr_image_det(data: ModelImageIn, token: str = Depends(verify_token)):
    """文字点选验证码识别"""
    img = validate_image_size(data.img_base64)
    img_pil = Image.open(BytesIO(img))
    res = det.detection(img)
    result = {
        ocr.classification(img_pil.crop(box)): [
            box[0] + (box[2] - box[0]) // 2,
            box[1] + (box[3] - box[1]) // 2
        ]
        for box in res
    }
    return {"result": result}


@app.post("/api/ocr/slider/gap", summary="缺口滑块识别", tags=["验证码识别"])
async def ocr_image_slider_gap(data: ModelSliderImageIn, token: str = Depends(verify_token)):
    """缺口滑块验证码识别"""
    gapimg = validate_image_size(data.gapimg_base64)
    fullimg = validate_image_size(data.fullimg_base64)
    result = det.slide_match(gapimg, fullimg)
    return {"result": result}


@app.post("/api/ocr/slider/shadow", summary="阴影滑块识别", tags=["验证码识别"])
async def ocr_image_slider_shadow(data: ModelSliderImageIn, token: str = Depends(verify_token)):
    """阴影滑块验证码识别"""
    shadowimg = validate_image_size(data.gapimg_base64)
    fullimg = validate_image_size(data.fullimg_base64)
    result = shadow_slide.slide_comparison(shadowimg, fullimg)
    return {"result": result}

# ==================== 管理界面路由 ====================

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    """管理员登录页面"""
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>StupidOCR - 管理员登录</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 20px;
            }
            .container {
                background: white;
                border-radius: 12px;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                padding: 40px;
                max-width: 400px;
                width: 100%;
            }
            h1 {
                color: #333;
                margin-bottom: 10px;
                font-size: 28px;
                text-align: center;
            }
            .subtitle {
                color: #666;
                margin-bottom: 30px;
                font-size: 14px;
                text-align: center;
            }
            .form-group {
                margin-bottom: 20px;
            }
            label {
                display: block;
                margin-bottom: 8px;
                color: #333;
                font-weight: 500;
                font-size: 14px;
            }
            input[type="text"], input[type="password"] {
                width: 100%;
                padding: 12px;
                border: 2px solid #e0e0e0;
                border-radius: 6px;
                font-size: 14px;
                transition: border-color 0.3s;
            }
            input:focus {
                outline: none;
                border-color: #667eea;
            }
            button {
                width: 100%;
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.3s;
                background: #667eea;
                color: white;
            }
            button:hover {
                background: #5568d3;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
            }
            .message {
                margin-top: 20px;
                padding: 12px;
                border-radius: 6px;
                display: none;
            }
            .message.error {
                background: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔐 管理员登录</h1>
            <p class="subtitle">请输入管理员账号和密码</p>
            <form id="loginForm">
                <div class="form-group">
                    <label for="username">账号</label>
                    <input type="text" id="username" name="username" required autofocus>
                </div>
                <div class="form-group">
                    <label for="password">密码</label>
                    <input type="password" id="password" name="password" required>
                </div>
                <button type="submit">登录</button>
            </form>
            <div id="message" class="message"></div>
        </div>
        <script>
            document.getElementById('loginForm').addEventListener('submit', async function(e) {
                e.preventDefault();
                const username = document.getElementById('username').value;
                const password = document.getElementById('password').value;
                const response = await fetch('/api/admin/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: username, password: password })
                });
                const data = await response.json();
                if (response.ok) {
                    window.location.href = '/admin';
                } else {
                    const messageDiv = document.getElementById('message');
                    messageDiv.textContent = '登录失败：' + (data.detail || '账号或密码错误');
                    messageDiv.className = 'message error';
                    messageDiv.style.display = 'block';
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Token 管理界面"""
    session_id = request.cookies.get("admin_session")
    if not verify_session(session_id):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    
    tokens = load_tokens()
    token_count = len(tokens)
    status_class = "configured" if token_count > 0 else "not-configured"
    status_text = f"已配置 {token_count} 个 Token" if token_count > 0 else "未配置"
    
    # 生成 token 列表 HTML
    token_list_html = ""
    def format_limit(value: Optional[int]) -> str:
        return "不限" if value is None else f"{value} 次"
    
    if tokens:
        for token in tokens:
            token_id = token.get('id', '')
            token_name = token.get('name', '未命名 Token')
            token_value = token.get('token', '')
            token_display = token_value[:20] + '...' if len(token_value) > 20 else token_value
            created_at = token.get('created_at', '')
            minute_limit = format_limit(token.get('minute_limit'))
            hour_limit = format_limit(token.get('hour_limit'))
            usage_count = token.get('usage_count', 0)
            token_list_html += f"""
            <tr>
                <td>{token_name}</td>
                <td>
                    <div style="display:flex;align-items:center;gap:8px;">
                        <code style="font-size: 11px;">{token_display}</code>
                        <button class="btn-copy" onclick="copyToken('{token_id}')">复制</button>
                    </div>
                </td>
                <td>{minute_limit}</td>
                <td>{hour_limit}</td>
                <td>{usage_count}</td>
                <td>{created_at[:10] if created_at else '-'}</td>
                <td>
                    <button class="btn-edit" onclick="editToken('{token_id}')">编辑</button>
                    <button class="btn-delete" onclick="deleteToken('{token_id}')">删除</button>
                    <button class="btn-reset" onclick="resetUsage('{token_id}')">清零次数</button>
                </td>
            </tr>
            """
    else:
        token_list_html = '<tr><td colspan="7" style="text-align: center; color: #999;">暂无 Token</td></tr>'
    
    # 读取模板文件
    template_path = os.path.join(os.path.dirname(__file__), "admin_template.html")
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        html_content = html_content.replace('{status_class}', status_class)
        html_content = html_content.replace('{status_text}', status_text)
        html_content = html_content.replace('{token_count}', str(token_count))
        html_content = html_content.replace('{token_list_html}', token_list_html)
    except FileNotFoundError:
        html_content = f"""
        <!DOCTYPE html>
        <html><head><title>Token 管理</title></head>
        <body><h1>Token 管理</h1><p>模板文件未找到，请确保 admin_template.html 存在</p></body></html>
        """
    
    return HTMLResponse(content=html_content)

# ==================== 管理 API 路由 ====================

@app.post("/api/admin/login")
async def admin_login(login_data: LoginModel):
    """管理员登录"""
    if verify_admin_credentials(login_data.username, login_data.password):
        session_id = create_session()
        response = JSONResponse({"success": True, "session_id": session_id})
        response.set_cookie(key="admin_session", value=session_id, httponly=True, max_age=3600*24)
        return response
    else:
        raise HTTPException(status_code=401, detail="账号或密码错误")


@app.post("/api/admin/token")
async def create_token(config: TokenConfigModel, request: Request):
    """创建新 Token"""
    session_id = request.cookies.get("admin_session")
    if not verify_session(session_id):
        raise HTTPException(status_code=401, detail="未授权")
    
    if config.token:
        token_value = config.token.strip()
        if len(token_value) < 16:
            raise HTTPException(status_code=400, detail="Token 长度至少需要 16 个字符")
    else:
        token_value = generate_token()
    
    token_name = config.name or f'Token {len(load_tokens()) + 1}'
    minute_limit = config.minute_limit
    hour_limit = config.hour_limit
    
    try:
        new_token = add_token_record(token_value, token_name, minute_limit, hour_limit)
        return {"success": True, "token": new_token, "message": "Token 已创建"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存 Token 失败: {str(e)}")


@app.put("/api/admin/token")
async def update_token(config: TokenUpdateModel, request: Request):
    """更新 Token"""
    session_id = request.cookies.get("admin_session")
    if not verify_session(session_id):
        raise HTTPException(status_code=401, detail="未授权")
    
    existing_token = get_token_by_id(config.token_id)
    if not existing_token:
        raise HTTPException(status_code=404, detail="Token 不存在")
    
    payload = config.dict(exclude_unset=True)
    
    new_token_value = existing_token.get('token')
    if 'token' in payload and payload.get('token'):
        token_value = payload.get('token').strip()
        if len(token_value) < 16:
            raise HTTPException(status_code=400, detail="Token 长度至少需要 16 个字符")
        new_token_value = token_value
    
    new_name = payload.get('name', existing_token.get('name'))
    new_minute_limit = payload.get('minute_limit') if 'minute_limit' in payload else existing_token.get('minute_limit')
    new_hour_limit = payload.get('hour_limit') if 'hour_limit' in payload else existing_token.get('hour_limit')
    
    updated_token = update_token_record(
        config.token_id,
        new_token_value,
        new_name,
        new_minute_limit,
        new_hour_limit
    )
    
    if not updated_token:
        raise HTTPException(status_code=500, detail="更新 Token 失败")
    
    return {"success": True, "token": updated_token, "message": "Token 已更新"}


@app.delete("/api/admin/token/{token_id}")
async def delete_token(token_id: str, request: Request):
    """删除 Token"""
    session_id = request.cookies.get("admin_session")
    if not verify_session(session_id):
        raise HTTPException(status_code=401, detail="未授权")
    
    if not get_token_by_id(token_id):
        raise HTTPException(status_code=404, detail="Token 不存在")
    
    if delete_token_record(token_id):
        return {"success": True, "message": "Token 已删除"}
    else:
        raise HTTPException(status_code=500, detail="删除 Token 失败")


@app.post("/api/admin/token/{token_id}/reset_usage")
async def reset_token_usage(token_id: str, request: Request):
    """清零指定 Token 的使用次数"""
    session_id = request.cookies.get("admin_session")
    if not verify_session(session_id):
        raise HTTPException(status_code=401, detail="未授权")
    
    if not get_token_by_id(token_id):
        raise HTTPException(status_code=404, detail="Token 不存在")
    
    if reset_token_usage_count(token_id):
        return {"success": True, "message": "使用次数已清零"}
    raise HTTPException(status_code=500, detail="清零失败")


@app.get("/api/admin/tokens")
async def get_tokens(request: Request):
    """获取所有 Token（不返回完整 token 值）"""
    session_id = request.cookies.get("admin_session")
    if not verify_session(session_id):
        raise HTTPException(status_code=401, detail="未授权")
    
    tokens = load_tokens()
    safe_tokens = []
    for token in tokens:
        safe_token = token.copy()
        safe_token['usage_count'] = safe_token.get('usage_count', 0)
        if 'token' in safe_token:
            safe_token['token'] = safe_token['token'][:20] + '...'
        safe_tokens.append(safe_token)
    
    return {"success": True, "tokens": safe_tokens}


@app.get("/api/admin/token/{token_id}")
async def get_token(token_id: str, request: Request):
    """获取单个 Token 的完整信息（用于编辑）"""
    session_id = request.cookies.get("admin_session")
    if not verify_session(session_id):
        raise HTTPException(status_code=401, detail="未授权")
    
    token = get_token_by_id(token_id)
    if not token:
        raise HTTPException(status_code=404, detail="Token 不存在")
    
    return {"success": True, "token": token}


@app.get("/api/admin/token/status")
async def get_token_status():
    """获取 Token 状态（不返回实际 token）"""
    tokens = load_tokens()
    return {
        "configured": len(tokens) > 0,
        "token_count": len(tokens)
    }

# ==================== 启动 ====================

if __name__ == '__main__':
    print(f'''
    StupidOCR v{APP_VERSION}
    软件主页：http://127.0.0.1:6688
    管理界面：http://127.0.0.1:6688/admin
    ''')
    
    workers = int(os.environ.get("UVICORN_WORKERS", 1))
    uvicorn.run(
        "StupidOCR:app",
        host="0.0.0.0",
        port=6688,
        access_log=True,
        workers=workers,
        reload=False
    )
