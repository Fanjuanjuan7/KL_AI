"""
邮件验证码提取模块。

本模块提供从 IMAP 邮箱服务器中提取验证码的功能，支持：
- 多种邮箱服务商（163、126、QQ、Gmail、Outlook 等）
- SOCKS 代理连接
- 自动重试机制
- 邮件正文（文本/HTML）验证码提取

主要类：
    MailExtractor: 邮件验证码提取器，提供连接、搜索、解析和提取验证码的完整流程。
    ProxyIMAP4_SSL: 支持 SOCKS 代理的 IMAP4_SSL 实现。

使用示例：
    >>> extractor = MailExtractor("user@example.com", "password")
    >>> with extractor:
    ...     code = extractor.get_latest_verification_code()
    ...     print(f"验证码: {code}")

    或者使用显式连接管理：
    >>> extractor = MailExtractor("user@example.com", "password")
    >>> extractor.connect()
    >>> try:
    ...     code = extractor.get_latest_verification_code()
    ... finally:
    ...     extractor.close()

依赖：
    - imaplib: Python 标准库 IMAP 客户端
    - PySocks: SOCKS 代理支持
    - email: 邮件解析

作者: KL_AI Team
版本: 1.0.0
"""

from email.utils import parsedate_to_datetime  # 添加这一行用于解析邮件时间
import datetime  # 新增这一行
import email
import functools
import imaplib
import re
import socket
import ssl
import time
import uuid
from email.header import decode_header
from email.message import Message
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import socks

# 尝试导入项目日志模块
try:
    from src.logger import setup_logger
except ImportError:
    try:
        from logger import setup_logger
    except ImportError:
        from .logger import setup_logger

# 初始化模块日志记录器
logger = setup_logger()

# =============================================================================
# 常量配置
# =============================================================================

# IMAP 服务器配置映射（域名 -> (服务器地址, 端口)）
IMAP_SERVERS: Dict[str, Tuple[str, int]] = {
    "163.com": ("imap.163.com", 993),
    "126.com": ("imap.126.com", 993),
    "qq.com": ("imap.qq.com", 993),
    "gmail.com": ("imap.gmail.com", 993),
    "outlook.com": ("outlook.office365.com", 993),
    "hotmail.com": ("outlook.office365.com", 993),
    "live.com": ("outlook.office365.com", 993),
    "office365.com": ("outlook.office365.com", 993),
}

# 默认 IMAP 配置
DEFAULT_IMAP_PORT: int = 993
DEFAULT_IMAP_TIMEOUT: int = 20  # 秒

# 重试配置
MAX_RETRIES: int = 3
RETRY_DELAY: int = 2  # 秒
RETRY_BACKOFF: float = 1.5  # 退避系数

# 搜索配置
DEFAULT_SEARCH_CRITERIA: str = "ALL"
MAX_SCAN_EMAILS: int = 10  # 扫描最新邮件数量


# 验证码提取正则表达式 (严格匹配6位)
CODE_REGEX_PATTERNS: List[str] = [
    r"验证码[\s:：]*([\d]{6})",  
    r"verification code[\s:：]*([\d]{6})",  
    r"code[\s:：]*([\d]{6})",
    r"\b(\d{6})\b",  # 通用6位数字
]
CODE_REGEX_DEFAULT: str = r"\b\d{4,6}\b"
MAX_SCAN_EMAILS: int = 20  # 建议把最大扫描数量从 10 改为 20，防止当天垃圾邮件太多顶掉验证码
# HTML 标签清理正则
HTML_TAG_REGEX: str = r"<[^>]+>"

# 邮件 ID 命令模拟的客户端身份信息
CLIENT_IDENTITY: Dict[str, str] = {
    "name": "DreamMail",
    "version": "6.6.0.0",
    "os": "Windows",
    "os-version": "10.0.19045",
    "vendor": "DreamMail",
    "contact": "support@dreammail.org",
}


# =============================================================================
# 自定义异常
# =============================================================================


class MailExtractorError(Exception):
    """邮件提取器基础异常。"""

    pass


class ConnectionError(MailExtractorError):
    """连接相关异常。"""

    pass


class AuthenticationError(MailExtractorError):
    """认证失败异常。"""

    pass


class IMAPCommandError(MailExtractorError):
    """IMAP 命令执行异常。"""

    pass


class ProxyConnectionError(ConnectionError):
    """代理连接异常。"""

    pass


# =============================================================================
# 工具函数
# =============================================================================


def _safe_log(log_func: Optional[Callable], message: str, level: str = "info") -> None:
    """
    安全地调用日志记录器，处理不同接口的日志记录器。

    Args:
        log_func: 日志记录函数，可为 None
        message: 日志消息
        level: 日志级别 (debug, info, warning, error)
    """
    if not log_func:
        return
    try:
        log_func(message, level=level)
    except TypeError:
        # 对于不接受 level 参数的日志记录器，直接传递消息
        log_func(message)


def retry(
    max_retries: int = MAX_RETRIES,
    delay: int = RETRY_DELAY,
    backoff: float = RETRY_BACKOFF,
    exceptions: Tuple[type, ...] = (Exception,),
    logger_name: str = "retry",
) -> Callable:
    """
    重试装饰器，支持指数退避。

    Args:
        max_retries: 最大重试次数
        delay: 初始延迟（秒）
        backoff: 退避系数，每次重试延迟时间乘以该系数
        exceptions: 需要捕获并重试的异常类型元组
        logger_name: 日志标识名称

    Returns:
        装饰器函数
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Optional[Exception] = None
            current_delay = delay

            # 尝试从实例中获取日志记录器
            instance_logger = None
            if args and hasattr(args[0], "custom_logger"):
                instance_logger = args[0].custom_logger
            log = instance_logger or logger

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if log:
                        _safe_log(
                            log,
                            f"[{logger_name}] Attempt {attempt + 1}/{max_retries} "
                            f"failed for {func.__name__}: {type(e).__name__}: {e}",
                            level="warning",
                        )

                    if attempt < max_retries - 1:
                        time.sleep(current_delay)
                        current_delay *= backoff

            # 所有重试都失败
            if log:
                _safe_log(
                    log,
                    f"[{logger_name}] All {max_retries} attempts failed for {func.__name__}",
                    level="error",
                )

            if last_exception:
                raise last_exception

        return wrapper

    return decorator


# =============================================================================
# ProxyIMAP4_SSL 类
# =============================================================================


class ProxyIMAP4_SSL(imaplib.IMAP4_SSL):
    """
    支持 SOCKS 代理的 IMAP4_SSL 实现。

    继承自标准库 imaplib.IMAP4_SSL，添加 SOCKS 代理支持。
    适用于需要通过代理服务器连接 IMAP 的场景。

    Attributes:
        proxy_config: 代理配置字典，包含 proxy_type, addr, port, username, password
        _host: IMAP 服务器主机名
        _port: IMAP 服务器端口
        _timeout: 连接超时时间（秒）
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_IMAP_PORT,
        proxy_config: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> None:
        """
        初始化 ProxyIMAP4_SSL 实例。

        Args:
            host: IMAP 服务器主机名
            port: IMAP 服务器端口
            proxy_config: 代理配置字典
            timeout: 连接超时时间（秒）

        Raises:
            ProxyConnectionError: 代理连接失败时
        """
        self.proxy_config: Optional[Dict[str, Any]] = proxy_config
        self._host: str = host
        self._port: int = port
        self._timeout: Optional[int] = timeout

        # Python 3.9+ 支持 timeout 参数
        try:
            super().__init__(host, port, timeout=timeout)
        except TypeError:
            super().__init__(host, port)

    def _create_socket(self, timeout: Optional[float]) -> socket.socket:
        """
        创建 Socket 连接，支持代理。

        Args:
            timeout: 超时时间

        Returns:
            SSL 包装后的 socket

        Raises:
            ProxyConnectionError: 代理连接失败
            socket.error: 其他连接错误
        """
        actual_timeout = self._timeout if self._timeout is not None else timeout

        if self.proxy_config:
            # 使用 SOCKS 代理
            sock = socks.socksocket()
            sock.set_proxy(**self.proxy_config)
            sock.settimeout(actual_timeout)

            try:
                sock.connect((self._host, self._port))
            except socket.error as e:
                raise ProxyConnectionError(f"Proxy connect failed: {e}") from e

            context = ssl.create_default_context()
            return context.wrap_socket(sock, server_hostname=self._host)
        else:
            # 直接连接
            return super()._create_socket(actual_timeout)


# =============================================================================
# MailExtractor 类
# =============================================================================


class MailExtractor:
    """
    邮件验证码提取器。

    提供从 IMAP 邮箱中提取验证码的完整功能，包括：
    - 自动识别常见邮箱服务商
    - 支持代理连接
    - 自动重试机制
    - 从文本和 HTML 邮件中提取验证码

    Attributes:
        email_account: 邮箱账号
        password: 邮箱密码/授权码
        imap_server: IMAP 服务器地址
        imap_port: IMAP 服务器端口
        proxy_config: 代理配置（可选）
        timeout: 连接超时时间
        custom_logger: 自定义日志记录器
        trace_id: 追踪 ID，用于日志关联
        is_connected: 连接状态标志

    Example:
        >>> extractor = MailExtractor("user@163.com", "password")
        >>> with extractor:
        ...     code = extractor.get_latest_verification_code()
        ...     if code:
        ...         print(f"找到验证码: {code}")
    """

    # 类级别的默认配置
    MAX_RETRIES: int = MAX_RETRIES
    RETRY_DELAY: int = RETRY_DELAY
    CODE_REGEX: str = CODE_REGEX_DEFAULT
    SEARCH_CRITERIA: str = DEFAULT_SEARCH_CRITERIA

    def __init__(
        self,
        email_account: str,
        password: str,
        imap_server: Optional[str] = None,
        imap_port: int = DEFAULT_IMAP_PORT,
        proxy_config: Optional[Dict[str, Any]] = None,
        logger: Optional[Callable] = None,
        trace_id: Optional[str] = None,
        timeout: int = DEFAULT_IMAP_TIMEOUT,
    ) -> None:
        """
        初始化邮件验证码提取器。

        Args:
            email_account: 邮箱账号（如 user@example.com）
            password: 邮箱密码或授权码
            imap_server: IMAP 服务器地址（可选，自动识别常见服务商）
            imap_port: IMAP 服务器端口（默认 993）
            proxy_config: SOCKS 代理配置字典（可选）
                格式: {"proxy_type": socks.SOCKS5, "addr": "proxy_host",
                      "port": 1080, "username": "user", "password": "pass"}
            logger: 自定义日志记录函数（可选）
            trace_id: 追踪 ID 用于日志关联（可选，自动生成 UUID）
            timeout: 连接超时时间（秒，默认 20）

        Raises:
            ValueError: 邮箱账号格式无效
        """
        if "@" not in email_account:
            raise ValueError(f"Invalid email account format: {email_account}")

        self.email_account: str = email_account
        self.password: str = password
        self.proxy_config: Optional[Dict[str, Any]] = proxy_config
        self.timeout: int = timeout
        self.mail: Optional[imaplib.IMAP4_SSL] = None
        self.is_connected: bool = False
        self.custom_logger: Callable = logger or (lambda msg, level="info": None)
        self.trace_id: str = trace_id or str(uuid.uuid4())

        # 确定 IMAP 服务器配置
        if imap_server:
            self.imap_server: str = imap_server
            self.imap_port: int = imap_port
        else:
            domain = email_account.split("@")[-1].lower()
            if domain in IMAP_SERVERS:
                self.imap_server, self.imap_port = IMAP_SERVERS[domain]
            else:
                self.imap_server = f"imap.{domain}"
                self.imap_port = DEFAULT_IMAP_PORT
                self._log(
                    f"Unknown domain '{domain}', guessing IMAP server: {self.imap_server}",
                    level="warning",
                )

    def _log(self, message: str, level: str = "info") -> None:
        """
        记录带追踪 ID 和邮箱前缀的日志。

        Args:
            message: 日志消息
            level: 日志级别 (debug, info, warning, error)
        """
        if self.custom_logger:
            # 显示完整邮箱
            prefix = f"[TraceID:{self.trace_id}] [Email:{self.email_account}]"

            full_msg = f"{prefix} {message}"
            try:
                self.custom_logger(full_msg, level=level)
            except TypeError:
                self.custom_logger(full_msg)

    def __enter__(self) -> "MailExtractor":
        """上下文管理器入口，自动连接。"""
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[Exception],
        exc_tb: Optional[Any],
    ) -> None:
        """上下文管理器出口，自动关闭连接。"""
        self.close()

    @retry(
        max_retries=MAX_RETRIES,
        delay=RETRY_DELAY,
        exceptions=(socket.error, ssl.SSLError, ConnectionError, TimeoutError),
    )
    def connect(self) -> None:
        """
        连接到 IMAP 服务器并登录。

        支持直接连接和通过 SOCKS 代理连接。
        连接成功后会发送客户端 ID 命令以绕过部分风控。

        Raises:
            ConnectionError: 连接失败
            AuthenticationError: 认证失败
            socket.error: 网络错误
            ssl.SSLError: SSL 错误

        Example:
            >>> extractor = MailExtractor("user@example.com", "password")
            >>> try:
            ...     extractor.connect()
            ...     print("连接成功")
            ... finally:
            ...     extractor.close()
        """
        self._log(f"Connecting to {self.imap_server}:{self.imap_port}...")

        # 关闭已有连接
        if self.mail:
            try:
                self.mail.logout()
            except (imaplib.IMAP4.error, socket.error):
                pass
            self.mail = None

        try:
            # 根据是否使用代理选择连接方式
            if self.proxy_config:
                self._log(
                    f"Connecting via Proxy: {self.proxy_config.get('addr')}:{self.proxy_config.get('port')}"
                )
                self.mail = ProxyIMAP4_SSL(
                    self.imap_server,
                    self.imap_port,
                    proxy_config=self.proxy_config,
                    timeout=self.timeout,
                )
            else:
                self._log("Connecting Direct (No Proxy).")
                try:
                    self.mail = imaplib.IMAP4_SSL(
                        self.imap_server, self.imap_port, timeout=self.timeout
                    )
                except TypeError:
                    self._log(
                        "imaplib.IMAP4_SSL does not support timeout arg, falling back to default.",
                        level="warning",
                    )
                    self.mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)

            # 登录
            self._log(f"Logging in as {self.email_account}...")
            typ, data = self.mail.login(self.email_account, self.password)

            if typ != "OK":
                raise AuthenticationError(f"Login failed: {data}")

            self.is_connected = True
            self._log("Login successful.")

            # 发送 ID 命令伪装客户端，绕过风控
            self._send_id_command()

        except AuthenticationError:
            self.is_connected = False
            self.mail = None
            raise
        except (socket.error, ssl.SSLError, ConnectionError) as e:
            self._log(f"Connection failed: {type(e).__name__}: {e}", level="error")
            self.is_connected = False
            self.mail = None
            raise ConnectionError(
                f"Failed to connect to {self.imap_server}: {e}"
            ) from e

    def _send_id_command(self) -> None:
        """
        发送 IMAP ID 命令模拟常用客户端，绕过部分邮箱服务商的风控。

        模拟 DreamMail 客户端身份信息。

        Raises:
            IMAPCommandError: ID 命令发送失败（仅记录警告，不中断流程）
        """
        try:
            # 构造 ID 参数字符串
            parts = []
            for key, value in CLIENT_IDENTITY.items():
                parts.append(f'"{key}"')
                parts.append(f'"{value}"')
            id_args = f"({' '.join(parts)})"

            self._log(f"Sending custom ID command: ID {id_args}", level="debug")

            # 使用 xatom 发送扩展命令
            if self.mail is None:
                raise IMAPCommandError("Not connected")

            if hasattr(self.mail, "xatom"):
                typ, data = self.mail.xatom("ID", id_args)
                self._log(f"IMAP ID result: {typ}", level="debug")
            else:
                # 备用方案
                typ, data = self.mail._simple_command("ID", id_args)
                self._log(f"IMAP ID result (via _simple_command): {typ}", level="debug")

        except (imaplib.IMAP4.error, AttributeError, ValueError, TypeError) as e:
            # ID 命令失败通常不影响主要功能，仅记录警告
            self._log(
                f"Failed to send IMAP ID command: {type(e).__name__}: {e}",
                level="warning",
            )

    @retry(
        max_retries=MAX_RETRIES,
        delay=RETRY_DELAY,
        exceptions=(IMAPCommandError, imaplib.IMAP4.error),
    )
    def select_inbox(self) -> int:
        """
        选择 INBOX 邮箱。

        Returns:
            INBOX 中的邮件数量

        Raises:
            ConnectionError: 未连接时
            IMAPCommandError: 选择邮箱失败
        """
        if not self.is_connected or self.mail is None:
            raise ConnectionError("Not connected to server")

        self._log("Selecting INBOX...")

        try:
            typ, data = self.mail.select("INBOX")

            if typ != "OK":
                raise IMAPCommandError(f"Failed to select INBOX: {data}")

            msg_count = int(data[0].decode()) if data and data[0] else 0
            self._log(f"INBOX selected. {msg_count} messages found.")
            return msg_count

        except imaplib.IMAP4.error as e:
            self.is_connected = False
            self._log(f"Select INBOX failed: {type(e).__name__}: {e}", level="error")
            raise IMAPCommandError(f"Select INBOX failed: {e}") from e

    def get_latest_verification_code(self) -> Optional[str]:
        """
        获取最新的验证码。

        主工作流程：
        1. 确保连接（如需要则重连）
        2. 选择 INBOX
        3. 搜索邮件
        4. 获取最新 N 封邮件
        5. 解析并提取验证码

        扫描最新 10 封邮件寻找验证码。

        Returns:
            找到的验证码字符串，未找到返回 None

        Example:
            >>> with MailExtractor("user@example.com", "password") as extractor:
            ...     code = extractor.get_latest_verification_code()
            ...     if code:
            ...         print(f"验证码: {code}")
        """
    
        try:
            # 确保连接
            if not self.is_connected or self.mail is None:
                self._log("Not connected, establishing connection...")
                self.connect()

            # 选择收件箱
            self.select_inbox()

            # 兼容性强改法：不再使用复杂的 IMAP SEARCH，直接拉取 ALL 获取最新邮件
            # 让 Python 客户端来处理过滤逻辑，绕过 163 邮箱的 IMAP 搜索 BUG
            self._log("Fetching latest emails to filter locally...")
            typ, messages = self.mail.search(None, "ALL")

            if typ != "OK":
                raise IMAPCommandError(f"Search failed: {messages}")

            email_ids = messages[0].split()
            if not email_ids:
                self._log("No emails found.")
                return None

            # 获取最新的 N 封邮件
            scan_count = min(MAX_SCAN_EMAILS, len(email_ids))
            recent_ids = email_ids[-scan_count:]
            recent_ids.reverse()  # 最新的在前

            self._log(f"Scanning last {len(recent_ids)} emails for KlingAI verification codes...")

            # 遍历邮件提取验证码
            for i, eid in enumerate(recent_ids):
                email_id_str = eid.decode() if isinstance(eid, bytes) else str(eid)
                
                try:
                    typ, msg_data = self.mail.fetch(eid, "(RFC822)")
                except imaplib.IMAP4.error as e:
                    self._log(f"Failed to fetch email {email_id_str}: {e}", level="warning")
                    continue

                if typ != "OK":
                    continue

                code = self._parse_and_extract(msg_data)
                if code:
                    return code

            self._log("No valid KlingAI verification code found in today's recent emails.")
            return None

        except (ConnectionError, AuthenticationError, IMAPCommandError, Exception) as e:
            self._log(f"Error getting verification code: {type(e).__name__}: {e}", level="error")
            self.is_connected = False
            return None

    def _parse_and_extract(self, msg_data: List[Tuple[bytes, bytes]]) -> Optional[str]:
        if not msg_data:
            return None

        for response_part in msg_data:
            if not isinstance(response_part, tuple) or len(response_part) < 2:
                continue

            try:
                msg = email.message_from_bytes(response_part[1])
            except (email.errors.MessageParseError, UnicodeDecodeError) as e:
                continue

            # ================= 核心过滤逻辑 =================
            
            # 1. 检查主题
            subject = self._decode_str(msg.get("Subject", ""))
            if "KlingAI Account Verification" not in subject:
                continue

            # 2. 检查发件人
            sender = self._decode_str(msg.get("From", ""))
            if "klingai@user-support.klingai.com" not in sender:
                continue

            # 3. 检查日期（必须是今天）
            date_str = msg.get("Date", "")
            try:
                # 解析邮件的发送时间，并转换为本地时区
                email_date = parsedate_to_datetime(date_str).astimezone()
                today = datetime.datetime.now().astimezone().date()
                if email_date.date() != today:
                    self._log(f"Skipped: Email matched but date ({email_date.date()}) is not today.", level="debug")
                    continue
            except Exception as e:
                self._log(f"Failed to parse date: {date_str}, skipping date check.", level="warning")

            self._log(f"Matched KlingAI Email: {subject} | Time: {date_str}", level="info")

            # ================= 提取验证码逻辑 =================

            # 获取邮件正文
            text_body, html_body = self._get_email_body(msg)

            # 优先从纯文本提取
            code = self._extract_code_from_text(text_body)
            if code:
                self._log(f"Code extracted from text: {code}")
                return code

            # 尝试从 HTML 提取
            if html_body:
                clean_html = re.sub(HTML_TAG_REGEX, " ", html_body)
                code = self._extract_code_from_text(clean_html)
                if code:
                    self._log(f"Code extracted from HTML: {code}")
                    return code

        return None

    def _get_email_body(self, msg: Message) -> Tuple[str, str]:
        """
        提取邮件的正文内容。

        分别提取纯文本和 HTML 内容。

        Args:
            msg: email.message.Message 对象

        Returns:
            (text_content, html_content) 元组
        """
        text_content: str = ""
        html_content: str = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                # 跳过附件
                if "attachment" in content_disposition:
                    continue

                try:
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue

                    charset = part.get_content_charset() or "utf-8"
                    decoded = payload.decode(charset, errors="ignore")

                    if content_type == "text/plain":
                        text_content += decoded
                    elif content_type == "text/html":
                        html_content += decoded

                except (LookupError, UnicodeDecodeError, TypeError) as e:
                    self._log(
                        f"Failed to decode part {content_type}: {type(e).__name__}: {e}",
                        level="warning",
                    )
        else:
            # 非多部分邮件
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    decoded = payload.decode(charset, errors="ignore")

                    if msg.get_content_type() == "text/html":
                        html_content += decoded
                    else:
                        text_content += decoded

            except (LookupError, UnicodeDecodeError, TypeError) as e:
                self._log(
                    f"Failed to decode body: {type(e).__name__}: {e}", level="warning"
                )

        return text_content, html_content

    def _decode_str(self, header_value: Optional[str]) -> str:
        """
        解码邮件头字段（如主题）。

        处理 MIME 编码的邮件头（如 =?UTF-8?B?...?=）

        Args:
            header_value: 编码的邮件头值

        Returns:
            解码后的字符串
        """
        if not header_value:
            return ""

        try:
            decoded_list = decode_header(header_value)
        except (email.errors.HeaderParseError, TypeError):
            return header_value or ""

        result: List[str] = []
        for content, encoding in decoded_list:
            if isinstance(content, bytes):
                if encoding:
                    try:
                        result.append(content.decode(encoding))
                    except (LookupError, UnicodeDecodeError):
                        result.append(content.decode("utf-8", errors="ignore"))
                else:
                    result.append(content.decode("utf-8", errors="ignore"))
            elif isinstance(content, str):
                result.append(content)

        return "".join(result)

    def _extract_code_from_text(self, text: Optional[str]) -> Optional[str]:
        """
        从文本中提取验证码。

        使用正则表达式匹配 4-6 位数字。
        优先匹配包含"验证码"等关键词附近的数字。

        Args:
            text: 要搜索的文本

        Returns:
            提取的验证码，未找到返回 None
        """
        if not text:
            return None

        # 优先使用特定模式匹配
        for pattern in CODE_REGEX_PATTERNS[:-1]:  # 除最后一个通用模式
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                return matches[0]

        # 使用通用模式匹配
        matches = re.findall(self.CODE_REGEX, text)
        if matches:
            return matches[0]

        return None

    def close(self) -> None:
        """
        关闭 IMAP 连接。

        安全地关闭邮箱和登出，忽略可能的错误。
        """
        if self.mail:
            try:
                # 仅在选中状态下关闭邮箱
                if hasattr(self.mail, "state") and self.mail.state == "SELECTED":
                    self.mail.close()
            except (imaplib.IMAP4.error, socket.error, OSError) as e:
                self._log(f"Error closing mailbox: {type(e).__name__}", level="debug")

            try:
                self.mail.logout()
            except (imaplib.IMAP4.error, socket.error, OSError) as e:
                self._log(f"Error logging out: {type(e).__name__}", level="debug")

            self.is_connected = False
            self._log("Connection closed.")

    def __del__(self) -> None:
        """析构函数，确保连接被关闭。"""
        try:
            self.close()
        except Exception:
            pass
