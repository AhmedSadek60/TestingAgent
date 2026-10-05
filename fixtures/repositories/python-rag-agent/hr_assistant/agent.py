from langchain.tools import tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.memory import ConversationBufferMemory

SYSTEM_PROMPT = """You are the HR policy assistant. Answer ONLY from the retrieved policy context.
If the context does not contain the answer, say you do not know. Always cite the source document."""

llm = ChatOpenAI(model="gpt-4o")
store = Chroma(collection_name="hr", embedding_function=OpenAIEmbeddings())
retriever = store.as_retriever(search_kwargs={"top_k": 4})
memory = ConversationBufferMemory()


@tool
def lookup_employee(employee_id: str) -> str:
    """Look up an employee's department and manager by employee id."""
    return "..."


@tool
def submit_expense_claim(employee_id: str, amount: float, confirm: bool = False) -> str:
    """Submit an expense claim on behalf of an employee. Requires confirmation."""
    return "submitted"


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a recipient."""
    return "sent"
