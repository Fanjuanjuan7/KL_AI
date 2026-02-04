import imaplib
import email
from email.header import decode_header
import re
import time
import functools
import socket
import ssl
import socks
import uuid
import traceback
from typing import Optional, List, Tuple, Dict

try:
    from src.logger import setup_logger
except ImportError:
    try:
        from logger import setup_logger
    except ImportError:
        from .logger import setup_logger

logger = setup_logger()

# IMAP Server Mappings
IMAP_SERVERS = {
    '163.com': ('imap.163.com', 993),
    '126.com': ('imap.126.com', 993),
    'qq.com': ('imap.qq.com', 993),
    'gmail.com': ('imap.gmail.com', 993),
    'outlook.com': ('outlook.office365.com', 993),
    'hotmail.com': ('outlook.office365.com', 993),
    'live.com': ('outlook.office365.com', 993),
    'office365.com': ('outlook.office365.com', 993),
}

class ProxyIMAP4_SSL(imaplib.IMAP4_SSL):
    """IMAP4_SSL with SOCKS proxy support. Matches JieMa implementation style."""
    def __init__(self, host, port, proxy_config=None):
        self.proxy_config = proxy_config
        # Store host/port for socket creation
        self._host = host
        self._port = port
        # Call parent init - it will call _create_socket
        super().__init__(host, port)

    def _create_socket(self, timeout):
        if self.proxy_config:
            s = socks.socksocket()
            s.set_proxy(**self.proxy_config)
            s.settimeout(timeout)
            try:
                s.connect((self._host, self._port))
            except Exception as e:
                raise socket.error(f"Proxy connect failed: {e}")
            
            # Use default SSL context (same as standard imaplib.IMAP4_SSL in Python 3.9+)
            context = ssl.create_default_context()
            return context.wrap_socket(s, server_hostname=self._host)
        else:
            # Use parent's implementation for direct connection
            return super()._create_socket(timeout)

def _safe_log(log_func, message, level="info"):
    """Safely call logger, handling both simple and level-aware loggers"""
    if not log_func:
        return
    try:
        log_func(message, level=level)
    except TypeError:
        # Fallback for simple loggers that don't accept level parameter
        log_func(message)

def retry(max_retries=3, delay=2, exceptions=(Exception,)):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    # Determine logger to use
                    l = getattr(args[0], 'custom_logger', None) if args and hasattr(args[0], 'custom_logger') else logger
                    if l:
                        _safe_log(l, f"Attempt {attempt + 1}/{max_retries} failed for {func.__name__}: {e}", level="warning")
                    time.sleep(delay)
            
            l = getattr(args[0], 'custom_logger', None) if args and hasattr(args[0], 'custom_logger') else logger
            if l:
                _safe_log(l, f"All {max_retries} attempts failed for {func.__name__}", level="error")
            raise last_exception
        return wrapper
    return decorator

class MailExtractor:
    # Config-like constants from JieMa
    MAX_RETRIES = 3
    RETRY_DELAY = 2
    CODE_REGEX = r'\b\d{4,6}\b'
    SEARCH_CRITERIA = "ALL"

    def __init__(self, email_account: str, password: str, imap_server: str = None, imap_port: int = 993, proxy_config: Optional[Dict] = None, logger: Optional[callable] = None, trace_id: str = None):
        self.email_account = email_account
        self.password = password
        self.proxy_config = proxy_config
        self.mail = None
        self.is_connected = False
        self.custom_logger = logger or (lambda msg, level="info": None)
        self.trace_id = trace_id or str(uuid.uuid4())
        
        if imap_server:
            self.imap_server = imap_server
            self.imap_port = imap_port
        else:
            domain = email_account.split('@')[-1].lower()
            if domain in IMAP_SERVERS:
                self.imap_server, self.imap_port = IMAP_SERVERS[domain]
            else:
                self.imap_server = f"imap.{domain}"
                self.imap_port = 993
                self._log(f"Unknown domain {domain}, guessing IMAP server: {self.imap_server}", level="warning")

    def _log(self, message: str, level: str = "info"):
        if self.custom_logger:
            prefix = f"[TraceID:{self.trace_id}] [Email:{self.email_account[:2]}***{self.email_account.split('@')[-1]}]"
            full_msg = f"{prefix} {message}"
            try:
                # Try to call with level parameter
                self.custom_logger(full_msg, level=level)
            except TypeError:
                # Fallback: call with just the message (for simple loggers)
                self.custom_logger(full_msg)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @retry(max_retries=MAX_RETRIES, delay=RETRY_DELAY)
    def connect(self):
        """
        Connect to IMAP server and login.
        Supports both direct connection (JieMa style) and proxy (KL_AI legacy).
        """
        self._log(f"Connecting to {self.imap_server}:{self.imap_port}...")
        
        # Close existing connection if any
        if self.mail:
            try:
                self.mail.logout()
            except:
                pass
            self.mail = None
        
        try:
            if self.proxy_config:
                self._log(f"Connecting via Proxy: {self.proxy_config.get('addr')}:{self.proxy_config.get('port')}")
                # Use ProxyIMAP4_SSL - don't use custom SSL context to match JieMa behavior
                self.mail = ProxyIMAP4_SSL(self.imap_server, self.imap_port, proxy_config=self.proxy_config)
            else:
                self._log("Connecting Direct (No Proxy).")
                # Use standard IMAP4_SSL (JieMa style) - no custom SSL context
                self.mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
                
            # Login
            self._log(f"Logging in as {self.email_account}...")
            typ, data = self.mail.login(self.email_account, self.password)
            
            if typ != 'OK':
                raise Exception(f"Login failed: {data}")
            
            self.is_connected = True
            self._log("Login successful.")
            
            # 发送 ID 命令伪装客户端 (JieMa logic)
            self._send_id_command()
            
        except Exception as e:
            self._log(f"Connection/Login failed: {type(e).__name__}: {e}", level="error")
            self.is_connected = False
            self.mail = None
            raise

    def _send_id_command(self):
        """发送 IMAP ID 命令以模拟常用客户端，绕过部分风控"""
        try:
            # 模拟 DreamMail 客户端的 ID 信息
            client_identity = {
                "name": "DreamMail",
                "version": "6.6.0.0",
                "os": "Windows",
                "os-version": "10.0.19045",
                "vendor": "DreamMail",
                "contact": "support@dreammail.org"
            }
            
            # 构造 ID 参数字符串
            parts = []
            for k, v in client_identity.items():
                parts.append(f'"{k}"')
                parts.append(f'"{v}"')
            id_args = f'({" ".join(parts)})'
            
            self._log(f"Sending custom ID command: ID {id_args}")
            
            # 使用 xatom 发送扩展命令
            if hasattr(self.mail, 'xatom'):
                typ, data = self.mail.xatom('ID', id_args)
                self._log(f"Sent IMAP ID result: {typ} {data}")
            else:
                # Fallback if xatom not available (should be in standard imaplib)
                typ, data = self.mail._simple_command('ID', id_args)
                self._log(f"Sent IMAP ID result (via _simple_command): {typ} {data}")
                
        except Exception as e:
            self._log(f"Failed to send IMAP ID command: {type(e).__name__}: {e}", level="warning")

    @retry(max_retries=3, delay=1)
    def select_inbox(self):
        """Select INBOX and ensure it is selected before searching."""
        if not self.is_connected or not self.mail:
            raise Exception("Not connected to server")
            
        self._log("Selecting INBOX...")
        try:
            typ, data = self.mail.select("INBOX")
            
            if typ != 'OK':
                raise Exception(f"Failed to select INBOX: {data}")
            
            msg_count = data[0].decode() if data and data[0] else "0"
            self._log(f"INBOX selected. {msg_count} messages found.")
        except Exception as e:
            # Mark connection as broken
            self.is_connected = False
            self._log(f"Select INBOX failed: {e}", level="error")
            raise

    def get_latest_verification_code(self) -> str:
        """
        Main workflow: Select INBOX -> Search -> Fetch -> Parse -> Extract Code
        Scans the latest 10 emails to find a verification code.
        """
        try:
            # Ensure connection - reconnect if needed
            if not self.is_connected or not self.mail:
                self._log("Not connected, establishing connection...")
                self.connect()
                
            self.select_inbox()
            
            # Search
            self._log(f"Searching for emails with criteria: {self.SEARCH_CRITERIA}")
            typ, messages = self.mail.search(None, self.SEARCH_CRITERIA)
            
            if typ != 'OK':
                raise Exception(f"Search failed: {messages}")
                
            email_ids = messages[0].split()
            if not email_ids:
                self._log("No emails found.")
                return None
            
            # Scan the last 10 emails (reversed)
            scan_count = 10
            recent_ids = email_ids[-scan_count:]
            recent_ids.reverse() # Newest first
            
            self._log(f"Scanning last {len(recent_ids)} emails for verification codes...")
            
            for i, eid in enumerate(recent_ids):
                self._log(f"Checking email {i+1}/{len(recent_ids)} (ID: {eid.decode()})")
                typ, msg_data = self.mail.fetch(eid, "(RFC822)")
                
                if typ != 'OK':
                    self._log(f"Failed to fetch email {eid}", level="warning")
                    continue
                    
                code = self._parse_and_extract(msg_data)
                if code and code != "未匹配到验证码":
                    return code
            
            self._log("No verification code found in recent emails.")
            return None
            
        except Exception as e:
            self._log(f"Error getting verification code: {type(e).__name__}: {e}", level="error")
            # Mark connection as broken so next call will reconnect
            self.is_connected = False
            return None # Return None on error to indicate failure

    def _parse_and_extract(self, msg_data) -> Optional[str]:
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                
                # Decode subject
                subject = self._decode_str(msg["Subject"])
                self._log(f"Parsing email: {subject}", level="debug")
                
                # Get body (text and html)
                text_body, html_body = self._get_email_body(msg)
                
                # Try extract from text first
                code = self._extract_code_from_text(text_body)
                if code:
                    self._log(f"Code extracted from text: {code}")
                    return code
                
                # Try extract from html if text failed
                if html_body:
                    # Remove HTML tags for regex matching
                    clean_html = re.sub(r'<[^>]+>', ' ', html_body)
                    code = self._extract_code_from_text(clean_html)
                    if code:
                        self._log(f"Code extracted from HTML: {code}")
                        return code
                
        return None

    def _get_email_body(self, msg) -> Tuple[str, str]:
        text_content = ""
        html_content = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                
                if "attachment" in content_disposition:
                    continue
                    
                try:
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                        
                    charset = part.get_content_charset() or 'utf-8'
                    decoded = payload.decode(charset, errors='ignore')
                    
                    if content_type == "text/plain":
                        text_content += decoded
                    elif content_type == "text/html":
                        html_content += decoded
                except Exception as e:
                    self._log(f"Failed to decode part {content_type}: {e}", level="warning")
        else:
            # Not multipart
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or 'utf-8'
                    decoded = payload.decode(charset, errors='ignore')
                    if msg.get_content_type() == "text/html":
                        html_content += decoded
                    else:
                        text_content += decoded
            except Exception as e:
                self._log(f"Failed to decode body: {e}", level="warning")
                
        return text_content, html_content

    def _decode_str(self, header_value):
        if not header_value:
            return ""
        decoded_list = decode_header(header_value)
        result = []
        for content, encoding in decoded_list:
            if isinstance(content, bytes):
                if encoding:
                    try:
                        result.append(content.decode(encoding))
                    except LookupError:
                        # Fallback for unknown encodings
                        result.append(content.decode('utf-8', errors='ignore'))
                else:
                    result.append(content.decode('utf-8', errors='ignore'))
            elif isinstance(content, str):
                result.append(content)
        return "".join(result)

    def _extract_code_from_text(self, text: str) -> Optional[str]:
        if not text:
            return None
            
        # 优化正则表达式：优先匹配 "验证码" 附近的数字，或者纯数字
        # Using JieMa's regex: \b\d{4,6}\b
        matches = re.findall(self.CODE_REGEX, text)
        
        if matches:
            # 简单的过滤逻辑，假设验证码通常是独立的
            return matches[0]
        return None

    def close(self):
        if self.mail:
            try:
                # Close only if selected state
                if self.mail.state == 'SELECTED':
                    self.mail.close()
            except Exception as e:
                self._log(f"Error closing mailbox: {e}", level="debug")
                
            try:
                self.mail.logout()
            except Exception as e:
                self._log(f"Error logging out: {e}", level="debug")
            
            self.is_connected = False
            self._log("Connection closed.")
