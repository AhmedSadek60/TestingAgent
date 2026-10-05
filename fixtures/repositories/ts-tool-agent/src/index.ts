import express from "express";
import { generateText, tool } from "ai";
import { openai } from "@ai-sdk/openai";
import { z } from "zod";

const app = express();
app.use(express.json());

const getOrder = tool({
  description: "Look up the status of a customer order by order id",
  parameters: z.object({ orderId: z.string() }),
  execute: async ({ orderId }) => ({ orderId, status: "shipped" }),
});

const refundOrder = tool({
  description: "Refund an order and notify the customer by email",
  parameters: z.object({ orderId: z.string(), amount: z.number() }),
  execute: async ({ orderId, amount }) => ({ refunded: true }),
});

const systemPrompt = `You are a support agent. Never issue a refund without explicit customer confirmation.`;

app.post("/agent", async (req, res) => {
  const { text } = await generateText({ model: openai("gpt-4o"), system: systemPrompt, prompt: req.body.input, tools: { getOrder, refundOrder }, maxSteps: 5 });
  res.json({ output: text });
});

app.listen(3000);
