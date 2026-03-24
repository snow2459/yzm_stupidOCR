"""
StupidOCR - 基于 DDDDOCR 的验证码识别服务
提供多种验证码识别接口，支持 Token 认证和管理
"""

import os
import gc
import base64
import binascii
from contextlib import contextmanager
import re
import secrets
import sqlite3
import threading
import time
import hashlib
import hmac
from datetime import datetime
from io import BytesIO
from typing import Callable, Dict, Generator, List, Optional

# 小机器默认限制底层推理库的线程与内存碎片，用户可通过环境变量覆盖
DEFAULT_OCR_CPU_THREADS = os.environ.get("OCR_CPU_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", DEFAULT_OCR_CPU_THREADS)
os.environ.setdefault("OPENBLAS_NUM_THREADS", DEFAULT_OCR_CPU_THREADS)
os.environ.setdefault("MKL_NUM_THREADS", DEFAULT_OCR_CPU_THREADS)
os.environ.setdefault("NUMEXPR_NUM_THREADS", DEFAULT_OCR_CPU_THREADS)
os.environ.setdefault("MALLOC_ARENA_MAX", "2")
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")

import ddddocr
import uvicorn
from PIL import Image
from fastapi import FastAPI, HTTPException, Depends, Header, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field, validator
from fastapi.middleware.cors import CORSMiddleware

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
OCR_MODEL_IDLE_SECONDS = max(int(os.environ.get("OCR_MODEL_IDLE_SECONDS", "300")), 0)
UVICORN_ACCESS_LOG = str(os.environ.get("UVICORN_ACCESS_LOG", "1")).lower() in {"1", "true", "yes", "on"}

# 文件路径
BASE_DIR = os.path.dirname(__file__)
TOKEN_DB_PATH = os.environ.get("TOKEN_DB_PATH", os.path.join(BASE_DIR, "tokens.db"))
ADMIN_TEMPLATE_PATH = os.path.join(BASE_DIR, "admin_template.html")
try:
    with open(ADMIN_TEMPLATE_PATH, "r", encoding="utf-8") as template_file:
        ADMIN_TEMPLATE_HTML = template_file.read()
except FileNotFoundError:
    ADMIN_TEMPLATE_HTML = ""

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
login_nonces: Dict[str, float] = {}
login_nonces_lock = threading.Lock()
LOGIN_NONCE_TTL_SECONDS = 60

# ==================== OCR 模型初始化 ====================
def build_common_ocr() -> ddddocr.DdddOcr:
    return ddddocr.DdddOcr(show_ad=False, beta=True)


def build_number_ocr() -> ddddocr.DdddOcr:
    model = ddddocr.DdddOcr(show_ad=False, beta=True)
    model.set_ranges(0)
    return model


def build_compute_ocr() -> ddddocr.DdddOcr:
    model = ddddocr.DdddOcr(show_ad=False, beta=True)
    model.set_ranges("0123456789+-x÷=")
    return model


def build_alphabet_ocr() -> ddddocr.DdddOcr:
    model = ddddocr.DdddOcr(show_ad=False, beta=True)
    model.set_ranges(3)
    return model


def build_det_ocr() -> ddddocr.DdddOcr:
    return ddddocr.DdddOcr(det=True, show_ad=False)


def build_shadow_slide_ocr() -> ddddocr.DdddOcr:
    return ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)


OCR_MODEL_BUILDERS: Dict[str, Callable[[], ddddocr.DdddOcr]] = {
    "common": build_common_ocr,
    "number": build_number_ocr,
    "compute": build_compute_ocr,
    "alphabet": build_alphabet_ocr,
    "det": build_det_ocr,
    "shadow_slide": build_shadow_slide_ocr,
}
OCR_PERSISTENT_MODELS = {"common"}


class OCRModelManager:
    """按需加载 OCR 模型，并在空闲后自动释放"""

    def __init__(self, idle_seconds: int, persistent_models: Optional[set] = None):
        self.idle_seconds = idle_seconds
        self.persistent_models = persistent_models or set()
        self.instances: Dict[str, ddddocr.DdddOcr] = {}
        self.last_used_at: Dict[str, float] = {}
        self.active_counts: Dict[str, int] = {}
        self.model_locks: Dict[str, threading.Lock] = {}
        self.manager_lock = threading.Lock()
        self.cleanup_thread: Optional[threading.Thread] = None

    def preload(self, model_name: str):
        with self.manager_lock:
            if model_name in self.instances:
                self.last_used_at[model_name] = time.time()
                return
            builder = OCR_MODEL_BUILDERS[model_name]
            self.instances[model_name] = builder()
            self.last_used_at[model_name] = time.time()
            self.model_locks.setdefault(model_name, threading.Lock())

    def start_cleanup_worker(self):
        if self.idle_seconds <= 0:
            return
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            return
        self.cleanup_thread = threading.Thread(target=self.cleanup_worker, daemon=True)
        self.cleanup_thread.start()

    def get_loaded_models(self) -> List[str]:
        with self.manager_lock:
            return list(self.instances.keys())

    @contextmanager
    def acquire(self, model_name: str) -> Generator[ddddocr.DdddOcr, None, None]:
        with self.manager_lock:
            model = self.instances.get(model_name)
            if model is None:
                builder = OCR_MODEL_BUILDERS[model_name]
                model = builder()
                self.instances[model_name] = model
            self.active_counts[model_name] = self.active_counts.get(model_name, 0) + 1
            self.last_used_at[model_name] = time.time()
            model_lock = self.model_locks.setdefault(model_name, threading.Lock())

        try:
            with model_lock:
                yield model
        finally:
            with self.manager_lock:
                active_count = self.active_counts.get(model_name, 1) - 1
                if active_count > 0:
                    self.active_counts[model_name] = active_count
                else:
                    self.active_counts.pop(model_name, None)
                self.last_used_at[model_name] = time.time()

    def cleanup_worker(self):
        interval = max(5, min(self.idle_seconds, 60))
        while True:
            time.sleep(interval)
            now = time.time()
            unloaded = False

            with self.manager_lock:
                idle_model_names = [
                    model_name
                    for model_name, _ in self.instances.items()
                    if model_name not in self.persistent_models
                    if self.active_counts.get(model_name, 0) == 0
                    and now - self.last_used_at.get(model_name, now) >= self.idle_seconds
                ]
                for model_name in idle_model_names:
                    self.instances.pop(model_name, None)
                    self.last_used_at.pop(model_name, None)
                    self.active_counts.pop(model_name, None)
                    unloaded = True

            if unloaded:
                gc.collect()


ocr_models = OCRModelManager(OCR_MODEL_IDLE_SECONDS, OCR_PERSISTENT_MODELS)

# ==================== 工具函数 ====================
def normalize_base64_image(img_base64: str) -> str:
    """标准化 base64 图片字符串，兼容 data URI 前缀"""
    normalized = (img_base64 or "").strip()
    if not normalized:
        raise ValueError("图片数据不能为空")

    if normalized.startswith("data:") and "," in normalized:
        normalized = normalized.split(",", 1)[1].strip()

    if not normalized:
        raise ValueError("图片数据不能为空")
    return normalized


def estimate_base64_decoded_size(img_base64: str) -> int:
    """根据 base64 长度估算解码后的字节数，避免超大图片先落入内存"""
    padding = len(img_base64) - len(img_base64.rstrip("="))
    return max((len(img_base64) * 3) // 4 - padding, 0)


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
        normalized = normalize_base64_image(img_base64)
        estimated_size = estimate_base64_decoded_size(normalized)
        if estimated_size > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"图片大小超过限制，最大允许 {max_size / 1024 / 1024:.2f}MB"
            )

        img_data = base64.b64decode(normalized, validate=True)
        if len(img_data) > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"图片大小超过限制，最大允许 {max_size / 1024 / 1024:.2f}MB"
            )
        # 验证是否为有效图片
        try:
            with Image.open(BytesIO(img_data)) as img:
                img.verify()
        except Exception:
            raise HTTPException(status_code=400, detail="无效的图片格式")

        return img_data
    except HTTPException:
        raise
    except (ValueError, binascii.Error) as e:
        raise HTTPException(status_code=400, detail=f"图片解码失败: {str(e)}")


def extract_text_from_probability(result: Dict) -> str:
    """从概率结果中提取文本"""
    return "".join(result['charsets'][i.index(max(i))] for i in result['probability'])

# ==================== Token 管理 ====================

token_cache: List[Dict] = []
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
    global token_cache, token_value_map, rate_limit_state
    tokens = load_tokens_from_db()
    with token_cache_lock:
        token_cache = tokens
        token_value_map = {t['token']: t for t in tokens if t.get('token')}
        # 清理已删除 token 的限流状态
        rate_limit_state = {k: v for k, v in rate_limit_state.items() if k in token_value_map}


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
    return hmac.compare_digest(str(username), str(ADMIN_USERNAME)) and hmac.compare_digest(str(password), str(ADMIN_PASSWORD))


def compute_admin_login_sig(username: str, password: str, nonce: str) -> str:
    """
    基于一次性 nonce 的登录签名（避免明文传输密码）
    注意：这不是 HTTPS 的替代品，仅降低被动窃听风险。
    """
    raw = f"{username}:{password}:{nonce}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def prune_login_nonces(now: float):
    expired = [k for k, exp in login_nonces.items() if exp < now]
    for k in expired:
        login_nonces.pop(k, None)


def issue_login_nonce() -> str:
    nonce = secrets.token_urlsafe(24)
    now = time.time()
    with login_nonces_lock:
        prune_login_nonces(now)
        login_nonces[nonce] = now + LOGIN_NONCE_TTL_SECONDS
    return nonce


def consume_login_nonce(nonce: str) -> bool:
    now = time.time()
    with login_nonces_lock:
        prune_login_nonces(now)
        exp = login_nonces.pop(nonce, None)
    return exp is not None and exp >= now


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
        has_tokens = bool(token_cache)
        token_config = token_value_map.get(x_token)
    
    if not has_tokens:
        raise HTTPException(status_code=403, detail="Token 未配置，请先访问管理界面配置 Token")
    
    if not token_config:
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
ocr_models.preload("common")
ocr_models.start_cleanup_worker()

# ==================== 数据模型 ====================

class ModelImageIn(BaseModel):
    """单图片输入模型"""
    img_base64: str = Field(..., description="Base64编码的图片数据")
    
    @validator('img_base64')
    def validate_base64(cls, v):
        return normalize_base64_image(v)


class ModelSliderImageIn(BaseModel):
    """滑块图片输入模型"""
    gapimg_base64: str = Field(..., description="Base64编码的缺口图片数据")
    fullimg_base64: str = Field(..., description="Base64编码的完整图片数据")
    
    @validator('gapimg_base64', 'fullimg_base64')
    def validate_base64(cls, v):
        return normalize_base64_image(v)


class LoginModel(BaseModel):
    """登录模型"""
    username: str
    password: Optional[str] = None
    nonce: Optional[str] = None
    password_sig: Optional[str] = None


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
    with ocr_models.acquire("common") as model:
        result = model.classification(img)
    return {"result": result}


@app.post("/api/ocr/number", summary="数字", tags=["验证码识别"])
async def ocr_image_number(data: ModelImageIn, token: str = Depends(verify_token)):
    """数字验证码识别"""
    img = validate_image_size(data.img_base64)
    with ocr_models.acquire("number") as model:
        result = model.classification(img, probability=True)
    string = extract_text_from_probability(result)
    return {"result": string}


@app.post("/api/ocr/compute", summary="算术", tags=["验证码识别"])
async def ocr_image_compute(data: ModelImageIn, token: str = Depends(verify_token)):
    """算术验证码识别"""
    img = validate_image_size(data.img_base64)
    with ocr_models.acquire("compute") as model:
        result = model.classification(img, probability=True)
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
    with ocr_models.acquire("alphabet") as model:
        result = model.classification(img, probability=True)
    string = extract_text_from_probability(result)
    return {"result": string}


@app.post("/api/ocr/detection", summary="文字点选", tags=["验证码识别"])
async def ocr_image_det(data: ModelImageIn, token: str = Depends(verify_token)):
    """文字点选验证码识别"""
    img = validate_image_size(data.img_base64)
    with ocr_models.acquire("det") as det_model, ocr_models.acquire("common") as common_model:
        res = det_model.detection(img)
        with Image.open(BytesIO(img)) as img_pil:
            result = {
                common_model.classification(img_pil.crop(box)): [
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
    with ocr_models.acquire("det") as det_model:
        result = det_model.slide_match(gapimg, fullimg)
    return {"result": result}


@app.post("/api/ocr/slider/shadow", summary="阴影滑块识别", tags=["验证码识别"])
async def ocr_image_slider_shadow(data: ModelSliderImageIn, token: str = Depends(verify_token)):
    """阴影滑块验证码识别"""
    shadowimg = validate_image_size(data.gapimg_base64)
    fullimg = validate_image_size(data.fullimg_base64)
    with ocr_models.acquire("shadow_slide") as model:
        result = model.slide_comparison(shadowimg, fullimg)
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
            async function sha256Hex(input) {
                function sha256HexFallback(ascii) {
                    function rightRotate(value, amount) {
                        return (value >>> amount) | (value << (32 - amount));
                    }

                    var mathPow = Math.pow;
                    var maxWord = mathPow(2, 32);
                    var lengthProperty = 'length';
                    var i, j;
                    var result = '';

                    var words = [];
                    var asciiBitLength = ascii[lengthProperty] * 8;

                    var hash = sha256HexFallback.h = sha256HexFallback.h || [];
                    var k = sha256HexFallback.k = sha256HexFallback.k || [];
                    var primeCounter = k[lengthProperty];

                    var isComposite = {};
                    for (var candidate = 2; primeCounter < 64; candidate++) {
                        if (!isComposite[candidate]) {
                            for (i = 0; i < 313; i += candidate) {
                                isComposite[i] = candidate;
                            }
                            hash[primeCounter] = (mathPow(candidate, .5) * maxWord) | 0;
                            k[primeCounter++] = (mathPow(candidate, 1 / 3) * maxWord) | 0;
                        }
                    }

                    ascii += '\x80';
                    while (ascii[lengthProperty] % 64 - 56) ascii += '\x00';
                    for (i = 0; i < ascii[lengthProperty]; i++) {
                        j = ascii.charCodeAt(i);
                        words[i >> 2] |= j << ((3 - i) % 4) * 8;
                    }
                    words[words[lengthProperty]] = ((asciiBitLength / maxWord) | 0);
                    words[words[lengthProperty]] = (asciiBitLength);

                    for (j = 0; j < words[lengthProperty];) {
                        var w = words.slice(j, j += 16);
                        var oldHash = hash.slice(0);

                        for (i = 0; i < 64; i++) {
                            var w15 = w[i - 15], w2 = w[i - 2];

                            var a = hash[0], e = hash[4];
                            var temp1 = hash[7]
                                + (rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25))
                                + ((e & hash[5]) ^ ((~e) & hash[6]))
                                + k[i]
                                + (w[i] = (i < 16) ? w[i] : (
                                    w[i - 16]
                                    + (rightRotate(w15, 7) ^ rightRotate(w15, 18) ^ (w15 >>> 3))
                                    + w[i - 7]
                                    + (rightRotate(w2, 17) ^ rightRotate(w2, 19) ^ (w2 >>> 10))
                                ) | 0);

                            var temp2 = (rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22))
                                + ((a & hash[1]) ^ (a & hash[2]) ^ (hash[1] & hash[2]));

                            hash = [(temp1 + temp2) | 0].concat(hash);
                            hash[4] = (hash[4] + temp1) | 0;
                            hash.pop();
                        }

                        for (i = 0; i < 8; i++) {
                            hash[i] = (hash[i] + oldHash[i]) | 0;
                        }
                    }

                    for (i = 0; i < 8; i++) {
                        for (j = 3; j + 1; j--) {
                            var b = (hash[i] >> (j * 8)) & 255;
                            result += ((b < 16) ? 0 : '') + b.toString(16);
                        }
                    }
                    return result;
                }

                // 优先使用 WebCrypto（localhost/HTTPS 环境）；非安全上下文退回纯 JS 实现
                if (window.crypto && crypto.subtle && typeof TextEncoder !== 'undefined') {
                    const bytes = new TextEncoder().encode(input);
                    const digest = await crypto.subtle.digest('SHA-256', bytes);
                    return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
                }
                const utf8 = unescape(encodeURIComponent(input));
                return sha256HexFallback(utf8);
            }

            document.getElementById('loginForm').addEventListener('submit', async function(e) {
                e.preventDefault();
                const username = document.getElementById('username').value;
                const password = document.getElementById('password').value;
                let body = { username: username, password: password };

                try {
                    const nonceResp = await fetch('/api/admin/login_nonce', { cache: 'no-store' });
                    if (nonceResp.ok) {
                        const nonceData = await nonceResp.json();
                        const nonce = nonceData.nonce;
                        const passwordSig = await sha256Hex(`${username}:${password}:${nonce}`);
                        // 默认走加密登录，携带明文作为多进程/多实例场景下的回退
                        body = { username: username, password: password, nonce: nonce, password_sig: passwordSig };
                    }
                } catch (err) {
                    // 兼容不支持 WebCrypto 的环境：退回明文（建议部署侧配合内网/反代/HTTPS）
                    body = { username: username, password: password };
                }

                const response = await fetch('/api/admin/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    cache: 'no-store',
                    body: JSON.stringify(body)
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
    try:
        html_content = ADMIN_TEMPLATE_HTML
        if not html_content:
            raise FileNotFoundError
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

@app.get("/api/admin/login_nonce")
async def admin_login_nonce():
    """获取一次性登录 nonce（用于避免明文传输密码）"""
    nonce = issue_login_nonce()
    return JSONResponse({"nonce": nonce, "ttl": LOGIN_NONCE_TTL_SECONDS}, headers={"Cache-Control": "no-store"})


@app.post("/api/admin/login")
async def admin_login(login_data: LoginModel, request: Request):
    """管理员登录"""
    username = (login_data.username or "").strip()
    password = (login_data.password or "").strip() if login_data.password is not None else None
    nonce = (login_data.nonce or "").strip() if login_data.nonce is not None else None
    password_sig = (login_data.password_sig or "").strip() if login_data.password_sig is not None else None

    authenticated = False
    nonce_checked = False

    if nonce and password_sig:
        if consume_login_nonce(nonce):
            nonce_checked = True
            expected = compute_admin_login_sig(username, ADMIN_PASSWORD, nonce)
            authenticated = hmac.compare_digest(str(username), str(ADMIN_USERNAME)) and hmac.compare_digest(password_sig, expected)
        elif password is None:
            # 老版本前端不带明文时，直接提示过期
            raise HTTPException(status_code=401, detail="登录凭证过期，请刷新重试")
    
    if not authenticated and password is not None:
        authenticated = verify_admin_credentials(username, password)
    
    if password is None and not nonce_checked and not password_sig:
        raise HTTPException(status_code=400, detail="请求参数错误")

    if authenticated:
        session_id = create_session()
        response = JSONResponse({"success": True, "session_id": session_id})
        response.set_cookie(
            key="admin_session",
            value=session_id,
            httponly=True,
            samesite="strict",
            secure=(request.url.scheme == "https"),
            max_age=3600 * 24
        )
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
        access_log=UVICORN_ACCESS_LOG,
        workers=workers,
        reload=False
    )
