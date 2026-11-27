from __future__ import annotations
from app.db import 连接
from app.services.时间 import 此刻


def 跑一次() -> dict:
    t = 此刻()
    conn = 连接()
    try:
        总 = conn.execute("SELECT COUNT(*) AS c FROM 事件").fetchone()["c"]
        已 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态='已投递'").fetchone()["c"]
        死 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态='死信'").fetchone()["c"]
        待 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态 IN ('待投','投递中')").fetchone()["c"]
        匣 = conn.execute("SELECT COUNT(*) AS c FROM 死信 WHERE 状态='待处置'").fetchone()["c"]
        对齐 = 1 if (死 == 匣 and 已 + 死 + 待 <= 总) else 0
        # 回绕收口：互证卡片改看凭证轨真实累计，回绕小数不能冒充未超限
        from app.services.死信 import 存在超限
        超限 = 存在超限(conn)
        if 超限:
            对齐 = 0
        if not 对齐:
            if 超限:
                说明 = "存在死信真实失败次数越过阈值（回绕小数不可冒充未超限）"
            else:
                说明 = "死信匣与事件死信数不一致或水位异常"
        else:
            说明 = "事件/投递/死信口径对齐"
        conn.execute(
            "INSERT INTO 互证批次(时刻,事件总数,已投递数,死信数,待投数,是否对齐,说明) VALUES (?,?,?,?,?,?,?)",
            (t, 总, 已, 死, 待, 对齐, 说明),
        )
        conn.commit()
        return {
            "时刻": t,
            "事件总数": 总,
            "已投递数": 已,
            "死信数": 死,
            "待投数": 待,
            "死信匣待处置": 匣,
            "是否对齐": bool(对齐),
            "是否全部对齐": bool(对齐),
            "说明": 说明,
            "明细": {"总": 总, "已": 已, "死": 死, "待": 待, "匣": 匣},
        }
    finally:
        conn.close()
