"""失败测甲（放前）：次数回绕成小数后仍判未超限 —— 绕红。

夹具键：次数将回绕。库内 257 次失败被短整型回绕成小数 1，
死信列表与阈值判断必须看真实累计，回绕小数不能冒充未超限。
"""
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ['数据目录'] = _tmp
os.environ['夹具目录'] = str(Path(__file__).resolve().parents[2] / 'fixtures')

from app import seed
from app.db import 连接
from app.services import 夹具, 死信

阈值 = 250


def _造回绕死信() -> str:
    """按夹具 次数将回绕 造一条真实 257 次、库内回绕成 1 的死信。"""
    样 = 夹具.读取('次数将回绕')
    事件号 = 'EVT-WRAP-A-257'
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
            ('DL-WRAP-A-257', 事件号, 'ep-audit', 真实次数 & 0xFF, '回绕假零', '待处置', t),
        )
        conn.commit()
    finally:
        conn.close()
    return 事件号


def setup_module():
    seed.初始化库()
    _造回绕死信()


def test_库内确实停在回绕小数():
    """前置事实：死信表里的裸值是回绕后的小数。"""
    conn = 连接()
    try:
        裸值 = conn.execute(
            "SELECT 失败次数 FROM 死信 WHERE 事件号='EVT-WRAP-A-257'"
        ).fetchone()['失败次数']
    finally:
        conn.close()
    assert 裸值 == 1, f'夹具前提不成立：库内应为回绕小数 1，实为 {裸值}'


def test_死信列表展示真实累计而非回绕小数():
    rows = 死信.列表()
    row = next(r for r in rows if r['事件号'] == 'EVT-WRAP-A-257')
    assert row['失败次数'] == 257, f'死信列表仍停在回绕小数：{row["失败次数"]}'


def test_回绕小数不得判未超限():
    rows = 死信.列表()
    row = next(r for r in rows if r['事件号'] == 'EVT-WRAP-A-257')
    assert row['失败次数'] >= 阈值
    assert row['未超限'] is False, '回绕小数冒充了未超限'


def test_失败展示未超限不认回绕影子():
    conn = 连接()
    try:
        assert 死信.失败展示未超限(conn) is False, '回绕影子被当成全部未超限'
    finally:
        conn.close()


def test_阈值设置按真实累计判定():
    """阈值 250 对比的必须是真实累计 257，而不是回绕值 1。"""
    row = next(r for r in 死信.列表() if r['事件号'] == 'EVT-WRAP-A-257')
    真实 = int(row['失败次数'])
    assert 真实 >= 阈值, '阈值各说各话：阈值看小数，账实停在回绕值上'
    assert row['未超限'] is False
    assert row['回绕归档值'] == 1, '只读归档轨仍保留回绕小数镜像'
