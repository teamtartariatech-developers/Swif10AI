Socket.IO events for chat activity UI

connected
- Emitted when a client connects.
- Payload: { status: "ok" }

message_received
- Emitted when a user message is received.
- Payload: { message_id: string }

assistant_token
- Emitted for streamed tokens during generation/summarization.
- Payload: { message_id: string, token: string }

assistant_done
- Emitted when the final answer is ready.
- Payload: { message_id: string, text: string }

agent_status
- Stage updates to reflect progress.
- Payload: { stage: "planning" | "retrieving" | "done", detail?: string }

agent_think
- Short, user-safe “thoughts” for the activity panel.
- Payload: { message_id: string, thought: string }

agent_tool_start
- A tool call is starting.
- Payload: { tool: string, args: object }

agent_tool_result
- A tool call finished.
- Payload: { tool: string, result: object }

error
- Emitted on failures.
- Payload: { message_id?: string, error: string }

Frontend notes
- Show a live activity list:
  1) Show message_received
  2) Render agent_status/agent_think as steps/sub-steps
  3) When agent_tool_start fires, add an expandable item with args; update with agent_tool_result
  4) Stream assistant_token into the composing message bubble
  5) Replace with assistant_done content upon completion
  6) Show error banner if error event arrives

