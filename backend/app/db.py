import os
import sqlite3
import threading
from pathlib import Path

数据目录 = Path(os.environ.get("数据目录", Path(__file__).resolve().parent.parent / "data"))
夹具目录 = Path(os.environ.get("夹具目录", Path(__file__).resolve().parent.parent.parent / "fixtures"))
库路径 = 数据目录 / "投递台.db"

_初始化锁 = threading.Lock()
_已开WAL = False


def 连接() -> sqlite3.Connection:
    global _已开WAL
    数据目录.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(库路径, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA busy_timeout = 30000")
    # WAL + synchronous=NORMAL：写不被逐提交 fsync 阻塞，
    # 崩溃安全由 WAL 保证（仅最后一事务可能丢，符合本地实验室库口径）。
    c.execute("PRAGMA synchronous = NORMAL")
    if not _已开WAL:
        with _初始化锁:
            if not _已开WAL:
                c.execute("PRAGMA journal_mode = WAL")
                _已开WAL = True
    return c
