"""Knowledge tables used by the static RepositoryAnalyzer.

The tables are data, not code: each dependency or regex maps to a *capability* (rag, tools, memory,
browser, multi_agent, mcp, coding, planning, ...) and a human label. New ecosystems are added by
extending these tables - no analyzer logic changes.
"""

from __future__ import annotations

import re

# dependency name (lower-case, normalised) -> (kind, label, capability)
# kinds: framework | provider | vectordb | database | browser | memory | observability | mcp | other
DEPENDENCIES: dict[str, tuple[str, str, str | None]] = {
    # agent frameworks
    "langchain": ("framework", "LangChain", "tools"),
    "langchain-core": ("framework", "LangChain", "tools"),
    "langchain-community": ("framework", "LangChain", None),
    "langgraph": ("framework", "LangGraph", "planning"),
    "@langchain/core": ("framework", "LangChain.js", "tools"),
    "@langchain/langgraph": ("framework", "LangGraph.js", "planning"),
    "llama-index": ("framework", "LlamaIndex", "rag"),
    "llama_index": ("framework", "LlamaIndex", "rag"),
    "llamaindex": ("framework", "LlamaIndex.TS", "rag"),
    "crewai": ("framework", "CrewAI", "multi_agent"),
    "autogen": ("framework", "AutoGen", "multi_agent"),
    "pyautogen": ("framework", "AutoGen", "multi_agent"),
    "autogen-agentchat": ("framework", "AutoGen", "multi_agent"),
    "openai-agents": ("framework", "OpenAI Agents SDK", "tools"),
    "@openai/agents": ("framework", "OpenAI Agents SDK (JS)", "tools"),
    "swarm": ("framework", "OpenAI Swarm", "multi_agent"),
    "pydantic-ai": ("framework", "PydanticAI", "tools"),
    "semantic-kernel": ("framework", "Semantic Kernel", "tools"),
    "microsoft.semantickernel": ("framework", "Semantic Kernel", "tools"),
    "haystack-ai": ("framework", "Haystack", "rag"),
    "dspy": ("framework", "DSPy", "planning"),
    "dspy-ai": ("framework", "DSPy", "planning"),
    "smolagents": ("framework", "smolagents", "tools"),
    "google-adk": ("framework", "Google ADK", "tools"),
    "ai": ("framework", "Vercel AI SDK", "tools"),
    "@mastra/core": ("framework", "Mastra", "tools"),
    "langchain4j": ("framework", "LangChain4j", "tools"),
    "dev.langchain4j": ("framework", "LangChain4j", "tools"),
    "spring-ai": ("framework", "Spring AI", "tools"),
    "org.springframework.ai": ("framework", "Spring AI", "tools"),
    "letta": ("framework", "Letta", "memory"),
    "agno": ("framework", "Agno", "tools"),
    "phidata": ("framework", "Phidata", "tools"),
    "instructor": ("framework", "Instructor", None),
    "guidance": ("framework", "Guidance", None),
    "transformers": ("framework", "Hugging Face Transformers", None),
    "litellm": ("framework", "LiteLLM", None),
    "fastapi": ("other", "FastAPI", "api"),
    "flask": ("other", "Flask", "api"),
    "django": ("other", "Django", "api"),
    "express": ("other", "Express", "api"),
    "fastify": ("other", "Fastify", "api"),
    "@nestjs/core": ("other", "NestJS", "api"),
    "gin-gonic/gin": ("other", "Gin", "api"),
    "actix-web": ("other", "Actix", "api"),
    "axum": ("other", "Axum", "api"),
    "laravel/framework": ("other", "Laravel", "api"),
    "rails": ("other", "Rails", "api"),
    "sinatra": ("other", "Sinatra", "api"),
    "gradio": ("other", "Gradio", "ui"),
    "streamlit": ("other", "Streamlit", "ui"),
    "chainlit": ("other", "Chainlit", "ui"),
    "react": ("other", "React", "ui"),
    "next": ("other", "Next.js", "ui"),
    "vue": ("other", "Vue", "ui"),
    # model providers / SDKs
    "openai": ("provider", "OpenAI", None),
    "anthropic": ("provider", "Anthropic", None),
    "@anthropic-ai/sdk": ("provider", "Anthropic", None),
    "google-generativeai": ("provider", "Google Gemini", None),
    "google-genai": ("provider", "Google Gemini", None),
    "@google/generative-ai": ("provider", "Google Gemini", None),
    "@google/genai": ("provider", "Google Gemini", None),
    "ollama": ("provider", "Ollama", None),
    "cohere": ("provider", "Cohere", None),
    "mistralai": ("provider", "Mistral", None),
    "groq": ("provider", "Groq", None),
    "boto3": ("provider", "AWS (Bedrock?)", None),
    "langchain-openai": ("provider", "OpenAI", None),
    "langchain-anthropic": ("provider", "Anthropic", None),
    "langchain-google-genai": ("provider", "Google Gemini", None),
    "together": ("provider", "Together AI", None),
    "replicate": ("provider", "Replicate", None),
    "azure-ai-openai": ("provider", "Azure OpenAI", None),
    "@ai-sdk/openai": ("provider", "OpenAI", None),
    "@ai-sdk/anthropic": ("provider", "Anthropic", None),
    "@ai-sdk/google": ("provider", "Google Gemini", None),
    "langchain4j-open-ai": ("provider", "OpenAI", None),
    "langchain4j-anthropic": ("provider", "Anthropic", None),
    "langchain4j-google-ai-gemini": ("provider", "Google Gemini", None),
    "langchain4j-ollama": ("provider", "Ollama", None),
    "com.openai": ("provider", "OpenAI", None),
    "github.com/sashabaranov/go-openai": ("provider", "OpenAI", None),
    "async-openai": ("provider", "OpenAI", None),
    "openai-php/client": ("provider", "OpenAI", None),
    "ruby-openai": ("provider", "OpenAI", None),
    # vector DBs / retrieval
    "chromadb": ("vectordb", "Chroma", "rag"),
    "qdrant-client": ("vectordb", "Qdrant", "rag"),
    "@qdrant/js-client-rest": ("vectordb", "Qdrant", "rag"),
    "pinecone-client": ("vectordb", "Pinecone", "rag"),
    "pinecone": ("vectordb", "Pinecone", "rag"),
    "@pinecone-database/pinecone": ("vectordb", "Pinecone", "rag"),
    "weaviate-client": ("vectordb", "Weaviate", "rag"),
    "faiss-cpu": ("vectordb", "FAISS", "rag"),
    "faiss-gpu": ("vectordb", "FAISS", "rag"),
    "pgvector": ("vectordb", "pgvector", "rag"),
    "lancedb": ("vectordb", "LanceDB", "rag"),
    "pymilvus": ("vectordb", "Milvus", "rag"),
    "sentence-transformers": ("other", "Sentence-Transformers (embeddings)", "rag"),
    "rank-bm25": ("other", "BM25 retrieval", "rag"),
    "pypdf": ("other", "PDF loading", "documents"),
    "pypdf2": ("other", "PDF loading", "documents"),
    "pdfplumber": ("other", "PDF loading", "documents"),
    "unstructured": ("other", "Document parsing", "documents"),
    "python-docx": ("other", "DOCX parsing", "documents"),
    "pdf-parse": ("other", "PDF loading", "documents"),
    # databases
    "sqlalchemy": ("database", "SQLAlchemy", None),
    "psycopg2": ("database", "PostgreSQL", None),
    "psycopg": ("database", "PostgreSQL", None),
    "pg": ("database", "PostgreSQL", None),
    "asyncpg": ("database", "PostgreSQL", None),
    "pymongo": ("database", "MongoDB", None),
    "mongoose": ("database", "MongoDB", None),
    "redis": ("database", "Redis", "state"),
    "ioredis": ("database", "Redis", "state"),
    "mysql-connector-python": ("database", "MySQL", None),
    "mysql2": ("database", "MySQL", None),
    "sqlite3": ("database", "SQLite", None),
    "better-sqlite3": ("database", "SQLite", None),
    "prisma": ("database", "Prisma", None),
    "@prisma/client": ("database", "Prisma", None),
    # memory
    "mem0ai": ("memory", "Mem0", "memory"),
    "mem0": ("memory", "Mem0", "memory"),
    "zep-cloud": ("memory", "Zep", "memory"),
    "zep-python": ("memory", "Zep", "memory"),
    "langgraph-checkpoint": ("memory", "LangGraph checkpointer", "memory"),
    "langgraph-checkpoint-sqlite": ("memory", "LangGraph SQLite checkpointer", "memory"),
    "langgraph-checkpoint-postgres": ("memory", "LangGraph Postgres checkpointer", "memory"),
    # browser automation / computer use
    "playwright": ("browser", "Playwright", "browser"),
    "@playwright/test": ("browser", "Playwright", "browser"),
    "selenium": ("browser", "Selenium", "browser"),
    "selenium-webdriver": ("browser", "Selenium", "browser"),
    "puppeteer": ("browser", "Puppeteer", "browser"),
    "puppeteer-core": ("browser", "Puppeteer", "browser"),
    "browser-use": ("browser", "browser-use", "browser"),
    "@browserbasehq/stagehand": ("browser", "Stagehand", "browser"),
    "pyautogui": ("browser", "PyAutoGUI (computer use)", "computer_use"),
    "pyppeteer": ("browser", "Pyppeteer", "browser"),
    "chromedp": ("browser", "chromedp", "browser"),
    # MCP
    "mcp": ("mcp", "MCP SDK", "mcp"),
    "fastmcp": ("mcp", "FastMCP", "mcp"),
    "@modelcontextprotocol/sdk": ("mcp", "MCP SDK (TS)", "mcp"),
    "mcp-use": ("mcp", "mcp-use", "mcp"),
    "langchain-mcp-adapters": ("mcp", "LangChain MCP adapters", "mcp"),
    "github.com/mark3labs/mcp-go": ("mcp", "mcp-go", "mcp"),
    "rmcp": ("mcp", "rmcp (Rust MCP)", "mcp"),
    "modelcontextprotocol": ("mcp", "MCP SDK", "mcp"),
    # coding agents
    "gitpython": ("other", "GitPython (repository manipulation)", "coding"),
    "pygithub": ("other", "GitHub API", "coding"),
    "aider-chat": ("framework", "Aider", "coding"),
    "openhands-ai": ("framework", "OpenHands", "coding"),
    "swebench": ("other", "SWE-bench", "coding"),
    "unidiff": ("other", "Diff handling", "coding"),
    # observability
    "opentelemetry-api": ("observability", "OpenTelemetry", None),
    "langfuse": ("observability", "Langfuse", None),
    "langsmith": ("observability", "LangSmith", None),
    "arize-phoenix": ("observability", "Arize Phoenix", None),
    "sentry-sdk": ("observability", "Sentry", None),
    "prometheus-client": ("observability", "Prometheus", None),
}

# regex signals scanned in source files. (kind, regex, capability, detail template)
CODE_SIGNALS: list[tuple[str, re.Pattern[str], str | None, str]] = [
    (
        "rag",
        re.compile(
            r"\b(as_retriever|similarity_search|vectorstore|VectorStore|retrieve\(|RetrievalQA|"
            r"create_retrieval_chain|embed_documents|embeddings\.create|text_splitter|RecursiveCharacterTextSplitter|"
            r"top_k|topK|FAISS|Chroma\(|QdrantClient|cosine_similarity)\b"
        ),
        "rag",
        "retrieval/embedding code",
    ),
    (
        "memory",
        re.compile(
            r"\b(ConversationBufferMemory|ConversationSummaryMemory|ChatMessageHistory|MemorySaver|"
            r"SqliteSaver|PostgresSaver|checkpointer|session_memory|long_term_memory|memory_store|"
            r"remember\(|MessagesPlaceholder|thread_id|conversation_history|chat_history)\b"
        ),
        "memory",
        "conversation/long-term memory handling",
    ),
    (
        "planning",
        re.compile(
            r"\b(plan_and_execute|PlanAndExecute|planner|replan|create_plan|task_decomposition|"
            r"StateGraph|add_conditional_edges|ReAct|create_react_agent|AgentExecutor|"
            r"max_iterations|max_steps|agent_loop|while not done)\b"
        ),
        "planning",
        "planning/ReAct control loop",
    ),
    (
        "multi_agent",
        re.compile(
            r"\b(handoffs?\s*=|Handoff\(|Crew\(|crew\.kickoff|GroupChat|AssistantAgent|UserProxyAgent|"
            r"supervisor|sub_?agents?|delegate_to|transfer_to_|route_to_agent|AgentTeam|Swarm\()",
            re.I,
        ),
        "multi_agent",
        "multi-agent delegation",
    ),
    (
        "browser",
        re.compile(
            r"\b(page\.goto|page\.click|chromium\.launch|webdriver\.|driver\.get\(|browser\.new_page|"
            r"puppeteer\.launch|computer_use|computer-use|screenshot\(|BrowserAgent|browser_use)\b"
        ),
        "browser",
        "browser automation",
    ),
    (
        "coding",
        re.compile(
            r"\b(git\s+diff|git\s+apply|apply_patch|write_file|edit_file|str_replace|run_tests?|"
            r"subprocess\.run|child_process|os\.system|exec\.Command|Runtime\.getRuntime\(\)\.exec|"
            r"pytest|unified_diff|patch_file|create_pull_request)\b"
        ),
        "coding",
        "code editing / command execution",
    ),
    (
        "human_approval",
        re.compile(
            r"\b(interrupt_before|interrupt_after|human_in_the_loop|require_approval|needs_approval|"
            r"confirm_action|request_confirmation|ask_user_confirmation|approval_required)\b",
            re.I,
        ),
        "human_approval",
        "human approval step",
    ),
    (
        "sanitization",
        re.compile(
            r"\b(sanitize|sanitise|strip_instructions|prompt_guard|guardrails?|moderation|"
            r"content_filter|PromptInjection|llm_guard)\b",
            re.I,
        ),
        "guardrails",
        "input/output sanitisation or guardrails",
    ),
    (
        "streaming",
        re.compile(r"\b(text/event-stream|EventSource|StreamingResponse|yield\s+f?[\"']data:|stream=True|astream)\b"),
        "streaming",
        "streaming responses",
    ),
    (
        "multimodal",
        re.compile(
            r"\b(image_url|input_image|vision|PIL\.Image|imageData|inline_data|mime_type\s*[:=]\s*[\"']image/|"
            r"whisper|speech_to_text|text_to_speech|tts)\b",
            re.I,
        ),
        "multimodal",
        "image/audio handling",
    ),
    (
        "voice",
        re.compile(r"\b(whisper|speech_recognition|text_to_speech|tts|stt|twilio|livekit|webrtc)\b", re.I),
        "voice",
        "speech/voice handling",
    ),
    (
        "background",
        re.compile(
            r"\b(celery|apscheduler|cron|bullmq|schedule\.every|background_tasks|BackgroundTasks|"
            r"asyncio\.create_task|worker_loop|long_running)\b",
            re.I,
        ),
        "long_running",
        "background/long-running work",
    ),
    (
        "event_driven",
        re.compile(r"\b(webhook|kafka|rabbitmq|pubsub|sqs|on_event|EventHandler|subscribe\()\b", re.I),
        "event_driven",
        "event/webhook triggers",
    ),
]

MODEL_NAME = re.compile(
    r"\b(gpt-[0-9][\w.\-]*|o[134]-?(?:mini|preview)?|claude-[\w.\-]+|gemini-[\w.\-]+|llama-?[\d.]+[\w.\-:]*|"
    r"mistral-[\w.\-]+|mixtral-[\w.\-]+|qwen[\d.]*[\w.\-:]*|deepseek-[\w.\-]+|phi-?\d[\w.\-]*)\b",
    re.I,
)
ENV_KEY = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{2,})\s*=", re.M)
PROVIDER_ENV = {
    "OPENAI_API_KEY": "OpenAI",
    "ANTHROPIC_API_KEY": "Anthropic",
    "GEMINI_API_KEY": "Google Gemini",
    "GOOGLE_API_KEY": "Google Gemini",
    "OPENROUTER_API_KEY": "OpenRouter",
    "GROQ_API_KEY": "Groq",
    "COHERE_API_KEY": "Cohere",
    "MISTRAL_API_KEY": "Mistral",
    "AZURE_OPENAI_API_KEY": "Azure OpenAI",
    "OLLAMA_HOST": "Ollama",
    "OLLAMA_BASE_URL": "Ollama",
    "HF_TOKEN": "Hugging Face",
}

PROMPT_ASSIGN = re.compile(
    r"""(?P<name>\b(?:SYSTEM_PROMPT|system_prompt|systemPrompt|SYSTEM_MESSAGE|system_message|instructions|INSTRUCTIONS|
    AGENT_PROMPT|agent_prompt|PROMPT_TEMPLATE|prompt_template|systemInstruction|system_instruction)\b)\s*[:=]\s*
    (?P<q>\"\"\"|'''|`|"|')(?P<body>.{20,4000}?)(?P=q)""",
    re.S | re.X,
)

TOOL_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # (framework, regex with named group 'name' and optional 'desc', language-agnostic)
    (
        "LangChain @tool",
        re.compile(
            r"@tool(?:\([^)]*\))?\s*\n\s*(?:async\s+)?def\s+(?P<name>\w+)\s*\([^)]*\)[^:]*:\s*\n?\s*(?:\"\"\"(?P<desc>.*?)\"\"\")?",
            re.S,
        ),
        "python",
    ),
    (
        "MCP (FastMCP) @tool",
        re.compile(
            r"@(?:\w+)\.tool\([^)]*\)\s*\n\s*(?:async\s+)?def\s+(?P<name>\w+)\s*\([^)]*\)[^:]*:\s*\n?\s*(?:\"\"\"(?P<desc>.*?)\"\"\")?",
            re.S,
        ),
        "python",
    ),
    (
        "function_tool",
        re.compile(
            r"@function_tool(?:\([^)]*\))?\s*\n\s*(?:async\s+)?def\s+(?P<name>\w+)\s*\([^)]*\)[^:]*:\s*\n?\s*(?:\"\"\"(?P<desc>.*?)\"\"\")?",
            re.S,
        ),
        "python",
    ),
    (
        "OpenAI function schema",
        re.compile(
            r"[\"']function[\"']\s*:\s*\{\s*[\"']name[\"']\s*:\s*[\"'](?P<name>[\w\-.]+)[\"']\s*,\s*[\"']description[\"']\s*:\s*[\"'](?P<desc>[^\"']*)[\"']",
            re.S,
        ),
        "any",
    ),
    (
        "tool schema (name/description)",
        re.compile(
            r"\{\s*[\"']?name[\"']?\s*:\s*[\"'](?P<name>[\w\-.]+)[\"']\s*,\s*[\"']?description[\"']?\s*:\s*[\"'](?P<desc>[^\"']{5,300})[\"']\s*,\s*[\"']?(?:input_schema|inputSchema|parameters)[\"']?",
            re.S,
        ),
        "any",
    ),
    (
        "JS const x = tool()",
        re.compile(
            r"\b(?:const|let|var)\s+(?P<name>\w+)\s*=\s*tool\(\s*\{\s*description\s*:\s*[\"'`](?P<desc>[^\"'`]{3,300})[\"'`]",
            re.S,
        ),
        "js",
    ),
    (
        "JS tool()",
        re.compile(
            r"\btool\(\s*\{\s*(?:name\s*:\s*[\"'](?P<name>[\w\-.]+)[\"']\s*,\s*)?description\s*:\s*[\"'`](?P<desc>[^\"'`]{3,300})[\"'`]",
            re.S,
        ),
        "js",
    ),
    (
        "MCP server.tool()",
        re.compile(
            r"\b(?:server|mcp|app)\.(?:tool|registerTool)\(\s*[\"'](?P<name>[\w\-.]+)[\"']\s*,\s*(?:[\"'](?P<desc>[^\"']{3,300})[\"'])?",
            re.S,
        ),
        "js",
    ),
    (
        "DynamicTool",
        re.compile(
            r"new\s+(?:Dynamic(?:Structured)?Tool|FunctionTool)\(\s*\{\s*name\s*:\s*[\"'](?P<name>[\w\-.]+)[\"']\s*,\s*description\s*:\s*[\"'](?P<desc>[^\"']*)[\"']",
            re.S,
        ),
        "js",
    ),
    (
        "Java/Kotlin @Tool",
        re.compile(
            r"@Tool(?:\((?:[^)]*description\s*=\s*)?\"(?P<desc>[^\"]*)\"[^)]*\))?\s*(?:public\s+|private\s+|fun\s+|static\s+)*(?:[\w<>\[\],.?]+\s+)?(?P<name>\w+)\s*\(",
            re.S,
        ),
        "java",
    ),
    (
        "C# KernelFunction",
        re.compile(
            r"\[KernelFunction(?:\(\"(?P<name2>[\w]+)\"\))?\]\s*(?:\[Description\(\"(?P<desc>[^\"]*)\"\)\]\s*)?public\s+[\w<>\[\]?,. ]+\s+(?P<name>\w+)\s*\(",
            re.S,
        ),
        "csharp",
    ),
    (
        "MCP (Go NewTool)",
        re.compile(
            r"mcp\.NewTool\(\s*\"(?P<name>[\w\-.]+)\"\s*,\s*(?:mcp\.WithDescription\(\"(?P<desc>[^\"]*)\"\))?", re.S
        ),
        "go",
    ),
    (
        "Go FunctionDefinition",
        re.compile(r"Name:\s*\"(?P<name>[\w\-.]+)\"\s*,\s*Description:\s*\"(?P<desc>[^\"]*)\"", re.S),
        "go",
    ),
    (
        "Rust #[tool]",
        re.compile(
            r"#\[tool\((?:[^)]*description\s*=\s*\"(?P<desc>[^\"]*)\")?[^)]*\)\]\s*(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>\w+)",
            re.S,
        ),
        "rust",
    ),
    (
        "PHP tool",
        re.compile(
            r"new\s+Tool\(\s*(?:name:\s*)?[\"'](?P<name>[\w\-.]+)[\"']\s*,\s*(?:description:\s*)?[\"'](?P<desc>[^\"']*)[\"']",
            re.S,
        ),
        "php",
    ),
    (
        "Ruby tool",
        re.compile(r"\btool\s+:(?P<name>\w+)\s+do\s*(?:\n\s*description\s+[\"'](?P<desc>[^\"']*)[\"'])?", re.S),
        "ruby",
    ),
    (
        "Swift tool",
        re.compile(r"Tool\(\s*name:\s*\"(?P<name>[\w\-.]+)\"\s*,\s*description:\s*\"(?P<desc>[^\"]*)\"", re.S),
        "swift",
    ),
]

AGENT_DEF_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("CrewAI", re.compile(r"\bAgent\(\s*(?:\s*role\s*=\s*[\"'](?P<role>[^\"']+)[\"'])", re.S)),
    ("OpenAI Agents SDK", re.compile(r"\bAgent\(\s*name\s*=\s*[\"'](?P<name>[^\"']+)[\"']", re.S)),
    (
        "AutoGen",
        re.compile(
            r"\b(?:AssistantAgent|UserProxyAgent|ConversableAgent)\(\s*(?:name\s*=\s*)?[\"'](?P<name>[^\"']+)[\"']",
            re.S,
        ),
    ),
    ("LangGraph node", re.compile(r"\.add_node\(\s*[\"'](?P<name>[^\"']+)[\"']", re.S)),
    (
        "generic agent class",
        re.compile(r"^\s*(?:export\s+)?(?:public\s+)?(?:abstract\s+)?class\s+(?P<name>\w*Agent\w*)\b", re.M),
    ),
    (
        "generic agent object",
        re.compile(r"^\s*(?:def|func|fn|function)\s+(?P<name>(?:run|create|build|make)_?\w*agent\w*)\b", re.M | re.I),
    ),
]

ROUTE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "FastAPI/Flask",
        re.compile(r"@(?:\w+)\.(?P<method>get|post|put|delete|patch|route)\(\s*[\"'](?P<path>/[^\"']*)[\"']"),
    ),
    (
        "Express/Fastify",
        re.compile(
            r"\b(?:app|router|fastify)\.(?P<method>get|post|put|delete|patch)\(\s*[\"'`](?P<path>/[^\"'`]*)[\"'`]"
        ),
    ),
    ("Spring", re.compile(r"@(?P<method>Get|Post|Put|Delete|Patch)Mapping\(\s*(?:value\s*=\s*)?\"(?P<path>/[^\"]*)\"")),
    (
        "Go net/http / gin",
        re.compile(
            r"\b(?:r|router|mux|e|app|group)\.(?P<method>GET|POST|PUT|DELETE|PATCH|HandleFunc|Handle)\(\s*\"(?P<path>/[^\"]*)\""
        ),
    ),
    ("Rails/Sinatra", re.compile(r"^\s*(?P<method>get|post|put|delete|patch)\s+['\"](?P<path>/[^'\"]*)['\"]", re.M)),
    ("Laravel", re.compile(r"Route::(?P<method>get|post|put|delete|patch)\(\s*['\"](?P<path>/[^'\"]*)['\"]")),
    ("ASP.NET", re.compile(r"\[Http(?P<method>Get|Post|Put|Delete|Patch)\(\"(?P<path>[^\"]*)\"\)\]")),
]

SIDE_EFFECT_WORDS = {
    "destructive": re.compile(
        r"\b(delete|remove|drop|destroy|erase|purge|wipe|truncate|kill|terminate|cancel)\b", re.I
    ),
    "external": re.compile(
        r"\b(send|email|mail|post|publish|notify|sms|message|charge|pay|transfer|purchase|order|book|"
        r"submit|invite|tweet|webhook|push)\b",
        re.I,
    ),
    "write": re.compile(
        r"\b(create|update|write|save|insert|modify|edit|set|add|upload|commit|apply|patch|rename|move)\b", re.I
    ),
    "read": re.compile(
        r"\b(get|read|list|search|find|fetch|lookup|look up|query|retrieve|show|view|describe|calculate|compute)\b",
        re.I,
    ),
}


def infer_side_effects(name: str, description: str) -> str:
    text = f"{name.replace('_', ' ').replace('-', ' ')} {description}"
    for kind in ("destructive", "external", "write", "read"):
        if SIDE_EFFECT_WORDS[kind].search(text):
            return kind
    return "unknown"


def normalise_dep(name: str) -> str:
    return name.strip().lower().replace("_", "-") if not name.startswith("@") else name.strip().lower()
