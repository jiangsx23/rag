"""文档解析 - 用 pdfplumber（轻量版，Windows 友好）"""
from pathlib import Path
from dataclasses import dataclass
from loguru import logger

@dataclass
class ParsedElement:
    text: str
    element_type: str  # text / table / image / formula
    metadata: dict

class DocumentParser:
    """文档解析器 - 支持 PDF/DOCX/TXT/MD/HTML（不用 unstructured，绕过所有系统依赖）"""
    
    def parse(self, file_path: str) -> list[ParsedElement]:
        path = Path(file_path)
        logger.info(f"Parsing {path.name}...")
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            results = self._parse_pdf(path)
        elif suffix == ".txt":
            results = self._parse_txt(path)
        elif suffix == ".md":
            results = self._parse_txt(path)
        elif suffix == ".docx":
            results = self._parse_docx(path)
        elif suffix == ".doc":
            results = self._parse_doc(path)
        elif suffix == ".pptx":
            results = self._parse_pptx(path)
        elif suffix in {".html", ".htm"}:
            results = self._parse_html(path)
        else:
            logger.warning(f"不支持的文件类型: {suffix}")
            return []

        logger.info(f"Parsed {len(results)} elements from {path.name}")
        return results

    def _parse_pdf(self, path: Path) -> list[ParsedElement]:
        """用 pdfplumber 解析 PDF（支持表格提取）"""
        import pdfplumber
        results = []
        with pdfplumber.open(str(path)) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                # 提取文本
                text = page.extract_text() or ""
                if text.strip():
                    results.append(ParsedElement(
                        text=text,
                        element_type="text",
                        metadata={
                            "source": path.name,
                            "page": page_num,
                            "category": "text",
                        }
                    ))
                
                # 提取表格
                try:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table:
                            continue
                        # 把表格转成 markdown 风格的文本
                        table_text = self._format_table(table)
                        if table_text.strip():
                            results.append(ParsedElement(
                                text=f"[表格]\n{table_text}",
                                element_type="table",
                                metadata={
                                    "source": path.name,
                                    "page": page_num,
                                    "category": "table",
                                }
                            ))
                except Exception as e:
                    logger.warning(f"第 {page_num} 页表格提取失败: {e}")
        return results

    def _format_table(self, table: list) -> str:
        """把表格转成 markdown 风格"""
        lines = []
        for row in table:
            cells = [str(cell) if cell else "" for cell in row]
            lines.append(" | ".join(cells))
        return "\n".join(lines)

    def _parse_txt(self, path: Path) -> list[ParsedElement]:
        """解析 TXT / Markdown"""
        # 尝试多种编码
        text = None
        for encoding in ["utf-8", "gbk", "utf-16"]:
            try:
                text = path.read_text(encoding=encoding)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            logger.error(f"无法读取 {path.name}：编码不支持")
            return []
        return [ParsedElement(
            text=text,
            element_type="text",
            metadata={"source": path.name, "page": 1, "category": "text"}
        )]

    def _parse_docx(self, path: Path) -> list[ParsedElement]:
        """解析 Word 文档"""
        from docx import Document
        doc = Document(str(path))
        # 提取段落
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paragraphs)
        # 提取表格
        table_texts = []
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                table_texts.append(" | ".join(cells))
            table_texts.append("---")
        
        results = []
        if text:
            results.append(ParsedElement(
                text=text,
                element_type="text",
                metadata={"source": path.name, "page": 1, "category": "text"}
            ))
        if table_texts:
            results.append(ParsedElement(
                text="[表格]\n" + "\n".join(table_texts),
                element_type="table",
                metadata={"source": path.name, "page": 1, "category": "table"}
            ))
        return results

    def _parse_html(self, path: Path) -> list[ParsedElement]:
        """解析 HTML（用 BeautifulSoup）"""
        try:
            from bs4 import BeautifulSoup
            html = path.read_text(encoding="utf-8")
            soup = BeautifulSoup(html, "html.parser")
            # 移除 script/style
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return [ParsedElement(
                text=text,
                element_type="text",
                metadata={"source": path.name, "page": 1, "category": "text"}
            )]
        except ImportError:
            # 没有 bs4 就降级用 html.parser
            import html.parser
            logger.warning(f"BeautifulSoup 未安装，{path.name} 解析可能不完整")
            return []
    def _parse_doc(self, path):
        """解析 .doc 格式（尝试用 python-docx，部分 .doc 可读）"""
        try:
            from docx import Document
            doc = Document(str(path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            text = "\n".join(paragraphs)
            if text:
                return [ParsedElement(
                    text=text, element_type="text",
                    metadata={"source": path.name, "page": 1, "category": "text"},
                )]
        except Exception as e:
            logger.warning(f"{path.name} .doc 解析失败: {e}")
        return []

    def _parse_pptx(self, path):
        """解析 .pptx 文件，提取所有 slide 文本"""
        try:
            from pptx import Presentation
            prs = Presentation(str(path))
            results = []
            for slide_num, slide in enumerate(prs.slides, 1):
                texts = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            t = para.text.strip()
                            if t:
                                texts.append(t)
                    if shape.has_table:
                        for row in shape.table.rows:
                            cells = [cell.text.strip() for cell in row.cells]
                            texts.append(" | ".join(cells))
                if texts:
                    results.append(ParsedElement(
                        text="\n".join(texts), element_type="text",
                        metadata={"source": path.name, "page": slide_num, "category": "slide"},
                    ))
            return results
        except Exception as e:
            logger.warning(f"{path.name} PPTX 解析失败: {e}")
            return []

    def _classify(self, el) -> str:
        return el.element_type
