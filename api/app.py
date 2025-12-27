"""
Vercel 部署入口点
从 StupidOCR.py 导出 FastAPI 应用
"""
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from StupidOCR import app

# Vercel 需要通过 ASGI 服务器处理请求
# 导出 app 实例供 Vercel 使用
handler = app

__all__ = ["app", "handler"]