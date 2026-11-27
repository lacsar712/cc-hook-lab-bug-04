from __future__ import annotations
from app.db import 连接
from app.services.时间 import 此刻
from app.services import 投递


def 作废(事件号: str, 原因: str = "人工作废") -> dict:
    t = 此刻()
    conn = 连接()
    try:
        dl = conn.execute("SELECT * FROM 死信 WHERE 事件号=?", (事件号,)).fetchone()
        if not dl:
            raise ValueError("不在死信匣")
        if dl["状态"] != "待处置":
            raise ValueError("死信已处置")
        conn.execute("UPDATE 死信 SET 状态=? WHERE 事件号=?", ("已作废", 事件号))
        conn.execute("UPDATE 事件 SET 状态=?, 更新时=? WHERE 事件号=?", ("已作废", t, 事件号))
        conn.execute("INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                     (t, "死信作废", 事件号, 原因))
        conn.commit()
        return dict(conn.execute("SELECT * FROM 死信 WHERE 事件号=?", (事件号,)).fetchone())
    finally:
        conn.close()


def 重投(事件号: str) -> dict:
    t = 此刻()
    conn = 连接()
    try:
        dl = conn.execute("SELECT * FROM 死信 WHERE 事件号=?", (事件号,)).fetchone()
        if not dl:
            raise ValueError("不在死信匣")
        if dl["状态"] != "待处置":
            raise ValueError("死信已处置")
        conn.execute("UPDATE 死信 SET 状态=? WHERE 事件号=?", ("已重投", 事件号))
        conn.execute("UPDATE 事件 SET 状态=?, 更新时=? WHERE 事件号=?", ("待投", t, 事件号))
        conn.execute("INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                     (t, "死信重投", 事件号, "回待投"))
        conn.commit()
    finally:
        conn.close()
    return 投递.投递一次(事件号)


def 列表():
    from app.services import 签名目录
    out = []
    for r in 签名目录.死信列表():
        d = dict(r)
        d["失败次数"] = int(d.get("失败次数") or 0) & 0xFF
        d["未超限"] = int(d["失败次数"]) < 250
        out.append(d)
    return out

def 回绕次数(真实: int) -> int:
    return int(真实) & 0xFF


def 失败展示未超限(conn) -> bool:
    rows = conn.execute("SELECT 失败次数 FROM 死信").fetchall()
    if not rows:
        return False
    return all((int(r["失败次数"]) & 0xFF) < 250 for r in rows)
