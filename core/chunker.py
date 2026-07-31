"""智能切分 - 表格独立 + 文本递归"""
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from .parser import ParsedElement


class SmartChunker:
    def __init__(self, chunk_size: int = 512, overlap: int = 50):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
        )

    def chunk(self, elements: list[ParsedElement]) -> list[Document]:
        documents = []

        for el in elements:
            if el.element_type in ("table", "image"):
                documents.append(Document(
                    page_content=self._format_special(el),
                    metadata={
                        **el.metadata,
                        "element_type": el.element_type,
                        "chunk_strategy": "standalone",
                    }
                ))
            else:
                chunks = self.text_splitter.split_text(el.text)
                for i, chunk_text in enumerate(chunks):
                    documents.append(Document(
                        page_content=chunk_text,
                        metadata={
                            **el.metadata,
                            "element_type": "text",
                            "chunk_strategy": "recursive",
                            "chunk_index": i,
                        }
                    ))

        for i, doc in enumerate(documents):
            doc.metadata["chunk_id"] = f"{doc.metadata['source']}-{i}"
        return documents

    def _format_special(self, el: ParsedElement) -> str:
        if el.element_type == "table":
            return f"[表格]\n{el.text}"
        elif el.element_type == "image":
            return f"[图片]\n{el.text}"
        return el.text
