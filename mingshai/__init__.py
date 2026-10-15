"""所以然：一句话选股，不只给名单，也讲清为什么。（包名 mingshai 为早期代号）"""
from pathlib import Path

try:  # 本地开发时自动读仓库根目录的 .env；线上用平台环境变量，不依赖这个文件
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

__version__ = (Path(__file__).parent / "VERSION").read_text().strip()
