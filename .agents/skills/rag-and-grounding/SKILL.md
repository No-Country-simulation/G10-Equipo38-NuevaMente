---
name: rag-and-grounding
description: Implements the technical core for document ingestion, semantic chunking, ChromaDB vector indexing, context retrieval, and hallucination mitigation via the Ragas faithfulness algorithm to compute anclaje_fuente_score (0.0 to 1.0). Use when building or tuning the RAG pipeline, parser, vector store, or factual verification guardrails.
---

# RAG and Grounding

## Overview

The technical core of **NuevaMente**. Ingests complex technical documents (PDFs, Markdown, and plain text), executes semantic chunking, indexes embeddings into a vector store (ChromaDB / FAISS), performs similarity search with Maximal Marginal Relevance (MMR), and verifies factual fidelity using the **Ragas Faithfulness Algorithm** to calculate `anclaje_fuente_score` (0.0 to 1.0).

This ensures educational content is strictly grounded in original documentation, eliminating LLM hallucinations.

## When to Use

- Ingesting technical manuals, software docs, whitepapers, or API guides.
- Configuring chunking size, overlap, and metadata tagging.
- Setting up embeddings and querying ChromaDB / FAISS.
- Computing `anclaje_fuente_score` before delivering content to the user or downstream storage.
- Implementing the Critic verification loop in LangGraph.

---

## 1. Document Ingestion & Semantic Chunking

### Pipeline Architecture

```
Raw Document (PDF/MD/TXT)
  │
  ▼
[Document Loader] ──→ Extracts raw text & structural headings
  │
  ▼
[Semantic / Markdown Chunker] ──→ Chunks (500–800 tokens, 100 token overlap)
  │
  ▼
[Metadata Tagging] ──→ Adds doc_name, page, section_title, chunk_index
  │
  ▼
[Embedding Engine] ──→ Generates dense vector representation
  │
  ▼
[Vector Store (ChromaDB)] ──→ Indexed for semantic & MMR retrieval
```

### Python Implementation: Ingestion & ChromaDB

```python
from pathlib import Path
from typing import List, Dict, Any
from langchain_community.document_loaders import PyPDFLoader, TextLoader, UnstructuredMarkdownLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma

def load_document(file_path: str | Path) -> List[Document]:
    path = Path(file_path)
    suffix = path.suffix.lower()
    
    if suffix == ".pdf":
        loader = PyPDFLoader(str(path))
    elif suffix in [".md", ".markdown"]:
        loader = UnstructuredMarkdownLoader(str(path))
    elif suffix in [".txt", ".rst"]:
        loader = TextLoader(str(path), encoding="utf-8")
    else:
        raise ValueError(f"Unsupported file format: {suffix}")
        
    docs = loader.load()
    # Enrich metadata
    for i, doc in enumerate(docs):
        doc.metadata["source_name"] = path.name
        doc.metadata["page"] = doc.metadata.get("page", 0) + 1
    return docs

def chunk_documents(docs: List[Document], chunk_size: int = 750, chunk_overlap: int = 120) -> List[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n## ", "\n### ", "\n\n", "\n", " ", ""],
        add_start_index=True
    )
    return splitter.split_documents(docs)

def index_in_chromadb(chunks: List[Document], embedding_function, persist_directory: str = ".data/chroma_db"):
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embedding_function,
        persist_directory=persist_directory
    )
    return vectorstore
```

---

## 2. Context Retrieval (MMR)

Use Maximal Marginal Relevance (MMR) rather than simple cosine similarity to maximize informative diversity and prevent retrieving multiple nearly-identical chunks:

```python
def retrieve_grounded_context(vectorstore: Chroma, query: str, k: int = 5, fetch_k: int = 15) -> List[Document]:
    """Retrieves top k diverse, highly relevant chunks using MMR."""
    retriever = vectorstore.as_retriever(
        search_type="mmr",
        search_kwargs={"k": k, "fetch_k": fetch_k, "lambda_mult": 0.7}
    )
    return retriever.invoke(query)
```

---

## 3. Ragas Faithfulness Algorithm (`anclaje_fuente_score`)

Adapted from Ragas (`src/ragas/metrics/_faithfulness.py`). Faithfulness is defined as the fraction of factual statements in the generated educational output that can be logically inferred from the retrieved source context.

### Two-Step Verification Protocol

1. **Statement Decomposition (`StatementGenerator`)**:
   Break down the generated text into atomic, self-contained factual claims. Ensure all pronouns ("it", "he", "this") are resolved to explicit nouns.
2. **Natural Language Inference (`NLIStatementJudge`)**:
   For each statement, determine whether the retrieved source context provides sufficient factual support:
   - `verdict = 1`: Statement is directly supported or strictly inferable.
   - `verdict = 0`: Statement cannot be inferred, contradicts source, or adds extraneous ungrounded claims (hallucination).
3. **Score Calculation**:
   $$\text{anclaje\_fuente\_score} = \frac{\sum \text{verdicts}}{\text{total statements}}$$

### Standalone Faithfulness Implementation

```python
from typing import List, Tuple
from pydantic import BaseModel, Field

class AtomicStatements(BaseModel):
    statements: List[str] = Field(description="List of atomic, self-contained factual statements.")

class StatementJudgement(BaseModel):
    statement: str
    reason: str
    verdict: int = Field(description="1 if directly inferable from context, 0 otherwise")

class FaithfulnessVerdict(BaseModel):
    judgements: List[StatementJudgement]
    anclaje_fuente_score: float

STATEMENT_DECOMPOSITION_PROMPT = """Given the educational text below, break it down into a list of atomic, self-contained factual claims.
Rules:
- Each statement must express only ONE factual idea.
- Resolve all pronouns into their canonical referents.
- Do not omit technical specifications, code references, or metrics.

Educational Text:
{text}
"""

NLI_EVALUATION_PROMPT = """Your task is to judge the faithfulness of each statement against the provided technical context.
For each statement:
- Return verdict=1 IF AND ONLY IF the statement is directly mentioned or logically entailed by the technical context.
- Return verdict=0 if the statement is NOT mentioned in the context, makes an unverified assumption, or contradicts the context.
- State your brief reason.

Technical Context:
{context}

Statements to judge:
{statements}
"""

def compute_anclaje_fuente_score(generated_text: str, context_chunks: List[str], llm_callable) -> FaithfulnessVerdict:
    """
    Computes anclaje_fuente_score (0.0 to 1.0) using Ragas methodology.
    """
    combined_context = "\n---\n".join(context_chunks)
    
    # Step 1: Extract atomic statements
    statements_resp: AtomicStatements = llm_callable(
        STATEMENT_DECOMPOSITION_PROMPT.format(text=generated_text),
        response_model=AtomicStatements
    )
    
    if not statements_resp.statements:
        return FaithfulnessVerdict(judgements=[], anclaje_fuente_score=1.0)

    # Step 2: Judge each statement against context
    judgements_resp = llm_callable(
        NLI_EVALUATION_PROMPT.format(
            context=combined_context,
            statements="\n".join(f"- {s}" for s in statements_resp.statements)
        ),
        response_model=List[StatementJudgement]
    )
    
    total = len(judgements_resp)
    supported = sum(1 for j in judgements_resp if j.verdict == 1)
    score = round(supported / total, 3) if total > 0 else 1.0
    
    return FaithfulnessVerdict(judgements=judgements_resp, anclaje_fuente_score=score)
```

---

## 4. Score Thresholds and Rejection Policy

| Score Range | Classification | Action in NuevaMente |
|---|---|---|
| **0.85 – 1.00** | **High Fidelity (Grounding Verified)** | Approved. Send to UI & upload to OCI Object Storage. |
| **0.70 – 0.84** | **Moderate Fidelity** | Trigger Critic feedback loop in LangGraph with failed statements. |
| **< 0.70** | **Low Fidelity (Hallucination Detected)** | Reject output. Prompt LLM to restrict answer exclusively to retrieved text. |

This policy guarantees zero hallucinations across all generated educational formats.
