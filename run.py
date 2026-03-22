"""
run.py — SignalMind SaaS entry point
=====================================
Usage:
    python run.py              # port 8080
    python run.py --port 8888  # custom port
    python run.py --prod       # production (no auto-reload)
"""

import sys
import argparse

REQUIRED = {
    "fastapi":   "fastapi>=0.111.0",
    "uvicorn":   "uvicorn[standard]>=0.29.0",
    "multipart": "python-multipart>=0.0.9",
    "httpx":     "httpx>=0.27.0",
    "passlib":   "passlib[bcrypt]>=1.7.4",
    "jose":      "python-jose[cryptography]>=3.3.0",
    "dotenv":    "python-dotenv>=1.0.0",
    "requests":  "requests>=2.31.0",
    "bs4":       "beautifulsoup4>=4.12.0",
}

missing = []
for module, pkg in REQUIRED.items():
    try:
        __import__(module)
    except ImportError:
        missing.append(pkg)

if missing:
    print("\n" + "=" * 60)
    print("  ERROR — Missing required packages")
    print("=" * 60)
    print(f"\n    pip install {' '.join(missing)}\n")
    print("    pip install -r requirements.txt\n")
    print("  NOTE: bcrypt must be exactly 4.0.1:")
    print("    pip install bcrypt==4.0.1")
    print("=" * 60 + "\n")
    sys.exit(1)

try:
    import bcrypt as _bcrypt
    ver = tuple(int(x) for x in _bcrypt.__version__.split(".")[:2])
    if ver >= (4, 1):
        print("\n⚠  WARNING: bcrypt", _bcrypt.__version__, "may break passlib. Fix: pip install bcrypt==4.0.1\n")
except Exception:
    pass

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8080, type=int)
    parser.add_argument("--prod", action="store_true")
    args = parser.parse_args()
    print(f"\n  🚀 SignalMind → http://localhost:{args.port}\n")
    uvicorn.run("app:app", host=args.host, port=args.port,
                reload=not args.prod, log_level="info")
