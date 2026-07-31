# 📚 如何向知识库添加新文档

## 快速流程（3 步）

### 第 1 步：放文件

把文档放到 `data/raw/` 下的对应分类文件夹：

```
data/raw/
├── 1-产品与服务详情/          ← 票务、酒店、邮轮、餐饮、商品、设施等
├── 2-运营流程与标准作业程序/   ← 客诉处理、退款政策、CRM 等
├── 3-特殊情况与应急预案/      ← 紧急情况处理
├── 4-客户关系与支持话术/      ← 客户话术
└── 5-内部知识与工具/          ← 员工培训、操作手册、岗位职责等
```

**支持的格式：** `.pdf` `.docx` `.doc` `.pptx` `.txt` `.md` `.html`

> 不支持：`.jpeg/.png`（图片）、`.ppt`（旧版 PPT）

### 第 2 步：入库到 Qdrant

```bash
cd d:\Documents\rag
source venv/Scripts/activate
TRANSFORMERS_OFFLINE=1 python scripts/ingest.py
```

这个命令会：
1. 解析所有文档 → 2. 智能切分 → 3. BGE 嵌入 → 4. 写入 Qdrant

> ⚠️ 会**清空重建**整个 Qdrant collection，不是增量追加。
> 如果只想追加，后面有"增量追加"方案。

### 第 3 步：更新 QA 数据集（可选）

如果想基于新文档生成评测数据：

```bash
python eval/generate_qa_from_docs.py
```

这会用 DeepSeek 给每个文档生成 2-8 条问答对，输出到 `eval/dataset.jsonl`。

---

## 增量追加（不清空原有数据）

`scripts/ingest.py` 默认删旧建新。如果想增量追加，修改为：

```python
# scripts/ingest.py main() 中注释掉删重建，改为检测是否存在
if client.collection_exists(collection_name):
    # 已存在，直接追加
    logger.info(f"Collection {collection_name} 已存在，追加新文档")
else:
    client.create_collection(...)
```

然后只对新文档单独切片+写入：

```python
# 只处理新增文件
vector_store.add_documents(new_chunks)
```

## QA 数据集重新生成

`eval/generate_qa_from_docs.py` 每次都会从 Qdrant 读取所有 chunks 重新生成全覆盖的数据集，覆盖旧文件。

---

## 常见问题

**Q: 文档入库后需要重启 API 吗？**
A: 不需要。Pipeline 启动时从 Qdrant 加载 BM25 索引，重启后才生效。但向量检索是实时的。如果等不及重启，可以手动触发 BM25 刷新（`GET /refresh` 端点需要新增）。

**Q: 怎么确认入库成功？**
```bash
# 检查 Qdrant collection 的向量数
python -c "
from app.config import settings
from qdrant_client import QdrantClient
c = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
info = c.get_collection('knowledge_base')
print(f'Vectors: {info.points_count}')
"
```

**Q: 支持哪些文件类型？**
| 格式 | 解析方式 | 状态 |
|---|---|---|
| `.pdf` | pdfplumber | ✅ |
| `.docx` | python-docx | ✅ |
| `.doc` | python-docx（部分兼容） | ✅ 部分可用 |
| `.pptx` | python-pptx | ✅ |
| `.txt` / `.md` | 直接读取 | ✅ |
| `.html` | BeautifulSoup | ✅ |
| `.ppt` | 旧版二进制格式 | ❌ |
| `.jpeg/.png` | 图片 | ❌ 需要 OCR |

**Q: 怎么把图片里的文字也入库？**
A: 安装 tesseract-ocr + pytesseract，然后在 `core/parser.py` 中添加 OCR 解析方法。
