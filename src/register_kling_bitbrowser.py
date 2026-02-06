import json
import os
import re
import time
import threading
import psutil
import socks
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass

import requests
import urllib.parse
import traceback
import platform
import datetime
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service

try:
    from src.captcha_receiver import MailExtractor
    from src.email_pool import EmailPool
except ImportError:
    from captcha_receiver import MailExtractor
    from email_pool import EmailPool


# =============================================================================
# CONSTANTS
# =============================================================================

# Time constants (in seconds)
DEFAULT_TIMEOUT_SEC = 120
DEFAULT_POLL_INTERVAL_SEC = 0.5
PAGE_READY_TIMEOUT_SEC = 12
CONNECTIVITY_MAX_WAIT_SEC = 20
SLIDER_MAX_WAIT_SEC = 60
CODE_EXTRACTION_TIMEOUT_SEC = 400
BROWSER_START_WAIT_SEC = 5
POST_SLIDER_WAIT_SEC = 5
POST_SUBMIT_WAIT_SEC = 3

# Time constants (in milliseconds)
DEFAULT_TIMEOUT_MS = 100000
DEFAULT_POLL_MS = 500
ELEMENT_TIMEOUT_MS = 12000
SHORT_TIMEOUT_MS = 8000

# Retry constants
MAX_SLIDER_RETRIES = 8
MAX_REGISTRATION_ATTEMPTS = 3
MAX_CODE_RETRIES = 3
MAX_BROWSER_DELETE_RETRIES = 3
SAFE_CLICK_RETRIES = 2

# Resource limits
DEFAULT_MAX_CPU_PERCENT = 30
DEFAULT_MAX_MEM_MB = 200

# Slider pass cache timeout (seconds)
SLIDER_CACHE_TIMEOUT_SEC = 300

# Success URL keywords
SUCCESS_URL_KEYWORDS = ['dashboard', 'all-tools', 'home', 'portal', 'success']

# XPath patterns
RESEND_CODE_XPATH_PATTERNS = [
    "//a[contains(text(), 'Resend Code') or contains(text(), '重新发送') or contains(text(), '再发一条')]",
    "//*[contains(text(), 'Resend')]",
    "//*[contains(@class, 'highlight')]"
]

# Error messages
ERROR_EMAIL_UNAVAILABLE = "Email unavailable"
ERROR_TARGET_REACHED = "target_reached"
ERROR_STOPPED = "stopped"
ERROR_PROXY_CONNECTIVITY_FAILED = "proxy_connectivity_failed"
ERROR_ENGLISH_OPTION_CLICK_FAILED = "english_option_click_failed"
ERROR_MORE_TOOLS_CLICK_FAILED = "more_tools_click_failed"
ERROR_SIGNIN_CLICK_FAILED = "signin_click_failed"
ERROR_SIGNIN_EMAIL_CLICK_FAILED = "signin_email_click_failed"
ERROR_SIGNUP_CLICK_FAILED = "signup_click_failed"
ERROR_EMAIL_INPUT_FAILED = "email_input_failed"
ERROR_PASSWORD_INPUT_FAILED = "password_input_failed"
ERROR_CONFIRM_INPUT_FAILED = "confirm_input_failed"
ERROR_NEXT_CLICK_FAILED = "next_click_failed"
ERROR_EMAIL_USED_PROMPT = "email_used_prompt"
ERROR_SLIDER_FAILED = "slider_failed"
ERROR_SLIDER_REAPPEARED = "slider_reappeared"
ERROR_MISSING_CREDENTIALS = "missing_credentials"
ERROR_CODE_NOT_FOUND = "code_not_found"
ERROR_CODE_INPUT_NOT_VISIBLE = "code_input_not_visible"
ERROR_CODE_INPUT_ERROR = "code_input_error"
ERROR_URL_JUMP_FAILED = "url_jump_failed"


# =============================================================================
# CLASSES
# =============================================================================

class WindowsDumpManager:
    """Context manager for creating crash dump files on Windows."""
    
    def __init__(self, logger: Optional[Callable[[str], None]], email: str) -> None:
        self.logger = logger
        self.email = email
    
    def __enter__(self) -> 'WindowsDumpManager':
        return self
    
    def __exit__(self, exc_type: Optional[type], exc_val: Optional[BaseException], exc_tb: Optional[Any]) -> None:
        if exc_type:
            try:
                # Create dump file
                dump_dir = os.path.join(os.getcwd(), 'logs', 'dumps')
                os.makedirs(dump_dir, exist_ok=True)
                timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"dump_{timestamp}_{self.email}.txt"
                filepath = os.path.join(dump_dir, filename)
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(f"Timestamp: {timestamp}\n")
                    f.write(f"Email: {self.email}\n")
                    f.write(f"Exception: {exc_type.__name__}: {exc_val}\n")
                    f.write("-" * 50 + "\n")
                    traceback.print_tb(exc_tb, file=f)
                    f.write("-" * 50 + "\n")
                    f.write(f"System: {platform.system()} {platform.release()}\n")
                    
                if self.logger:
                    self.logger(f"已生成错误转储文件: {filepath}")
            except Exception as e:
                if self.logger:
                    self.logger(f"生成转储文件失败: {e}")


class StepRunner:
    """Executes steps with resource monitoring and timeout handling."""
    
    def __init__(
        self, 
        logger: Optional[Callable[[str], None]], 
        stop_event: Optional[threading.Event], 
        max_cpu: int = DEFAULT_MAX_CPU_PERCENT, 
        max_mem: int = DEFAULT_MAX_MEM_MB
    ) -> None:
        self.logger = logger
        self.stop_event = stop_event
        self.max_cpu = max_cpu
        self.max_mem = max_mem
        
    def _check_resources(self) -> None:
        """Check system resources and wait if they are too high."""
        try:
            for _ in range(5):
                cpu = psutil.cpu_percent(interval=None)  # Non-blocking first check
                if cpu == 0.0:  # First call often returns 0
                    cpu = psutil.cpu_percent(interval=0.1)
                
                mem = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
                
                if cpu < self.max_cpu and mem < self.max_mem:
                    return
                
                if self.logger:
                    self.logger(f"资源繁忙 (CPU={cpu:.1f}%, Mem={mem:.1f}MB)，等待释放...")
                time.sleep(1)
        except (psutil.Error, OSError):
            pass

    def run(self, name: str, func: Callable[[], Any], timeout: int = DEFAULT_TIMEOUT_SEC) -> Any:
        """Execute a function with timeout and resource checking."""
        if self.stop_event and self.stop_event.is_set():
            raise RuntimeError(ERROR_STOPPED)
            
        self._check_resources()
            
        if self.logger:
            self.logger(f"--- 阶段开始: {name} ---")
            
        t0 = time.time()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(func)
                res = future.result(timeout=timeout)
                return res
        except TimeoutError:
            if self.logger:
                self.logger(f"阶段 {name} 超时 ({timeout}s)")
            raise RuntimeError(f"Step {name} timed out")
        except Exception as e:
            if self.logger:
                self.logger(f"阶段 {name} 异常: {e}")
            raise e
        finally:
            dur = time.time() - t0
            if self.logger:
                self.logger(f"--- 阶段结束: {name} (耗时 {dur:.2f}s) ---")
            time.sleep(0.1)


@dataclass
class RegistrationEvents:
    """Event callbacks for registration process."""
    on_success: Optional[Callable[[str], None]] = None  # email
    on_failure: Optional[Callable[[str, str], None]] = None  # email, reason
    on_log: Optional[Callable[[str], None]] = None
    on_finish: Optional[Callable[[str, bool, str], None]] = None  # email, success, message


# =============================================================================
# PROCESS UTILITIES
# =============================================================================

def get_pid_by_port(port: int) -> Optional[int]:
    """Find PID of the process listening on a specific TCP port."""
    try:
        # psutil.net_connections requires root/admin on some OS/versions for all PIDs
        # But for user-owned processes it often works.
        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                return conn.pid
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    return None

def kill_process_tree(pid: int, logger: Optional[Callable[[str], None]] = None) -> None:
    """Kill a process and all its children forcibly."""
    try:
        if not psutil.pid_exists(pid):
            return
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
        try:
            parent.wait(3)
        except psutil.TimeoutExpired:
            pass
        if logger:
            logger(f"已强制结束进程 PID={pid}")
    except psutil.NoSuchProcess:
        pass
    except Exception as e:
        if logger:
            logger(f"结束进程 PID={pid} 失败: {e}")

def kill_all_bitbrowsers(logger: Optional[Callable[[str], None]] = None) -> None:
    """Force kill all BitBrowser processes."""
    target_names = ['BitBrowser.exe', 'BitBrowser']
    killed_count = 0
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if proc.info['name'] in target_names:
                kill_process_tree(proc.info['pid'], logger)
                killed_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if logger and killed_count > 0:
        logger(f"已清理 {killed_count} 个残留 BitBrowser 进程")


class BitBrowserClient:
    """Client for interacting with BitBrowser API."""
    
    def __init__(self, base_url: str, secret: Optional[str] = None) -> None:
        self.base_url = base_url.rstrip('/')
        self.secret = secret
        self.session = requests.Session()
        # Optimize connection pool
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=3)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def _headers(self) -> Dict[str, str]:
        h = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if self.secret:
            h['Authorization'] = self.secret
        return h

    def _request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict[str, Any]] = None, 
        timeout: int = 30
    ) -> requests.Response:
        url = f"{self.base_url}{endpoint}"
        try:
            t0 = time.time()
            if method.upper() == 'POST':
                r = self.session.post(url, headers=self._headers(), data=json.dumps(data) if data else None, timeout=timeout)
            else:
                r = self.session.get(url, headers=self._headers(), timeout=timeout)
            
            # Log slow requests
            elapsed = time.time() - t0
            if elapsed > 1.0:
                 print(f"Slow BitBrowser API: {method} {endpoint} took {elapsed:.2f}s")

            if r.status_code == 405:
                print(f"HTTP 405 Method Not Allowed: {method} {url}")
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 405:
                print(f"HTTP 405 Error Details: {method} {url} - {e.response.text}")
            raise e
        except requests.exceptions.RequestException:
            raise

    def update_browser(
        self, 
        name: str, 
        proxy: Optional[Dict[str, Any]] = None, 
        enable_udp: bool = False, 
        cmd_args: Optional[List[str]] = None
    ) -> str:
        payload: Dict[str, Any] = {
            'name': name,
            'remark': '',
            'proxyMethod': 2,
            'proxyType': 'noproxy',
            'isUDP': 1 if enable_udp else 0,
            'syncTabs': True,
            'syncCookies': True,
            'syncLocalStorage': True,
            'syncIndexedDb': True,
            'browserFingerPrint': {
                'coreVersion': '124'
            }
        }
        if cmd_args:
            payload['cmdArgs'] = cmd_args
        if proxy:
            payload.update(proxy)
        r = self._request('POST', '/browser/update', payload, timeout=30)
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

    def create_browser(
        self, 
        name: str, 
        proxy: Optional[Dict[str, Any]] = None, 
        enable_udp: bool = False, 
        cmd_args: Optional[List[str]] = None
    ) -> str:
        payload: Dict[str, Any] = {
            'name': name,
            'remark': '',
            'proxyMethod': 2,
            'proxyType': 'noproxy',
            'isUDP': 1 if enable_udp else 0,
            'syncTabs': True,
            'syncCookies': True,
            'syncLocalStorage': True,
            'syncIndexedDb': True,
            'browserFingerPrint': {
                'coreVersion': '124'
            }
        }
        if cmd_args:
            payload['cmdArgs'] = cmd_args
        if proxy:
            payload.update(proxy)
        r = self._request('POST', '/browser/create', payload, timeout=30)
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
        r = self._request('POST', '/browser/open', payload, timeout=60)
        data = r.json()
        d = data.get('data')
        if isinstance(d, dict):
            return d
        return {}

    def close_browser(self, browser_id: str) -> None:
        try:
            self._request('POST', '/browser/close', {'id': browser_id}, timeout=10)
        except (requests.exceptions.RequestException, RuntimeError):
            pass

    def delete_browser(self, browser_id: str) -> None:
        try:
            self._request('POST', '/browser/delete', {'id': browser_id}, timeout=30)
        except (requests.exceptions.RequestException, RuntimeError):
            pass

    def detail_browser(self, browser_id: str) -> Dict[str, Any]:
        try:
            r = self._request('POST', '/browser/detail', {'id': browser_id}, timeout=30)
            data = r.json()
            d = data.get('data')
            if isinstance(d, dict):
                return d
            return {}
        except (requests.exceptions.RequestException, RuntimeError):
            return {}


# =============================================================================
# FILE UTILITIES
# =============================================================================

def read_rows(input_path: str) -> List[Dict[str, Any]]:
    """Read rows from various file formats (CSV, JSON, XLSX)."""
    # Check for "----" format (New EmailPool format)
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            first_line = f.readline()
            if '----' in first_line:
                try:
                    from src.email_pool import EmailPool
                    pool = EmailPool(input_path)
                    return pool.get_all_rows()
                except ImportError:
                    pass
    except (IOError, UnicodeDecodeError):
        pass

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
    """Write rows to CSV file."""
    import csv
    if not rows:
        return
    # Fix: Collect all unique keys from all rows to ensure fieldnames cover everything
    headers = set()
    for r in rows:
        headers.update(r.keys())
    
    # Sort headers for consistency, prioritize common fields
    header_list = sorted(list(headers))
    priority_fields = ['email', 'password', 'status', 'host', 'port', 'msg']
    for f in reversed(priority_fields):
        if f in header_list:
            header_list.remove(f)
            header_list.insert(0, f)

    with open(input_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=header_list)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


# =============================================================================
# WEBDRIVER UTILITIES
# =============================================================================

def element_exists(driver: webdriver.Remote, xpath: str, timeout_ms: int, poll_ms: int) -> bool:
    """Check if an element exists in the DOM."""
    try:
        eff_timeout = min(timeout_ms, 60000)
        eff_poll = max(0.2, poll_ms / 1000.0)
        WebDriverWait(driver, eff_timeout / 1000.0, poll_frequency=eff_poll).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        return True
    except Exception:
        return False


def element_visible(driver: webdriver.Remote, xpath: str, timeout_ms: int, poll_ms: int) -> bool:
    """Check if an element is visible on the page."""
    try:
        eff_timeout = min(timeout_ms, 60000)
        eff_poll = max(0.2, poll_ms / 1000.0)
        WebDriverWait(driver, eff_timeout / 1000.0, poll_frequency=eff_poll).until(
            EC.visibility_of_element_located((By.XPATH, xpath))
        )
        return True
    except Exception:
        return False


def element_exists_visible(driver: webdriver.Remote, xpath: str, timeout_ms: int, poll_ms: int) -> bool:
    """Check if an element exists and is visible."""
    if not element_exists(driver, xpath, timeout_ms, poll_ms):
        return False
    return element_visible(driver, xpath, timeout_ms, poll_ms)


def wait_page_ready(driver: webdriver.Remote, timeout_sec: int = PAGE_READY_TIMEOUT_SEC) -> bool:
    """Wait for page to reach interactive or complete ready state."""
    try:
        WebDriverWait(driver, timeout_sec).until(
            lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
        )
        return True
    except Exception:
        return False


def log_page_timing(driver: webdriver.Remote, logger: Optional[Callable[[str], None]]) -> None:
    """Log page timing metrics."""
    if not logger:
        return
    try:
        data = driver.execute_script("""
            try {
              var t = performance.timing || {};
              var navs = performance.getEntriesByType && performance.getEntriesByType('navigation');
              var nav = (navs && navs.length) ? navs[0] : null;
              var ns = nav ? nav.startTime : (t.navigationStart || 0);
              var dcl = nav ? nav.domContentLoadedEventEnd : (t.domContentLoadedEventEnd || 0);
              var le = nav ? nav.loadEventEnd : (t.loadEventEnd || 0);
              return {
                readyState: document.readyState,
                navigationStart: ns,
                domContentLoadedEventEnd: dcl,
                loadEventEnd: le
              };
            } catch (e) {
              return { readyState: document.readyState };
            }
        """)
        ns = float(data.get('navigationStart') or 0.0)
        dcl = float(data.get('domContentLoadedEventEnd') or 0.0)
        le = float(data.get('loadEventEnd') or 0.0)
        rs = data.get('readyState')
        def fmt(ms: float) -> str:
            return f"{int(ms)}ms" if ms > 0 else "n/a"
        logger(f"PageTiming: readyState={rs}, domContentLoaded={fmt(dcl - ns)}, loadComplete={fmt(le - ns)}")
    except Exception:
        pass


def log_resource_status(driver: webdriver.Remote, logger: Optional[Callable[[str], None]], limit: int = 200) -> None:
    """Log resource loading status."""
    if not logger:
        return
    try:
        data = driver.execute_script("""
            try {
              var es = performance.getEntries ? performance.getEntries() : [];
              var out = {};
              for (var i=0;i<es.length;i++){
                var e = es[i];
                var t = e.entryType || e.initiatorType || 'unknown';
                out[t] = (out[t]||0) + 1;
              }
              return {counts: out, total: es.length};
            } catch (e) { return {error: ''+e}; }
        """)
        counts = data.get('counts') or {}
        total = data.get('total') or 0
        try:
            logger(f"Resources: total={total}, counts={json.dumps(counts, ensure_ascii=False)}")
        except Exception:
            logger(f"Resources: total={total}")
    except Exception:
        pass


def log_memory_usage(logger: Optional[Callable[[str], None]] = None) -> None:
    """Log current process memory usage."""
    if not logger:
        return
    try:
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        rss_mb = mem_info.rss / 1024 / 1024
        logger(f"当前进程内存占用: {rss_mb:.2f} MB")
    except (psutil.Error, OSError):
        pass


def log_resource_phase_timings(
    driver: webdriver.Remote, 
    logger: Optional[Callable[[str], None]], 
    contains: Optional[str] = None, 
    limit: int = 30
) -> None:
    """Log detailed resource timing information."""
    if not logger:
        return
    try:
        data = driver.execute_script("""
            try {
              var es = performance.getEntriesByType ? performance.getEntriesByType('resource') : [];
              var out = [];
              var kw = arguments[0] || '';
              var lim = arguments[1] || 30;
              for (var i=0;i<es.length;i++){
                var e = es[i];
                if (kw && e.name && e.name.indexOf(kw) === -1) continue;
                out.push({
                  name: e.name,
                  initiatorType: e.initiatorType,
                  duration: e.duration,
                  dns: (e.domainLookupEnd && e.domainLookupStart) ? (e.domainLookupEnd - e.domainLookupStart) : 0,
                  tcp: (e.connectEnd && e.connectStart) ? (e.connectEnd - e.connectStart) : 0,
                  ssl: (e.connectEnd && e.secureConnectionStart) ? (e.connectEnd - e.secureConnectionStart) : 0,
                  ttfb: (e.responseStart && e.requestStart) ? (e.responseStart - e.requestStart) : 0,
                  download: (e.responseEnd && e.responseStart) ? (e.responseEnd - e.responseStart) : 0
                });
              }
              if (out.length > lim) out = out.slice(out.length - lim);
              return out;
            } catch (e) { return []; }
        """, contains or "", limit)
        if not isinstance(data, list) or not data:
            return
        key = contains or "all"
        logger(f"ResourceTiming[{key}] count={len(data)}")
        for e in data:
            try:
                name = str(e.get('name') or '')
                it = str(e.get('initiatorType') or '')
                dur = float(e.get('duration') or 0.0)
                dns = float(e.get('dns') or 0.0)
                tcp = float(e.get('tcp') or 0.0)
                ssl = float(e.get('ssl') or 0.0)
                ttfb = float(e.get('ttfb') or 0.0)
                dl = float(e.get('download') or 0.0)
                logger(f"Timing {it} {int(dur)}ms dns={int(dns)} tcp={int(tcp)} ssl={int(ssl)} ttfb={int(ttfb)} dl={int(dl)} url={name}")
            except (ValueError, TypeError):
                pass
    except Exception:
        pass


def ensure_artifact_dir() -> str:
    """Ensure the test artifacts directory exists."""
    base = os.path.dirname(__file__)
    p = os.path.join(base, 'test_artifacts')
    try:
        os.makedirs(p, exist_ok=True)
    except (OSError, PermissionError):
        pass
    return p


def take_screenshot(driver: webdriver.Remote, name: str, logger: Optional[Callable[[str], None]] = None) -> None:
    """Take a screenshot and save it to the artifacts directory."""
    d = ensure_artifact_dir()
    path = os.path.join(d, name)
    try:
        driver.save_screenshot(path)
        if logger:
            logger(f"Screenshot: {path}")
    except Exception as e:
        if logger:
            logger(f"Screenshot error: {e}")


def write_artifact_json(name: str, data: Any, logger: Optional[Callable[[str], None]] = None) -> None:
    """Write data as JSON to the artifacts directory."""
    d = ensure_artifact_dir()
    path = os.path.join(d, name)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if logger:
            logger(f"Artifact: {path}")
    except Exception as e:
        if logger:
            logger(f"Artifact error: {e}")


def check_connectivity(
    driver: webdriver.Remote, 
    url: str, 
    logger: Optional[Callable[[str], None]], 
    max_wait: int = CONNECTIVITY_MAX_WAIT_SEC,
    udp_enabled: bool = False
) -> bool:
    """Check network connectivity by navigating to a URL."""
    ok = True
    try:
        try:
            driver.get(url)
        except Exception:
            driver.execute_script("window.location.href=arguments[0];", url)
        wait_page_ready(driver, max_wait)
        log_page_timing(driver, logger)
        log_resource_status(driver, logger)
        try:
            init_handles = driver.window_handles
            if logger:
                logger(f"初始窗口数量: {len(init_handles)}")
            if len(init_handles) > 1 and (not udp_enabled):
                try:
                    driver.switch_to.window(init_handles[0])
                    if logger:
                        logger("检测到初始阶段存在额外标签，已切回主标签")
                except Exception:
                    pass
        except Exception:
            pass
        take_screenshot(driver, "bitbrowser_initial.png", logger)
    except Exception as e:
        ok = False
        if logger:
            logger(f"Connectivity nav error: {e}")
    return ok


def xpath_count(driver: webdriver.Remote, xpath: str) -> int:
    """Count elements matching an XPath."""
    try:
        return int(driver.execute_script("""
            try {
              var r = document.evaluate(arguments[0], document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
              return r ? r.snapshotLength : 0;
            } catch (e) { return -1; }
        """, xpath))
    except Exception:
        return -1


def safe_click(
    driver: webdriver.Remote, 
    xpath: str, 
    timeout_ms: int, 
    poll_ms: int, 
    logger: Optional[Callable[[str], None]] = None, 
    retries: int = SAFE_CLICK_RETRIES
) -> bool:
    """Safely click an element with multiple fallback strategies."""
    for i in range(max(1, retries)):
        try:
            WebDriverWait(driver, min(timeout_ms, 12000) / 1000.0, poll_frequency=max(0.2, poll_ms/1000.0)).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            ).click()
            return True
        except Exception:
            try:
                el = WebDriverWait(driver, min(timeout_ms, 12000) / 1000.0, poll_frequency=max(0.2, poll_ms/1000.0)).until(
                    EC.presence_of_element_located((By.XPATH, xpath))
                )
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                try:
                    el.click()
                    return True
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                    return True
            except Exception as e:
                if logger:
                    logger(f"点击失败重试 {i+1}/{retries}: {e}")
        time.sleep(0.5)
    return False


def first_present_xpath(
    driver: webdriver.Remote, 
    xpaths: List[Optional[str]], 
    timeout_ms: int, 
    poll_ms: int
) -> Optional[str]:
    """Find the first visible XPath from a list."""
    for xp in xpaths:
        if not xp:
            continue
        try:
            if element_exists_visible(driver, xp, min(timeout_ms, 12000), poll_ms):
                return xp
        except Exception:
            pass
    return None


def safe_click_any(
    driver: webdriver.Remote, 
    xpaths: List[Optional[str]], 
    timeout_ms: int, 
    poll_ms: int, 
    logger: Optional[Callable[[str], None]] = None, 
    retries: int = SAFE_CLICK_RETRIES
) -> bool:
    """Safely click the first visible element from a list of XPaths."""
    xp = first_present_xpath(driver, xpaths, timeout_ms, poll_ms)
    if not xp:
        return False
    return safe_click(driver, xp, timeout_ms, poll_ms, logger, retries)


def js_click_xpath(driver: webdriver.Remote, xpath: str) -> bool:
    """Click an element using JavaScript execution."""
    try:
        r = driver.execute_script("""
          var xp = arguments[0];
          try {
            var res = document.evaluate(xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
            var el = res && res.singleNodeValue;
            if (el) {
              try { el.scrollIntoView({block:'center'}); } catch(e) {}
              try { el.click(); return true; } catch(e) {}
              try { el.dispatchEvent(new MouseEvent('click', {bubbles:true})); return true; } catch(e) {}
            }
          } catch (e) {}
          return false;
        """, xpath)
        return bool(r)
    except Exception:
        return False


def safe_send_keys(
    driver: webdriver.Remote, 
    xpath: str, 
    text: str, 
    timeout_ms: int, 
    poll_ms: int, 
    logger: Optional[Callable[[str], None]] = None, 
    retries: int = 1
) -> bool:
    """Safely send keys to an input element."""
    for i in range(max(1, retries)):
        try:
            el = WebDriverWait(driver, min(timeout_ms, 12000) / 1000.0, poll_frequency=max(0.2, poll_ms/1000.0)).until(
                EC.presence_of_element_located((By.XPATH, xpath))
            )
            el.send_keys(text)
            return True
        except Exception as e:
            if logger:
                logger(f"输入失败重试 {i+1}/{retries}: {e}")
        time.sleep(0.5)
    return False


def safe_send_keys_any(
    driver: webdriver.Remote, 
    xpaths: List[Optional[str]], 
    text: str, 
    timeout_ms: int, 
    poll_ms: int, 
    logger: Optional[Callable[[str], None]] = None, 
    retries: int = 1
) -> bool:
    """Send keys to the first available input element from a list of XPaths."""
    for xp in xpaths:
        if not xp:
            continue
        if safe_send_keys(driver, xp, text, timeout_ms, poll_ms, logger, retries):
            return True
        try:
            el = WebDriverWait(driver, min(timeout_ms, 12000) / 1000.0, poll_frequency=max(0.2, poll_ms/1000.0)).until(
                EC.presence_of_element_located((By.XPATH, xp))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try:
                el.send_keys(text)
                return True
            except Exception:
                pass
        except Exception:
            pass
    return False


def validate_xpaths(
    driver: webdriver.Remote, 
    xpaths: Dict[str, str], 
    logger: Optional[Callable[[str], None]], 
    keys: List[str], 
    timeout_ms: int, 
    poll_ms: int
) -> Dict[str, bool]:
    """Validate multiple XPath locators."""
    result: Dict[str, bool] = {}
    for k in keys:
        xp = xpaths.get(k) or ""
        ok = bool(xp) and element_exists_visible(driver, xp, min(timeout_ms, 8000), poll_ms)
        result[k] = ok
        if logger:
            logger(f"XPath验证 {k}: {'OK' if ok else 'FAIL'}")
    return result


def find_click(driver: webdriver.Remote, xpath: str, timeout_ms: int, poll_ms: int) -> None:
    """Find and click an element."""
    eff_timeout = min(timeout_ms, 60000)
    WebDriverWait(driver, eff_timeout / 1000.0, poll_frequency=poll_ms / 1000.0).until(
        EC.element_to_be_clickable((By.XPATH, xpath))
    ).click()


def find_click_any(driver: webdriver.Remote, xpath: str, timeout_ms: int, poll_ms: int) -> None:
    """Find and click an element with fallback strategies."""
    try:
        eff_timeout = min(timeout_ms, 60000)
        WebDriverWait(driver, eff_timeout / 1000.0, poll_frequency=poll_ms / 1000.0).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        ).click()
        return
    except Exception:
        eff_timeout = min(timeout_ms, 60000)
        el = WebDriverWait(driver, eff_timeout / 1000.0, poll_frequency=poll_ms / 1000.0).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
        try:
            el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", el)


# =============================================================================
# SLIDER UTILITIES
# =============================================================================

def _human_drag_track(distance: int, duration_ms: int = 900, jitter_px: int = 1, overshoot_px: int = 5) -> List[int]:
    """Generate a human-like drag track for slider solving."""
    steps = 15
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


_SLIDER_PASS_CACHE: Dict[str, Dict[str, Any]] = {}


def solve_slider(
    driver: webdriver.Remote, 
    xpaths: Dict[str, str], 
    timeout_ms: int, 
    poll_ms: int, 
    logger: Optional[Callable[[str], None]] = None
) -> bool:
    """
    Attempt to solve a slider captcha.
    
    Args:
        driver: Selenium WebDriver instance
        xpaths: Dictionary containing slider element XPaths
        timeout_ms: Maximum time to wait for slider in milliseconds
        poll_ms: Polling interval in milliseconds
        logger: Optional logging function
        
    Returns:
        True if slider was solved successfully, False otherwise
    """
    t0 = time.time()
    ok_xpath = xpaths.get('code_url_element') or xpaths.get('next_btn') or xpaths.get('password_input')

    def _slider_container_visible(short_timeout_ms: int = 600) -> bool:
        try:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            if element_visible(driver, xpaths['slider_container'], short_timeout_ms, poll_ms):
                return True
        except Exception:
            pass
        try:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            if element_exists(driver, xpaths['slider_iframe'], short_timeout_ms, poll_ms):
                try:
                    iframe = driver.find_element(By.XPATH, xpaths['slider_iframe'])
                    driver.switch_to.frame(iframe)
                except Exception:
                    return False
                try:
                    return element_visible(driver, xpaths['slider_container'], short_timeout_ms, poll_ms)
                finally:
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
        except Exception:
            pass
        return False

    def _ok_visible(short_timeout_ms: int) -> bool:
        if not ok_xpath:
            return False
        try:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            return element_visible(driver, ok_xpath, short_timeout_ms, poll_ms)
        except Exception:
            return False

    try:
        if _ok_visible(min(1500, timeout_ms)):
            if logger:
                logger("Slider: 已处于后续步骤，跳过滑块等待")
            return True
    except Exception:
        pass

    cache_key = ""
    domain_filter = None
    try:
        sid = str(getattr(driver, 'session_id', '') or '')
        cur = ''
        try:
            cur = driver.current_url or ''
        except Exception:
            cur = ''
        host = ''
        try:
            host = (urllib.parse.urlparse(cur).netloc or '').strip()
        except Exception:
            host = ''
        domain_filter = host or None
        cache_key = f"{sid}|{host}"
    except Exception:
        cache_key = ""

    try:
        cached = _SLIDER_PASS_CACHE.get(cache_key) if cache_key else None
        if cached and (time.time() - float(cached.get('t') or 0.0) < SLIDER_CACHE_TIMEOUT_SEC):
            if not _slider_container_visible(600):
                if logger:
                    logger("Slider: 命中通过缓存，且当前未检测到滑块")
                if cached.get('ok') is True:
                    return True
    except Exception:
        pass

    appear_wait_ms = min(max(timeout_ms, 15000), 60000)
    start = time.time()
    while (time.time() - start) * 1000 < appear_wait_ms:
        try:
            if _ok_visible(800):
                if logger:
                    logger("Slider: 等待期间检测到后续步骤，视为通过")
                if cache_key:
                    _SLIDER_PASS_CACHE[cache_key] = {'ok': True, 't': time.time()}
                return True
        except Exception:
            pass
        if element_visible(driver, xpaths['slider_iframe'], 800, poll_ms) or element_visible(driver, xpaths['slider_container'], 800, poll_ms):
            break
        time.sleep(max(0.2, poll_ms / 1000.0))
    else:
        if logger:
            logger(f"Slider: {int(appear_wait_ms)}ms 内未出现滑块")
            log_page_timing(driver, logger)
            log_resource_phase_timings(driver, logger, domain_filter, 30)
        return False
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
                    time.sleep(1.5)  # 找不到容器时增加等待
                    continue
                handle_wait_ms = min(timeout_ms, 4000)
                handle = None
                handle_candidates = [xpaths['slider_handle'], "//*[contains(@class,'slider-btn')]", "//*[contains(@class,'btn-icon')]"]
                # 显式等待滑块句柄出现
                try:
                    WebDriverWait(driver, 5).until(lambda d: any(element_exists(d, hx, 500, poll_ms) for hx in handle_candidates))
                except Exception:
                    pass

                for hx in handle_candidates:
                    try:
                        handle = WebDriverWait(driver, handle_wait_ms / 1000.0, poll_frequency=poll_ms / 1000.0).until(
                            EC.presence_of_element_located((By.XPATH, hx))
                        )
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
                dist = 238
            except Exception:
                dist = 238
            try:
                actions = ActionChains(driver)
                if handle is not None:
                    actions.move_to_element(handle).pause(0.01).move_by_offset(2, 0).click_and_hold(handle).pause(0.01)
                else:
                    h = container.size.get('height') or 20
                    actions.move_to_element_with_offset(container, 5, int(h/2)).pause(0.01).click_and_hold().pause(0.01)
                # Faster slider: reduce steps significantly
                steps = max(2, int(dist / 100))
                step_len = max(40, int(dist / steps))
                moved = 0
                # Use a single action chain for smoother and faster movement
                for _ in range(steps):
                    left = dist - moved
                    dx = min(step_len, left)
                    actions.move_by_offset(dx, 0)
                    moved += dx
                actions.release().perform()
                
                if _ok_visible(1500):
                    if cache_key:
                        _SLIDER_PASS_CACHE[cache_key] = {'ok': True, 't': time.time()}
                    if logger:
                        logger(f"Slider: 通过(检测到后续元素)，耗时 {time.time() - t0:.2f}s")
                    return True

                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass

                if not _slider_container_visible(600):
                    if _ok_visible(1200):
                        if cache_key:
                            _SLIDER_PASS_CACHE[cache_key] = {'ok': True, 't': time.time()}
                        if logger:
                            logger(f"Slider: 通过(检测到后续元素)，耗时 {time.time() - t0:.2f}s")
                        return True
                    
                    # 优化: 滑块消失且一段时间未重现，视为通过，避免死等后续元素导致重试
                    time.sleep(1.0)
                    if not _slider_container_visible(500):
                         # 二次确认: 等待 3s 确保不是因刷新导致的短暂消失
                         time.sleep(3.0)
                         if not _slider_container_visible(500):
                             if logger:
                                 logger(f"Slider: 通过(滑块消失并确认)，耗时 {time.time() - t0:.2f}s")
                             if cache_key:
                                _SLIDER_PASS_CACHE[cache_key] = {'ok': True, 't': time.time()}
                             return True

                    time.sleep(0.4)
                    continue

                time.sleep(0.1)
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
    if _ok_visible(min(5000, timeout_ms)):
        if cache_key:
            _SLIDER_PASS_CACHE[cache_key] = {'ok': True, 't': time.time()}
        if logger:
            logger(f"Slider: 通过(最终兜底检测)，耗时 {time.time() - t0:.2f}s")
        return True
    if logger:
        logger(f"Slider: 失败，耗时 {time.time() - t0:.2f}s")
        log_page_timing(driver, logger)
        log_resource_phase_timings(driver, logger, domain_filter, 30)
        log_performance_network(driver, logger, limit=120, domain_filter=domain_filter)
    return False


# =============================================================================
# CODE EXTRACTION UTILITIES
# =============================================================================

def extract_code_from_page_text(driver: webdriver.Remote) -> Optional[str]:
    """Extract 6-digit verification code from page body text."""
    try:
        body_text = driver.find_element(By.TAG_NAME, 'body').text
        m = re.search(r"\b(\d{6})\b", body_text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def extract_code_using_xpath(driver: webdriver.Remote, xpath: str) -> Optional[str]:
    """Extract 6-digit verification code from element at given XPath."""
    try:
        el = driver.find_element(By.XPATH, xpath)
        txt = el.text or el.get_attribute('value') or ''
        m = re.search(r"\b(\d{6})\b", txt)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def extract_code_from_url(url: str, logger: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """
    Extract verification code from a given URL (XinLan Mode).
    
    Args:
        url: URL to fetch and extract code from
        logger: Optional logging function
        
    Returns:
        6-digit verification code if found, None otherwise
    """
    try:
        # Use a real user-agent to avoid basic blocking
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            content = resp.text
            # Use strict 6-digit regex as per user requirement: "连续6位数字"
            # And also per user request: "使用正则表达式提取网页中的连续6位数字验证码"
            codes = re.findall(r'\b\d{6}\b', content)
            if codes:
                # Return the last one found as it's likely the newest one if multiple exist
                # Or first? Usually pages show latest at top or it's a dedicated page.
                # Let's return the first one for now.
                return codes[0] 
    except requests.exceptions.RequestException as e:
        if logger:
            logger(f"HTTP Code Extraction Failed: {e}")
    return None


def wait_extract_code(
    driver: webdriver.Remote, 
    xpath: Optional[str], 
    max_wait_sec: int = 20, 
    logger: Optional[Callable[[str], None]] = None, 
    extraction_mode: str = 'imap', 
    extraction_url: Optional[str] = None
) -> Optional[str]:
    """Wait and extract verification code with multiple strategies."""
    end = time.time() + max_wait_sec
    code: Optional[str] = None
    while time.time() < end:
        if logger:
            logger("等待验证码...")
            
        # XinLan Mode Support
        if extraction_mode == 'http' and extraction_url:
            code = extract_code_from_url(extraction_url, logger)
            if code:
                if logger:
                    logger(f"验证码通过HTTP提取: {code}")
                return code
            # If not found, fall through to sleep and retry
        else:
            # Original Logic
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


def extract_code_attempts(
    driver: webdriver.Remote, 
    xpath: Optional[str], 
    logger: Optional[Callable[[str], None]], 
    attempts: int = MAX_CODE_RETRIES
) -> Optional[str]:
    """Extract verification code with multiple attempts."""
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
        time.sleep(1 if i < attempts-1 else 0)
    return code


def extract_verification_code_flow(
    driver: webdriver.Remote, 
    code_url: str, 
    code_xpath: Optional[str], 
    logger: Optional[Callable[[str], None]], 
    debugger_http: Optional[str] = None, 
    timeout: int = CODE_EXTRACTION_TIMEOUT_SEC, 
    resend_xpath: Optional[str] = None
) -> Optional[str]:
    """
    Robust verification code retrieval with retry mechanism.
    
    Flow:
      Loop (Max 4 times):
        1. Wait 10s for code (poll 3s).
        2. If not found -> Refresh -> Wait 10s.
        3. If still not found -> Switch to Main Tab -> Click Resend -> Wait 2s -> Switch back.
    """
    main_handle = driver.current_window_handle
    start_time = time.time()
    max_retries = 4
    
    # Configuration
    INITIAL_WAIT = 10
    REFRESH_WAIT = 10
    RESEND_WAIT = 2
    
    # Open Code Tab initially
    code_tab_handle = None
    
    try:
        # Create new tab for code
        driver.switch_to.new_window('tab')
        code_tab_handle = driver.current_window_handle
        
        # Navigate to code URL
        if logger:
            logger(f"打开接码页面: {code_url}")
        try:
            driver.get(code_url)
        except Exception as e:
            if logger:
                logger(f"打开接码页面异常: {e}")
            # Try JS open as fallback if needed, but get() is standard
        
        for attempt in range(max_retries):
            # Check execution time
            if time.time() - start_time > timeout:
                if logger:
                    logger("验证码获取超时 (总时间限制)")
                break
                
            if logger:
                logger(f"=== 接码尝试轮次 {attempt + 1}/{max_retries} ===")
            
            # 1. First Detection (10s)
            if logger:
                logger(f"开始检测验证码 ({INITIAL_WAIT}s)...")
            code = wait_extract_code(driver, code_xpath, max_wait_sec=INITIAL_WAIT, logger=logger)
            if code:
                if logger:
                    logger(f"验证码获取成功: {code}")
                try:
                    driver.close()  # Close code tab
                    driver.switch_to.window(main_handle)
                except Exception:
                    pass
                return code
            
            # 2. Refresh & Second Detection
            if logger:
                logger("未检测到验证码，刷新页面...")
            try:
                driver.refresh()
                # Wait for ready state
                try:
                    WebDriverWait(driver, 5).until(lambda d: d.execute_script("return document.readyState") == "complete")
                except Exception:
                    pass
            except Exception as e:
                if logger:
                    logger(f"刷新失败: {e}")
                    
            if logger:
                logger(f"刷新后检测验证码 ({REFRESH_WAIT}s)...")
            code = wait_extract_code(driver, code_xpath, max_wait_sec=REFRESH_WAIT, logger=logger)
            if code:
                if logger:
                    logger(f"验证码获取成功 (刷新后): {code}")
                try:
                    driver.close()
                    driver.switch_to.window(main_handle)
                except Exception:
                    pass
                return code
            
            # 3. Resend Flow (if not last attempt)
            if attempt < max_retries - 1:
                if logger:
                    logger("仍未检测到，执行重发流程...")
                try:
                    # Switch to Main Tab
                    driver.switch_to.window(main_handle)
                    
                    # Locate and Click Resend
                    resend_xp = resend_xpath or "//a[contains(text(), 'Resend Code')]"
                    if logger:
                        logger(f"查找重发按钮: {resend_xp}")
                    
                    clicked = safe_click_any(driver, [resend_xp, "//*[contains(text(), 'Resend')]", "//*[contains(@class, 'highlight')]"], 5000, 1000, logger)
                    
                    if clicked:
                        if logger:
                            logger(f"已点击重发按钮，等待 {RESEND_WAIT}s...")
                        time.sleep(RESEND_WAIT)
                    else:
                        if logger:
                            logger("❌ 未找到重发按钮，跳过重发步骤")
                    
                    # Switch back to Code Tab
                    if code_tab_handle:
                        driver.switch_to.window(code_tab_handle)
                        # Refresh again to see new email? Usually good practice
                        driver.refresh()
                    
                except Exception as e:
                    if logger:
                        logger(f"重发流程异常: {e}")
                    # Try to recover focus to code tab
                    try:
                        if code_tab_handle:
                            driver.switch_to.window(code_tab_handle)
                    except Exception:
                        pass
            else:
                if logger:
                    logger("已达到最大重试次数，放弃")

    except Exception as e:
        if logger:
            logger(f"接码流程严重异常: {e}")
    finally:
        # Ensure we close the code tab and return to main
        try:
            current = driver.current_window_handle
            if current != main_handle:
                driver.close()
            driver.switch_to.window(main_handle)
        except Exception:
            try:
                driver.switch_to.window(main_handle)
            except Exception:
                pass
            
    return None


def get_verification_code(
    driver: webdriver.Remote, 
    code_url: str, 
    code_xpath: Optional[str] = None, 
    retries: int = MAX_CODE_RETRIES, 
    wait_seconds: int = 5, 
    logger: Optional[Callable[[str], None]] = None
) -> Optional[str]:
    """Get verification code by opening a new tab."""
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
            time.sleep(1)  # Reduced from wait_seconds
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


# =============================================================================
# BROWSER UTILITIES
# =============================================================================

def open_attached_driver(open_data: Dict[str, Any]) -> webdriver.Chrome:
    """Open a Chrome driver attached to an existing browser instance."""
    driver_path = open_data.get('driver')
    debugger_address = open_data.get('http')
    if not driver_path or not debugger_address:
        raise RuntimeError(f"No driver/http returned: {open_data}")
    options = webdriver.ChromeOptions()
    options.debugger_address = debugger_address
    try:
        options.page_load_strategy = 'none'
    except Exception:
        pass
    try:
        options.set_capability('goog:loggingPrefs', {'browser': 'ALL', 'performance': 'ALL'})
    except Exception:
        pass
    service = Service(executable_path=driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.set_page_load_timeout(25)
    except Exception:
        pass
    try:
        driver.implicitly_wait(0)
    except Exception:
        pass
    return driver


def create_cdp_tab(debugger_address: str, url: str, logger: Optional[Callable[[str], None]] = None) -> bool:
    """Create a new tab via Chrome DevTools Protocol."""
    try:
        if debugger_address.startswith('http://'):
            base = debugger_address
        else:
            base = f"http://{debugger_address}"
        encoded = urllib.parse.quote(url, safe='')
        target_url = f"{base}/json/new?{encoded}"
        
        if logger:
            logger(f"调试接口(PUT): {target_url}")
        
        # Try PUT first (Standard CDP)
        try:
            r = requests.put(target_url, timeout=2)
            if r.status_code == 200:
                return True
            if r.status_code != 405:
                if logger:
                    logger(f"PUT失败: {r.status_code}")
        except requests.exceptions.RequestException as e:
            if logger:
                logger(f"PUT请求异常: {e}")

        # Fallback to GET if PUT failed (Compatibility)
        if logger:
            logger(f"调试接口(GET重试): {target_url}")
        r = requests.get(target_url, timeout=2)
        if logger:
            logger(f"GET响应: {r.status_code}")
        return r.status_code == 200
    except requests.exceptions.RequestException as e:
        if logger:
            logger(f"调试接口最终异常: {e}")
        return False


def open_tab_via_debugger(debugger_address: str, url: str, logger: Optional[Callable[[str], None]] = None) -> bool:
    """Open a new tab via debugger protocol."""
    return create_cdp_tab(debugger_address, url, logger)


def proxy_payload(
    host: str, 
    port: str, 
    username: Optional[str], 
    password: Optional[str], 
    protocol: str = 'socks5'
) -> Dict[str, Any]:
    """Construct proxy payload for BitBrowser."""
    if not host or not port:
        return {'proxyType': 'noproxy'}
    
    # Map protocol to bitbrowser proxyType if needed
    # Common types: socks5, http, https
    ptype = protocol.lower() if protocol else 'socks5'
    if ptype not in ('socks5', 'http', 'https', 'ssh'):
        ptype = 'socks5'

    p: Dict[str, Any] = {
        'proxyType': ptype,
        'host': host,
        'port': str(port),
        'proxyHost': host,
        'proxyPort': str(port),
    }
    if username:
        p['proxyUserName'] = username
    if password:
        p['proxyPassword'] = password
    
    # Construct proxy string URL
    if username and password:
        p['proxy'] = f"{ptype}://{username}:{password}@{host}:{port}"
    else:
        p['proxy'] = f"{ptype}://{host}:{port}"
    return p


def log_window_urls(driver: webdriver.Remote, logger: Optional[Callable[[str], None]]) -> None:
    """Log all window URLs."""
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


def log_browser_console(driver: webdriver.Remote, logger: Optional[Callable[[str], None]], limit: int = 50) -> None:
    """Log browser console messages."""
    if not logger:
        return
    try:
        logs = driver.get_log('browser')
        if not logs:
            return
        size = len(logs)
        start = max(0, size - limit)
        for entry in logs[start:]:
            try:
                ts = entry.get('timestamp')
                lvl = entry.get('level')
                msg = entry.get('message')
                logger(f"Console[{lvl}]: {msg}")
            except Exception:
                pass
    except Exception:
        pass


def log_performance_network(
    driver: webdriver.Remote, 
    logger: Optional[Callable[[str], None]], 
    limit: int = 200, 
    domain_filter: Optional[str] = None
) -> None:
    """Log performance network events."""
    if not logger:
        return
    def redact(h: Any) -> Any:
        if not isinstance(h, dict):
            return h
        out = {}
        for k, v in h.items():
            try:
                kl = str(k).lower()
            except Exception:
                kl = ''
            if kl in ('authorization', 'cookie', 'set-cookie', 'x-api-key', 'proxy-authorization'):
                out[k] = '[REDACTED]'
            else:
                out[k] = v
        return out
    try:
        logs = driver.get_log('performance')
        if not logs:
            return
        size = len(logs)
        start = max(0, size - limit)
        for entry in logs[start:]:
            try:
                msg_str = entry.get('message') or ''
                data = json.loads(msg_str)
                m = data.get('message', {})
                method = m.get('method')
                params = m.get('params', {})
                if method in ('Network.requestWillBeSent', 'Network.responseReceived'):
                    req = params.get('request', {})
                    res = params.get('response', {})
                    url = req.get('url') or res.get('url') or ''
                    if domain_filter and (domain_filter not in url):
                        continue
                    if method == 'Network.requestWillBeSent':
                        logger(f"HTTP[REQ] {req.get('method')} {url}")
                        hdrs = req.get('headers') or {}
                        try:
                            logger(f"HTTP[REQ-HEADERS] {json.dumps(redact(hdrs), ensure_ascii=False)}")
                        except Exception:
                            pass
                    if method == 'Network.responseReceived':
                        status = res.get('status')
                        logger(f"HTTP[RES] {status} {url}")
                        hdrs = res.get('headers') or {}
                        try:
                            logger(f"HTTP[RES-HEADERS] {json.dumps(redact(hdrs), ensure_ascii=False)}")
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception:
        pass


def log_response_bodies(
    driver: webdriver.Remote, 
    logger: Optional[Callable[[str], None]], 
    limit: int = 10, 
    domain_filter: Optional[str] = None
) -> None:
    """Log response bodies from performance logs."""
    if not logger:
        return
    try:
        logs = driver.get_log('performance')
        if not logs:
            return
        ids = []
        for entry in reversed(logs):
            try:
                data = json.loads(entry.get('message') or '{}')
                m = data.get('message', {})
                method = m.get('method')
                params = m.get('params', {})
                if method == 'Network.responseReceived':
                    res = params.get('response', {})
                    url = res.get('url') or ''
                    if domain_filter and (domain_filter not in url):
                        continue
                    rid = params.get('requestId')
                    if rid:
                        ids.append((rid, url))
                        if len(ids) >= limit:
                            break
            except Exception:
                pass
        for rid, url in ids:
            try:
                body = driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': rid})
                txt = body.get('body', '')
                logger(f"HTTP[BODY] {url} len={len(txt)}")
            except Exception:
                pass
    except Exception:
        pass


def extract_verification_code_unified(
    driver: webdriver.Remote, 
    email_addr: str, 
    password: str, 
    resend_xpath: Optional[str], 
    logger: Optional[Callable[[str], None]], 
    timeout: int = CODE_EXTRACTION_TIMEOUT_SEC, 
    proxy_config: Optional[Dict[str, Any]] = None, 
    stop_event: Optional[threading.Event] = None, 
    extraction_mode: str = 'imap', 
    extraction_url: Optional[str] = None
) -> Optional[str]:
    """
    Unified verification code extraction using src.captcha_receiver and UI interaction (Resend Code).
    Optimized to reuse IMAP connection.
    Supports both IMAP and HTTP (XinLan) modes.
    
    Args:
        driver: Selenium WebDriver instance
        email_addr: Email address for IMAP mode
        password: Password or auth code
        resend_xpath: XPath for resend button
        logger: Optional logging function
        timeout: Maximum time to wait for code
        proxy_config: Proxy configuration for IMAP
        stop_event: Event to check for cancellation
        extraction_mode: 'imap' or 'http'
        extraction_url: URL for HTTP mode
        
    Returns:
        Verification code if found, None otherwise
    """
    if logger:
        logger(f"Unified Captcha: Start (Email: {email_addr}) Mode: {extraction_mode}")
        if proxy_config:
            logger(f"Unified Captcha: Using Proxy {proxy_config.get('addr')}:{proxy_config.get('port')}")

    start_time = time.time()
    max_retries = 3  # As per new requirement (User requested max 3)
    
    # Ensure we are on the right window
    main_handle = driver.current_window_handle

    # Initialize MailExtractor once (Only for IMAP)
    extractor = None
    imap_proxy = None
    
    if extraction_mode == 'imap':
        # NOTE: Updated to use proxy for IMAP by default to avoid blocking by email providers (e.g. 163.com)
        # User Request: Use the same proxy as the browser window.
        # We allow disabling it via env var "DISABLE_IMAP_PROXY" just in case.
        use_imap_proxy = os.getenv("DISABLE_IMAP_PROXY", "false").lower() != "true"
        imap_proxy = proxy_config if use_imap_proxy else None
        
        if logger and proxy_config:
            if use_imap_proxy:
                 logger(f"Unified Captcha: IMAP Proxy ENABLED (Addr: {proxy_config.get('addr')})")
            else:
                 logger("Unified Captcha: IMAP Proxy disabled via env var DISABLE_IMAP_PROXY")

        try:
            extractor = MailExtractor(email_addr, password, proxy_config=imap_proxy, logger=logger)
        except Exception as e:
            if logger:
                logger(f"Unified Captcha: ❌ 初始化邮箱连接失败: {e}")
            return None

    try:
        for attempt in range(max_retries):
            if stop_event and stop_event.is_set():
                if logger:
                    logger("Unified Captcha: Stopped by user")
                return None

            if time.time() - start_time > timeout:
                if logger:
                    logger("Unified Captcha: Timeout")
                break
                
            if logger:
                logger(f"=== 接码轮次 {attempt+1}/{max_retries} ===")
                logger(f"步骤: 正在获取验证码 ({extraction_mode})...")
            
            # Call backend to get captcha
            poll_timeout = 25  # Increased for robustness
            t_poll_start = time.time()
            code = None
            
            # Inner poll loop
            if extraction_mode == 'http':
                # XinLan HTTP Mode - Optimized
                new_tab_handle = None
                try:
                    if logger:
                        logger(f"Unified Captcha: 开始HTTP获取流程 (Attempt {attempt+1})...")
                    
                    # 1. Open Tab (Once per attempt)
                    driver.execute_script("window.open('');")
                    new_tab_handle = driver.window_handles[-1]
                    driver.switch_to.window(new_tab_handle)
                    
                    # 2. Navigate
                    if logger:
                        logger(f"Unified Captcha: 打开提取URL: {extraction_url}")
                    driver.set_page_load_timeout(30)
                    driver.get(extraction_url or '')
                    
                    # 3. Polling Loop
                    check_count = 0
                    last_refresh_time = time.time()  # Track last refresh
                    
                    while time.time() - t_poll_start < poll_timeout:
                        if stop_event and stop_event.is_set():
                            break
                        check_count += 1
                        
                        try:
                            # NOTE: Do NOT refresh at start. Check content first!
                            # Refresh only if necessary (long time no update).
                            
                            # Wait for body presence (fast check)
                            try:
                                WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                            except Exception:
                                if logger:
                                    logger("Unified Captcha: ⚠️ 页面加载超时或body未找到")
                                driver.refresh()
                                time.sleep(2)
                                continue

                            # Strategy 1: Specific Element (#msg2) - Primary
                            try:
                                msg2_element = WebDriverWait(driver, 1).until(EC.presence_of_element_located((By.ID, "msg2")))
                                msg2_text = msg2_element.text.strip()
                                
                                # Log found content for debugging
                                if msg2_text and logger: 
                                    logger(f"Unified Captcha: 发现 #msg2 元素, 内容: '{msg2_text}'")
                                
                                # Verify if it looks like a code (6 digits)
                                if re.match(r'^\d{6}$', msg2_text):
                                    code = msg2_text
                                    if logger:
                                        logger(f"Unified Captcha: ✅ 通过 #msg2 提取成功: {code}")
                                    break
                            except Exception:
                                pass  # Fallback to regex

                            # Strategy 2: Body Regex - Fallback
                            body_element = driver.find_element(By.TAG_NAME, 'body')
                            body_text = body_element.text
                            
                            # Regex Logic (Enhanced)
                            # 1. Strict isolated 6 digits
                            matches = re.findall(r'\b\d{6}\b', body_text)
                            temp_code = matches[0] if matches else None
                            
                            # 2. Loose 6 digits
                            if not temp_code:
                                matches = re.findall(r'\d{6}', body_text)
                                temp_code = matches[0] if matches else None
                            
                            if temp_code:
                                code = temp_code
                                if logger:
                                    logger(f"Unified Captcha: HTTP提取成功 (Regex): {code}")
                                break
                            else:
                                if logger: 
                                    logger("Unified Captcha: 未找到验证码... (Retry in 2s)")
                                
                                # Refresh Logic: Only refresh if it's been > 10s since last refresh/load
                                # This allows JS to update the page without us wiping it out constantly
                                if time.time() - last_refresh_time > 10:
                                    if logger:
                                        logger("Unified Captcha: 页面无变化超过10秒，尝试刷新...")
                                    driver.refresh()
                                    last_refresh_time = time.time()
                                    time.sleep(2)  # Wait for reload
                                
                        except Exception as e:
                            if logger:
                                logger(f"Unified Captcha: ⚠️ 提取循环异常: {e}")
                        
                        time.sleep(2)  # Short sleep to allow JS updates
                        
                except Exception as e:
                    if logger:
                        logger(f"Unified Captcha: ⚠️ HTTP模式致命错误: {e}")
                finally:
                    # Cleanup Tab
                    try:
                        if new_tab_handle:
                            current_handles = driver.window_handles
                            if new_tab_handle in current_handles:
                                driver.switch_to.window(new_tab_handle)
                                driver.close()
                        driver.switch_to.window(main_handle)
                    except Exception as close_err:
                        if logger:
                            logger(f"Unified Captcha: 标签页清理异常: {close_err}")
                        try:
                            driver.switch_to.window(main_handle)
                        except Exception:
                            pass
            
            else:
                # IMAP Mode
                check_count = 0
                while time.time() - t_poll_start < poll_timeout:
                    if stop_event and stop_event.is_set():
                        return None
                    check_count += 1
                    
                    try:
                        # Ensure extractor is alive
                        if not extractor:
                             if logger:
                                 logger("Unified Captcha: Re-initializing MailExtractor...")
                             extractor = MailExtractor(email_addr, password, proxy_config=imap_proxy, logger=logger)

                        if logger:
                            logger(f"Unified Captcha: 第 {check_count} 次检查邮箱...")
                        code = extractor.get_latest_verification_code()
                        if code:
                            break
                    except Exception as e:
                        if logger:
                            logger(f"Unified Captcha: ⚠️ 邮箱检查异常: {e}")
                        # Try to reconnect if needed
                        try:
                            if extractor:
                                try:
                                    if hasattr(extractor, 'close'):
                                        extractor.close()
                                    elif hasattr(extractor, 'logout'):
                                        extractor.logout()
                                except Exception:
                                    pass
                            extractor = None  # Force re-init next loop
                        except Exception:
                            pass
                        time.sleep(1)
                    time.sleep(3)

            if code and code not in ["未匹配到验证码", "未找到邮件"] and not code.startswith("错误:"):
                if logger:
                    logger(f"Unified Captcha: ✅ 成功获取验证码: {code}")
                return code
            else:
                if logger:
                    logger(f"Unified Captcha: ❌ 本轮未检测到验证码 (code={code})")
                
            # If not found, try Resend
            if attempt < max_retries - 1:
                if logger:
                    logger("Unified Captcha: 尝试点击重发按钮 (Resend Code)...")
                try:
                    # Ensure focus
                    try:
                        driver.switch_to.window(main_handle)
                        driver.switch_to.default_content()  # Ensure we are not in an iframe
                    except Exception:
                        pass
                        
                    resend_xp = resend_xpath or "//a[contains(text(), 'Resend Code')]"
                
                    # Check visibility
                    if element_visible(driver, resend_xp, 3000, 1000):
                        if js_click_xpath(driver, resend_xp):
                            if logger:
                                logger("Unified Captcha: ✅ 已点击重发按钮")
                        else:
                            # Fallback click
                            el = driver.find_element(By.XPATH, resend_xp)
                            el.click()
                            if logger:
                                logger("Unified Captcha: ✅ 已点击重发按钮 (原生)")
                        
                        if logger:
                            logger("Unified Captcha: 等待邮件发送 (5s)...")
                        time.sleep(5)  # Wait for email delivery
                    else:
                        if logger:
                            logger(f"Unified Captcha: ⚠️ 重发按钮不可见: {resend_xp}，可能还在冷却中")
                        # Still wait a bit before next check
                        time.sleep(3)
                except Exception as e:
                    if logger:
                        logger(f"Unified Captcha: ⚠️ 重发操作异常: {e}")
                    
    finally:
        if extractor:
            try:
                if hasattr(extractor, 'close'):
                    extractor.close()
                elif hasattr(extractor, 'logout'):
                    extractor.logout()
            except Exception:
                pass

    if logger:
        logger("Unified Captcha: ❌ 达到最大重试次数，接码失败")
    return None


# =============================================================================
# REGISTRATION STEP FUNCTIONS
# =============================================================================

def step_verify(
    ctx: Dict[str, Any],
    row: Dict[str, Any],
    xpaths: Dict[str, str],
    client: BitBrowserClient,
    platform_url: str,
    timeout_ms: int,
    poll_ms: int,
    logger: Optional[Callable[[str], None]],
    stop_event: Optional[threading.Event],
    target_check: Optional[Callable[[], bool]],
    email_pool: Optional[Any],
    headless_mode: bool,
    udp_enabled: bool
) -> None:
    """
    Step 1: Verification and initialization.
    
    Validates email, creates/opens browser, and navigates to platform.
    """
    email = str(row.get('email') or row.get('账号') or '').strip()
    password = str(row.get('password') or row.get('密码') or '').strip()
    host = str(row.get('host') or row.get('代理IP') or '').strip()
    port = str(row.get('port') or row.get('端口') or '').strip()
    proxy_username = str(row.get('proxyUserName') or row.get('用户名') or '').strip()
    proxy_password = str(row.get('proxyPassword') or row.get('密码2') or '').strip()
    protocol = str(row.get('protocol') or row.get('proxyType') or 'socks5').strip()
    window_name = str(row.get('windowName') or row.get('窗口名称') or email or 'win').strip()
    
    # Email Validation Logic
    if email_pool:
        is_valid, reason = email_pool.check_email_availability(email)
        if not is_valid:
            # Allow 'processing' as it is set by the current task runner immediately before execution
            if reason == "正在处理中":
                pass
            else:
                log_time = time.strftime('%Y-%m-%d %H:%M:%S')
                audit_msg = f"Audit: Invalid Email Rejected | Time: {log_time} | IP: {host} | Email: {email} | Reason: {reason}"
                if logger:
                    logger(audit_msg)
                raise RuntimeError(f"{ERROR_EMAIL_UNAVAILABLE}: {reason}")
    
    # Construct proxy_config
    proxy_config = None
    if host and port:
        try:
            ptype = socks.SOCKS5
            if protocol.lower() == 'http':
                ptype = socks.HTTP
            elif protocol.lower() == 'socks4':
                ptype = socks.SOCKS4
            
            proxy_config = {
                'proxy_type': ptype,
                'addr': host,
                'port': int(port),
                'username': proxy_username if proxy_username else None,
                'password': proxy_password if proxy_password else None,
                'rdns': True
            }
        except (ValueError, TypeError) as e:
            if logger:
                logger(f"代理配置异常: {e}")

    # Early target check
    if target_check and target_check():
        raise RuntimeError(ERROR_TARGET_REACHED)
        
    if stop_event and stop_event.is_set():
        raise RuntimeError(ERROR_STOPPED)

    if logger:
        logger(f"create_profile {window_name}")
    
    # Optimization: Disable QUIC
    browser_cmd_args = ["--disable-quic"]
    
    browser_id = None
    try:
        browser_id = client.update_browser(
            window_name, 
            proxy_payload(host, port, proxy_username, proxy_password, protocol), 
            enable_udp=udp_enabled, 
            cmd_args=browser_cmd_args
        )
    except (requests.exceptions.RequestException, RuntimeError):
        try:
            browser_id = client.create_browser(
                window_name, 
                proxy_payload(host, port, proxy_username, proxy_password, protocol), 
                enable_udp=udp_enabled, 
                cmd_args=browser_cmd_args
            )
        except Exception as create_err:
            if logger:
                logger(f"创建窗口失败: {create_err}")
            raise RuntimeError(f"create_browser failed: {create_err}")
    
    ctx['browser_id'] = browser_id
    if logger:
        logger(f"profile_id {browser_id}")
    
    open_data = client.open_browser(browser_id)
    ctx['open_data'] = open_data
    
    # Try to get PID for cleanup
    browser_pid = open_data.get('pid')
    if not browser_pid and open_data.get('http'):
        try:
            addr = open_data.get('http')
            if ':' in addr:
                port = int(addr.split(':')[-1])
                browser_pid = get_pid_by_port(port)
        except Exception:
            pass
    ctx['browser_pid'] = browser_pid
    
    if logger:
        logger(f"open_browser {browser_id} -> {open_data} (PID={browser_pid})")
    
    if not (open_data.get('driver') and open_data.get('http')):
        raise RuntimeError(f"open_browser 未返回 driver/http: {open_data}")
    
    # Wait for browser to fully start
    time.sleep(BROWSER_START_WAIT_SEC)
    
    driver = None
    try:
        driver = open_attached_driver(open_data)
    except Exception as attach_err:
        if logger:
            logger(f"连接浏览器失败，尝试重试: {attach_err}")
        time.sleep(3)
        driver = open_attached_driver(open_data)
        
    ctx['driver'] = driver
    ctx['t_attached'] = time.time()
    ctx['gate_state'] = 'attached'
    
    # Headless Mode
    if headless_mode:
        try:
            driver.set_window_position(-3000, 0)
        except Exception:
            try:
                driver.minimize_window()
            except Exception:
                pass

    if logger:
        logger("步骤: 打开平台网址")
    if not check_connectivity(driver, platform_url, logger, max_wait=45, udp_enabled=udp_enabled):
        take_screenshot(driver, f"{window_name}_connectivity_failed.png", logger)
        raise RuntimeError(ERROR_PROXY_CONNECTIVITY_FAILED)
        
    # Page Ready
    wait_page_ready(driver, 45)
    log_page_timing(driver, logger)
    log_resource_status(driver, logger)
    
    # DOM Check
    try:
        if not element_visible(driver, xpaths.get('language_menu', ''), min(timeout_ms, 30000), poll_ms):
            if logger:
                logger("DOM状态校验失败: language_menu 未可见 (30s)，尝试刷新后重试")
            try:
                driver.refresh()
            except Exception:
                pass
            wait_page_ready(driver, 45)
            log_page_timing(driver, logger)
            log_resource_status(driver, logger)
            if not element_visible(driver, xpaths.get('language_menu', ''), min(timeout_ms, 30000), poll_ms):
                if logger:
                    logger("DOM状态校验仍失败: language_menu 未可见，继续后续入口以免阻塞")
    except Exception:
        pass


def step_write(
    ctx: Dict[str, Any],
    row: Dict[str, Any],
    xpaths: Dict[str, str],
    platform_url: str,
    timeout_ms: int,
    poll_ms: int,
    logger: Optional[Callable[[str], None]],
    stop_event: Optional[threading.Event]
) -> None:
    """
    Step 2: Write and interact with registration form.
    
    Fills in email, password, and handles slider captcha.
    """
    driver = ctx['driver']
    open_data = ctx['open_data']
    email = str(row.get('email') or row.get('账号') or '').strip()
    password = str(row.get('password') or row.get('密码') or '').strip()
    
    # Check Ready
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
        wait_page_ready(driver, 20)
    
    # Debugger Tab
    enable_debugger_open = bool(ctx.get('udp_enabled', False))
    if enable_debugger_open and (not ready) and open_data.get('http'):
        if not ctx.get('debug_tab_opened', False):
            prev_handles = driver.window_handles
            open_tab_via_debugger(open_data.get('http') or '', platform_url, logger)
            try:
                WebDriverWait(driver, 8).until(lambda d: len(d.window_handles) > len(prev_handles))
                driver.switch_to.window(driver.window_handles[-1])
            except Exception:
                pass
            ctx['debug_tab_opened'] = True
    
    # Language Menu
    lang_clicked = False
    if safe_click_any(driver, [xpaths.get('language_menu'), xpaths.get('language_menu_alt')], timeout_ms, poll_ms, logger, retries=2) or \
       js_click_xpath(driver, xpaths.get('language_menu', '')) or \
       js_click_xpath(driver, xpaths.get('language_menu_alt', '')):
        if logger:
            logger("步骤: 打开语言菜单")
        lang_clicked = True
        try:
            WebDriverWait(driver, min(timeout_ms, 8000) / 1000.0).until(
                EC.presence_of_element_located((By.XPATH, xpaths.get('english_option') or xpaths.get('english_option_alt') or "//*[@role='menu']"))
            )
        except Exception:
            pass
        if not safe_click_any(driver, [xpaths.get('english_option'), xpaths.get('english_option_alt')], timeout_ms, poll_ms, logger, retries=2):
            if not js_click_xpath(driver, xpaths.get('english_option', '')) and not js_click_xpath(driver, xpaths.get('english_option_alt', '')):
                raise RuntimeError(ERROR_ENGLISH_OPTION_CLICK_FAILED)
    
    if logger:
        logger("步骤: 选择英文" + ("(已点击语言菜单)" if lang_clicked else "(跳过语言菜单点击)"))
    
    if stop_event and stop_event.is_set():
        raise RuntimeError(ERROR_STOPPED)
    
    # Navigate to Sign Up
    if logger:
        logger("步骤: 点击 Creative Studio")
    if not safe_click_any(driver, [xpaths.get('Creative Studio'), "//*[contains(text(),'Creative') or contains(text(),'创意') or contains(text(),'工作室')]"], timeout_ms, poll_ms, logger, retries=2):
        try:
            find_click(driver, xpaths.get('Creative Studio') or "//*[contains(text(),'Creative') or contains(text(),'创意') or contains(text(),'工作室')]", timeout_ms, poll_ms)
        except Exception as e:
            raise RuntimeError(str(e))
            
    if logger:
        logger("步骤: 点击 More Tools")
    prev_handles = driver.window_handles
    ctx['gate_state'] = 'pre_open_more_tools'
    if not safe_click_any(driver, [xpaths.get('More Tools'), "//*[contains(text(),'More') or contains(text(),'更多') or contains(text(),'工具')]"], timeout_ms, poll_ms, logger, retries=2):
        raise RuntimeError(ERROR_MORE_TOOLS_CLICK_FAILED)
        
    try:
        WebDriverWait(driver, min(8, timeout_ms / 1000.0), poll_frequency=poll_ms / 1000.0).until(
            lambda d: len(d.window_handles) > len(prev_handles)
        )
    except Exception:
        pass
    
    time.sleep(1.5)
    handle_lock = threading.Lock()
    with handle_lock:
        try:
            new_handles = driver.window_handles
            diff = [h for h in new_handles if h not in prev_handles]
            if diff:
                driver.switch_to.window(diff[-1])
            else:
                driver.switch_to.window(driver.window_handles[-1])
            ctx['gate_state'] = 'tab_switched'
        except Exception:
            pass
        
    if logger:
        logger("步骤: 已切换到新标签")
    wait_page_ready(driver, 20)
    
    time.sleep(0.5)
    if logger:
        logger("步骤: 点击 Sign In")
    if not safe_click_any(driver, [xpaths.get('signin_btn'), "//*[contains(text(),'Sign In') or contains(text(),'登录')]"], timeout_ms, poll_ms, logger, retries=2):
        raise RuntimeError(ERROR_SIGNIN_CLICK_FAILED)
        
    try:
        WebDriverWait(driver, min(timeout_ms, 10000) / 1000.0).until(
            EC.presence_of_element_located((By.XPATH, xpaths.get('signin_with_email') or xpaths.get('signin_with_email_alt') or "//*"))
        )
    except Exception:
        pass
    
    if logger:
        logger("步骤: 选择邮箱登录")
    if not safe_click_any(driver, [xpaths.get('signin_with_email'), "//*[contains(text(),'邮箱') or contains(text(),'email') or contains(text(),'邮件')]"], timeout_ms, poll_ms, logger, retries=2):
        raise RuntimeError(ERROR_SIGNIN_EMAIL_CLICK_FAILED)
        
    try:
        WebDriverWait(driver, min(timeout_ms, 10000) / 1000.0).until(
            EC.presence_of_element_located((By.XPATH, xpaths.get('Sign up for free') or "//*[contains(text(),'免费') or contains(text(),'注册') or contains(text(),'Sign up')]"))
        )
    except Exception:
        pass
    
    if logger:
        logger("步骤: 点击免费注册")
    if not safe_click_any(driver, [xpaths.get('Sign up for free'), "//*[contains(text(),'免费') or contains(text(),'注册') or contains(text(),'Sign up')]"], timeout_ms, poll_ms, logger, retries=2):
        raise RuntimeError(ERROR_SIGNUP_CLICK_FAILED)
        
    try:
        WebDriverWait(driver, min(timeout_ms, 12000) / 1000.0).until(
            EC.presence_of_element_located((By.XPATH, xpaths.get('Enter Email Address') or "//*[@placeholder][contains(@placeholder,'邮箱') or contains(@placeholder,'Email')]"))
        )
    except Exception:
        pass
    
    # Form Filling
    if logger:
        logger("步骤: 输入邮箱")
    if not safe_send_keys_any(driver, [xpaths.get('Enter Email Address'), "//*[@placeholder][contains(@placeholder,'邮箱') or contains(@placeholder,'Email')]"], email, timeout_ms, poll_ms, logger, retries=1):
        raise RuntimeError(ERROR_EMAIL_INPUT_FAILED)
        
    if logger:
        logger("步骤: 输入密码")
    if not safe_send_keys_any(driver, [xpaths.get('password_input'), "//*[@placeholder][contains(@placeholder,'密码') or contains(@placeholder,'Password')]"], password, timeout_ms, poll_ms, logger, retries=1):
        raise RuntimeError(ERROR_PASSWORD_INPUT_FAILED)
        
    if logger:
        logger("步骤: 确认密码")
    if not safe_send_keys_any(driver, [xpaths.get('Confirm Password'), "//*[@placeholder][contains(@placeholder,'确认') or contains(@placeholder,'Confirm')]"], password, timeout_ms, poll_ms, logger, retries=1):
        raise RuntimeError(ERROR_CONFIRM_INPUT_FAILED)
        
    if logger:
        logger("步骤: 点击下一步")
    if not safe_click_any(driver, [xpaths.get('next_btn'), "//*[contains(text(),'下一步') or contains(text(),'Next')]"], timeout_ms, poll_ms, logger, retries=2):
        raise RuntimeError(ERROR_NEXT_CLICK_FAILED)
        
    ctx['gate_state'] = 'registration_started'
    
    # Check Used Email
    try:
        if element_visible(driver, "//*[contains(text(), 'registered') or contains(text(), 'used')]", 2000, poll_ms):
            body_text = driver.find_element(By.TAG_NAME, 'body').text.lower()
            if "already registered" in body_text or "already used" in body_text or "account exists" in body_text:
                if logger:
                    logger("检测到邮箱已被使用提示")
                if email_pool := ctx.get('email_pool'):
                    email_pool.update_email_status(email, 'fail_used')
                raise RuntimeError(ERROR_EMAIL_USED_PROMPT)
    except RuntimeError as e:
        if str(e) == ERROR_EMAIL_USED_PROMPT:
            raise
    except Exception:
        pass

    # Slider
    if logger:
        logger("步骤: 等待并通过滑块")
    if stop_event and stop_event.is_set():
        raise RuntimeError(ERROR_STOPPED)
    
    code_input_el_xpath = xpaths.get('code_url_element')
    if not code_input_el_xpath:
        raise RuntimeError('code_input_xpath_missing')
    
    t_slider = time.time()
    slider_ok = False
    slider_iframe_xpath = xpaths.get('slider_iframe')
    slider_container_xpath = xpaths.get('slider_container')
    
    for attempt in range(MAX_SLIDER_RETRIES):
        if stop_event and stop_event.is_set():
            raise RuntimeError(ERROR_STOPPED)
        if attempt > 0 and logger:
            logger(f"Slider: 重试 {attempt+1}/{MAX_SLIDER_RETRIES}")
        
        if solve_slider(driver, xpaths, timeout_ms, poll_ms, logger=logger):
            slider_ok = True
            break
            
        if attempt < MAX_SLIDER_RETRIES - 1:
            try:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
                t_wait = time.time()
                while time.time() - t_wait < 6:
                    if stop_event and stop_event.is_set():
                        raise RuntimeError(ERROR_STOPPED)
                    if element_visible(driver, code_input_el_xpath, 400, poll_ms):
                        break
                    if (slider_iframe_xpath and element_visible(driver, slider_iframe_xpath, 400, poll_ms)) or \
                       (slider_container_xpath and element_visible(driver, slider_container_xpath, 400, poll_ms)):
                        break
                    time.sleep(0.3)
            except Exception:
                pass
            
    if not slider_ok:
        raise RuntimeError(ERROR_SLIDER_FAILED)
    if logger:
        logger(f"步骤: 滑块通过 (耗时 {time.time()-t_slider:.2f}s)")
    
    if logger:
        logger("步骤: 滑块通过，强制等待 5 秒...")
    time.sleep(1)
    if logger:
        logger("步骤: 滑块通过，立即跳转接码页等待验证码")


def step_confirm(
    ctx: Dict[str, Any],
    row: Dict[str, Any],
    xpaths: Dict[str, str],
    timeout_ms: int,
    poll_ms: int,
    logger: Optional[Callable[[str], None]],
    stop_event: Optional[threading.Event],
    target_check: Optional[Callable[[], bool]],
    email_pool: Optional[Any]
) -> None:
    """
    Step 3: Confirm and submit registration.
    
    Extracts verification code and submits the form.
    """
    driver = ctx['driver']
    
    if stop_event and stop_event.is_set():
        raise RuntimeError(ERROR_STOPPED)
    if target_check and target_check():
        if logger:
            logger("终止接码：已达到目标注册数量")
        raise RuntimeError(ERROR_TARGET_REACHED)
        
    default_resend_xp = RESEND_CODE_XPATH_PATTERNS[0]
    resend_xp = xpaths.get('resend_code') or default_resend_xp
    
    # Prepare Credentials
    auth_code_val = row.get('auth_code') or row.get('授权码') or row.get('authCode') or row.get('code_url')
    pwd_val = row.get('password') or row.get('密码')
    password_for_imap = str(auth_code_val or pwd_val or '').strip()
    
    if not password_for_imap:
         if logger:
             logger("❌ 错误: 缺少密码/授权码，无法获取验证码。")
         raise RuntimeError(ERROR_MISSING_CREDENTIALS)

    # Determine Mode
    auth_code_or_url = str(row.get('auth_code') or row.get('授权码') or '').strip()
    extraction_mode = 'imap'
    extraction_url = None
    if auth_code_or_url.startswith('http'):
        extraction_mode = 'http'
        extraction_url = auth_code_or_url
        
    # Proxy for IMAP
    proxy_config = None
    host = str(row.get('host') or row.get('代理IP') or '').strip()
    port = str(row.get('port') or row.get('端口') or '').strip()
    protocol = str(row.get('protocol') or row.get('proxyType') or 'socks5').strip()
    proxy_username = str(row.get('proxyUserName') or row.get('用户名') or '').strip()
    proxy_password = str(row.get('proxyPassword') or row.get('密码2') or '').strip()
    
    if host and port:
         try:
            ptype = socks.SOCKS5
            if protocol.lower() == 'http':
                ptype = socks.HTTP
            elif protocol.lower() == 'socks4':
                ptype = socks.SOCKS4
            proxy_config = {
                'proxy_type': ptype, 
                'addr': host, 
                'port': int(port), 
                'username': proxy_username, 
                'password': proxy_password, 
                'rdns': True
            }
         except (ValueError, TypeError):
             pass

    if logger:
        logger("使用 Unified Captcha 模式获取验证码...")
    code = extract_verification_code_unified(
        driver, 
        str(row.get('email') or row.get('账号') or ''), 
        password_for_imap, 
        resend_xp, 
        logger, 
        timeout=CODE_EXTRACTION_TIMEOUT_SEC, 
        proxy_config=proxy_config, 
        stop_event=stop_event,
        extraction_mode=extraction_mode, 
        extraction_url=extraction_url
    )
    
    if not code:
        if logger:
            logger("步骤: 获取验证码失败(超时)")
        # Diagnosis
        slider_iframe_xpath = xpaths.get('slider_iframe')
        slider_container_xpath = xpaths.get('slider_container')
        try:
            if (slider_iframe_xpath and element_visible(driver, slider_iframe_xpath, 1000, poll_ms)) or \
               (slider_container_xpath and element_visible(driver, slider_container_xpath, 1000, poll_ms)):
                if logger:
                    logger("诊断: 滑块验证框重新出现，判定为滑块实际上未通过")
                raise RuntimeError(ERROR_SLIDER_REAPPEARED)
        except RuntimeError as e:
             if str(e) == ERROR_SLIDER_REAPPEARED:
                 raise
        except Exception:
            pass
        raise RuntimeError(ERROR_CODE_NOT_FOUND)

    ctx['gate_state'] = 'code_received'
    if logger:
        logger("步骤: 获取验证码成功，已切回主窗口")
    
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    
    # Fill Code
    try:
        t0 = time.time()
        if logger:
            logger("正在等待验证码输入框...")
        code_input_el_xpath = xpaths.get('code_url_element')
        input_locators = [
            code_input_el_xpath,
            "//input[@autocomplete='one-time-code']",
            "//input[contains(@placeholder, 'verification') or contains(@placeholder, 'code') or contains(@placeholder, '验证码')]",
            "//input[@type='text' and string-length(@maxlength)='6']"
        ]
        input_locators = list(dict.fromkeys([x for x in input_locators if x]))
        
        found_input_xpath = None
        for xp in input_locators:
             try:
                 if element_visible(driver, xp, 1000, poll_ms):
                     found_input_xpath = xp
                     break
             except Exception:
                 pass
        
        if not found_input_xpath:
            end_find = time.time() + 10
            while time.time() < end_find:
                for xp in input_locators:
                    try:
                        if element_visible(driver, xp, 500, poll_ms):
                            found_input_xpath = xp
                            break
                    except Exception:
                        pass
                if found_input_xpath:
                    break
                time.sleep(1)

        if not found_input_xpath:
            if logger:
                logger("验证码输入框未出现(超时)")
            raise RuntimeError(ERROR_CODE_INPUT_NOT_VISIBLE)

        if logger:
            logger(f"验证码输入框就绪: {found_input_xpath} (耗时 {time.time()-t0:.2f}s)")
        el = driver.find_element(By.XPATH, found_input_xpath)
        el.clear()
        el.send_keys(code)
    except Exception:
        if logger:
            logger("验证码填写异常")
        raise RuntimeError(ERROR_CODE_INPUT_ERROR)

    if logger:
        logger("步骤: 填写验证码")
    find_click_any(driver, xpaths['final_submit_btn'], timeout_ms, poll_ms)
    
    if logger:
        logger("提交后强制等待 1 秒...")
    time.sleep(1)

    # 注册跳转流程重定义 (User Request 2)
    if logger:
        logger("步骤: 提交注册")
        logger("步骤: 等待URL跳转 (成功标志)...")
    
    jump_success = False
    try:
        def check_url_jump(d):
            u = d.current_url.lower()
            if any(k in u for k in SUCCESS_URL_KEYWORDS):
                return True
            return False
        WebDriverWait(driver, 15).until(check_url_jump)
        jump_success = True
        if logger:
            logger(f"检测到URL跳转成功: {driver.current_url}")
    except Exception as e:
        if logger:
            logger(f"等待URL跳转超时或失败: {e}")
        try:
             body_text = driver.find_element(By.TAG_NAME, 'body').text
             if "You have signed in" in body_text or "dashboard" in body_text.lower():
                 if logger:
                     logger("通过页面内容检测到注册成功")
                 jump_success = True
        except Exception:
            pass

    if not jump_success:
        raise RuntimeError(ERROR_URL_JUMP_FAILED)

    if logger:
        logger("步骤: 跳转成功，强制等待 3 秒...")
    time.sleep(POST_SUBMIT_WAIT_SEC)

    if email_pool:
        try:
            email_pool.update_email_status(str(row.get('email') or row.get('账号') or ''), 'submitted')
            if logger:
                logger(f"邮箱 {row.get('email') or row.get('账号')} 已标记为 submitted (注册成功)")
        except Exception as e:
            if logger:
                logger(f"标记邮箱状态失败: {e}")


# =============================================================================
# MAIN REGISTRATION FUNCTION
# =============================================================================

def perform_registration(
    row: Dict[str, Any],
    xpaths: Dict[str, str],
    platform_url: str,
    timeout_ms: int,
    poll_ms: int,
    client: BitBrowserClient,
    logger: Optional[Callable[[str], None]] = None,
    stop_event: Optional[threading.Event] = None,
    target_check: Optional[Callable[[], bool]] = None,
    headless_mode: bool = False,
    udp_enabled: bool = False,
    email_pool: Optional[Any] = None,
    keep_open_on_failure_ms: int = 0,
    allow_hold_on_early_failure: bool = False,
    events: Optional[RegistrationEvents] = None,
) -> Tuple[bool, str]:
    """
    Perform a single registration task using BitBrowser.
    
    Args:
        row: Dictionary containing email, password, proxy settings, etc.
        xpaths: Dictionary of XPath locators for UI elements
        platform_url: URL of the registration platform
        timeout_ms: Default timeout in milliseconds for element operations
        poll_ms: Polling interval in milliseconds
        client: BitBrowserClient instance
        logger: Optional logging function
        stop_event: Event to signal task cancellation
        target_check: Callable to check if target count is reached
        headless_mode: Whether to run browser in headless mode
        udp_enabled: Whether to enable UDP
        email_pool: Optional EmailPool for email status management
        keep_open_on_failure_ms: Time to keep browser open on failure
        allow_hold_on_early_failure: Whether to hold browser on early failure
        events: RegistrationEvents for callbacks
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    _logger_orig = logger
    def _log(msg: str, **kwargs):
        # Support level kwarg but ignore it for now or format it
        level = kwargs.get('level', '').upper()
        full_msg = f"[{level}] {msg}" if level else msg
        
        if _logger_orig:
            # Check if original logger supports kwargs, otherwise just send msg
            try:
                _logger_orig(full_msg)
            except TypeError:
                _logger_orig(full_msg)  # Fallback
                
        if events and events.on_log:
            events.on_log(full_msg)
    logger = _log
    
    log_memory_usage(logger)

    email = str(row.get('email') or row.get('账号') or '').strip()

    # Windows Compatibility: Dump Manager & Step Runner
    with WindowsDumpManager(logger, email) as dump_mgr:
        runner = StepRunner(logger, stop_event, max_cpu=DEFAULT_MAX_CPU_PERCENT, max_mem=DEFAULT_MAX_MEM_MB)
        
        # Shared Context for Steps
        ctx: Dict[str, Any] = {
            'driver': None,
            'browser_id': None,
            'gate_state': None,
            't_attached': 0,
            'open_data': None,
            'debug_tab_opened': False,
            'email_pool': email_pool,
            'udp_enabled': udp_enabled
        }
        
        # Resolve dynamic settings if they are callables
        current_timeout = timeout_ms() if callable(timeout_ms) else timeout_ms
        current_poll = poll_ms() if callable(poll_ms) else poll_ms
        
        if logger:
            logger(f"当前任务参数: Timeout={current_timeout}ms, Poll={current_poll}ms")
        
        # -----------------------------------------------------------------
        # Execution
        # -----------------------------------------------------------------
        result_ok = False
        try:
            runner.run("1. 验证与初始化", lambda: step_verify(
                ctx, row, xpaths, client, platform_url, current_timeout, current_poll,
                logger, stop_event, target_check, email_pool, headless_mode, udp_enabled
            ))
            runner.run("2. 填写表单与滑块", lambda: step_write(
                ctx, row, xpaths, platform_url, current_timeout, current_poll,
                logger, stop_event
            ))
            runner.run("3. 确认提交与验证", lambda: step_confirm(
                ctx, row, xpaths, current_timeout, current_poll,
                logger, stop_event, target_check, email_pool
            ))
            result_ok = True
            return True, 'success'
        except Exception as e:
            result_ok = False
            fail_reason = str(e)
            if logger:
                logger(f"任务执行失败: {fail_reason}")
            return False, fail_reason
            
        finally:
            driver = ctx.get('driver')
            browser_id = ctx.get('browser_id')
            gate_state = ctx.get('gate_state')
            t_attached = ctx.get('t_attached')
            
            # Rollback
            if not result_ok and email_pool:
                try:
                    email_pool.update_email_status(email, 'failed')
                    if logger:
                        logger(f"邮箱 {email} 已标记为 failed (注册未完成)")
                except Exception:
                    pass

            if logger:
                logger(f"正在清理资源: {browser_id}")
            
            elapsed = time.time() - t_attached if t_attached else 0
            early_failure = (not result_ok) and (gate_state in ('attached', 'pre_open_more_tools', 'tab_switched'))
            is_stopped = stop_event and stop_event.is_set()
            hold_close = (not result_ok) and (not is_stopped) and \
                         ((elapsed < (keep_open_on_failure_ms / 1000.0)) or \
                          (allow_hold_on_early_failure and early_failure))
            
            if hold_close and logger:
                if allow_hold_on_early_failure and early_failure:
                    logger("因早期失败，保持窗口并等待后续重试")
                else:
                    logger(f"因失败且未达到最小保持时间({keep_open_on_failure_ms}ms)，暂不关闭窗口")
            
            try:
                if driver and not hold_close:
                    try:
                        driver.execute_script("try{document.activeElement && document.activeElement.blur();}catch(e){}")
                    except Exception:
                        pass
                    try:
                        driver.get("about:blank")
                    except Exception:
                        pass
                    
                    def _quit_driver():
                        try:
                            driver.quit()
                        except Exception:
                            pass
                    t_quit = threading.Thread(target=_quit_driver)
                    t_quit.start()
                    t_quit.join(timeout=3.0)
            except Exception as e:
                if logger:
                    logger(f"driver.quit error: {e}")

            try:
                if browser_id and not hold_close:
                    try:
                        client.close_browser(browser_id)
                    except Exception:
                        pass
                    
                    # Force kill if PID known and process still exists
                    pid = ctx.get('browser_pid')
                    if pid:
                        # Give it a moment to close gracefully
                        time.sleep(1.0)
                        if psutil.pid_exists(pid):
                            if logger:
                                logger(f"检测到浏览器进程 PID={pid} 仍存在，执行强制清理")
                            kill_process_tree(pid, logger)
            except Exception:
                pass

            if browser_id and not result_ok:
                if logger:
                    logger(f"任务失败，正在删除窗口: {browser_id}")
                for _ in range(MAX_BROWSER_DELETE_RETRIES):
                    try:
                        client.delete_browser(browser_id)
                        if logger:
                            logger(f"已删除失败窗口: {browser_id}")
                        break
                    except Exception:
                        time.sleep(2.0)


# =============================================================================
# BATCH PROCESSING
# =============================================================================

def run_batch(
    input_path: str,
    xpaths_path: str,
    platform_url: str,
    base_url: str,
    secret: Optional[str],
    concurrency: int,
    timeout_ms: Any,  # int or Callable
    poll_ms: Any,  # int or Callable
    logger: Optional[Callable[[str], None]] = None,
    stop_event: Optional[threading.Event] = None,
    progress_cb: Optional[Any] = None,
    ip_manager: Optional[Any] = None,
    exhaustion_cb: Optional[Any] = None,
    target_success_count: int = 99999999,
    email_pool: Optional[Any] = None,
    headless_mode: bool = False,
    udp_enabled: bool = False,
    events: Optional[RegistrationEvents] = None,
) -> None:
    """
    Run batch registration tasks.
    
    Args:
        input_path: Path to input CSV/JSON/XLSX file
        xpaths_path: Path to XPath configuration JSON file
        platform_url: URL of the registration platform
        base_url: BitBrowser API base URL
        secret: BitBrowser API secret
        concurrency: Number of concurrent tasks
        timeout_ms: Timeout in milliseconds (int or callable)
        poll_ms: Polling interval in milliseconds (int or callable)
        logger: Optional logging function
        stop_event: Event to signal cancellation
        progress_cb: Progress callback function
        ip_manager: IP pool manager
        exhaustion_cb: Callback for IP exhaustion
        target_success_count: Target number of successful registrations
        email_pool: Email pool manager
        headless_mode: Run browsers in headless mode
        udp_enabled: Enable UDP
        events: RegistrationEvents for callbacks
    """
    rows = read_rows(input_path)
    if not rows:
        if logger:
            logger("未发现可处理的任务行，直接退出")
        return
    if stop_event is None:
        stop_event = threading.Event()
    with open(xpaths_path, 'r', encoding='utf-8') as f:
        xpaths = json.load(f)
    client = BitBrowserClient(base_url, secret)
    def _ping(url: str) -> bool:
        try:
            # 优化: 使用 list 接口代替 update/create 来进行健康检查，避免产生残留窗口
            payload = {'page': 0, 'pageSize': 1}
            h = client._headers()
            r = requests.post(f"{url.rstrip('/')}/browser/list", headers=h, data=json.dumps(payload), timeout=5)
            if r.status_code == 200:
                try:
                    r.json()
                    return True
                except (json.JSONDecodeError, ValueError):
                    pass
            return False
        except requests.exceptions.RequestException:
            return False
    if logger:
        logger("开始进行比特浏览器接口连通性检查 (health-check)")
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
    global_success_count = 0

    # 资源预检查: 估算当前 IP 池和邮箱池最大可支持的注册数量
    # 说明: 这里只做告警与日志输出，不改变原有业务决策逻辑
    try:
        ip_stats = ip_manager.get_stats() if ip_manager else None
    except Exception:
        ip_stats = None
    try:
        email_total = len(rows)
    except Exception:
        email_total = 0
    if logger:
        if ip_stats:
            logger(f"资源预检查: IP池总数={ip_stats['total_ips']}, 剩余可用名额={ip_stats['remaining_usage_count']}, 邮箱任务数={email_total}, 目标成功数={target_success_count}")
        else:
            logger(f"资源预检查: 无IP池(或读取失败)，邮箱任务数={email_total}, 目标成功数={target_success_count}")
    
    def task(idx: int, r: Dict[str, Any]) -> Tuple[int, bool, str]:
        nonlocal global_success_count
        email = str(r.get('email') or r.get('账号') or '').strip()
        if logger:
            logger(f"任务启动 #{idx+1}: {email}")
        
        if stop_event and stop_event.is_set():
            return idx, False, ERROR_STOPPED
            
        with lock:
            if global_success_count >= target_success_count:
                if logger:
                    logger(f"任务 #{idx+1} 终止: 已达到目标 ({global_success_count}/{target_success_count})")
                return idx, False, ERROR_TARGET_REACHED
        
        email = str(r.get('email') or r.get('账号') or '').strip()
        
        # Pre-Registration Check (Duplicate/Validation)
        if email_pool:
            is_avail, reason = email_pool.check_email_availability(email)
            if not is_avail:
                 if logger:
                     logger(f"跳过 {email}: 邮箱不可用 ({reason})")
                 r['status'] = 'surplus' if 'local_status' in reason else 'bad'
                 return idx, False, f'email_unavailable_{reason}'
        
        # Mark as processing
        if email_pool:
            email_pool.update_email_status(email, 'processing')
        
        # IP Allocation Logic
        if ip_manager:
            while True:
                if stop_event and stop_event.is_set():
                    if email_pool:
                        email_pool.update_email_status(email, 'stopped')
                    return idx, False, ERROR_STOPPED
                with lock:
                    if global_success_count >= target_success_count:
                        if email_pool:
                            email_pool.update_email_status(email, 'skipped')
                        return idx, False, ERROR_TARGET_REACHED
                
                ip_entry, status = ip_manager.allocate_ip(email)
                if status == 'success' and ip_entry:
                    # Inject IP into row
                    r['host'] = ip_entry['host']
                    r['port'] = ip_entry['port']
                    r['proxyUserName'] = ip_entry.get('proxyUserName', '')
                    r['proxyPassword'] = ip_entry.get('proxyPassword', '')
                    r['protocol'] = ip_entry.get('protocol', 'socks5')
                    if logger:
                        logger(f"已分配IP: {r['host']}:{r['port']} ({r['protocol']}) 给 {email}")
                    break
                elif status == 'email_used':
                    if logger:
                        logger(f"跳过 {email}: 此邮箱已在IP池中使用过")
                    if email_pool:
                        email_pool.update_email_status(email, 'skipped_ip_used')
                    return idx, False, 'email_already_used_in_pool'
                elif status == 'ip_busy':
                    if logger:
                        logger(f"IP资源繁忙 (并发限制)，等待重试: {email}")
                    time.sleep(2)
                    continue
                elif status == 'ip_exhausted':
                    if exhaustion_cb:
                        if logger:
                            logger("IP池耗尽，等待用户处理...")
                        action = exhaustion_cb('ip_exhausted', ip_manager.get_stats())
                        if action == 'retry':
                            if logger:
                                logger("用户选择重试分配IP")
                            continue
                        else:
                            if logger:
                                logger("用户取消任务")
                            if email_pool:
                                email_pool.update_email_status(email, 'cancelled')
                            return idx, False, 'ip_exhausted_cancelled'
                    else:
                        if email_pool:
                            email_pool.update_email_status(email, 'failed_ip_exhausted')
                        return idx, False, 'ip_exhausted_no_handler'
                else:
                    if email_pool:
                        email_pool.update_email_status(email, f'failed_ip_error_{status}')
                    return idx, False, f'ip_allocation_error: {status}'

        try:
            attempts = 0
            ok = False
            msg = ''
            while attempts < MAX_REGISTRATION_ATTEMPTS and not ok:
                attempts += 1
                ok, msg = perform_registration(
                    r,
                    xpaths,
                    platform_url,
                    timeout_ms,
                    poll_ms,
                    client,
                    logger,
                    stop_event,
                    target_check=lambda: global_success_count >= target_success_count,
                    headless_mode=headless_mode,
                    udp_enabled=udp_enabled,
                    email_pool=email_pool,
                    keep_open_on_failure_ms=0,
                    allow_hold_on_early_failure=False,
                    events=events,
                )
                if not ok:
                    if events and events.on_failure:
                        events.on_failure(email, msg)
                    if logger:
                        logger(f"重试 {attempts}/{MAX_REGISTRATION_ATTEMPTS}: {email} 失败原因: {msg}")
                    if msg and ('proxy' in msg or 'network' in msg):
                        time.sleep(3)
                        continue
                    else:
                        break
            
            if ip_manager and not ok:
                # Release IP on failure
                 ip_manager.release_ip(r.get('host'), r.get('port'), email)
                 if logger:
                     logger(f"注册失败，已释放IP资源: {email}")
                     
            with lock:
                if ok:
                    if events and events.on_success:
                        events.on_success(email)
                    global_success_count += 1
                    if global_success_count > target_success_count:
                        r['status'] = 'surplus'
                        msg = 'Excess registration (target exceeded)'
                        if logger:
                            logger(f"⚠️ 超量注册检测: {email} (当前成功数: {global_success_count}, 目标: {target_success_count})")
                        if email_pool:
                            email_pool.update_email_status(email, 'surplus')
                    else:
                        r['status'] = 'good'
                        if email_pool:
                            email_pool.update_email_status(email, 'success')

                    if global_success_count >= target_success_count:
                        if not stop_event.is_set():
                            stop_event.set()
                            if logger:
                                logger(f"🛑 终止条件触发: 任务完成目标 ({target_success_count})，已触发停止信号")
                else:
                    r['status'] = 'fail'
                    if email_pool:
                        if msg == 'email_already_registered_local':
                            pass
                        elif msg == 'email_used_prompt':
                            email_pool.update_email_status(email, 'fail_used')
                        else:
                            # User requested to reset to unused on failure
                            email_pool.update_email_status(email, 'new')
        finally:
            # Check if we need to release IP mapping if task failed
            if ip_manager and 'ip_entry' in locals() and ip_entry:
                 # If not successful, we should ensure the specific IP binding is released 
                 # so it doesn't stay 'bound' to this email if the email is going back to 'new' state.
                 # release_ip handles the logic of "unbinding" this email from this IP.
                 if not ok:
                     try:
                         ip_manager.release_ip(ip_entry['host'], ip_entry['port'], email)
                     except Exception:
                         pass
                 
                 # Always release the active count
                 ip_manager.release_active_ip(ip_entry['host'], ip_entry['port'])
                
        r['msg'] = msg
        if events and events.on_finish:
            events.on_finish(email, ok, msg)
        return idx, ok, msg

    rounds = 0
    
    # Outer Loop: Rounds (Passes through the list)
    while True:
        # --- 优先级 1: 目标数量限制 ---
        with lock:
            if global_success_count >= target_success_count:
                if logger:
                    logger(f"🛑 终止条件触发: 已达到目标注册数量 ({target_success_count})")
                break

        # --- 优先级 2: 待处理任务 ---
        pending_idx = [i for i, r in enumerate(rows) if str(r.get('status', '')).strip() != 'good']
        if not pending_idx:
            if logger:
                logger("🛑 终止条件触发: 所有任务已完成 (无待处理项)")
            break
            
        if stop_event and stop_event.is_set():
            if logger:
                logger("🛑 终止条件触发: 收到外部停止信号")
            break
            
        if logger:
            logger(f"🚀 开始第 {rounds + 1} 轮 (持续运行直至达到目标)")
            logger(f"📊 当前状态: 成功 {global_success_count}/{target_success_count}, 待处理 {len(pending_idx)}")
            
        # Progress callback start of round
        if progress_cb and callable(progress_cb):
             total = len(rows)
             succ = sum(1 for rr in rows if str(rr.get('status', '')).strip() == 'good')
             fail = sum(1 for rr in rows if str(rr.get('status', '')).strip() == 'fail')
             progress_cb(total, succ, fail, rounds + 1, 0)

        # Check target count before round processing (Double Check)
        with lock:
            remaining_needed = target_success_count - global_success_count
            if remaining_needed <= 0:
                break

        # Inner Loop: Process Pending Tasks in Batches
        # We process 'pending_idx' in chunks of 'concurrency'
        round_tasks_indices = pending_idx[:]
        chunk_size = concurrency
        
        # Iterate chunks
        for i in range(0, len(round_tasks_indices), chunk_size):
            if stop_event and stop_event.is_set():
                break
            
            # Check Global Target (Pre-batch check)
            with lock:
                if global_success_count >= target_success_count:
                    if logger:
                        logger(f"🛑 批次前检查: 已达到目标 ({global_success_count}/{target_success_count})")
                    break
                remaining_needed = target_success_count - global_success_count
            
            # Determine batch
            batch_indices = round_tasks_indices[i : i + chunk_size]
            # Cap batch size by remaining needed to avoid over-execution
            current_batch_limit = min(len(batch_indices), remaining_needed)
            actual_batch_indices = batch_indices[:current_batch_limit]
            
            if not actual_batch_indices:
                break
            
            # Submit Batch
            futures = []
            if logger:
                logger(f"Process Batch: {len(actual_batch_indices)} tasks")
            with ThreadPoolExecutor(max_workers=len(actual_batch_indices)) as ex:
                for idx in actual_batch_indices:
                    futures.append(ex.submit(task, idx, rows[idx]))
                
                for fut in as_completed(futures):
                    try:
                        idx_res, ok, msg = fut.result()
                        # Log result
                        
                        if progress_cb and callable(progress_cb):
                             total = len(rows)
                             succ = sum(1 for rr in rows if str(rr.get('status', '')).strip() == 'good')
                             fail = sum(1 for rr in rows if str(rr.get('status', '')).strip() == 'fail')
                             progress_cb(total, succ, fail, rounds + 1, 0)

                        with lock:
                             if global_success_count >= target_success_count:
                                 if not stop_event.is_set():
                                     stop_event.set()
                                     if logger:
                                         logger(f"🛑 终止条件触发: 已达到目标注册数量 ({target_success_count})")
                    except Exception:
                        pass
            
            if stop_event.is_set():
                break

        write_rows_csv(input_path, rows)
        rounds += 1
        
        if logger:
            logger(f"🏁 第 {rounds} 轮结束")
        
        # Check if we should continue to next round
        # The loop condition at the top will handle max_rounds and target checks.
        # We just need to break if stop_event is set.
        if stop_event.is_set():
            break
            
        time.sleep(1)  # Brief pause between rounds


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main() -> None:
    """Main entry point for command-line execution."""
    import argparse
    p = argparse.ArgumentParser()
    base_dir = os.path.dirname(__file__)
    p.add_argument('--input', default=os.path.join(base_dir, 'kl-mail.csv'))
    p.add_argument('--xpaths', default=os.path.join(base_dir, 'kling_xpaths.json'))
    p.add_argument('--platform-url', default='https://klingai.com/global')
    p.add_argument('--bitbrowser-url', default='http://127.0.0.1:54345')
    p.add_argument('--bitbrowser-secret', default=os.environ.get('BITBROWSER_SECRET'))
    p.add_argument('--concurrency', type=int, default=1)
    p.add_argument('--timeout-ms', type=int, default=DEFAULT_TIMEOUT_MS)
    p.add_argument('--poll-ms', type=int, default=DEFAULT_POLL_MS)
    args = p.parse_args()
    def stdout_logger(msg: str) -> None:
        print(msg)
    run_batch(
        args.input, 
        args.xpaths, 
        args.platform_url, 
        args.bitbrowser_url, 
        args.bitbrowser_secret, 
        args.concurrency, 
        args.timeout_ms, 
        args.poll_ms, 
        logger=stdout_logger
    )


if __name__ == '__main__':
    main()
