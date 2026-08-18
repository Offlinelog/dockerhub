import os

from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
GITHUB_REF = os.getenv("GITHUB_REF", "main")

SWR_REGISTRY = os.getenv("SWR_REGISTRY", "").rstrip("/")
SWR_NAMESPACE = os.getenv("SWR_NAMESPACE", "").strip("/")

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
DB_PATH = os.getenv("DB_PATH", "mirror.db")
