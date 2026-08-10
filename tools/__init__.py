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
from .sub_agent_tool import register as register_sub_agent
from .web_search import register as register_web_search
from .web_fetch import register as register_web_fetch
from .github_repo import register as register_github_repo
from .youtube_analyze import register as register_youtube_analyze
from .pdf_extract import register as register_pdf_extract
from .wechat_article import register as register_wechat_article
from .stock_quote import register as register_stock_quote
from .stock_analysis import register as register_stock_analysis
from .chanlun_analysis import register as register_chanlun_analysis
from .service_check import register as register_service_check
from .log_search import register as register_log_search
from .system_monitor import register as register_system_monitor
from .db_diagnose import register as register_db_diagnose

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
    register_sub_agent()
    register_web_search()
    register_web_fetch()
    register_github_repo()
    register_youtube_analyze()
    register_pdf_extract()
    register_wechat_article()
    register_stock_quote()
    register_stock_analysis()
    register_chanlun_analysis()
    register_service_check()
    register_log_search()
    register_system_monitor()
    register_db_diagnose()

    # ── 技能 ──（文件化懒加载，借鉴 Pi progressive disclosure）
    # 启动时只扫描 skills/ 目录建立 id→filepath 索引，不加载 system_prompt 内容；
    # get(skill_id) 首次调用时才读文件解析并缓存。新增/修改 skill 只需增改文件。
    skill_registry.discover()


# 导入时自动注册
register_all_tools()
