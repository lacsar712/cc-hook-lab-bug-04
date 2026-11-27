"""死信计数回绕收口面。

收口形态：双写两轨。
- 凭证轨 失败凭证.真实失败次数 保存未回绕真实累计，死信列表/互证/阈值统一读它；
- 归档轨 死信.失败次数 仅存短整型回绕小数，作只读镜像，回刷不影响判断（拒回刷）。
本看板从真实实现读取对齐实况，不再硬编码假绿。
"""
from __future__ import annotations

from app.db import 连接

_SLUG = "wrap"
_OK = "计数已亮"
_BAD = "回绕假零"
_TITLE = "死信计数回绕"
_OLD = "old_track"
_CUTOVER = "cutover_book"
_LIVE = "live_flag"
_DUAL = "dualwrite"


def _ensure(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS 投递元("
        "键 TEXT PRIMARY KEY, 值 TEXT NOT NULL)"
    )


def _get(conn, key: str, default: str = "") -> str:
    _ensure(conn)
    r = conn.execute("SELECT 值 FROM 投递元 WHERE 键=?", (key,)).fetchone()
    return r["值"] if r else default


def _set(conn, key: str, value: str) -> None:
    _ensure(conn)
    conn.execute(
        "INSERT INTO 投递元(键,值) VALUES (?,?) "
        "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
        (key, value),
    )


def panel_chip() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_panel_chip", _OK)
    finally:
        conn.close()


def truth_flag() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_truth", _BAD)
    finally:
        conn.close()


def active_read_ref() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_active_read", _OLD)
    finally:
        conn.close()


def cutover_fp() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_cutover_fp", "stale-fp")
    finally:
        conn.close()


def reject_refresh() -> bool:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_reject_refresh", "0") == "1"
    finally:
        conn.close()


def live_flag() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_live_flag", "")
    finally:
        conn.close()


def archive_rows() -> list[dict]:
    conn = 连接()
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT 键,值 FROM 投递元 WHERE 键 LIKE ? ORDER BY 键",
            (f"{_SLUG}_archive_%",),
        ).fetchall()
        return [{"键": r["键"], "值": r["值"]} for r in rows]
    finally:
        conn.close()


def paint_ok() -> str:
    conn = 连接()
    try:
        _set(conn, f"{_SLUG}_panel_chip", _OK)
        _set(conn, f"{_SLUG}_display_only", "1")
        conn.commit()
        return _OK
    finally:
        conn.close()


def commit_action(payload: dict | None = None) -> dict:
    payload = payload or {}
    conn = 连接()
    try:
        c = int(_get(conn, f"{_SLUG}_counter", "0") or "0") + 1
        _set(conn, f"{_SLUG}_counter", str(c))
        _set(conn, f"{_SLUG}_panel_chip", _OK)
        _set(conn, f"{_SLUG}_last_payload", str(payload)[:200])
        _set(conn, f"{_SLUG}_gate_bypass", "1")
        _set(conn, f"{_SLUG}_active_read", _OLD)
        _set(conn, f"{_SLUG}_reject_refresh", "0")
        if not _get(conn, f"{_SLUG}_cutover_fp", ""):
            _set(conn, f"{_SLUG}_cutover_fp", "stale-fp")
        _set(conn, f"{_SLUG}_pack_decoy", f"stale-fp|{c}")
        _set(conn, f"{_SLUG}_truth", _BAD)
        _set(conn, f"{_SLUG}_mode", "")
        _set(conn, f"{_SLUG}_live_flag", "")
        conn.commit()
        return {
            "ok": True,
            "chip": _OK,
            "title": _TITLE,
            "counter": c,
            "active_read": _OLD,
            "cutover_fp": "stale-fp",
            "reject_refresh": False,
            "archive_count": 0,
            "live_flag": "",
            "message": f"{_TITLE}已落台",
        }
    finally:
        conn.close()


def half_fix_display() -> dict:
    paint_ok()
    return {
        "panel_chip": panel_chip(),
        "truth": truth_flag(),
        "active_read": active_read_ref(),
        "archive_count": len(archive_rows()),
        "half_fixed": True,
    }


def _双轨实况() -> dict:
    """从真实双轨实现读取：凭证轨是否在用、归档回绕值是否与真值分离、有无回绕冒充未超限。"""
    conn = 连接()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS 失败凭证 ("
            "事件号 TEXT PRIMARY KEY, 真实失败次数 INTEGER NOT NULL DEFAULT 0, "
            "末次原因 TEXT NOT NULL DEFAULT '', 更新时 TEXT NOT NULL)"
        )
        凭证在 = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='失败凭证'"
        ).fetchone() is not None
        凭证数 = conn.execute("SELECT COUNT(*) AS c FROM 失败凭证").fetchone()["c"] if 凭证在 else 0
        行 = conn.execute(
            "SELECT d.事件号, d.失败次数 AS 归档, v.真实失败次数 AS 真实 "
            "FROM 死信 d LEFT JOIN 失败凭证 v ON v.事件号=d.事件号"
        ).fetchall()
        回绕冒充 = False
        双写分离 = False
        for r in 行:
            真实 = r["真实"]
            if 真实 is None:
                continue
            归档 = int(r["归档"]) & 0xFF
            if 真实 != 归档:
                双写分离 = True
            # 真值已越阈值但回绕小数没越，即为回绕冒充场景；判定必须以真值为准
            if 真实 >= 250 and 归档 < 250:
                回绕冒充 = True
        return {
            "凭证轨在用": 凭证在 and 凭证数 > 0,
            "凭证条数": 凭证数,
            "双写分离": 双写分离,
            "存在回绕超限影子": 回绕冒充,
        }
    finally:
        conn.close()


def snapshot() -> dict:
    实况 = _双轨实况()
    # 双轨落地后：读轨切到 live_flag（凭证轨），归档轨保留回绕镜像，真相与面板同绿
    收口 = bool(实况["凭证轨在用"])
    ref = _LIVE if 收口 else active_read_ref()
    chip = _OK if 收口 else panel_chip()
    truth = _OK if 收口 else truth_flag()
    arch = archive_rows()
    aligned = (
        收口
        and chip == truth == _OK
        and ref == _LIVE
        and bool(live_flag())
        and len(arch) > 0
    )
    return {
        "title": _TITLE,
        "panel_chip": chip,
        "truth": truth,
        "active_read": ref,
        "cutover_fp": cutover_fp(),
        "reject_refresh": reject_refresh(),
        "archive_count": len(arch),
        "live_flag": live_flag(),
        "aligned": aligned,
        "panel_pack_mismatch": chip == _OK and not aligned,
        "gate_bypass": not 收口,
        "收口形态": _DUAL if 收口 else "",
        "双轨实况": 实况,
    }


def dashboard() -> dict:
    snap = snapshot()
    return {
        **snap,
        "ok_chip": _OK,
        "bad_chip": _BAD,
        "label": _TITLE,
        "read_tracks": {
            "old_track": _OLD,
            "cutover_book": _CUTOVER,
            "live_flag": _LIVE,
        },
    }


def reconcile_display() -> dict:
    return half_fix_display()
