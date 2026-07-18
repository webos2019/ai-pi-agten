"""工具模块 — 导入时自动注册所有工具与技能"""

from .calculator import register as register_calculator
from .datetime_tool import register as register_datetime
from .text_transform import register as register_text_transform
from .unit_convert import register as register_unit_convert
from .get_location import register as register_get_location
from .get_weather import register as register_get_weather
from .web_browse import register as register_web_browse
from .local_text_read import register as register_local_text_read
from .list_files import register as register_list_files

from tool_registry import tool_registry
from skill_registry import skill_registry


def register_all_tools():
    """注册所有工具与技能"""
    # ── 工具 ──
    register_calculator()
    register_datetime()
    register_text_transform()
    register_unit_convert()
    register_get_location()
    register_get_weather()
    register_web_browse()
    register_local_text_read()
    register_list_files()

    # ── 技能 ──（文件化懒加载，借鉴 Pi progressive disclosure）
    # 启动时只扫描 skills/ 目录建立 id→filepath 索引，不加载 system_prompt 内容；
    # get(skill_id) 首次调用时才读文件解析并缓存。新增/修改 skill 只需增改文件。
    skill_registry.discover()


# 导入时自动注册
register_all_tools()
