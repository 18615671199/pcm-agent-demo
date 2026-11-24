"""PDF / DOC 切片器 —— 抽纯文本后按段落切分"""
from __future__ import annotations
import os
import re
from pathlib import Path
from typing import List


def _extract_pdf(path: str) -> str:
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None

    if pdfplumber:
        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                text_parts.append(t)
        return "\n".join(text_parts)

    # fallback: pypdf
    from pypdf import PdfReader
    reader = PdfReader(path)
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _extract_doc(path: str) -> str:
    """支持 .docx，.doc 尝试用 python-docx 或回退"""
    try:
        from docx import Document
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text)
    except Exception:
        # 可能是老 .doc 格式，返回空并打日志
        print(f"[WARN] 无法解析 DOC: {path}（可能为老 .doc 二进制格式）")
        return ""


def extract_text(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext in (".docx", ".doc"):
        return _extract_doc(path)
    return ""


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 80) -> list[str]:
    """按字符数切块，带 overlap；先按双换行切段落，再补齐"""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    # 先按句号/换行切
    sentences = re.split(r"(?<=[。！？.!?])\s*", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) < chunk_size:
            cur += s
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)

    # 如果第一刀太碎（< 100 字符），合并
    merged: list[str] = []
    for c in chunks:
        if merged and len(c) < 100:
            merged[-1] += c
        else:
            merged.append(c)
    return merged


def chunk_file(path: str) -> list[str]:
    text = extract_text(path)
    return chunk_text(text)
