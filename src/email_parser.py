import os
import re
from typing import List, Tuple

EMAIL_REGEX = re.compile(
    r"(?:[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*)@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
    r"(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))+"
)

DATE_ONLY_REGEX = re.compile(r"^\s*\d{4}/\d{2}/\d{2}\s*$")
COMMENT_PREFIXES = ("#", ";", "//")

def _read_text_with_encoding(path: str) -> Tuple[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), "utf-8"
    except UnicodeDecodeError:
        with open(path, "r", encoding="gbk", errors="ignore") as f:
            return f.read(), "gbk"

def parse_emails_from_text(text: str) -> Tuple[List[str], List[str]]:
    emails: List[str] = []
    invalid_lines: List[str] = []
    seen = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(COMMENT_PREFIXES):
            continue
        if DATE_ONLY_REGEX.match(line):
            continue

        found = EMAIL_REGEX.findall(line)
        if not found:
            if "@" in line:
                invalid_lines.append(raw_line)
            continue
        for e in found:
            lower = e.lower()
            if lower not in seen:
                seen.add(lower)
                emails.append(e)
    return emails, invalid_lines

def parse_emails_from_file(path: str) -> Tuple[List[str], List[str], str]:
    text, encoding = _read_text_with_encoding(path)
    emails, invalid = parse_emails_from_text(text)
    log_dir = os.path.join(os.path.dirname(path), "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "parser.log")
    if invalid:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                for l in invalid:
                    f.write(l + "\n")
        except Exception:
            pass
    return emails, invalid, encoding
