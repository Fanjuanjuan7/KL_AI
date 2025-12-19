import json
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests
import urllib.parse
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class BitBrowserClient:
    def __init__(self, base_url: str, secret: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.secret = secret

    def _headers(self) -> Dict[str, str]:
        h = {'Content-Type': 'application/json'}
        if self.secret:
            h['Authorization'] = self.secret
        return h

    def update_browser(self, name: str, proxy: Optional[Dict[str, Any]] = None) -> str:
        payload: Dict[str, Any] = {
            'name': name,
            'remark': '',
            'proxyMethod': 2,
            'proxyType': 'noproxy',
            'browserFingerPrint': {
                'coreVersion': '124'
            }
        }
        if proxy:
            payload.update(proxy)
        r = requests.post(f"{self.base_url}/browser/update", headers=self._headers(), data=json.dumps(payload), timeout=30)
        r.raise_for_status()
        data = r.json()
        d = data.get('data')
        bid = None
        if isinstance(d, dict):
            bid = d.get('id')
        elif isinstance(d, str):
            bid = d
        if not bid:
            bid = data.get('id')
        if not bid:
            raise RuntimeError(f"update_browser failed: {data}")
        return bid

    def create_browser(self, name: str, proxy: Optional[Dict[str, Any]] = None) -> str:
        payload: Dict[str, Any] = {
            'name': name,
            'remark': '',
            'proxyMethod': 2,
            'proxyType': 'noproxy',
            'browserFingerPrint': {
                'coreVersion': '124'
            }
        }
        if proxy:
            payload.update(proxy)
        r = requests.post(f"{self.base_url}/browser/create", headers=self._headers(), data=json.dumps(payload), timeout=30)
        r.raise_for_status()
        data = r.json()
        d = data.get('data')
        bid = None
        if isinstance(d, dict):
            bid = d.get('id')
        elif isinstance(d, str):
            bid = d
        if not bid:
            bid = data.get('id')
        if not bid:
            raise RuntimeError(f"create_browser failed: {data}")
        return bid

    def open_browser(self, browser_id: str) -> Dict[str, Any]:
        payload = {'id': browser_id}
        r = requests.post(f"{self.base_url}/browser/open", headers=self._headers(), data=json.dumps(payload), timeout=60)
        r.raise_for_status()
        data = r.json()
        d = data.get('data')
        if isinstance(d, dict):
            return d
        return {}

    def close_browser(self, browser_id: str) -> None:
        try:
            requests.post(f"{self.base_url}/browser/close", headers=self._headers(), data=json.dumps({'id': browser_id}), timeout=30)
        except Exception:
            pass

    def delete_browser(self, browser_id: str) -> None:
        try:
            requests.post(f"{self.base_url}/browser/delete", headers=self._headers(), data=json.dumps({'id': browser_id}), timeout=30)
        except Exception:
            pass


def read_rows(input_path: str) -> List[Dict[str, Any]]:
    if input_path.lower().endswith('.csv'):
        import csv
        rows: List[Dict[str, Any]] = []
        with open(input_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows
    if input_path.lower().endswith('.json'):
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and 'rows' in data:
                return data['rows']
        return []
    if input_path.lower().endswith('.xlsx'):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(input_path)
            sheet = wb.active
            headers = [c.value for c in next(sheet.iter_rows(min_row=1, max_row=1))]
            rows = []
            for r in sheet.iter_rows(min_row=2):
                row = {headers[i]: (r[i].value if i < len(r) else None) for i in range(len(headers))}
                rows.append(row)
            return rows
        except Exception:
            return []
    return []


def write_rows_csv(input_path: str, rows: List[Dict[str, Any]]) -> None:
    import csv
    headers = list(rows[0].keys()) if rows else []
    with open(input_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def element_exists(driver: webdriver.Remote, xpath: str, timeout_ms: int, poll_ms: int) -> bool:
    try:
        eff_timeout = min(timeout_ms, 30000)
        eff_poll = max(0.2, poll_ms / 1000.0)
        WebDriverWait(driver, eff_timeout / 1000.0, poll_frequency=eff_poll).until(EC.presence_of_element_located((By.XPATH, xpath)))
        return True
    except Exception:
        return False


def find_click(driver: webdriver.Remote, xpath: str, timeout_ms: int, poll_ms: int) -> None:
    eff_timeout = min(timeout_ms, 10000)
    WebDriverWait(driver, eff_timeout / 1000.0, poll_frequency=poll_ms / 1000.0).until(EC.element_to_be_clickable((By.XPATH, xpath))).click()


def find_click_any(driver: webdriver.Remote, xpath: str, timeout_ms: int, poll_ms: int) -> None:
    try:
        eff_timeout = min(timeout_ms, 10000)
        WebDriverWait(driver, eff_timeout / 1000.0, poll_frequency=poll_ms / 1000.0).until(EC.element_to_be_clickable((By.XPATH, xpath))).click()
        return
    except Exception:
        eff_timeout = min(timeout_ms, 10000)
        el = WebDriverWait(driver, eff_timeout / 1000.0, poll_frequency=poll_ms / 1000.0).until(EC.presence_of_element_located((By.XPATH, xpath)))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        try:
            el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", el)


def _human_drag_track(distance: int, duration_ms: int = 900, jitter_px: int = 1, overshoot_px: int = 5) -> List[int]:
    steps = 25
    arr: List[int] = []
    moved = 0
    for i in range(steps):
        remain = distance - moved
        if remain <= 0:
            break
        prog = i / steps
        base = int((1 - (prog - 0.5) ** 2) * 8) + 1
        jitter = ((i % 3) - 1) * jitter_px
        dx = max(1, base + jitter)
        moved += dx
        arr.append(dx)
    while sum(arr) < distance:
        arr.append(1)
    arr.append(overshoot_px)
    arr.append(-overshoot_px + 1)
    return arr


def solve_slider(driver: webdriver.Remote, xpaths: Dict[str, str], timeout_ms: int, poll_ms: int) -> bool:
    # 等待滑块弹窗出现（容器或 iframe 任一）
    appear_wait_ms = min(timeout_ms, 25000)
    start = time.time()
    while (time.time() - start) * 1000 < appear_wait_ms:
        if element_exists(driver, xpaths['slider_iframe'], 800, poll_ms) or element_exists(driver, xpaths['slider_container'], 800, poll_ms):
            break
        time.sleep(max(0.1, poll_ms/1000.0))
    else:
        # 长时间未出现滑块，不视为成功，返回 False 以便上层处理
        return False
    ok_xpath = xpaths.get('code_url_element') or xpaths.get('next_btn') or xpaths.get('password_input')
    try:
        iframe_probe_ms = min(timeout_ms, 6000)
        if element_exists(driver, xpaths['slider_iframe'], iframe_probe_ms, poll_ms):
            iframe = driver.find_element(By.XPATH, xpaths['slider_iframe'])
            driver.switch_to.frame(iframe)
        for i in range(10):
            # 每次尝试重新定位容器与句柄，避免刷新后引用失效
            try:
                # 确保位于正确的文档或iframe中
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
                if element_exists(driver, xpaths['slider_iframe'], 1500, poll_ms):
                    try:
                        iframe = driver.find_element(By.XPATH, xpaths['slider_iframe'])
                        driver.switch_to.frame(iframe)
                    except Exception:
                        pass
                container = None
                for xp in [xpaths['slider_container'], "//*[contains(@class,'slider-shadow')]", "//*[contains(@class,'kwai-captcha-slider-wrapper')]"]:
                    try:
                        container = driver.find_element(By.XPATH, xp)
                        if container:
                            break
                    except Exception:
                        pass
                if not container:
                    time.sleep(0.2)
                    continue
                handle_wait_ms = min(timeout_ms, 4000)
                handle = None
                handle_candidates = [xpaths['slider_handle'], "//*[contains(@class,'slider-btn')]", "//*[contains(@class,'btn-icon')]"]
                for hx in handle_candidates:
                    try:
                        handle = WebDriverWait(driver, handle_wait_ms / 1000.0, poll_frequency=poll_ms / 1000.0).until(EC.presence_of_element_located((By.XPATH, hx)))
                        if handle:
                            break
                    except Exception:
                        handle = None
                try:
                    width = driver.execute_script("return Math.floor(arguments[0].getBoundingClientRect().width)||arguments[0].offsetWidth||200;", container)
                except Exception:
                    width = container.size.get('width') or 200
                handle_w = 24
                try:
                    if handle is not None:
                        hw = handle.size.get('width')
                        if hw:
                            handle_w = hw
                except Exception:
                    pass
                dist = 218
            except Exception:
                dist = 218
            try:
                actions = ActionChains(driver)
                if handle is not None:
                    actions.move_to_element(handle).pause(0.05).move_by_offset(2, 0).click_and_hold(handle).pause(0.05)
                else:
                    h = container.size.get('height') or 20
                    actions.move_to_element_with_offset(container, 5, int(h/2)).pause(0.05).click_and_hold().pause(0.05)
                steps = max(6, int(dist / 24))
                step_len = max(6, int(dist / steps))
                moved = 0
                for _ in range(steps):
                    left = dist - moved
                    dx = min(step_len, left)
                    actions.move_by_offset(dx, 0).pause(0.01)
                    moved += dx
                actions.release().perform()
                time.sleep(0.15)
                try:
                    print(f"slider attempt {i+1}/10 dist={dist}")
                except Exception:
                    pass
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
                disappeared = not element_exists(driver, xpaths['slider_container'], 2000, max(200, poll_ms))
                if disappeared:
                    return True
                if ok_xpath and element_exists(driver, ok_xpath, min(5000, timeout_ms), poll_ms):
                    return True
                time.sleep(0.2)
            except Exception:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
                time.sleep(0.2)
    except Exception:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    if ok_xpath and element_exists(driver, ok_xpath, min(5000, timeout_ms), poll_ms):
        return True
    return False


def extract_code_from_page_text(driver: webdriver.Remote) -> Optional[str]:
    try:
        body_text = driver.find_element(By.TAG_NAME, 'body').text
        m = re.search(r"\b(\d{6})\b", body_text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def extract_code_using_xpath(driver: webdriver.Remote, xpath: str) -> Optional[str]:
    try:
        el = driver.find_element(By.XPATH, xpath)
        txt = el.text or el.get_attribute('value') or ''
        m = re.search(r"\b(\d{6})\b", txt)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def wait_extract_code(driver: webdriver.Remote, xpath: Optional[str], max_wait_sec: int = 20, logger: Optional[Any] = None) -> Optional[str]:
    end = time.time() + max_wait_sec
    code: Optional[str] = None
    while time.time() < end:
        if logger:
            logger("等待验证码...")
        if xpath:
            try:
                WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.XPATH, xpath)))
                code = extract_code_using_xpath(driver, xpath)
                if code:
                    if logger:
                        logger(f"验证码通过XPath提取: {code}")
                    return code
            except Exception:
                pass
        code = extract_code_from_page_text(driver)
        if code:
            if logger:
                logger(f"验证码通过文本提取: {code}")
            return code
        time.sleep(1)
    return None


def extract_code_attempts(driver: webdriver.Remote, xpath: Optional[str], logger: Optional[Any], attempts: int = 3) -> Optional[str]:
    code: Optional[str] = None
    for i in range(max(1, attempts)):
        if logger:
            logger(f"尝试提取验证码 {i+1}/{attempts}")
        try:
            if xpath:
                try:
                    el = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, xpath)))
                    txt = (el.text or el.get_attribute('value') or '').strip()
                    if logger:
                        logger(f"元素文本: '{txt}'")
                    m = re.search(r"\b(\d{6})\b", txt)
                    if m:
                        code = m.group(1)
                        return code
                    clean = txt.replace(' ', '').replace('\n', '')
                    if clean.isdigit() and len(clean) == 6:
                        code = clean
                        return code
                except Exception as e:
                    if logger:
                        logger(f"元素提取失败: {e}")
            page_txt = ''
            try:
                body = driver.find_element(By.TAG_NAME, 'body')
                page_txt = body.text
            except Exception:
                pass
            if page_txt:
                m2 = re.search(r"\b(\d{6})\b", page_txt)
                if m2:
                    code = m2.group(1)
                    if logger:
                        logger(f"页面文本提取验证码: {code}")
                    return code
        except Exception as e:
            if logger:
                logger(f"提取尝试异常: {e}")
        time.sleep(2 if i < attempts-1 else 0)
    return code


def extract_verification_code_flow(driver: webdriver.Remote, code_url: str, code_xpath: Optional[str], logger: Optional[Any], debugger_http: Optional[str] = None) -> Optional[str]:
    main_handle = driver.current_window_handle
    try:
        # if logger:
        #     logger("等待接码邮件到达 5 秒")
        # time.sleep(5)
        for open_try in range(3):
            created = False
            prev_len = len(driver.window_handles)
            if debugger_http:
                try:
                    base = debugger_http if debugger_http.startswith('http') else f"http://{debugger_http}"
                    encoded = urllib.parse.quote(code_url, safe='')
                    if logger:
                        logger(f"调试接口创建标签: {base}/json/new?{encoded}")
                    r = requests.get(f"{base}/json/new?{encoded}", timeout=8)
                    created = r.status_code == 200
                except Exception as e:
                    if logger:
                        logger(f"调试接口创建失败: {e}")
            if created:
                try:
                    WebDriverWait(driver, 10).until(lambda d: len(d.window_handles) > prev_len)
                    driver.switch_to.window(driver.window_handles[-1])
                    if logger:
                        try:
                            logger(f"已切换到新标签: {driver.current_url}")
                        except Exception:
                            logger("已切换到新标签")
                except Exception:
                    created = False
            if not created:
                driver.switch_to.new_window('tab')
                if logger:
                    logger(f"打开接码链接: {code_url}")
                try:
                    driver.get(code_url)
                except Exception as nav_err:
                    if logger:
                        logger(f"导航失败: {nav_err}")
                    try:
                        driver.execute_script("window.location.href=arguments[0];", code_url)
                    except Exception:
                        pass
            loaded = False
            try:
                WebDriverWait(driver, 15).until(lambda d: d.execute_script("return document.readyState") in ("interactive", "complete"))
                loaded = True
                if logger:
                    logger("接码页已加载")
            except Exception:
                if logger:
                    logger("接码页加载超时")
            if loaded:
                break
            try:
                driver.refresh()
            except Exception:
                pass
            time.sleep(1)
        code: Optional[str] = None
        for rtry in range(3):
            if logger:
                logger(f"验证码抓取窗口刷新尝试 {rtry+1}/3")
            start = time.time()
            while (time.time() - start) < 5 and not code:
                try:
                    if code_xpath:
                        try:
                            el = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.XPATH, code_xpath)))
                            txt = (el.text or el.get_attribute('value') or '').strip()
                            if logger:
                                logger(f"元素文本: '{txt}'")
                            m = re.search(r"\b(\d{6})\b", txt)
                            if m:
                                code = m.group(1)
                                break
                            clean = txt.replace(' ', '').replace('\n', '')
                            if clean.isdigit() and len(clean) == 6:
                                code = clean
                                break
                        except Exception:
                            pass
                    if not code:
                        try:
                            body_txt = driver.find_element(By.TAG_NAME, 'body').text
                            m2 = re.search(r"\b(\d{6})\b", body_txt)
                            if m2:
                                code = m2.group(1)
                                if logger:
                                    logger(f"页面文本提取验证码: {code}")
                                break
                        except Exception:
                            pass
                except Exception:
                    pass
                time.sleep(1)
            if code:
                break
            try:
                driver.refresh()
                if logger:
                    logger("已刷新接码页面")
                WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") in ("interactive", "complete"))
            except Exception:
                time.sleep(1)
        driver.close()
        driver.switch_to.window(main_handle)
        return code
    except Exception as e:
        if logger:
            logger(f"接码流程异常: {e}")
        try:
            driver.close()
        except Exception:
            pass
        try:
            driver.switch_to.window(main_handle)
        except Exception:
            pass
        return None


def get_verification_code(driver: webdriver.Remote, code_url: str, code_xpath: Optional[str] = None, retries: int = 3, wait_seconds: int = 5, logger: Optional[Any] = None) -> Optional[str]:
    main_handle = driver.current_window_handle
    for i in range(retries):
        try:
            if logger:
                logger(f"打开接码标签尝试 {i+1}/{retries}: {code_url}")
            driver.switch_to.new_window('tab')
            try:
                driver.get(code_url)
            except Exception as nav_err:
                if logger:
                    logger(f"直接导航失败，尝试JS打开: {nav_err}")
                try:
                    driver.execute_script("window.open(arguments[0], '_blank')", code_url)
                except Exception as js_err:
                    if logger:
                        logger(f"JS window.open 失败: {js_err}")
            time.sleep(wait_seconds)
            code = wait_extract_code(driver, code_xpath, max_wait_sec=15, logger=logger)
            driver.close()
            driver.switch_to.window(main_handle)
            if code:
                if logger:
                    logger("接码成功，返回主标签")
                return code
        except Exception as e:
            if logger:
                logger(f"接码标签异常: {e}")
            try:
                driver.close()
            except Exception:
                pass
            try:
                driver.switch_to.window(main_handle)
            except Exception:
                pass
        time.sleep(2)
    return None


def open_attached_driver(open_data: Dict[str, Any]) -> webdriver.Chrome:
    driver_path = open_data.get('driver')
    debugger_address = open_data.get('http')
    if not driver_path or not debugger_address:
        raise RuntimeError(f"No driver/http returned: {open_data}")
    from selenium.webdriver.chrome.service import Service
    options = webdriver.ChromeOptions()
    options.debugger_address = debugger_address
    try:
        options.page_load_strategy = 'none'
    except Exception:
        pass
    service = Service(executable_path=driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.set_page_load_timeout(25)
    except Exception:
        pass
    return driver


def open_tab_via_debugger(debugger_address: str, url: str, logger: Optional[Any] = None) -> bool:
    try:
        if debugger_address.startswith('http://'):
            base = debugger_address
        else:
            base = f"http://{debugger_address}"
        encoded = urllib.parse.quote(url, safe='')
        if logger:
            logger(f"调试接口: {base}/json/new?{encoded}")
        r = requests.get(f"{base}/json/new?{encoded}", timeout=10)
        if logger:
            logger(f"调试接口响应: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        if logger:
            logger(f"调试接口异常: {e}")
        return False


def proxy_payload(host: str, port: str, username: Optional[str], password: Optional[str]) -> Dict[str, Any]:
    if not host or not port:
        return {'proxyType': 'noproxy'}
    p: Dict[str, Any] = {
        'proxyType': 'socks5',
        'host': host,
        'port': str(port),
        'proxyHost': host,
        'proxyPort': str(port),
    }
    if username:
        p['proxyUserName'] = username
    if password:
        p['proxyPassword'] = password
    if username and password:
        p['proxy'] = f"socks5://{username}:{password}@{host}:{port}"
    else:
        p['proxy'] = f"socks5://{host}:{port}"
    return p


def log_window_urls(driver: webdriver.Remote, logger: Optional[Any]) -> None:
    if not logger:
        return
    try:
        handles = driver.window_handles
        logger(f"当前窗口句柄数量: {len(handles)}")
        for i, h in enumerate(handles):
            try:
                driver.switch_to.window(h)
                url = driver.current_url
            except Exception:
                url = '(不可获取URL)'
            logger(f"句柄[{i}]: {url}")
    except Exception as e:
        logger(f"枚举窗口失败: {e}")


def perform_registration(
    row: Dict[str, Any],
    xpaths: Dict[str, str],
    platform_url: str,
    timeout_ms: int,
    poll_ms: int,
    client: BitBrowserClient,
    logger: Optional[Any] = None,
    stop_event: Optional[threading.Event] = None,
) -> Tuple[bool, str]:
    email = str(row.get('email') or row.get('账号') or '').strip()
    password = str(row.get('password') or row.get('密码') or '').strip()
    code_url = str(row.get('code_url') or row.get('接收验证码链接') or '').strip()
    host = str(row.get('host') or row.get('代理IP') or '').strip()
    port = str(row.get('port') or row.get('端口') or '').strip()
    proxy_username = str(row.get('proxyUserName') or row.get('用户名') or '').strip()
    proxy_password = str(row.get('proxyproxyPasswordPassword') or row.get('proxyPassword') or row.get('密码2') or '').strip()
    window_name = str(row.get('windowName') or row.get('窗口名称') or email or 'win').strip()
    if stop_event and stop_event.is_set():
        return False, 'stopped'
    if logger:
        logger(f"create_profile {window_name}")
    browser_id = None
    try:
        browser_id = client.update_browser(window_name, proxy_payload(host, port, proxy_username, proxy_password))
    except Exception:
        browser_id = client.update_browser(window_name, proxy_payload(host, port, proxy_username, proxy_password))
    if logger:
        logger(f"profile_id {browser_id}")
    open_data = client.open_browser(browser_id)
    if logger:
        logger(f"open_browser {browser_id} -> {open_data}")
    if not (open_data.get('driver') and open_data.get('http')):
        raise RuntimeError(f"open_browser 未返回 driver/http: {open_data}")
    driver = open_attached_driver(open_data)
    result_ok = False
    try:
        if logger:
            logger("步骤: 打开平台网址")
        try:
            driver.get(platform_url)
        except Exception:
            try:
                driver.execute_script("window.location.href=arguments[0];", platform_url)
            except Exception:
                pass
        ready = False
        for xp_key in ('language_menu', 'signin_btn', 'Creative Studio'):
            try:
                xp_val = xpaths.get(xp_key)
                if xp_val and element_exists(driver, xp_val, 8000, poll_ms):
                    ready = True
                    break
            except Exception:
                pass
        if not ready:
            try:
                driver.refresh()
            except Exception:
                pass
            for xp_key in ('language_menu', 'signin_btn', 'Creative Studio'):
                try:
                    xp_val = xpaths.get(xp_key)
                    if xp_val and element_exists(driver, xp_val, 8000, poll_ms):
                        ready = True
                        break
                except Exception:
                    pass
        if not ready and open_data.get('http'):
            prev_handles = driver.window_handles
            open_tab_via_debugger(open_data.get('http') or '', platform_url, logger)
            try:
                WebDriverWait(driver, 8).until(lambda d: len(d.window_handles) > len(prev_handles))
                driver.switch_to.window(driver.window_handles[-1])
            except Exception:
                pass
        if element_exists(driver, xpaths['language_menu'], min(timeout_ms, 8000), poll_ms):
            if logger:
                logger("步骤: 打开语言菜单")
            find_click(driver, xpaths['language_menu'], timeout_ms, poll_ms)
            if logger:
                logger("步骤: 选择英文")
            find_click(driver, xpaths['english_option'], timeout_ms, poll_ms)
        if stop_event and stop_event.is_set():
            return False, 'stopped'
        if logger:
            logger("步骤: 点击 Creative Studio")
        find_click(driver, xpaths['Creative Studio'], timeout_ms, poll_ms)
        if logger:
            logger("步骤: 点击 More Tools")
        prev_handles = driver.window_handles
        find_click(driver, xpaths['More Tools'], timeout_ms, poll_ms)
        WebDriverWait(driver, min(5, timeout_ms / 1000.0), poll_frequency=poll_ms / 1000.0).until(lambda d: len(d.window_handles) > len(prev_handles))
        driver.switch_to.window(driver.window_handles[-1])
        if logger:
            logger("步骤: 已切换到新标签")
        if logger:
            logger("步骤: 点击 Sign In")
        find_click_any(driver, xpaths['signin_btn'], timeout_ms, poll_ms)
        if logger:
            logger("步骤: 选择邮箱登录")
        find_click_any(driver, xpaths['signin_with_email'], timeout_ms, poll_ms)
        if logger:
            logger("步骤: 点击免费注册")
        find_click_any(driver, xpaths['Sign up for free'], timeout_ms, poll_ms)
        if logger:
            logger("步骤: 输入邮箱")
        WebDriverWait(driver, min(timeout_ms, 10000) / 1000.0).until(EC.presence_of_element_located((By.XPATH, xpaths['Enter Email Address']))).send_keys(email)
        if logger:
            logger("步骤: 输入密码")
        WebDriverWait(driver, min(timeout_ms, 10000) / 1000.0).until(EC.presence_of_element_located((By.XPATH, xpaths['password_input']))).send_keys(password)
        if logger:
            logger("步骤: 确认密码")
        WebDriverWait(driver, min(timeout_ms, 10000) / 1000.0).until(EC.presence_of_element_located((By.XPATH, xpaths['Confirm Password']))).send_keys(password)
        if logger:
            logger("步骤: 点击下一步")
        find_click_any(driver, xpaths['next_btn'], timeout_ms, poll_ms)
        if logger:
            logger("步骤: 等待并通过滑块")
        if stop_event and stop_event.is_set():
            return False, 'stopped'
        if not solve_slider(driver, xpaths, timeout_ms, poll_ms):
            try:
                if element_exists(driver, xpaths['next_btn'], min(timeout_ms, 5000), poll_ms):
                    find_click_any(driver, xpaths['next_btn'], timeout_ms, poll_ms)
                time.sleep(2)
            except Exception:
                pass
            if not solve_slider(driver, xpaths, timeout_ms, poll_ms):
                return False, 'slider_failed'
        if logger:
            logger("步骤: 滑块通过")
        if logger:
            logger("步骤: 打开接码页面并解析验证码")
        code = extract_verification_code_flow(driver, code_url, xpaths.get('code_input'), logger, open_data.get('http'))
        if not code:
            if logger:
                logger("步骤: 获取验证码失败")
            return False, 'code_not_found'
        if logger:
            logger("步骤: 获取验证码成功")
        WebDriverWait(driver, min(timeout_ms, 10000) / 1000.0).until(EC.presence_of_element_located((By.XPATH, xpaths['code_url_element']))).send_keys(code)
        if logger:
            logger("步骤: 填写验证码")
        find_click_any(driver, xpaths['final_submit_btn'], timeout_ms, poll_ms)
        if logger:
            logger("步骤: 提交注册")
        # Ensure we find the close popup
        # Retry finding the element in case of stale element or transient error
        popup_found = False
        for _ in range(3):
            if element_exists(driver, xpaths['close_popup_svg'], 30000, poll_ms):
                popup_found = True
                break
            time.sleep(1)

        if popup_found:
            try:
                find_click(driver, xpaths['close_popup_svg'], timeout_ms, poll_ms)
                time.sleep(3)
            except Exception:
                pass
        else:
            if logger:
                logger("步骤: 未找到关闭弹窗按钮")
            return False, 'popup_not_found'
        result_ok = True
        return True, '成功'
    except Exception as e:
        return False, str(e)
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        try:
            client.close_browser(browser_id)
        except Exception:
            pass
        if not result_ok:
            try:
                client.delete_browser(browser_id)
            except Exception:
                pass


def run_batch(
    input_path: str,
    xpaths_path: str,
    platform_url: str,
    base_url: str,
    secret: Optional[str],
    concurrency: int,
    timeout_ms: int,
    poll_ms: int,
    max_rounds: int = 5,
    logger: Optional[Any] = None,
    stop_event: Optional[threading.Event] = None,
    progress_cb: Optional[Any] = None,
) -> None:
    rows = read_rows(input_path)
    if not rows:
        return
    with open(xpaths_path, 'r', encoding='utf-8') as f:
        xpaths = json.load(f)
    client = BitBrowserClient(base_url, secret)
    def _ping(url: str) -> bool:
        try:
            payload = {'name': 'health-check', 'proxyMethod': 2, 'proxyType': 'noproxy', 'browserFingerPrint': {'coreVersion': '124'}}
            r = requests.post(f"{url.rstrip('/')}/browser/update", headers=client._headers(), data=json.dumps(payload), timeout=5)
            r.raise_for_status()
            try:
                data = r.json()
                d = data.get('data')
                bid = None
                if isinstance(d, dict):
                    bid = d.get('id')
                elif isinstance(d, str):
                    bid = d
                if not bid:
                    bid = data.get('id')
                if bid:
                    try:
                        requests.post(f"{url.rstrip('/')}/browser/delete", headers=client._headers(), data=json.dumps({'id': bid}), timeout=5)
                    except Exception:
                        pass
            except Exception:
                pass
            return True
        except Exception:
            return False
    if not _ping(client.base_url):
        candidates = [client.base_url] + [f"http://127.0.0.1:{p}" for p in (54345, 54346, 54321, 54322, 50325, 55555)]
        for c in candidates:
            if _ping(c):
                client.base_url = c.rstrip('/')
                if logger:
                    logger(f"已自动切换比特浏览器接口到: {client.base_url}")
                break
        else:
            if logger:
                logger("比特浏览器接口不可用，请确认应用已启动并开启本地API")
            return
    lock = threading.Lock()

    def task(idx: int, r: Dict[str, Any]) -> Tuple[int, bool, str]:
        if stop_event and stop_event.is_set():
            return idx, False, 'stopped'
        ok, msg = perform_registration(r, xpaths, platform_url, timeout_ms, poll_ms, client, logger, stop_event)
        with lock:
            r['status'] = 'good' if ok else 'fail'
        return idx, ok, msg

    pending_idx = [i for i, r in enumerate(rows) if str(r.get('status', '')).strip() != 'good']
    rounds = 0
    while pending_idx and rounds < max_rounds:
        if stop_event and stop_event.is_set():
            break
        futures = []
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            for i in pending_idx:
                futures.append(ex.submit(task, i, rows[i]))
            for fut in as_completed(futures):
                try:
                    i, ok, msg = fut.result()
                    if logger:
                        logger(f"row {i} {'ok' if ok else 'fail'} {msg}")
                    if progress_cb:
                        total = len(rows)
                        succ = sum(1 for rr in rows if str(rr.get('status', '')).strip() == 'good')
                        fail = sum(1 for rr in rows if str(rr.get('status', '')).strip() == 'fail')
                        progress_cb({'total': total, 'success': succ, 'fail': fail})
                except Exception:
                    pass
        write_rows_csv(input_path, rows)
        pending_idx = [i for i, r in enumerate(rows) if str(r.get('status', '')).strip() != 'good']
        rounds += 1
        if pending_idx:
            time.sleep(3)


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    import os
    base_dir = os.path.dirname(__file__)
    p.add_argument('--input', default=os.path.join(base_dir, 'kl-mail.csv'))
    p.add_argument('--xpaths', default=os.path.join(base_dir, 'kling_xpaths.json'))
    p.add_argument('--platform-url', default='https://klingai.com/global')
    p.add_argument('--bitbrowser-url', default='http://127.0.0.1:54345')
    p.add_argument('--bitbrowser-secret', default=os.environ.get('BITBROWSER_SECRET'))
    p.add_argument('--concurrency', type=int, default=1)
    p.add_argument('--timeout-ms', type=int, default=100000)
    p.add_argument('--poll-ms', type=int, default=500)
    p.add_argument('--max-rounds', type=int, default=5)
    args = p.parse_args()
    run_batch(args.input, args.xpaths, args.platform_url, args.bitbrowser_url, args.bitbrowser_secret, args.concurrency, args.timeout_ms, args.poll_ms, args.max_rounds)


if __name__ == '__main__':
    main()
