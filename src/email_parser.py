"""
Email address parsing module for KL_AI application.

Provides utilities for extracting and validating email addresses from text files,
supporting multiple encodings and duplicate removal.

Example:
    >>> from email_parser import parse_emails_from_file, parse_emails_from_text
    >>> emails, invalid, encoding = parse_emails_from_file("emails.txt")
    >>> emails, invalid = parse_emails_from_text("user@example.com, admin@test.org")
"""

import os
import re
import logging
from pathlib import Path
from typing import List, Tuple, Set, Optional, Union

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns for better performance
_EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

_DATE_ONLY_PATTERN = re.compile(r"^\s*\d{4}/\d{2}/\d{2}\s*$")
_COMMENT_PREFIXES = ("#", ";", "//")

# Maximum line length to prevent regex DoS
_MAX_LINE_LENGTH = 1000


def _read_text_with_encoding(path: Union[str, Path]) -> Tuple[str, str]:
    """
    Read file with automatic encoding detection (UTF-8 -> GBK).

    Args:
        path: Path to the file

    Returns:
        Tuple of (file content, encoding used)

    Raises:
        FileNotFoundError: If file does not exist
        PermissionError: If file cannot be read
        UnicodeDecodeError: If file cannot be decoded with supported encodings
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    encodings = ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'latin1']

    for encoding in encodings:
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read(), encoding
        except UnicodeDecodeError:
            continue

    raise UnicodeDecodeError(f"Unable to decode file {path} with supported encodings")


def parse_emails_from_text(text: str) -> Tuple[List[str], List[str]]:
    """
    Extract email addresses from text content.

    Args:
        text: Text content to parse

    Returns:
        Tuple of (valid emails list, invalid lines list)
    """
    emails: List[str] = []
    invalid_lines: List[str] = []
    seen: Set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()

        # Skip empty lines
        if not line:
            continue

        # Skip comment lines
        if line.startswith(_COMMENT_PREFIXES):
            continue

        # Skip date-only lines
        if _DATE_ONLY_PATTERN.match(line):
            continue

        # Skip overly long lines (potential DoS)
        if len(line) > _MAX_LINE_LENGTH:
            logger.warning(f"Skipping overly long line ({len(line)} chars)")
            invalid_lines.append(raw_line)
            continue

        # Find all emails in line
        found = _EMAIL_PATTERN.findall(line)

        if not found:
            # If line contains @ but no valid email found, mark as invalid
            if "@" in line:
                invalid_lines.append(raw_line)
            continue

        for email in found:
            # Validate email format more strictly
            if not _is_valid_email(email):
                invalid_lines.append(raw_line)
                continue

            lower_email = email.lower()
            if lower_email not in seen:
                seen.add(lower_email)
                emails.append(email)

    return emails, invalid_lines


def _is_valid_email(email: str) -> bool:
    """
    Perform additional validation on extracted email.

    Args:
        email: Email address to validate

    Returns:
        True if valid, False otherwise
    """
    if not email or len(email) > 254:  # RFC 5321 limit
        return False

    if email.count('@') != 1:
        return False

    local, domain = email.rsplit('@', 1)

    # Local part validation
    if not local or len(local) > 64:
        return False

    # Domain validation
    if not domain or '.' not in domain or domain.startswith('.') or domain.endswith('.'):
        return False

    return True


def parse_emails_from_file(path: Union[str, Path]) -> Tuple[List[str], List[str], str]:
    """
    Extract email addresses from a file.

    Args:
        path: Path to the file to parse

    Returns:
        Tuple of (valid emails list, invalid lines list, encoding used)

    Raises:
        FileNotFoundError: If file does not exist
        PermissionError: If file cannot be read
    """
    path = Path(path)
    text, encoding = _read_text_with_encoding(path)
    emails, invalid = parse_emails_from_text(text)

    # Log invalid entries
    if invalid:
        try:
            log_dir = path.parent.parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "parser.log"

            with open(log_path, "a", encoding="utf-8") as f:
                timestamp = datetime.now().isoformat()
                for line in invalid:
                    f.write(f"[{timestamp}] {path.name}: {line}\n")

            logger.info(f"Logged {len(invalid)} invalid lines to {log_path}")
        except (OSError, PermissionError) as e:
            logger.warning(f"Failed to write invalid lines log: {e}")

    logger.info(f"Parsed {len(emails)} emails from {path} (encoding: {encoding})")
    return emails, invalid, encoding


# Import for type hint
from datetime import datetime
from typing import Union
