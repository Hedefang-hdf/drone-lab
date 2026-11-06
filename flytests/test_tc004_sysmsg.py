"""TC-004 系统消息 —— 故障零容忍

判定口径：整条日志不得出现 ERROR 级消息（WARNING 记录但不判失败）。

为什么系统消息要单独立一个 TC：
    姿态、高度、油门、振动都是"飞得好不好"；
    系统消息是"飞控自己报了什么问题"。后者是**一票否决**——
    哪怕姿态再稳，只要日志里有 "EKF variance"、"compass timeout"、
    "battery critical"，这次飞行在测试报告里就是不合格。

面试可讲点：
    这是"功能指标"和"健康指标"的区别。测试报告只写功能指标是外行做法，
    真正会判读的人先扫一遍 ERROR，再看各项数值。
"""

from __future__ import annotations

import pytest


@pytest.mark.safety
def test_no_error_messages(m):
    """全程不得出现 ERROR 级系统消息。"""
    assert not m.errors, (
        f"{m.source}: 出现 {len(m.errors)} 条 ERROR：\n  "
        + "\n  ".join(m.errors[:5])
        + (f"\n  ...（共 {len(m.errors)} 条）" if len(m.errors) > 5 else "")
        + "\nERROR 属于一票否决项，必须先定位根因再谈性能指标。"
    )


@pytest.mark.safety
def test_warnings_are_tracked(m):
    """WARNING 不判失败，但必须被记录（数量与内容进入测试报告）。"""
    if m.warnings:
        pytest.skip(f"记录 {len(m.warnings)} 条 WARNING（不判失败）：{m.warnings[:3]}")
