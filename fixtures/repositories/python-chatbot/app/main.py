"""Acme Chat: a small conversational API."""
from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI

app = FastAPI()
client = OpenAI()
SYSTEM_PROMPT = "You are Acme Chat, a concise and friendly assistant. Keep answers under three sentences."
chat_history: dict[str, list[dict]] = {}


class ChatIn(BaseModel):
    message: str
    session_id: str = "default"


@app.post("/chat")
def chat(body: ChatIn):
    history = chat_history.setdefault(body.session_id, [])
    history.append({"role": "user", "content": body.message})
    out = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "system", "content": SYSTEM_PROMPT}, *history])
    reply = out.choices[0].message.content
    history.append({"role": "assistant", "content": reply})
    return {"output": reply}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
