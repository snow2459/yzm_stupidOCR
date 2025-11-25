import ddddocr
import uvicorn
import base64
import re
import json
import secrets
import hashlib
from io import BytesIO
from PIL import Image
from fastapi import FastAPI, HTTPException, Depends, Header, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field, validator
from fastapi.middleware.cors import CORSMiddleware
from concurrent.futures import ThreadPoolExecutor
import asyncio
import os
from datetime import datetime
from typing import Optional, List, Dict

description = """
* 增强版DDDDOCR

* 识别效果完全靠玄学，可能可以识别，可能不能识别。——DDDDOCR

  <img src="https://img.shields.io/badge/GitHub-ffffff"></a> <a href="https://github.com/81NewArk/StupidOCR"> <img src="https://img.shields.io/github/stars/81NewArk/StupidOCR?style=social"> <img src="https://badges.pufler.dev/visits/81NewArk/StupidOCR">
"""

# 禁用Swagger UI、ReDoc和OpenAPI文档
app = FastAPI(title="StupidOCR", description=description, version="1.0.8", docs_url=None, redoc_url=None,
              openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 从环境变量获取线程池大小，默认为2
OCR_THREAD_POOL_SIZE = int(os.environ.get("OCR_THREAD_POOL_SIZE", 2))

# 图片大小限制（默认最大 5MB）
MAX_IMAGE_SIZE = int(os.environ.get("MAX_IMAGE_SIZE", 5 * 1024 * 1024))

# Token 存储文件路径
TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".token_config.json")

# 管理员账号密码（可通过环境变量配置）
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "yzm_admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "7jnyxx54")

# 会话存储（简单实现，生产环境建议使用 Redis 或数据库）
admin_sessions = set()

# 创建线程池
ocr_thread_pool = ThreadPoolExecutor(max_workers=OCR_THREAD_POOL_SIZE)

# HTTP Basic Auth
security = HTTPBasic()


def load_tokens() -> List[Dict]:
    """从文件加载所有 token"""
    try:
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # 兼容旧格式
                if 'token' in config and isinstance(config['token'], str):
                    return [{
                        'id': '1',
                        'token': config['token'],
                        'name': '默认 Token',
                        'created_at': config.get('updated_at', datetime.now().isoformat()),
                        'updated_at': config.get('updated_at', datetime.now().isoformat())
                    }]
                return config.get('tokens', [])
    except Exception:
        pass
    return []


def save_tokens(tokens: List[Dict]) -> bool:
    """保存所有 token 到文件"""
    try:
        config = {
            'tokens': tokens,
            'updated_at': datetime.now().isoformat()
        }
        with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        # 设置文件权限为仅所有者可读写
        os.chmod(TOKEN_FILE, 0o600)
        return True
    except Exception as e:
        return False


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


def generate_token() -> str:
    """生成新的 token"""
    return secrets.token_urlsafe(32)


async def verify_token(x_token: Optional[str] = Header(None, alias="X-Token")):
    """验证 token 的依赖函数"""
    if not x_token:
        raise HTTPException(status_code=403, detail="缺少 Token，请在请求头中添加 X-Token")
    
    tokens = load_tokens()
    if not tokens:
        raise HTTPException(status_code=403, detail="Token 未配置，请先访问管理界面配置 Token")
    
    # 检查 token 是否在列表中
    token_values = [t.get('token') for t in tokens]
    if x_token not in token_values:
        raise HTTPException(status_code=403, detail="Token 验证失败")
    
    return x_token


def safe_eval_arithmetic(expression: str) -> float:
    """
    安全地计算算术表达式，只允许数字和基本运算符
    替换 eval() 以避免代码注入风险
    使用受限的命名空间执行，只允许基本的数学运算
    """
    # 移除所有空格
    expression = expression.replace(" ", "")
    
    # 只允许数字、小数点、运算符和括号
    if not re.match(r'^[0-9+\-*/().]+$', expression):
        raise ValueError("表达式包含非法字符")
    
    # 验证括号匹配
    if expression.count('(') != expression.count(')'):
        raise ValueError("括号不匹配")
    
    # 验证表达式格式：不能以运算符开头或结尾（除了负号在开头）
    if expression and expression[0] in '*/+':
        raise ValueError("表达式格式错误")
    if expression and expression[-1] in '+-*/':
        raise ValueError("表达式格式错误")
    
    # 使用受限的命名空间执行计算
    # 只允许基本的数学运算，禁止所有内置函数和模块
    try:
        # 创建完全受限的命名空间
        safe_dict = {
            "__builtins__": {},  # 完全禁用内置函数
        }
        # 编译表达式
        code = compile(expression, "<string>", "eval")
        # 在受限环境中执行，只允许基本的算术运算
        result = eval(code, safe_dict)
        
        # 验证结果是数字类型
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
        # 解码 base64
        img_data = base64.b64decode(img_base64)
        
        # 检查大小
        if len(img_data) > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"图片大小超过限制，最大允许 {max_size / 1024 / 1024:.2f}MB"
            )
        
        # 验证是否为有效图片
        try:
            img = Image.open(BytesIO(img_data))
            img.verify()  # 验证图片完整性（verify() 会消耗图片对象，但不影响后续使用原始数据）
        except Exception:
            raise HTTPException(status_code=400, detail="无效的图片格式")
        
        return img_data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"图片解码失败: {str(e)}")

ocr = ddddocr.DdddOcr(show_ad=False, beta=True)
number_ocr = ddddocr.DdddOcr(show_ad=False, beta=True)
number_ocr.set_ranges(0)
compute_ocr = ddddocr.DdddOcr(show_ad=False, beta=True)
compute_ocr.set_ranges("0123456789+-x÷=")
alphabet_ocr = ddddocr.DdddOcr(show_ad=False, beta=True)
alphabet_ocr.set_ranges(3)
det = ddddocr.DdddOcr(det=True, show_ad=False)
shadow_slide = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)


class ModelImageIn(BaseModel):
    img_base64: str = Field(..., description="Base64编码的图片数据")
    
    @validator('img_base64')
    def validate_base64(cls, v):
        if not v or len(v) == 0:
            raise ValueError("图片数据不能为空")
        # 检查是否为有效的 base64 字符串
        try:
            base64.b64decode(v, validate=True)
        except Exception:
            raise ValueError("无效的 base64 编码")
        return v


class ModelSliderImageIn(BaseModel):
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


@app.post("/api/ocr/image", summary="通用", tags=["验证码识别"])
async def ocr_image(data: ModelImageIn, token: str = Depends(verify_token)):
    # 使用线程池执行OCR识别任务
    loop = asyncio.get_event_loop()
    img = validate_image_size(data.img_base64)
    result = await loop.run_in_executor(ocr_thread_pool, ocr.classification, img)
    print("识别结果：", result)
    return {"result": result}


@app.post("/api/ocr/number", summary="数字", tags=["验证码识别"])
async def ocr_image_number(data: ModelImageIn, token: str = Depends(verify_token)):
    img = validate_image_size(data.img_base64)
    result = number_ocr.classification(img, probability=True)
    string = "".join(result['charsets'][i.index(max(i))] for i in result['probability'])
    return {"result": string}


@app.post("/api/ocr/compute", summary="算术", tags=["验证码识别"])
async def ocr_image_compute(data: ModelImageIn, token: str = Depends(verify_token)):
    img = validate_image_size(data.img_base64)
    result = compute_ocr.classification(img, probability=True)
    string = "".join(result['charsets'][i.index(max(i))] for i in result['probability'])
    string = string.split("=")[0].replace("x", "*").replace("÷", "/")
    try:
        # 使用安全的算术表达式计算，替换 eval()
        result = safe_eval_arithmetic(string)
        # 如果是整数，返回整数；否则返回浮点数
        if result.is_integer():
            result = int(result)
    except ValueError as e:
        result = f"Error: {str(e)}"
    except Exception as e:
        result = "Error: 计算失败"
    return {"result": result}


@app.post("/api/ocr/alphabet", summary="字母", tags=["验证码识别"])
async def ocr_image_alphabet(data: ModelImageIn, token: str = Depends(verify_token)):
    img = validate_image_size(data.img_base64)
    result = alphabet_ocr.classification(img, probability=True)
    string = "".join(result['charsets'][i.index(max(i))] for i in result['probability'])
    return {"result": string}


@app.post("/api/ocr/detection", summary="文字点选", tags=["验证码识别"])
async def ocr_image_det(data: ModelImageIn, token: str = Depends(verify_token)):
    img = validate_image_size(data.img_base64)
    img_pil = Image.open(BytesIO(img))
    res = det.detection(img)
    result = {ocr.classification(img_pil.crop(box)): [box[0] + (box[2] - box[0]) // 2, box[1] + (box[3] - box[1]) // 2]
              for box in res}
    return {"result": result}


@app.post("/api/ocr/slider/gap", summary="缺口滑块识别", tags=["验证码识别"])
async def ocr_image_slider_gap(data: ModelSliderImageIn, token: str = Depends(verify_token)):
    gapimg = validate_image_size(data.gapimg_base64)
    fullimg = validate_image_size(data.fullimg_base64)
    result = det.slide_match(gapimg, fullimg)
    return {"result": result}


@app.post("/api/ocr/slider/shadow", summary="阴影滑块识别", tags=["验证码识别"])
async def ocr_image_slider_shadow(data: ModelSliderImageIn, token: str = Depends(verify_token)):
    shadowimg = validate_image_size(data.gapimg_base64)
    fullimg = validate_image_size(data.fullimg_base64)
    result = shadow_slide.slide_comparison(shadowimg, fullimg)
    return {"result": result}


# Token 管理相关的数据模型
class LoginModel(BaseModel):
    username: str
    password: str


class TokenConfigModel(BaseModel):
    token: Optional[str] = Field(None, description="Token 值，留空则自动生成")
    name: Optional[str] = Field(None, description="Token 名称")


class TokenUpdateModel(BaseModel):
    token_id: str
    token: Optional[str] = Field(None, description="Token 值")
    name: Optional[str] = Field(None, description="Token 名称")


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    """登录页面"""
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>StupidOCR - 管理员登录</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
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
            input[type="text"]:focus, input[type="password"]:focus {
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
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ username: username, password: password })
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    // Cookie 由服务器设置，直接跳转
                    window.location.href = '/admin';
                } else {
                    const errorData = await response.json();
                    showMessage('登录失败：' + (errorData.detail || '账号或密码错误'), 'error');
                }
            });
            
            function showMessage(text, type) {
                const messageDiv = document.getElementById('message');
                messageDiv.textContent = text;
                messageDiv.className = 'message ' + type;
                messageDiv.style.display = 'block';
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """管理界面"""
    # 检查会话
    session_id = request.cookies.get("admin_session")
    if not verify_session(session_id):
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    
    tokens = load_tokens()
    token_count = len(tokens)
    status_class = "configured" if token_count > 0 else "not-configured"
    status_text = f"已配置 {token_count} 个 Token" if token_count > 0 else "未配置"
    
    # 生成 token 列表 HTML（只显示前20个字符）
    token_list_html = ""
    if tokens:
        for token in tokens:
            token_id = token.get('id', '')
            token_name = token.get('name', '未命名 Token')
            token_value = token.get('token', '')
            token_display = token_value[:20] + '...' if len(token_value) > 20 else token_value
            created_at = token.get('created_at', '')
            token_list_html += f"""
            <tr>
                <td>{token_name}</td>
                <td><code style="font-size: 11px;">{token_display}</code></td>
                <td>{created_at[:10] if created_at else '-'}</td>
                <td>
                    <button class="btn-edit" onclick="editToken('{token_id}')">编辑</button>
                    <button class="btn-delete" onclick="deleteToken('{token_id}')">删除</button>
                </td>
            </tr>
            """
    else:
        token_list_html = '<tr><td colspan="4" style="text-align: center; color: #999;">暂无 Token</td></tr>'
    
    # 读取模板文件
    template_path = os.path.join(os.path.dirname(__file__), "admin_template.html")
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        # 替换模板变量（使用 replace 避免 CSS 花括号冲突）
        html_content = html_content.replace('{status_class}', status_class)
        html_content = html_content.replace('{status_text}', status_text)
        html_content = html_content.replace('{token_count}', str(token_count))
        html_content = html_content.replace('{token_list_html}', token_list_html)
    except FileNotFoundError:
        # 如果模板文件不存在，返回简单提示
        html_content = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>StupidOCR - Token 管理</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 20px;
            }}
            .container {{
                background: white;
                border-radius: 12px;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                padding: 40px;
                max-width: 600px;
                width: 100%;
            }}
            h1 {{
                color: #333;
                margin-bottom: 10px;
                font-size: 28px;
            }}
            .subtitle {{
                color: #666;
                margin-bottom: 30px;
                font-size: 14px;
            }}
            .status {{
                padding: 12px;
                border-radius: 8px;
                margin-bottom: 30px;
                font-size: 14px;
            }}
            .status.configured {{
                background: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
            }}
            .status.not-configured {{
                background: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }}
            .form-group {{
                margin-bottom: 20px;
            }}
            label {{
                display: block;
                margin-bottom: 8px;
                color: #333;
                font-weight: 500;
                font-size: 14px;
            }}
            input[type="text"] {{
                width: 100%;
                padding: 12px;
                border: 2px solid #e0e0e0;
                border-radius: 6px;
                font-size: 14px;
                transition: border-color 0.3s;
            }}
            input[type="text"]:focus {{
                outline: none;
                border-color: #667eea;
            }}
            .token-display {{
                background: #f5f5f5;
                padding: 12px;
                border-radius: 6px;
                font-family: 'Courier New', monospace;
                font-size: 12px;
                word-break: break-all;
                margin-top: 8px;
                color: #333;
            }}
            .button-group {{
                display: flex;
                gap: 10px;
                margin-top: 30px;
            }}
            button {{
                flex: 1;
                padding: 12px 24px;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.3s;
            }}
            .btn-primary {{
                background: #667eea;
                color: white;
            }}
            .btn-primary:hover {{
                background: #5568d3;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
            }}
            .btn-secondary {{
                background: #6c757d;
                color: white;
            }}
            .btn-secondary:hover {{
                background: #5a6268;
            }}
            .btn-danger {{
                background: #dc3545;
                color: white;
            }}
            .btn-danger:hover {{
                background: #c82333;
            }}
            .message {{
                margin-top: 20px;
                padding: 12px;
                border-radius: 6px;
                display: none;
            }}
            .message.success {{
                background: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
            }}
            .message.error {{
                background: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }}
            .info-box {{
                background: #e7f3ff;
                border-left: 4px solid #2196F3;
                padding: 15px;
                margin-top: 20px;
                border-radius: 4px;
            }}
            .info-box h3 {{
                color: #1976D2;
                font-size: 14px;
                margin-bottom: 8px;
            }}
            .info-box p {{
                color: #555;
                font-size: 13px;
                line-height: 1.6;
            }}
            .info-box code {{
                background: #fff;
                padding: 2px 6px;
                border-radius: 3px;
                font-family: 'Courier New', monospace;
                font-size: 12px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔐 Token 管理</h1>
            <p class="subtitle">配置 API 访问令牌以保护您的 OCR 服务</p>
            
            <div class="status {'configured' if current_token else 'not-configured'}">
                <strong>当前状态：</strong>{token_status}
            </div>
            
            <form id="tokenForm">
                <div class="form-group">
                    <label for="token">Token（留空将自动生成）</label>
                    <input type="text" id="token" name="token" placeholder="留空将自动生成安全的随机 Token">
                    {'<div class="token-display">当前 Token: ' + current_token + '</div>' if current_token else ''}
                </div>
                
                <div class="button-group">
                    <button type="submit" class="btn-primary">保存 Token</button>
                    <button type="button" class="btn-secondary" onclick="generateToken()">生成新 Token</button>
                    {'<button type="button" class="btn-danger" onclick="clearToken()">清除 Token</button>' if current_token else ''}
                </div>
            </form>
            
            <div id="message" class="message"></div>
            
            <div class="info-box">
                <h3>📖 使用说明</h3>
                <p>
                    1. 配置 Token 后，所有 OCR API 接口都需要在请求头中添加 <code>X-Token</code> 字段<br>
                    2. 示例请求：<code>curl -H "X-Token: your-token" -X POST http://localhost:6688/api/ocr/image -d '{{"img_base64": "..."}}'</code><br>
                    3. 未提供或错误的 Token 将返回 403 错误<br>
                    4. Token 存储在本地文件 <code>.token_config.json</code> 中
                </p>
            </div>
        </div>
        
        <script>
            document.getElementById('tokenForm').addEventListener('submit', async function(e) {{
                e.preventDefault();
                const token = document.getElementById('token').value;
                
                const response = await fetch('/api/admin/token', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                    }},
                    body: JSON.stringify({{ token: token || null }})
                }});
                
                const data = await response.json();
                
                if (response.ok) {{
                    showMessage('Token 保存成功！', 'success');
                    if (data.token) {{
                        setTimeout(() => {{
                            location.reload();
                        }}, 1500);
                    }}
                }} else {{
                    showMessage('保存失败：' + (data.detail || '未知错误'), 'error');
                }}
            }});
            
            function generateToken() {{
                // 生成一个随机 token（前端示例，实际由后端生成）
                const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
                let token = '';
                for (let i = 0; i < 43; i++) {{
                    token += chars.charAt(Math.floor(Math.random() * chars.length));
                }}
                document.getElementById('token').value = token;
            }}
            
            async function clearToken() {{
                if (!confirm('确定要清除 Token 吗？清除后所有 API 将无法访问！')) {{
                    return;
                }}
                
                const response = await fetch('/api/admin/token', {{
                    method: 'DELETE'
                }});
                
                const data = await response.json();
                
                if (response.ok) {{
                    showMessage('Token 已清除', 'success');
                    setTimeout(() => {{
                        location.reload();
                    }}, 1500);
                }} else {{
                    showMessage('清除失败：' + (data.detail || '未知错误'), 'error');
                }}
            }}
            
            function showMessage(text, type) {{
                const messageDiv = document.getElementById('message');
                messageDiv.textContent = text;
                messageDiv.className = 'message ' + type;
                messageDiv.style.display = 'block';
                
                setTimeout(() => {{
                    messageDiv.style.display = 'none';
                }}, 5000);
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


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
    
    tokens = load_tokens()
    
    if config.token:
        token_value = config.token.strip()
        if len(token_value) < 16:
            raise HTTPException(status_code=400, detail="Token 长度至少需要 16 个字符")
    else:
        token_value = generate_token()
    
    # 生成新 ID
    new_id = str(max([int(t.get('id', '0')) for t in tokens] + [0]) + 1)
    
    new_token = {
        'id': new_id,
        'token': token_value,
        'name': config.name or f'Token {new_id}',
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }
    
    tokens.append(new_token)
    
    if save_tokens(tokens):
        return {"success": True, "token": new_token, "message": "Token 已创建"}
    else:
        raise HTTPException(status_code=500, detail="保存 Token 失败")


@app.put("/api/admin/token")
async def update_token(config: TokenUpdateModel, request: Request):
    """更新 Token"""
    session_id = request.cookies.get("admin_session")
    if not verify_session(session_id):
        raise HTTPException(status_code=401, detail="未授权")
    
    tokens = load_tokens()
    token_index = None
    
    for i, token in enumerate(tokens):
        if token.get('id') == config.token_id:
            token_index = i
            break
    
    if token_index is None:
        raise HTTPException(status_code=404, detail="Token 不存在")
    
    if config.token:
        token_value = config.token.strip()
        if len(token_value) < 16:
            raise HTTPException(status_code=400, detail="Token 长度至少需要 16 个字符")
        tokens[token_index]['token'] = token_value
    # 如果未提供 token，保持原值
    
    if config.name:
        tokens[token_index]['name'] = config.name
    
    tokens[token_index]['updated_at'] = datetime.now().isoformat()
    
    if save_tokens(tokens):
        return {"success": True, "token": tokens[token_index], "message": "Token 已更新"}
    else:
        raise HTTPException(status_code=500, detail="更新 Token 失败")


@app.delete("/api/admin/token/{token_id}")
async def delete_token(token_id: str, request: Request):
    """删除 Token"""
    session_id = request.cookies.get("admin_session")
    if not verify_session(session_id):
        raise HTTPException(status_code=401, detail="未授权")
    
    tokens = load_tokens()
    tokens = [t for t in tokens if t.get('id') != token_id]
    
    if save_tokens(tokens):
        return {"success": True, "message": "Token 已删除"}
    else:
        raise HTTPException(status_code=500, detail="删除 Token 失败")


@app.get("/api/admin/tokens")
async def get_tokens(request: Request):
    """获取所有 Token（不返回完整 token 值）"""
    session_id = request.cookies.get("admin_session")
    if not verify_session(session_id):
        raise HTTPException(status_code=401, detail="未授权")
    
    tokens = load_tokens()
    # 隐藏完整 token，只显示前 20 个字符
    safe_tokens = []
    for token in tokens:
        safe_token = token.copy()
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
    
    tokens = load_tokens()
    token = next((t for t in tokens if t.get('id') == token_id), None)
    
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


if __name__ == '__main__':
    print('''
       软件主页：http://127.0.0.1:6688
       ''')

    # 从环境变量获取worker数量，默认为1
    workers = int(os.environ.get("UVICORN_WORKERS", 1))
    
    uvicorn.run(
        "StupidOCR:app",
        host="0.0.0.0",
        port=6688,
        access_log=True,
        workers=workers,
        reload=False
    )