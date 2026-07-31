"""LLM 生成 - DeepSeek"""
from typing import Generator as TypingGenerator
from langchain_core.prompts import ChatPromptTemplate

from core.llm import get_default_llm


class Generator:
    """用 DeepSeek 生成答案"""

    SYSTEM_PROMPT = """你是企业知识库助手。请仅基于以下 context 回答用户问题。
如果 context 中没有相关信息，请明确说"我不知道"，不要编造。
回答时请在关键信息后用 [来源: doc_name, page X] 的格式标注来源。

Context:
{context}
"""

    def __init__(self, llm=None):
        self.llm = llm or get_default_llm()
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", self.SYSTEM_PROMPT),
            ("human", "{question}"),
        ])

    def generate(self, question: str, context: str) -> str:
        """生成答案（非流式）"""
        chain = self.prompt | self.llm
        return chain.invoke({"context": context, "question": question}).content

    def generate_stream(self, question: str, context: str) -> TypingGenerator[str, None, None]:
        """流式生成答案，逐 token yield

        直接调 llm.stream() 绕过 LangChain chain（更可预测，更易测试）。
        用法：
            for token in gen.generate_stream("问题", "上下文"):
                print(token, end="")
        """
        messages = self.prompt.format_messages(context=context, question=question)
        for chunk in self.llm.stream(messages):
            content = chunk.content
            if content:
                yield content
