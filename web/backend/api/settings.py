# -*- coding: utf-8 -*-
"""
System Settings API - 系统设置接口
Provides CRUD for system-wide settings (PB params, theme, font)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
import math
import os
import sys

from web.backend.config import PROJECT_ROOT
from web.backend.database import get_db
from web.backend.security import require_admin_token
from web.backend.time_utils import sh_timestamp_str

router = APIRouter(dependencies=[Depends(require_admin_token)])

SENSITIVE_SETTINGS = {"tushare_token"}


def _serialize_setting(key: str, value: str, description: str):
    if key in SENSITIVE_SETTINGS:
        return {
            "value": "",
            "description": description,
            "masked": True,
            "has_value": bool(value),
        }
    return {
        "value": value,
        "description": description,
    }

class SettingUpdate(BaseModel):
    value: str

class SettingsBatch(BaseModel):
    settings: Dict[str, str]

class ScoringRulesUpdate(BaseModel):
    content: str  # Raw YAML string

@router.get("/settings")
def get_all_settings():
    """Get all system settings"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT key, value, description FROM system_settings')
        rows = c.fetchall()

    settings = {}
    for row in rows:
        settings[row["key"]] = _serialize_setting(row["key"], row["value"], row["description"])

    return {"status": "success", "data": settings}

# ⚠️ 必须在 /settings/{key} 之前定义，否则会被参数路由拦截
@router.get("/settings/scoring-rules")
def get_scoring_rules():
    """读取 scoring.yaml 原始内容"""
    yaml_path = os.path.join(PROJECT_ROOT, "config", "scoring.yaml")
    if not os.path.exists(yaml_path):
        raise HTTPException(status_code=404, detail="scoring.yaml 文件不存在")
    with open(yaml_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"status": "success", "content": content}

def _validate_scoring_yaml(cfg: dict):
    """业务结构校验（对齐 config/scoring.yaml 真实 schema），失败时抛出 ValueError。

    真实结构（scoring.yaml version 2）：
      positives:
        weights:           ← 在 positives 下，不在顶层
          pb: 0.4
          pe_ttm: 0.4
        pb:
          direction: lower_better   ← 值是 lower_better / higher_better
          anchors_by_industry: ...  ← 在各因子下，不在顶层
        pe_ttm:
          ...
    """
    # 顶层必须有 positives
    if "positives" not in cfg:
        raise ValueError("缺少必需字段: positives")

    positives = cfg["positives"]
    if not isinstance(positives, dict):
        raise ValueError("positives 必须是 dict")

    # positives.weights 必须存在且包含 pb / pe_ttm
    weights = positives.get("weights")
    if not isinstance(weights, dict):
        raise ValueError("positives.weights 必须是 dict")
    for w in ("pb", "pe_ttm"):
        if w not in weights:
            raise ValueError(f"positives.weights 缺少字段: {w}")
        if not isinstance(weights[w], (int, float)):
            raise ValueError(f"positives.weights.{w} 必须是数值，当前: {weights[w]!r}")

    # 各因子（weights 除外）的 direction 只允许 lower_better / higher_better
    valid_directions = {"lower_better", "higher_better"}
    for factor_name, factor_cfg in positives.items():
        if factor_name == "weights" or not isinstance(factor_cfg, dict):
            continue
        direction = factor_cfg.get("direction")
        if direction is not None and direction not in valid_directions:
            raise ValueError(
                f"positives.{factor_name}.direction 非法值: {direction!r}，"
                f"允许值: {sorted(valid_directions)}"
            )
        # anchors_by_industry 若存在，必须是 dict，且每个值必须是有限非负数值
        anchors = factor_cfg.get("anchors_by_industry")
        if anchors is not None:
            if not isinstance(anchors, dict):
                raise ValueError(f"positives.{factor_name}.anchors_by_industry 必须是 dict")
            for ak, av in anchors.items():
                if not isinstance(av, (int, float)) or not math.isfinite(av) or av < 0:
                    raise ValueError(
                        f"positives.{factor_name}.anchors_by_industry.{ak!r} 非法值: {av!r}，"
                        f"必须为有限非负数值"
                    )
        # anchor_default 若存在，必须是有限非负数值
        anchor_default = factor_cfg.get("anchor_default")
        if anchor_default is not None:
            if not isinstance(anchor_default, (int, float)) or not math.isfinite(anchor_default) or anchor_default < 0:
                raise ValueError(
                    f"positives.{factor_name}.anchor_default 非法值: {anchor_default!r}，"
                    f"必须为有限非负数值"
                )


@router.put("/settings/scoring-rules")
def update_scoring_rules(data: ScoringRulesUpdate):
    """保存 scoring.yaml（先校验 YAML 语法和业务结构）"""
    import yaml
    try:
        cfg = yaml.safe_load(data.content)  # 语法校验
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"YAML 语法错误: {e}")

    if not isinstance(cfg, dict):
        raise HTTPException(status_code=400, detail="scoring.yaml 顶层必须是 YAML 映射（dict）")

    try:
        _validate_scoring_yaml(cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"配置结构错误: {e}")

    yaml_path = os.path.join(PROJECT_ROOT, "config", "scoring.yaml")
    backup_path = yaml_path + ".bak"
    if os.path.exists(yaml_path):
        import shutil
        shutil.copy2(yaml_path, backup_path)

    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(data.content)
    return {"status": "success", "message": "scoring.yaml 已保存（原文件已备份为 .bak）"}


# ⚠️ pb-anchors 路由必须放在 /settings/{key} 之前，否则会被参数路由拦截

class PbAnchorsUpdate(BaseModel):
    anchor_default: float
    anchors: Dict[str, float]  # key 是 anchor_key（anchor_industry 语义），value 必须 ≥ 0


@router.get("/settings/pb-anchors")
def get_pb_anchors():
    """获取行业锚点 PB 配置（按 l1/l2 分组展示，用 anchor_industry 作为 canonical key）"""
    import yaml
    from web.backend.database import get_db

    # 1. 读取 scoring.yaml
    yaml_path = os.path.join(PROJECT_ROOT, "config", "scoring.yaml")
    if not os.path.exists(yaml_path):
        raise HTTPException(status_code=404, detail="scoring.yaml 文件不存在")
    with open(yaml_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    pb_cfg = cfg.get("positives", {}).get("pb", {})
    anchor_default = float(pb_cfg.get("anchor_default", 1.0))
    anchors_by_industry = pb_cfg.get("anchors_by_industry") or {}

    # 2. 从 stock_industry 表获取行业层级
    try:
        with get_db() as conn:
            count_row = conn.execute("SELECT COUNT(*) FROM stock_industry").fetchone()
            db_count = count_row[0] if count_row else 0
    except Exception:
        db_count = 0

    if db_count == 0:
        return {
            "status": "success",
            "anchor_default": anchor_default,
            "industries": [],
            "warning": "请先同步行业分类数据",
        }

    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT tdx_l1, tdx_l2, anchor_industry "
            "FROM stock_industry "
            "WHERE tdx_l1 != '' AND tdx_l2 != '' "
            "ORDER BY tdx_l1, tdx_l2"
        ).fetchall()

    # 3. 按 l1 分组，构建返回结构
    groups: dict = {}
    l1_order: list = []
    for row in rows:
        l1 = row["tdx_l1"]
        l2 = row["tdx_l2"]
        ak = row["anchor_industry"] or l2  # anchor_key = anchor_industry

        if l1 not in groups:
            groups[l1] = []
            l1_order.append(l1)

        # anchor 值：优先匹配 anchor_key，匹配不到用 anchor_default
        if ak in anchors_by_industry:
            anchor_val = float(anchors_by_industry[ak])
            source = "explicit"
        else:
            anchor_val = anchor_default
            source = "default"

        groups[l1].append({
            "l2": l2,
            "anchor_key": ak,
            "anchor": anchor_val,
            "source": source,
        })

    industries = [{"l1": l1, "children": groups[l1]} for l1 in l1_order]

    return {
        "status": "success",
        "anchor_default": anchor_default,
        "industries": industries,
    }


@router.put("/settings/pb-anchors")
def update_pb_anchors(data: PbAnchorsUpdate):
    """更新 scoring.yaml 中的 positives.pb 锚点子树（先验后写，原子替换）"""
    import yaml
    import tempfile
    import shutil

    # 1. Pydantic 层已保证类型，这里做业务值校验（有限 + 非负）
    if not math.isfinite(data.anchor_default) or data.anchor_default < 0:
        raise HTTPException(status_code=400, detail="anchor_default 必须为有限非负数值")
    for k, v in data.anchors.items():
        if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise HTTPException(status_code=400, detail=f"锚点值非法：{k} = {v!r}，必须为有限非负数值")

    yaml_path = os.path.join(PROJECT_ROOT, "config", "scoring.yaml")
    if not os.path.exists(yaml_path):
        raise HTTPException(status_code=404, detail="scoring.yaml 文件不存在")

    # 2. 读取并解析当前 YAML
    with open(yaml_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise HTTPException(status_code=500, detail="scoring.yaml 格式异常")

    # 3. 在内存中修改 PB 子树（保留其他所有内容不变）
    if "positives" not in cfg or not isinstance(cfg["positives"], dict):
        raise HTTPException(status_code=500, detail="scoring.yaml 缺少 positives 段")
    if "pb" not in cfg["positives"] or not isinstance(cfg["positives"]["pb"], dict):
        raise HTTPException(status_code=500, detail="scoring.yaml 缺少 positives.pb 段")

    cfg["positives"]["pb"]["anchor_default"] = data.anchor_default
    cfg["positives"]["pb"]["anchors_by_industry"] = dict(data.anchors)

    # 4. 校验修改后的完整 cfg（先验后写）
    try:
        _validate_scoring_yaml(cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"配置结构错误: {e}")

    # 5. 校验通过后：备份 + 原子写回
    backup_path = yaml_path + ".bak"
    shutil.copy2(yaml_path, backup_path)

    yaml_dir = os.path.dirname(yaml_path)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=yaml_dir, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, yaml_path)  # 原子替换
    except Exception as e:
        # 写临时文件失败时清理
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"写入配置失败: {e}")

    return {"status": "success", "message": "行业锚点 PB 已保存（原文件已备份为 .bak）"}


@router.get("/settings/{key}")
def get_setting(key: str):
    """Get a single setting by key"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT value, description FROM system_settings WHERE key = ?', (key,))
        row = c.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")

    return {
        "status": "success",
        "key": key,
        **_serialize_setting(key, row["value"], row["description"]),
    }

@router.put("/settings/{key}")
def update_setting(key: str, data: SettingUpdate):
    """Update a single setting"""
    updated_at = sh_timestamp_str()
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE system_settings
            SET value = ?, updated_at = ?
            WHERE key = ?
        ''', (data.value, updated_at, key))

        if c.rowcount == 0:
            c.execute('''
                INSERT INTO system_settings (key, value, updated_at)
                VALUES (?, ?, ?)
            ''', (key, data.value, updated_at))

        conn.commit()

    return {"status": "success", "message": f"Setting '{key}' updated"}

@router.put("/settings")
def update_settings_batch(data: SettingsBatch):
    """Update multiple settings at once"""
    updated_at = sh_timestamp_str()
    with get_db() as conn:
        c = conn.cursor()
        updated = 0
        for key, value in data.settings.items():
            c.execute('''
                UPDATE system_settings
                SET value = ?, updated_at = ?
                WHERE key = ?
            ''', (value, updated_at, key))

            if c.rowcount == 0:
                c.execute('''
                    INSERT INTO system_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                ''', (key, value, updated_at))

            updated += 1

        conn.commit()

    return {"status": "success", "message": f"Updated {updated} settings"}

# --- Helper functions for other modules ---

def get_setting_value(key: str, default: str = None) -> str:
    """Get a setting value (for internal use by other modules)"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT value FROM system_settings WHERE key = ?', (key,))
        row = c.fetchone()

    if row:
        return row["value"]
    return default

def get_pb_settings() -> dict:
    """Get PB-related settings for position calculation"""
    return {
        "pb_bottom": float(get_setting_value("pb_bottom", "1.0")),
        "pb_average": float(get_setting_value("pb_average", "2.5")),
        "max_position": float(get_setting_value("max_position", "0.95"))
    }
