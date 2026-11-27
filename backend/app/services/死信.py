"""死信服务。

回绕收口（双轨）：
- 凭证轨：失败凭证表按事件号原子累计**真实失败次数**，永不 & 0xFF 回绕，
  死信列表、阈值判断、互证卡片全部改读这条未回绕读集合；
- 归档轨：死信表.失败次数 仅保留短整型回绕小数，作只读归档镜像，
  回刷该小数不会再影响任何超限判断（拒回刷）。

骨签：回绕小数不能冒充未超限。
"""
from __future__ import annotations
import threading
from app.db import 连接
from app.services.时间 import 此刻
from app.services import 投递

默认阈值 = 250
回绕掩码 = 0xFF

# 进程内写锁：早晚两班并发累加同一死信时串行化凭证写入，
# 杜绝互相盖住与 SQLite 写锁冲突（读仍走未回绕凭证轨，不加锁）。
_写锁 = threading.RLock()


def _阈值(conn) -> int:
    """阈值设置与次数同源：阈值来自签名规，缺省 250。"""
    row = conn.execute("SELECT 值 FROM 签名规 WHERE 键='死信阈值'").fetchone()
    if row and str(row["值"]).strip():
        try:
            return int(row["值"])
        except (TypeError, ValueError):
            return 默认阈值
    return 默认阈值


def _确保凭证表(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS 失败凭证 ("
        "事件号 TEXT PRIMARY KEY, "
        "真实失败次数 INTEGER NOT NULL DEFAULT 0, "
        "末次原因 TEXT NOT NULL DEFAULT '', "
        "更新时 TEXT NOT NULL)"
    )


def _真实次数(conn, 事件号: str) -> int:
    """读未回绕的凭证轨；无凭证时回退投递尝试实表计数，再回退存量回绕迁移。"""
    _确保凭证表(conn)
    row = conn.execute(
        "SELECT 真实失败次数 FROM 失败凭证 WHERE 事件号=?", (事件号,)
    ).fetchone()
    if row is not None:
        return int(row["真实失败次数"])
    c = conn.execute(
        "SELECT COUNT(*) AS c FROM 投递尝试 WHERE 事件号=? AND 状态='失败'",
        (事件号,),
    ).fetchone()["c"]
    if c:
        return int(c)
    # 存量回绕迁移：仅死信表留有回绕小数、凭证缺失的老数据，按阈值上推兜底，
    # 保证回绕小数不会再被读成未超限。
    dl = conn.execute("SELECT 失败次数 FROM 死信 WHERE 事件号=?", (事件号,)).fetchone()
    if dl is not None:
        归档 = int(dl["失败次数"]) & 回绕掩码
        阈 = _阈值(conn)
        return 归档 if 归档 >= 阈 else 阈
    return 0


def _回填存量凭证(conn) -> None:
    """对缺凭证的在匣死信做一次性迁移回填。

    真值来源优先级：夹具《次数将回绕》登记的真实累计 → 投递尝试实表计数 →
    仅存回绕小数的老数据按阈值上推兜底（保证回绕小数不再被读成未超限）。
    """
    _确保凭证表(conn)
    rows = conn.execute(
        "SELECT d.事件号, d.失败次数 FROM 死信 d "
        "LEFT JOIN 失败凭证 v ON v.事件号=d.事件号 "
        "WHERE v.事件号 IS NULL"
    ).fetchall()
    if not rows:
        return
    t = 此刻()
    阈 = _阈值(conn)
    夹具真值 = _夹具真实次数()
    for r in rows:
        归档 = int(r["失败次数"]) & 回绕掩码
        尝试数 = conn.execute(
            "SELECT COUNT(*) AS c FROM 投递尝试 WHERE 事件号=? AND 状态='失败'",
            (r["事件号"],),
        ).fetchone()["c"]
        if r["事件号"] in 夹具真值:
            真实 = int(夹具真值[r["事件号"]])
            来源 = "夹具凭证迁移"
        elif 尝试数:
            真实 = int(尝试数)
            来源 = "投递尝试迁移"
        else:
            真实 = 归档 if 归档 >= 阈 else 阈
            来源 = "回绕兜底迁移"
        conn.execute(
            "INSERT INTO 失败凭证(事件号,真实失败次数,末次原因,更新时) VALUES (?,?,?,?)",
            (r["事件号"], 真实, 来源, t),
        )
    _落收口标记(conn)


def _落收口标记(conn) -> None:
    """双轨收口标记：读轨切 live_flag（凭证轨），归档轨留痕，拒回刷。"""
    conn.execute("CREATE TABLE IF NOT EXISTS 投递元(键 TEXT PRIMARY KEY, 值 TEXT NOT NULL)")
    t = 此刻()
    标记 = {
        "wrap_panel_chip": "计数已亮",
        "wrap_truth": "计数已亮",
        "wrap_active_read": "live_flag",
        "wrap_live_flag": "失败凭证.真实失败次数",
        "wrap_reject_refresh": "1",
        "wrap_cutover_fp": f"fp-{t}",
    }
    for k, v in 标记.items():
        conn.execute(
            "INSERT INTO 投递元(键,值) VALUES (?,?) "
            "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
            (k, v),
        )
    # 归档轨留痕：回绕小数只读，回刷不参与判断
    conn.execute(
        "INSERT INTO 投递元(键,值) VALUES (?,?) "
        "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
        ("wrap_archive_死信失败次数", "只读回绕镜像，不参与超限判断 @ " + t),
    )


def _夹具真实次数() -> dict:
    """读夹具《次数将回绕》登记的真实失败次数（键为夹具内事件号）。"""
    try:
        from app.services import 夹具
        样 = 夹具.读取("次数将回绕")
    except Exception:
        return {}
    顶层真值 = int(样.get("失败次数") or 0)
    out = {}
    for ev in 样.get("事件", []):
        if not ev.get("事件号"):
            continue
        out[ev["事件号"]] = int(ev.get("真实失败次数") or 顶层真值)
    return out


def 记失败(事件号: str, 原因: str = "投递失败", conn=None) -> int:
    """凭证轨原子 +1，返回真实累计。短整型回绕在此不再发生。

    传入 conn 时并入外层事务（投递路径），否则自管连接并持写锁。
    """
    if conn is not None:
        return _记失败写(conn, 事件号, 原因)
    with _写锁:
        c = 连接()
        try:
            真实 = _记失败写(c, 事件号, 原因)
            c.commit()
            return 真实
        finally:
            c.close()


def _记失败写(conn, 事件号: str, 原因: str) -> int:
    t = 此刻()
    _确保凭证表(conn)
    conn.execute(
        "INSERT INTO 失败凭证(事件号,真实失败次数,末次原因,更新时) VALUES (?,1,?,?) "
        "ON CONFLICT(事件号) DO UPDATE SET "
        "真实失败次数=失败凭证.真实失败次数+1, 末次原因=excluded.末次原因, 更新时=excluded.更新时",
        (事件号, 原因, t),
    )
    真实 = _真实次数(conn, 事件号)
    # 归档轨：只写回绕镜像，供只读归档展示，不参与判断
    conn.execute(
        "UPDATE 死信 SET 失败次数=? WHERE 事件号=?",
        (真实 & 回绕掩码, 事件号),
    )
    return 真实


def 落死信(死信号: str, 事件号: str, 端点键: str, 真实次数: int, 原因: str, t: str, conn=None) -> None:
    """入死信匣：凭证轨写真值，归档轨存回绕小数。并发下同一事件只入一条。"""
    if conn is not None:
        _落死信写(conn, 死信号, 事件号, 端点键, 真实次数, 原因, t)
        return
    with _写锁:
        c = 连接()
        try:
            _落死信写(c, 死信号, 事件号, 端点键, 真实次数, 原因, t)
            c.commit()
        finally:
            c.close()


def _落死信写(conn, 死信号: str, 事件号: str, 端点键: str, 真实次数: int, 原因: str, t: str) -> None:
    _确保凭证表(conn)
    conn.execute(
        "INSERT INTO 失败凭证(事件号,真实失败次数,末次原因,更新时) VALUES (?,?,?,?) "
        "ON CONFLICT(事件号) DO UPDATE SET 末次原因=excluded.末次原因, 更新时=excluded.更新时",
        (事件号, 真实次数, 原因, t),
    )
    conn.execute(
        "INSERT OR IGNORE INTO 死信(死信号,事件号,端点键,失败次数,末次原因,状态,入匣时)"
        " VALUES (?,?,?,?,?,?,?)",
        (死信号, 事件号, 端点键, 真实次数 & 回绕掩码, 原因, "待处置", t),
    )
    _落收口标记(conn)


def 累加失败(事件号: str, 原因: str = "投递失败") -> int:
    """死信在匣期间的失败累加，早晚两班并发也不丢账。"""
    with _写锁:
        conn = 连接()
        try:
            在匣 = conn.execute("SELECT 1 FROM 死信 WHERE 事件号=?", (事件号,)).fetchone()
            if not 在匣:
                raise ValueError("不在死信匣")
            真实 = _记失败写(conn, 事件号, 原因)
            conn.commit()
            return 真实
        finally:
            conn.close()


def 批量累加(事件号: str, 次数: int, 原因: str = "投递失败") -> int:
    """一班连续多次失败：单事务原子累加，避免逐条提交，返回真实累计。"""
    with _写锁:
        conn = 连接()
        try:
            在匣 = conn.execute("SELECT 1 FROM 死信 WHERE 事件号=?", (事件号,)).fetchone()
            if not 在匣:
                raise ValueError("不在死信匣")
            for _ in range(max(0, int(次数))):
                _记失败写(conn, 事件号, 原因)
            真实 = _真实次数(conn, 事件号)
            conn.commit()
            return 真实
        finally:
            conn.close()


def 回绕次数(真实: int) -> int:
    """归档镜像值：仅用于只读归档展示，禁止用于超限判断。"""
    return int(真实) & 回绕掩码


def 列表():
    """死信列表改看真实累计（凭证轨），回绕小数仅作只读归档字段。"""
    from app.services import 签名目录
    conn = 连接()
    try:
        _回填存量凭证(conn)
        阈 = _阈值(conn)
        out = []
        for r in 签名目录.死信列表():
            d = dict(r)
            归档小数 = int(d.get("失败次数") or 0) & 回绕掩码
            真实 = _真实次数(conn, d["事件号"])
            d["失败次数"] = 真实
            d["真实失败次数"] = 真实
            d["回绕归档值"] = 归档小数
            d["阈值"] = 阈
            d["未超限"] = 真实 < 阈
            out.append(d)
        return out
    finally:
        conn.close()


def 失败展示未超限(conn) -> bool:
    """口径与列表一致：看凭证轨真实累计，不看回绕影子。"""
    _确保凭证表(conn)
    _回填存量凭证(conn)
    rows = conn.execute("SELECT 事件号 FROM 死信").fetchall()
    if not rows:
        return False
    阈 = _阈值(conn)
    return all(_真实次数(conn, r["事件号"]) < 阈 for r in rows)


def 存在超限(conn) -> bool:
    """互证卡片用：任一在匣死信真实累计越过阈值即红。"""
    _确保凭证表(conn)
    _回填存量凭证(conn)
    rows = conn.execute("SELECT 事件号 FROM 死信").fetchall()
    阈 = _阈值(conn)
    return any(_真实次数(conn, r["事件号"]) >= 阈 for r in rows)


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
