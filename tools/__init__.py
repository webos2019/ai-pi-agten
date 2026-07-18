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
from skill_registry import skill_registry, SkillMeta


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

    # ── 技能 ──
    skill_registry.register(SkillMeta(
        id="utility-skill",
        name="实用工具",
        description="提供计算器、日期时间、单位换算等实用工具能力",
        system_prompt=(
            "你是一个实用工具助手，擅长使用各种工具解决用户问题。"
            "对于数学计算、日期查询、单位换算等问题，请使用相应工具获取准确结果。"
        ),
        tool_names=["calculator", "datetime", "unit_convert", "get_location", "get_weather"],
        output_policy="concise-utility",
        result_policy="tool-first",
        routing_hints=["计算", "时间", "换算", "天气", "位置"],
        tags=["utility", "calculator", "datetime", "weather"],
        fallback_policy="direct-answer",
        default=True,
    ))

    skill_registry.register(SkillMeta(
        id="reader-skill",
        name="信息读取",
        description="提供本地文件读取、网页浏览等信息获取能力",
        system_prompt=(
            "你是一个信息读取助手，擅长读取本地文件和浏览网页。"
            "对于需要查看文件内容或获取实时信息的请求，请使用相应工具。"
        ),
        tool_names=["local-text-read", "list_files", "web_browse", "get_weather", "get_location"],
        output_policy="detailed-explanation",
        result_policy="summary-first",
        routing_hints=["文件", "读取", "浏览", "查看", "内容"],
        tags=["reader", "file", "web", "information"],
        fallback_policy="skip-capability",
    ))


# 导入时自动注册
register_all_tools()
