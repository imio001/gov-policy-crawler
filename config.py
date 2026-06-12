"""
爬虫配置文件
"""
import os
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).parent

# 数据存储目录
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "log"

# 创建必要的目录
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# 爬虫配置
MAX_PAGES = 5  # 爬取页数
REQUEST_DELAY = (2, 4)  # 请求间隔（秒）
PAGE_LOAD_WAIT = 10  # 页面加载等待时间（秒）

# 网站配置
BASE_URL = "https://sousuo.www.gov.cn"
LIST_URL = "https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary?q=&t=zhengcelibrary&orpro=%2Frobots.txt"

# User-Agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"

# 日志配置
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR
LOG_RETENTION_DAYS = 30  # 日志保留天数

# 通知配置（是否启用弹窗通知）
ENABLE_POPUP_NOTIFICATION = True

# 选择器配置（用于应对网站版式变化，可随时调整）
SELECTORS = {
    "article_link": "a[href*='/zhengce/zhengceku/']",
    "article_title": "h5.dysMiddleResultConItemTitle",
    "next_button": "button.btn-next",
    "detail_table": "tbody",
    "content_container": ["div.pages_content", "div#UCAP-CONTENT", "div.TRS_Editor", "div.trs_editor_view"],
    "title_element": "h1",
}

# 正则表达式模式（用于部门文件）
REGEX_PATTERNS = {
    "doc_number": r'([\u4e00-\u9fa5]+〔\d{4}〕\d+号)',  # 发文字号
    "date": r'(\d{4}年\d{1,2}月\d{1,2}日)',  # 日期
    "index_number": r'(\d{6,}/\d{5,}|\d{6,}-\d{5,})',  # 索引号
}

# 字段标签映射（用于表格解析）
FIELD_MAPPINGS = {
    "索引号": "index_number",
    "主题分类": "category",
    "发文机关": "issuing_authority",
    "成文日期": "draft_date",
    "标　　题": "title",
    "发文字号": "doc_number",
    "发布日期": "publish_date",
}