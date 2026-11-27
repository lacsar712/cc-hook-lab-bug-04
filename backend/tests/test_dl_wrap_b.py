"""失败测乙（放中）：次数对账把回绕影子误标成未超限 —— 数红。

互证卡片不得拿 失败展示未超限 的回绕结果强行置“对齐”。
存在回绕超限时，互证必须翻红并在说明里点破。
"""
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ['数据目录'] = _tmp
os.environ['夹具目录'] = str(Path(__file__).resolve().parents[2] / 'fixtures')

from app import seed
from app.db import 连接
from app.services import 夹具, 死信, 互证

阈值 = 250


def _造回绕死信() -> str:
    样 = 夹具.读取('次数将回绕')
    事件号 = 'EVT-WRAP-B-257'
    真实次数 = int(样['失败次数'])
    t = '2026-11-27T00:00:00Z'
    conn = 连接()
    try:
        conn.execute(
            "INSERT INTO 事件(事件号,事件类型,端点键,载荷摘要,优先级,状态,入队时,更新时)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (事件号, 'sync.fail', 'ep-audit', 'wrap=257', '紧急', '死信', t, t),
        )
        conn.execute(
            "INSERT INTO 死信(死信号,事件号,端点键,失败次数,末次原因,状态,入匣时)"
            " VALUES (?,?,?,?,?,?,?)",
            ('DL-WRAP-B-257', 事件号, 'ep-audit', 真实次数 & 0xFF, '回绕假零', '待处置', t),
        )
        conn.commit()
    finally:
        conn.close()
    return 事件号


def setup_module():
    seed.初始化库()
    _造回绕死信()


def test_互证不被回绕影子置对齐():
    rep = 互证.跑一次()
    assert rep['是否对齐'] is False, '互证把回绕影子误标成未超限，假绿'
    assert rep['是否全部对齐'] is False


def test_互证说明点破回绕超限():
    rep = 互证.跑一次()
    assert ('回绕' in rep['说明']) or ('超限' in rep['说明']) or ('失败次数' in rep['说明']), \
        f'互证说明未点破真实累计超限：{rep["说明"]}'


def test_互证批次落库也是红账():
    互证.跑一次()
    conn = 连接()
    try:
        末批 = conn.execute(
            "SELECT * FROM 互证批次 ORDER BY 批次号 DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert int(末批['是否对齐']) == 0, '红账被写成绿账'


def test_回绕超限与展示未超限口径一致():
    """死信口径与互证口径必须同源：都看真实累计。"""
    conn = 连接()
    try:
        assert 死信.失败展示未超限(conn) is False
    finally:
        conn.close()
    assert 互证.跑一次()['是否对齐'] is False
