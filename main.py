cron: 0 */6 * * *
new Env("Linux.Do 签到")

import os
import random
import time
import functools
import sys
import re
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate
from curl_cffi import requests
from bs4 import BeautifulSoup

def retry_decorator(retries=3, min_delay=5, max_delay=10):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    logger.warning(f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}")
                    if attempt < retries - 1:
                        sleep_s = random.uniform(min_delay, max_delay)
                        logger.info(f"将在 {sleep_s:.2f}s 后重试")
                        time.sleep(sleep_s)
            return None
        return wrapper
    return decorator

# 环境清理
os.environ.pop("DISPLAY", None)
os.environ.pop("DYLD_LIBRARY_PATH", None)

# 变量获取
USERNAME = os.environ.get("LINUXDO_USERNAME") or os.environ.get("USERNAME")
PASSWORD = os.environ.get("LINUXDO_PASSWORD") or os.environ.get("PASSWORD")
BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in ["false", "0", "off"]

# 通知变量
GOTIFY_URL = os.environ.get("GOTIFY_URL")
GOTIFY_TOKEN = os.environ.get("GOTIFY_TOKEN")
SC3_PUSH_KEY = os.environ.get("SC3_PUSH_KEY")
WXPUSH_URL = os.environ.get("WXPUSH_URL")
WXPUSH_TOKEN = os.environ.get("WXPUSH_TOKEN")

HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"
SESSION_URL = "https://linux.do/session"
CSRF_URL = "https://linux.do/session/csrf"

class LinuxDoBrowser:
    def __init__(self) -> None:
        from sys import platform
        platform_id = {
            "linux": "X11; Linux x86_64",
            "linux2": "X11; Linux x86_64",
            "darwin": "Macintosh; Intel Mac OS X 10_15_7",
            "win32": "Windows NT 10.0; Win64; x64"
        }.get(platform, "X11; Linux x86_64")

        # 浏览器内核配置
        co = ChromiumOptions().headless(True).incognito(True).set_argument("--no-sandbox")
        self.ua = f"Mozilla/5.0 ({platform_id}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        co.set_user_agent(self.ua)
        
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        
        # Requests 会话配置 (使用 curl_cffi 模拟浏览器指纹)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.ua,
            "Accept-Language": "zh-CN,zh;q=0.9",
        })

    def login(self):
        logger.info("🚀 开始登录流程")
        try:
            # 步骤 0: 模拟真实用户，先访问主页
            logger.info("正在进行预热访问...")
            self.session.get(HOME_URL, impersonate="chrome120")

            # 步骤 1: 获取 CSRF Token (带上 Referer)
            logger.info("正在获取 CSRF token...")
            csrf_headers = {
                "X-Requested-With": "XMLHttpRequest",
                "Referer": LOGIN_URL,
                "Accept": "application/json, text/javascript, */*; q=0.01",
            }
            resp_csrf = self.session.get(CSRF_URL, headers=csrf_headers, impersonate="chrome120")
            
            if resp_csrf.status_code != 200:
                logger.error(f"❌ 获取 CSRF 失败 [状态码: {resp_csrf.status_code}]")
                if "cloudflare" in resp_csrf.text.lower():
                    logger.error("⚠️ 检测到 Cloudflare 验证拦截，IP 可能已被加入黑名单")
                return False

            try:
                csrf_token = resp_csrf.json().get("csrf")
                logger.info("✅ CSRF Token 获取成功")
            except Exception:
                logger.error(f"❌ 无法解析 JSON。响应前100位: {resp_csrf.text[:100]}")
                return False

            # 步骤 2: 模拟提交登录表单
            logger.info("正在提交登录请求...")
            login_headers = csrf_headers.copy()
            login_headers.update({
                "X-CSRF-Token": csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://linux.do",
            })
            data = {
                "login": USERNAME,
                "password": PASSWORD,
                "second_factor_method": "1",
                "timezone": "Asia/Shanghai",
            }
            resp_login = self.session.post(SESSION_URL, data=data, headers=login_headers, impersonate="chrome120")

            if resp_login.status_code != 200:
                logger.error(f"❌ 登录请求失败，状态码: {resp_login.status_code}")
                return False
            
            login_json = resp_login.json()
            if login_json.get("error"):
                logger.error(f"❌ 登录业务报错: {login_json.get('error')}")
                return False

            logger.success("✨ 接口登录成功")
            self.print_connect_info()

            # 步骤 3: 将 Session Cookies 注入 DrissionPage
            cookies_list = [{"name": n, "value": v, "domain": ".linux.do", "path": "/"} 
                           for n, v in self.session.cookies.get_dict().items()]
            self.page.set.cookies(cookies_list)
            
            # 浏览器跳转验证
            self.page.get(HOME_URL)
            time.sleep(5)
            
            if "avatar" in self.page.html or self.page.ele("@id=current-user"):
                logger.success("✅ 浏览器登录状态确认成功")
                return True
            else:
                logger.error("❌ 浏览器验证未通过，可能需要人工过验证码")
                return False

        except Exception as e:
            logger.error(f"💥 登录异常: {str(e)}")
            return False

    def click_topic(self):
        try:
            topic_list = self.page.ele("@id=list-area").eles(".:title")
            if not topic_list:
                logger.error("未找到可浏览的主题")
                return False
            
            logger.info(f"📋 发现 {len(topic_list)} 个帖子，随机抽取 10 个浏览")
            selected = random.sample(topic_list, min(len(topic_list), 10))
            
            for topic in selected:
                self.click_one_topic(topic.attr("href"))
            return True
        except Exception as e:
            logger.error(f"浏览任务中断: {e}")
            return False

    @retry_decorator()
    def click_one_topic(self, topic_url):
        new_tab = self.browser.new_tab()
        try:
            new_tab.get(topic_url)
            # 20% 概率自动点赞
            if random.random() < 0.2:
                self.click_like(new_tab)
            self.browse_post(new_tab)
        finally:
            new_tab.close()

    def browse_post(self, page):
        # 随机滚动模拟阅读
        for _ in range(random.randint(4, 8)):
            scroll_px = random.randint(300, 700)
            page.run_js(f"window.scrollBy(0, {scroll_px})")
            time.sleep(random.uniform(2, 4))
            # 触底则跳出
            if page.run_js("window.scrollY + window.innerHeight >= document.body.scrollHeight"):
                break

    def click_like(self, page):
        try:
            btn = page.ele(".discourse-reactions-reaction-button")
            if btn:
                btn.click()
                logger.info("👍 已自动点赞")
        except:
            pass

    def print_connect_info(self):
        try:
            resp = self.session.get("https://connect.linux.do/", impersonate="chrome120")
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table tr")
            info = [[c.text.strip() for c in r.select("td")[:3]] for r in rows if len(r.select("td")) >= 3]
            if info:
                print("\n" + tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
        except:
            logger.warning("无法获取详细连接信息")

    def send_notifications(self, browse_enabled):
        # 此处保留你原有的通知推送逻辑
        msg = f"✅ Linux.Do 签到成功: {USERNAME}"
        if browse_enabled:
            msg += " (已完成模拟浏览)"
        logger.info(f"推送消息: {msg}")
        # ... 原代码中的推送模块 ...

    def run(self):
        try:
            if self.login():
                if BROWSE_ENABLED:
                    self.click_topic()
                self.send_notifications(BROWSE_ENABLED)
        finally:
            self.browser.quit()

if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        logger.error("请先在环境变量中配置 LINUXDO_USERNAME 和 LINUXDO_PASSWORD")
        sys.exit(1)
    LinuxDoBrowser().run()
