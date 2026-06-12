"""
爬虫守护进程 - 实现定时任务和开机自启
"""
import os
import sys
import time
import schedule
import logging
import traceback
from datetime import datetime
from pathlib import Path

# 添加项目路径到系统路径
sys.path.insert(0, str(Path(__file__).parent))

from gov_crawler import run_crawler, setup_logger
from config import MAX_PAGES, LOG_DIR, DATA_DIR


class CrawlerDaemon:
    """爬虫守护进程"""

    def __init__(self):
        self.logger = setup_logger("daemon")
        self.is_running = False
        self.last_run_time = None
        self.run_count = 0
        self.error_count = 0

    def cleanup_old_files(self, days: int = 30):
        """清理旧文件"""
        try:
            # 清理旧日志
            for log_file in LOG_DIR.glob("crawler_*.log"):
                if log_file.stat().st_mtime < time.time() - days * 86400:
                    log_file.unlink()
                    self.logger.info(f"删除旧日志: {log_file.name}")

            # 清理旧数据（保留最近7天）
            for data_file in DATA_DIR.glob("gov_policies_*.xlsx"):
                if data_file.stat().st_mtime < time.time() - 7 * 86400:
                    data_file.unlink()
                    self.logger.info(f"删除旧数据: {data_file.name}")
        except Exception as e:
            self.logger.warning(f"清理旧文件失败: {e}")

    def run_task(self):
        """执行爬取任务"""
        if self.is_running:
            self.logger.warning("上一个任务仍在运行，跳过本次执行")
            return

        self.is_running = True
        self.logger.info("=" * 60)
        self.logger.info(f"开始执行定时任务 #{self.run_count + 1}")
        self.logger.info(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        try:
            # 清理旧文件
            self.cleanup_old_files()

            # 执行爬虫
            count, filepath = run_crawler(max_pages=MAX_PAGES)

            self.run_count += 1
            self.last_run_time = datetime.now()

            if count > 0:
                self.logger.info(f"任务成功完成: 爬取 {count} 条数据")
                self.error_count = 0  # 重置错误计数
            else:
                self.error_count += 1
                self.logger.warning(f"任务完成但无数据，连续错误次数: {self.error_count}")

        except Exception as e:
            self.error_count += 1
            self.logger.error(f"任务执行失败: {e}")
            self.logger.error(traceback.format_exc())

            # 连续错误超过3次，发送告警
            if self.error_count >= 3:
                self.logger.critical(f"连续失败 {self.error_count} 次，请检查爬虫程序！")
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0,
                        f"爬虫程序连续失败 {self.error_count} 次！\n请检查日志文件: {LOG_DIR}",
                        "爬虫告警",
                        0x30  # 警告图标
                    )
                except:
                    pass

        finally:
            self.is_running = False
            self.logger.info(f"任务结束，完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self.logger.info("=" * 60)

    def run_schedule(self, time_str: str = "15:00", weekday: str = "monday"):
        """
        运行定时调度
        time_str: 执行时间，格式 "HH:MM"
        weekday: 星期几 (monday, tuesday, wednesday, thursday, friday, saturday, sunday)
        """
        # 设置定时任务 - 每周指定时间执行
        getattr(schedule.every(), weekday).at(time_str).do(self.run_task)

        self.logger.info(f"爬虫守护进程已启动")
        self.logger.info(f"定时任务设置: 每天 {time_str} 执行一次")
        self.logger.info(f"日志目录: {LOG_DIR}")
        self.logger.info(f"数据目录: {DATA_DIR}")
        self.logger.info("等待执行...")

        # 循环执行
        while True:
            try:
                schedule.run_pending()
                time.sleep(60)  # 每分钟检查一次
            except KeyboardInterrupt:
                self.logger.info("收到中断信号，守护进程退出")
                break
            except Exception as e:
                self.logger.error(f"调度循环出错: {e}")
                time.sleep(60)

    def run_once(self):
        """立即执行一次任务"""
        self.logger.info("立即执行爬取任务...")
        self.run_task()


# ==================== 开机自启配置 ====================

def setup_auto_start():
    """
    配置开机自启（Windows）
    创建启动脚本添加到启动文件夹
    """
    try:
        import ctypes
        import winshell

        # 获取当前脚本路径
        current_file = Path(__file__).resolve()
        batch_file = current_file.parent / "start_crawler.bat"

        # 创建批处理文件
        batch_content = f'''@echo off
cd /d "{current_file.parent}"
start /B python "{current_file}" --daemon
'''
        with open(batch_file, 'w', encoding='utf-8') as f:
            f.write(batch_content)

        # 添加到启动文件夹
        startup_folder = Path(os.environ.get('APPDATA')) / "Microsoft\\Windows\\Start Menu\\Programs\\Startup"
        startup_script = startup_folder / "start_crawler.bat"

        if not startup_script.exists():
            import shutil
            shutil.copy(batch_file, startup_script)
            print(f"✅ 已添加开机自启: {startup_script}")

            # 弹窗提示
            ctypes.windll.user32.MessageBoxW(
                0,
                f"爬虫开机自启已配置完成！\n\n启动脚本位置:\n{startup_script}\n\n爬虫将在每天15:00自动执行爬取任务。",
                "配置成功",
                0x40
            )
        else:
            print("开机自启脚本已存在")

    except ImportError:
        print("需要安装 winshell 模块: pip install winshell")
        print("手动配置开机自启:")
        print(f"1. 创建文件: {Path(__file__).parent / 'start_crawler.bat'}")
        print("2. 将文件复制到启动文件夹")
    except Exception as e:
        print(f"配置开机自启失败: {e}")


# ==================== 主入口 ====================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="政府政策爬虫守护进程")
    parser.add_argument("--daemon", action="store_true", help="以守护进程模式运行")
    parser.add_argument("--once", action="store_true", help="立即执行一次爬取")
    parser.add_argument("--setup-auto", action="store_true", help="配置开机自启")
    parser.add_argument("--time", type=str, default="15:00", help="设置执行时间，格式 HH:MM，默认 15:00")

    args = parser.parse_args()

    if args.setup_auto:
        setup_auto_start()
    elif args.once:
        daemon = CrawlerDaemon()
        daemon.run_once()
    elif args.daemon:
        daemon = CrawlerDaemon()
        daemon.run_schedule(args.time)
    else:
        print("使用方法:")
        print("  定时模式: python crawler_daemon.py --daemon --time 15:00")
        print("  立即执行: python crawler_daemon.py --once")
        print("  配置自启: python crawler_daemon.py --setup-auto")
        print("  查看帮助: python crawler_daemon.py --help")