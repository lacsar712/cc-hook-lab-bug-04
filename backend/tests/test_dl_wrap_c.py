"""争用失败测（放后）：早晚两班一起累加同一死信，小数互相盖住 —— 加红。

并发双请求累加同一事件/同一死信：
- 死信失败次数必须等于投递尝试里的真实失败条数（账实一致，不能停在回绕小数）；
- 并发不得抛库错误、不得丢累加、不得因唯一约束让一班的失败蒸发；
- 越过 256 回绕点后，计数仍是真实累计，回绕小数不冒充未超限。
"""
import os
import sqlite3
import tempfile
import threading
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ['数据目录'] = _tmp
os.environ['夹具目录'] = str(Path(__file__).resolve().parents[2] / 'fixtures')

from app import seed
from app.db import 连接
from app.services import 入队, 投递, 死信


def setup_module():
    seed.初始化库()


def _真实失败条数(事件号: str) -> int:
    conn = 连接()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM 投递尝试 WHERE 事件号=? AND 状态='失败'",
            (事件号,),
        ).fetchone()['c']
    finally:
        conn.close()


def _并发投递(事件号: str, 每班次数: int):
    报错 = []

    def 一班():
        for _ in range(每班次数):
            try:
                投递.投递一次(事件号, 强制失败=True)
            except ValueError:
                # 事件已转死信后再投会被业务拒绝，属预期，不算丢账
                pass
            except sqlite3.Error as e:  # pragma: no cover - 见红路径
                报错.append(e)

    t1 = threading.Thread(target=一班)
    t2 = threading.Thread(target=一班)
    t1.start(); t2.start()
    t1.join(); t2.join()
    return 报错


def _并发累加死信(事件号: str, 每班次数: int):
    """早晚两班对同一死信匣记录并发累加（每班一批，单事务原子）。"""
    报错 = []

    def 一班():
        try:
            死信.批量累加(事件号, 每班次数)
        except (ValueError, sqlite3.Error) as e:  # pragma: no cover - 见红路径
            报错.append(e)

    t1 = threading.Thread(target=一班)
    t2 = threading.Thread(target=一班)
    t1.start(); t2.start()
    t1.join(); t2.join()
    return 报错


def test_并发投递不抛库错误():
    ev = 入队.建事件({'事件类型': 'race.fail', '端点键': 'ep-billing', '载荷摘要': 'race-1'})
    报错 = _并发投递(ev['事件号'], 6)
    assert not 报错, f'并发投递触发库错误：{报错}'


def test_并发投递后进唯一一条死信且账实相符():
    ev = 入队.建事件({'事件类型': 'race.fail', '端点键': 'ep-billing', '载荷摘要': 'race-2'})
    事件号 = ev['事件号']
    _并发投递(事件号, 6)

    真实 = _真实失败条数(事件号)
    assert 真实 >= 2, '并发请求的失败尝试丢了'

    conn = 连接()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM 死信 WHERE 事件号=?", (事件号,)
        ).fetchone()['c']
    finally:
        conn.close()
    assert n == 1, f'同一事件出现 {n} 条死信，并发唯一约束没接住'

    row = next(r for r in 死信.列表() if r['事件号'] == 事件号)
    assert row['失败次数'] == 真实, (
        f'账实不符：死信失败次数 {row["失败次数"]}，真实失败条数 {真实}，小数互相盖住'
    )


def test_并发累加同一死信不丢次数():
    ev = 入队.建事件({'事件类型': 'race.fail', '端点键': 'ep-audit', '载荷摘要': 'race-3'})
    事件号 = ev['事件号']
    # 先打进死信匣
    for _ in range(5):
        r = 投递.投递一次(事件号, 强制失败=True)
        if r.get('事件状态') == '死信':
            break
    基线 = _真实失败条数(事件号)

    报错 = _并发累加死信(事件号, 50)
    assert not 报错, f'并发累加触发库错误：{报错}'

    row = next(r for r in 死信.列表() if r['事件号'] == 事件号)
    assert row['失败次数'] == 基线 + 100, (
        f'并发累加被互相盖住：期望 {基线 + 100}，实为 {row["失败次数"]}'
    )


def test_并发累加越过回绕点仍以真实累计为准():
    """两班各 130，共 260 次累加，越过 256 回绕点后账实不停在小数。"""
    ev = 入队.建事件({'事件类型': 'race.fail', '端点键': 'ep-partner', '载荷摘要': 'race-4'})
    事件号 = ev['事件号']
    for _ in range(5):
        r = 投递.投递一次(事件号, 强制失败=True)
        if r.get('事件状态') == '死信':
            break
    基线 = _真实失败条数(事件号)

    报错 = _并发累加死信(事件号, 130)
    assert not 报错
    期望 = 基线 + 260
    assert 期望 > 256, f'夹具前提：应越过回绕点，期望累计 {期望}'

    row = next(r for r in 死信.列表() if r['事件号'] == 事件号)
    assert row['失败次数'] == 期望, '越回绕点后死信计数停在回绕小数'
    assert row['失败次数'] & 0xFF != row['失败次数'], '累计未越过回绕点，前提失效'
    assert row['未超限'] is False, '越过回绕点仍被判未超限'
