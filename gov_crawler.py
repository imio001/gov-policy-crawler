"""
政府政策文件爬虫
支持两种页面格式（表格和文本），具备健壮性和容错能力
"""
import time
import random
import re
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from functools import wraps

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException,
    WebDriverException, StaleElementReferenceException,
    InvalidSessionIdException
)
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options

from config import (
    DATA_DIR, LOG_DIR, MAX_PAGES, REQUEST_DELAY, PAGE_LOAD_WAIT,
    BASE_URL, LIST_URL, USER_AGENT, SELECTORS, REGEX_PATTERNS
)


# ==================== 日志配置 ====================

def setup_logger(name: str = "gov_crawler") -> logging.Logger:
    """配置日志系统"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 避免重复添加handler
    if logger.handlers:
        return logger

    # 日志格式
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 控制台输出
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件输出 - 按日期分文件
    log_file = LOG_DIR / f"crawler_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


# ==================== 通知系统 ====================

class NotificationSystem:
    """通知系统 - 支持弹窗和日志"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def send_alert(self, title: str, message: str, level: str = "warning"):
        """发送预警通知"""
        self.logger.error(f"[{level.upper()}] {title}: {message}")

        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x30)
        except Exception as e:
            self.logger.debug(f"弹窗通知失败: {e}")

    def send_info(self, title: str, message: str):
        """发送信息通知"""
        self.logger.info(f"{title}: {message}")

    def send_success(self, title: str, message: str):
        """发送成功通知"""
        self.logger.info(f"[SUCCESS] {title}: {message}")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
        except Exception as e:
            self.logger.debug(f"弹窗通知失败: {e}")


# ==================== 重试装饰器 ====================

def retry_on_failure(max_retries: int = 3, delay: float = 2.0, backoff: float = 2.0):
    """失败重试装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        time.sleep(current_delay)
                        current_delay *= backoff
            raise last_exception
        return wrapper
    return decorator


# ==================== 数据类 ====================

@dataclass
class PolicyData:
    """政策数据类"""
    article_title: str = ""
    title: str = ""
    index_number: str = ""
    category: str = ""
    issuing_authority: str = ""
    doc_number: str = ""
    draft_date: str = ""
    publish_date: str = ""
    content: str = ""
    url: str = ""

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "文章标题": self.article_title,
            "标题": self.title,
            "索引号": self.index_number,
            "主题分类": self.category,
            "发文机关": self.issuing_authority,
            "发文字号": self.doc_number,
            "成文日期": self.draft_date,
            "发布日期": self.publish_date,
            "正文": self.content,
            "文章链接": self.url,
        }


# ==================== 浏览器管理器 ====================

class BrowserManager:
    """浏览器驱动管理器"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.driver = None

    def init_driver(self) -> webdriver.Edge:
        """初始化浏览器驱动 - 支持无头模式"""
        try:
            edge_options = Options()

            # 基础设置
            edge_options.add_argument('--disable-gpu')
            edge_options.add_argument('--no-sandbox')
            edge_options.add_argument('--disable-dev-shm-usage')
            edge_options.add_argument('--user-agent=' + USER_AGENT)
            edge_options.add_argument('--disable-extensions')
            edge_options.add_argument('--disable-notifications')

            # 【关键】检查是否在 GitHub Actions 环境中运行
            import os
            if os.environ.get('HEADLESS') == 'true' or os.environ.get('GITHUB_ACTIONS') == 'true':
                edge_options.add_argument('--headless')  # 无头模式
                edge_options.add_argument('--window-size=1920,1080')
                print("✅ 已启用无头模式（GitHub Actions）")

            # 页面加载策略
            edge_options.page_load_strategy = 'eager'

            # 禁用日志
            edge_options.add_experimental_option('excludeSwitches', ['enable-logging', 'enable-automation'])

            service = Service(EdgeChromiumDriverManager().install())
            service.send_remote_websocket_requests = False

            self.driver = webdriver.Edge(service=service, options=edge_options)
            self.driver.set_page_load_timeout(30)

            self.logger.info("浏览器驱动初始化成功")
            return self.driver
        except Exception as e:
            self.logger.error(f"浏览器驱动初始化失败: {e}")
            raise

    def quit(self):
        """关闭浏览器"""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            self.logger.info("浏览器已关闭")


# ==================== 主爬虫类 ====================

class GovernmentPolicyCrawler:
    """政府政策文件爬虫"""

    def __init__(self, logger: logging.Logger = None):
        self.logger = logger or setup_logger()
        self.notifier = NotificationSystem(self.logger)
        self.browser_manager = BrowserManager(self.logger)
        self.driver = None
        self.retry_count = 0
        self.max_retries = 3

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """启动爬虫"""
        try:
            self.driver = self.browser_manager.init_driver()
            self.logger.info("爬虫启动成功")
        except Exception as e:
            self.logger.error(f"爬虫启动失败: {e}")
            self.notifier.send_alert("爬虫启动失败", str(e))
            raise

    def stop(self):
        """停止爬虫"""
        self.browser_manager.quit()
        self.logger.info("爬虫已停止")

    def safe_get(self, url: str, max_retries: int = 2):
        """安全地访问URL，处理超时问题"""
        for attempt in range(max_retries + 1):
            try:
                self.driver.get(url)
                return True
            except TimeoutException:
                self.logger.warning(f"访问超时，尝试 {attempt + 1}/{max_retries}: {url[:80]}")
                if attempt < max_retries:
                    # 尝试停止页面加载
                    try:
                        self.driver.execute_script("window.stop();")
                    except:
                        pass
                    time.sleep(3)
                else:
                    raise
            except Exception as e:
                self.logger.error(f"访问失败: {e}")
                raise
        return False

    def safe_find_element(self, by: str, value: str, timeout: int = PAGE_LOAD_WAIT, raise_error: bool = False):
        """安全查找元素"""
        try:
            element = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
            return element
        except TimeoutException:
            if raise_error:
                raise
            return None

    def safe_find_elements(self, by: str, value: str, timeout: int = PAGE_LOAD_WAIT):
        """安全查找多个元素"""
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
            return self.driver.find_elements(by, value)
        except TimeoutException:
            return []

    def wait_for_articles(self) -> bool:
        """等待文章列表加载 - 增加重试机制"""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                WebDriverWait(self.driver, PAGE_LOAD_WAIT).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["article_link"]))
                )
                return True
            except TimeoutException:
                self.logger.warning(f"等待文章列表超时，尝试 {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    # 刷新页面重试
                    self.driver.refresh()
                    time.sleep(3)
                else:
                    self.logger.error("等待文章列表失败")
                    return False
        return False

    def get_article_links(self) -> List[Dict]:
        """获取当前页的所有文章链接"""
        articles = []
        try:
            # 等待页面稳定
            time.sleep(2)

            # 尝试主选择器
            link_elements = self.safe_find_elements(By.CSS_SELECTOR, SELECTORS["article_link"], timeout=5)

            # 备用选择器 - 应对版式变化
            if not link_elements:
                self.logger.warning("主选择器未找到文章，尝试备用选择器...")
                link_elements = self.safe_find_elements(By.CSS_SELECTOR, "a[href*='/content_']", timeout=5)

            if not link_elements:
                # 尝试查找所有a标签中的政策链接
                all_links = self.driver.find_elements(By.TAG_NAME, "a")
                for link in all_links:
                    href = link.get_attribute('href')
                    if href and ('/zhengce/' in href or '/content_' in href):
                        text = link.text.strip()
                        if text and len(text) > 5:
                            link_elements.append(link)

            if not link_elements:
                self.logger.warning("所有选择器都未找到文章链接")
                return []

            for elem in link_elements:
                try:
                    # 查找标题元素
                    title = elem.text.strip()

                    # 如果标题为空，尝试查找子元素
                    if not title:
                        title_elem = elem.find_element(By.TAG_NAME, "h5") if elem.find_elements(By.TAG_NAME, "h5") else None
                        if title_elem:
                            title = title_elem.text.strip()

                    href = elem.get_attribute('href')
                    if title and href and len(title) > 5:
                        articles.append({'title': title, 'url': href})
                except StaleElementReferenceException:
                    continue
                except Exception as e:
                    self.logger.debug(f"解析文章元素失败: {e}")

            self.logger.info(f"找到 {len(articles)} 篇文章")
            return articles
        except Exception as e:
            self.logger.error(f"获取文章链接失败: {e}")
            return []

    def click_next_page(self) -> bool:
        """点击下一页 - 增强版"""
        try:
            # 等待一下确保页面稳定
            time.sleep(2)

            # 先滚动到页面底部，确保下一页按钮可见
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

            next_btn_selectors = [
                "button.btn-next",
                ".btn-next",
                "a.next",
                ".pagination .next",
                "[aria-label='下一页']",
                "li.next a"
            ]

            for selector in next_btn_selectors:
                try:
                    btns = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for btn in btns:
                        if btn.is_displayed() and btn.is_enabled():
                            # 滚动到按钮位置
                            self.driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                            time.sleep(0.5)
                            # 尝试普通点击
                            try:
                                btn.click()
                            except:
                                # 如果普通点击失败，使用JS点击
                                self.driver.execute_script("arguments[0].click();", btn)

                            self.logger.info(f"点击下一页成功: {selector}")
                            time.sleep(2)
                            return True
                except:
                    continue

            # 尝试通过XPath查找
            xpath_patterns = [
                "//button[contains(text(), '下一页')]",
                "//a[contains(text(), '下一页')]",
                "//span[contains(text(), '下一页')]/parent::button",
                "//span[contains(text(), '下一页')]/parent::a"
            ]

            for xpath in xpath_patterns:
                try:
                    elements = self.driver.find_elements(By.XPATH, xpath)
                    for elem in elements:
                        if elem.is_displayed() and elem.is_enabled():
                            self.driver.execute_script("arguments[0].scrollIntoView(true);", elem)
                            time.sleep(0.5)
                            elem.click()
                            self.logger.info(f"通过XPath点击下一页成功: {xpath}")
                            time.sleep(2)
                            return True
                except:
                    continue

            self.logger.info("未找到可用的下一页按钮")
            return False
        except Exception as e:
            self.logger.error(f"点击下一页失败: {e}")
            return False

    def parse_table_metadata(self) -> PolicyData:
        """解析表格格式的元数据（国务院文件）"""
        data = PolicyData()

        try:
            rows = self.safe_find_elements(By.CSS_SELECTOR, f"{SELECTORS['detail_table']} tr", timeout=5)

            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                for i, cell in enumerate(cells):
                    cell_text = cell.text.strip()

                    if '索 引 号：' in cell_text and i + 1 < len(cells):
                        data.index_number = cells[i + 1].text.strip()
                    elif '主题分类：' in cell_text and i + 1 < len(cells):
                        data.category = cells[i + 1].text.strip()
                    elif '发文机关：' in cell_text and i + 1 < len(cells):
                        data.issuing_authority = cells[i + 1].text.strip()
                    elif '成文日期：' in cell_text and i + 1 < len(cells):
                        data.draft_date = cells[i + 1].text.strip()
                    elif '标　　题：' in cell_text and i + 1 < len(cells):
                        data.title = cells[i + 1].text.strip()
                    elif '发文字号：' in cell_text and i + 1 < len(cells):
                        data.doc_number = cells[i + 1].text.strip()
                    elif '发布日期：' in cell_text and i + 1 < len(cells):
                        data.publish_date = cells[i + 1].text.strip()
        except Exception as e:
            self.logger.debug(f"表格解析失败: {e}")

        return data

    def parse_text_metadata(self) -> PolicyData:
        """解析文本格式的元数据（部门文件）"""
        data = PolicyData()

        try:
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            lines = page_text.split('\n')

            # 提取发文字号
            for line in lines[:30]:
                doc_match = re.search(REGEX_PATTERNS["doc_number"], line)
                if doc_match and not data.doc_number:
                    data.doc_number = doc_match.group(1)
                    # 提取发文机关
                    idx = line.find(data.doc_number)
                    if idx > 0:
                        organ = line[:idx].strip()
                        if organ and len(organ) < 50:
                            data.issuing_authority = organ

            # 提取日期
            all_dates = re.findall(REGEX_PATTERNS["date"], page_text[:3000])
            if all_dates:
                data.draft_date = all_dates[0]
                if len(all_dates) > 1:
                    data.publish_date = all_dates[1]

            # 提取索引号
            index_match = re.search(REGEX_PATTERNS["index_number"], page_text[:2000])
            if index_match:
                data.index_number = index_match.group(1)

            # 提取标题
            try:
                title_elem = self.driver.find_element(By.TAG_NAME, "h1")
                data.title = title_elem.text.strip()
            except:
                pass

            data.category = "部门文件（未分类）"
        except Exception as e:
            self.logger.debug(f"文本解析失败: {e}")

        return data

    def extract_content(self) -> str:
        """提取正文内容"""
        try:
            for selector in SELECTORS["content_container"]:
                try:
                    content_div = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if content_div:
                        paragraphs = content_div.find_elements(By.TAG_NAME, "p")
                        content_texts = []
                        for p in paragraphs:
                            text = p.text.strip()
                            if text and len(text) > 10:
                                content_texts.append(text)

                        if content_texts:
                            return '\n\n'.join(content_texts)
                except:
                    continue

            return "正文提取失败：未找到正文容器"
        except Exception as e:
            self.logger.error(f"提取正文失败: {e}")
            return f"提取正文出错: {str(e)[:100]}"

    def parse_detail_page(self, url: str, article_title: str) -> PolicyData:
        """解析文章详情页 - 增加超时处理"""
        # 使用safe_get避免超时
        try:
            self.safe_get(url, max_retries=2)
        except Exception as e:
            self.logger.error(f"访问详情页失败: {url} - {e}")
            data = PolicyData(article_title=article_title, url=url)
            data.content = f"页面访问失败: {str(e)}"
            return data

        time.sleep(random.uniform(1, 2))

        data = PolicyData(article_title=article_title, url=url)

        try:
            # 尝试表格解析
            has_table = self.safe_find_element(By.TAG_NAME, "tbody", timeout=3)
            if has_table:
                data = self.parse_table_metadata()
                data.article_title = article_title
                data.url = url
            else:
                data = self.parse_text_metadata()
                data.article_title = article_title
                data.url = url

            # 提取正文
            data.content = self.extract_content()

            # 如果标题为空，使用文章标题
            if not data.title:
                data.title = article_title

            self.logger.debug(f"解析成功: {article_title[:30]}")
            return data
        except Exception as e:
            self.logger.error(f"解析详情页失败: {e}")
            data.content = f"解析失败: {str(e)}"
            return data

    def save_to_excel(self, all_data: List[PolicyData]) -> str:
        """保存数据到Excel"""
        try:
            df_data = [d.to_dict() for d in all_data]
            df = pd.DataFrame(df_data)

            columns_order = ['文章标题', '标题', '索引号', '主题分类', '发文机关',
                            '发文字号', '成文日期', '发布日期', '正文', '文章链接']
            df = df[columns_order]

            filename = f"gov_policies_{datetime.now().strftime('%Y%m%d')}.xlsx"
            filepath = DATA_DIR / filename

            df.to_excel(filepath, index=False, engine='openpyxl')
            self.logger.info(f"数据已保存: {filepath}")
            return str(filepath)
        except Exception as e:
            self.logger.error(f"保存Excel失败: {e}")
            raise

    def run(self, max_pages: int = MAX_PAGES) -> Tuple[int, str]:
        """
        运行爬虫
        返回: (爬取数量, 保存路径)
        """
        start_time = datetime.now()
        self.logger.info(f"========== 开始爬取任务 ==========")
        self.logger.info(f"目标页数: {max_pages}")

        all_data = []

        try:
            # 先访问第一页
            self.logger.info(f"\n📄 正在访问列表页...")
            self.safe_get(LIST_URL)

            if not self.wait_for_articles():
                self.logger.error("列表页加载失败，停止爬取")
                return 0, ""

            for page_num in range(1, max_pages + 1):
                self.logger.info(f"\n{'=' * 40}")
                self.logger.info(f"📄 正在处理第 {page_num} 页")
                self.logger.info(f"{'=' * 40}")

                # 获取当前页的文章链接
                articles = self.get_article_links()
                self.logger.info(f"第 {page_num} 页找到 {len(articles)} 篇文章")

                if not articles:
                    self.logger.warning(f"第 {page_num} 页无文章，停止爬取")
                    break

                # 爬取当前页的所有文章
                for idx, article in enumerate(articles, 1):
                    self.logger.info(f"  [{idx}/{len(articles)}] 爬取: {article['title'][:40]}...")

                    try:
                        detail_data = self.parse_detail_page(article['url'], article['title'])
                        all_data.append(detail_data)
                    except Exception as e:
                        self.logger.error(f"  爬取失败: {e}")
                        continue

                    time.sleep(random.uniform(*REQUEST_DELAY))

                # 如果已经是最后一页，跳出循环
                if page_num >= max_pages:
                    self.logger.info("已达到目标页数，停止爬取")
                    break

                # ========== 关键修复：返回列表页并点击下一页 ==========
                self.logger.info(f"\n  🔄 正在准备翻到第 {page_num + 1} 页...")

                # 重要：必须重新加载列表页，因为当前可能在详情页
                self.logger.info(f"  重新加载列表页...")
                self.safe_get(LIST_URL)
                time.sleep(2)

                # 等待列表页加载
                if not self.wait_for_articles():
                    self.logger.warning(f"列表页重新加载失败，停止翻页")
                    break

                # 点击下一页
                self.logger.info(f"  点击下一页按钮...")
                if not self.click_next_page():
                    self.logger.warning("无法点击下一页，停止爬取")
                    break

                # 等待新页面加载完成
                time.sleep(random.uniform(3, 5))
                self.logger.info(f"  ✅ 成功进入第 {page_num + 1} 页")

            # 保存数据
            if all_data:
                filepath = self.save_to_excel(all_data)
                elapsed = (datetime.now() - start_time).total_seconds()
                self.logger.info(f"\n{'=' * 60}")
                self.logger.info(f"✅ 爬取完成！")
                self.logger.info(f"📊 共爬取 {len(all_data)} 篇文章")
                self.logger.info(f"⏱️  耗时 {elapsed:.2f} 秒")
                self.logger.info(f"💾 保存路径: {filepath}")
                self.logger.info(f"{'=' * 60}")

                self.notifier.send_success(
                    "爬取任务完成",
                    f"共爬取 {len(all_data)} 篇文章\n耗时 {elapsed:.2f} 秒\n保存至: {filepath}"
                )

                return len(all_data), filepath
            else:
                self.logger.warning("未爬取到任何数据")
                self.notifier.send_alert("爬取任务警告", "未爬取到任何数据")
                return 0, ""

        except Exception as e:
            self.logger.error(f"爬取过程发生错误: {e}")
            self.logger.error(traceback.format_exc())
            self.notifier.send_alert("爬取任务失败", str(e))
            raise


# ==================== 独立运行函数 ====================

def run_crawler(max_pages: int = MAX_PAGES) -> Tuple[int, str]:
    """运行爬虫的独立函数"""
    logger = setup_logger()

    with GovernmentPolicyCrawler(logger) as crawler:
        return crawler.run(max_pages)


if __name__ == "__main__":
    run_crawler()