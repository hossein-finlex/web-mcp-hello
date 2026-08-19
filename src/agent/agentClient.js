/**
 * agentClient.js — the browser half of the WebMCP bridge.
 *
 * Claude runs on the Python backend. It has no access to this page; it only
 * ever sees the tool list the browser publishes. When it decides to call one,
 * the backend forwards the call here and this module executes it against the
 * page's real WebMCP registry:
 *
 *     backend: {type:'tool_use', name, input}
 *        -> executeTool(name, input)         // mutates React state
 *        -> screen repaints                  // the visible actuation
 *        -> {type:'tool_result', content}
 *
 * That is the whole point of the architecture: the agent is genuinely outside
 * the page, and the only thing crossing the boundary is a tool name plus JSON.
 */

import { AGENT_WS_URL } from '../api';
import { listTools, executeTool, readToolResult } from '../webmcp-polyfill';

export function createAgentClient({ onEvent, onStatus }) {
  let socket = null;
  let closedByUs = false;

  const emit = (event) => onEvent?.(event);
  const status = (next, detail) => onStatus?.(next, detail);

  function connect() {
    return new Promise((resolve, reject) => {
      closedByUs = false;
      status('connecting');

      let ws;
      try {
        ws = new WebSocket(AGENT_WS_URL);
      } catch (err) {
        status('offline', err?.message);
        reject(err);
        return;
      }
      socket = ws;

      ws.onmessage = (raw) => {
        let message;
        try {
          message = JSON.parse(raw.data);
        } catch {
          return;
        }
        if (message.type === 'ready') {
          status('online', message);
          resolve(message);
          return;
        }
        handle(message, ws);
      };

      ws.onerror = () => {
        // The browser gives no detail on WS failures for security reasons; the
        // most likely cause by far is that the backend isn't running.
        status('offline', 'Could not open the agent WebSocket.');
      };

      ws.onclose = () => {
        socket = null;
        if (!closedByUs) status('offline', 'The agent connection closed.');
        reject(new Error('closed'));
      };
    });
  }

  async function handle(message, ws) {
    switch (message.type) {
      case 'text_delta':
        emit({ kind: 'text_delta', text: message.text });
        break;

      case 'turn_status':
        emit({ kind: 'status', status: message.status });
        break;

      case 'tool_use': {
        emit({
          kind: 'call',
          tool: message.name,
          args: message.input,
          text: `${message.name}(${compact(message.input)})`,
        });

        // Execute through WebMCP. Never through the DOM.
        const result = await executeTool(message.name, message.input || {});
        const payload = readToolResult(result);
        const isError = !!result?.isError;

        emit({
          kind: 'observation',
          tool: message.name,
          isError,
          text: isError ? `error: ${payload?.error ?? 'unknown'}` : summarise(payload),
          payload,
        });

        if (ws.readyState === WebSocket.OPEN) {
          ws.send(
            JSON.stringify({
              type: 'tool_result',
              tool_use_id: message.id,
              content: JSON.stringify(payload ?? null),
              is_error: isError,
            })
          );
        }
        break;
      }

      // Server tools never reach this browser as a tool_use — they run in the
      // backend. These events exist so the user can still see them happening.
      case 'server_tool':
        emit({
          kind: message.phase === 'start' ? 'server_call' : 'server_done',
          tool: message.name,
          text:
            message.phase === 'start'
              ? `${message.name}(${compact(message.input)})`
              : message.summary,
          isError: !!message.is_error,
        });
        break;

      case 'server_tool_progress':
        emit({ kind: 'server_progress', tool: message.name, text: message.message });
        break;

      case 'usage':
        emit({ kind: 'usage', usage: message.usage, model: message.model });
        break;

      case 'error':
        emit({ kind: 'error', text: message.message });
        break;

      case 'turn_end':
        emit({ kind: 'turn_end', stopReason: message.stop_reason });
        break;

      case 'reset_ok':
        emit({ kind: 'reset_ok' });
        break;

      default:
        break;
    }
  }

  function send(text) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      emit({ kind: 'error', text: 'Not connected to the agent backend.' });
      emit({ kind: 'turn_end', stopReason: 'error' });
      return false;
    }
    // The live tool list travels with every message. It genuinely changes as
    // the user navigates, which is the capability discovery WebMCP is for.
    socket.send(JSON.stringify({ type: 'user_message', text, tools: listTools() }));
    return true;
  }

  function resetConversation() {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'reset' }));
    }
  }

  function close() {
    closedByUs = true;
    socket?.close();
    socket = null;
  }

  return { connect, send, resetConversation, close };
}

function compact(value) {
  const json = JSON.stringify(value ?? {});
  return json.length > 120 ? `${json.slice(0, 117)}...` : json;
}

function summarise(payload) {
  if (payload == null) return 'null';
  if (Array.isArray(payload.contracts)) {
    const names = payload.contracts.slice(0, 4).map((c) => c.id);
    const more = payload.contracts.length - names.length;
    return `${payload.contracts.length} match${payload.contracts.length === 1 ? '' : 'es'}` +
      (names.length ? ` [${names.join(', ')}${more > 0 ? `, +${more}` : ''}]` : '');
  }
  if (payload.contract?.id) {
    return `${payload.contract.id} ${payload.contract.insured_company ?? ''}`.trim();
  }
  return compact(payload);
}
