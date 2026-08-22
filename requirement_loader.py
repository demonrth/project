"""Load an external project brief for Real-mode proposal generation."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


SUPPORTED_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".docx",
    ".pdf",
}
MAX_REQUIREMENT_CHARS = 40_000


def load_requirement_file(path: Path, *, max_chars: int = MAX_REQUIREMENT_CHARS) -> str:
    """Return normalized requirement text from a supported local document."""

    source = path.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"要求文件不存在：{source}")
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        supported = "、".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"不支持 {suffix or '无扩展名'} 文件；支持：{supported}")

    if suffix == ".docx":
        text = _read_docx(source)
    elif suffix == ".pdf":
        text = _read_pdf(source)
    else:
        text = _read_text(source)
        if suffix == ".json":
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                # A JSON-looking brief is still useful as plain text.
                pass

    normalized = _normalize(text)
    if not normalized:
        raise ValueError("未能从要求文件中提取可用文字。扫描版 PDF 请先进行 OCR。")
    replacement_count = normalized.count("\ufffd")
    if replacement_count > max(12, len(normalized) // 50):
        raise ValueError(
            "PDF 内嵌字体编码无法可靠还原中文，请先导出为 DOCX/TXT 或进行 OCR。"
        )
    if len(normalized) > max_chars:
        normalized = normalized[:max_chars].rstrip() + "\n\n[内容过长，已截取前 40,000 字符]"
    return normalized


def infer_topic(requirements: str, *, default: str) -> str:
    """Infer a concise project title from the first meaningful brief line."""

    lines = [line.strip() for line in requirements.splitlines() if line.strip()]
    labels = ("项目名称", "项目题目", "课题名称", "课题题目", "题目", "主题")
    for line in lines:
        cleaned = re.sub(r"^[#>*\-\d.、\s]+", "", line).strip()
        for label in labels:
            match = re.match(rf"^{label}\s*[:：]\s*(.+)$", cleaned)
            if match:
                return _short_topic(match.group(1), default)
    for line in lines:
        cleaned = re.sub(r"^[#>*\-\d.、\s]+", "", line).strip()
        if 4 <= len(cleaned) <= 120 and not cleaned.startswith(("要求", "说明", "注意")):
            return _short_topic(cleaned, default)
    return default


def _short_topic(value: str, default: str) -> str:
    topic = re.sub(r"\s+", " ", value).strip(" ：:。.;；")
    return topic[:120] if topic else default


def _read_text(path: Path) -> str:
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别要求文件编码，请保存为 UTF-8 后重试。")


def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            document = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValueError(f"无法读取 DOCX：{exc}") from exc

    root = ElementTree.fromstring(document)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(namespace + "p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == namespace + "t" and node.text:
                parts.append(node.text)
            elif node.tag == namespace + "tab":
                parts.append("\t")
            elif node.tag in {namespace + "br", namespace + "cr"}:
                parts.append("\n")
        value = "".join(parts).strip()
        if value:
            paragraphs.append(value)
    return "\n".join(paragraphs)


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("读取 PDF 需要安装 pypdf：python -m pip install pypdf") from exc
    try:
        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        raise ValueError(f"无法读取 PDF：{exc}") from exc


def _normalize(value: str) -> str:
    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    return value.strip()
