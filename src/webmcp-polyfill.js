/**
 * webmcp-polyfill.js
 * ------------------------------------------------------------------
 * WebMCP ("Web Model Context Protocol") exposes `navigator.modelContext`
 * so a page can hand *structured tools* to an AI agent instead of forcing
 * the agent to scrape the DOM.
 *
 * Native support is not shipped everywhere yet, so this file does two jobs:
 *
 *  1. PAGE SIDE (the actual polyfill)
 *     If `navigator.modelContext` is missing, install a stub implementing the
 *     proposed surface -- registerTool / unregisterTool / provideContext --
 *     which simply logs every registration + invocation to the dev console.
 *     The app never crashes and you can watch the protocol traffic in DevTools.
 *
 *  2. AGENT SIDE (a local bridge, for this demo only)
 *     A real agent lives outside the page: the browser hands it the tool list
 *     and marshals `callTool` back into the page. There is no page-facing API
 *     for "be the agent", so this module keeps a *mirror* of every registered
 *     tool and exposes `listTools()` / `executeTool()` on top of it. The
 *     simulated assistant in AssistantChat.js uses that bridge and nothing
 *     else -- no querySelector, no fragile selectors, no DOM knowledge.
 *
 * The mirror is maintained whether the browser is native or polyfilled, so the
 * demo behaves identically in both cases.
 * ------------------------------------------------------------------
 */

/** name -> full tool descriptor (including its execute handler) */
const mirror = new Map();

/** Subscribers watching the registry (used by the WebMCP inspector panel). */
const registryObservers = new Set();

/** Subscribers watching tool traffic (call / result / error). */
const trafficObservers = new Set();

let callCounter = 0;

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

/** Wrap a plain JS value in the MCP tool-result shape. */
export function toolResult(data, text) {
  return {
    content: [{ type: 'text', text: text ?? JSON.stringify(data) }],
    structuredContent: data,
  };
}

/** Wrap an error in the MCP tool-result shape. */
export function toolError(message) {
  return {
    isError: true,
    content: [{ type: 'text', text: message }],
    structuredContent: { error: message },
  };
}

/** Agent-side: pull the payload back out of an MCP tool result. */
export function readToolResult(result) {
  if (!result) return null;
  if (result.structuredContent !== undefined) return result.structuredContent;
  const text = result.content?.find((c) => c.type === 'text')?.text;
  if (typeof text !== 'string') return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function notifyRegistry() {
  const snapshot = listTools();
  registryObservers.forEach((fn) => fn(snapshot));
}

function notifyTraffic(event) {
  trafficObservers.forEach((fn) => fn(event));
}

/* ------------------------------------------------------------------ */
/* 1. The polyfill stub for navigator.modelContext                     */
/* ------------------------------------------------------------------ */

function createStub() {
  const registered = new Map();

  const log = (...args) =>
    // eslint-disable-next-line no-console
    console.info('%c[webmcp-polyfill]', 'color:#7c5cff;font-weight:600', ...args);

  return {
    __polyfilled: true,

    registerTool(tool) {
      registered.set(tool.name, tool);
      log('registerTool', tool.name, {
        description: tool.description,
        inputSchema: tool.inputSchema,
      });
      return {
        unregister: () => {
          registered.delete(tool.name);
          log('unregister', tool.name);
        },
      };
    },

    unregisterTool(name) {
      registered.delete(name);
      log('unregisterTool', name);
    },

    /** Spec-shaped bulk registration: replaces the whole tool set. */
    provideContext({ tools = [] } = {}) {
      registered.clear();
      tools.forEach((t) => registered.set(t.name, t));
      log('provideContext', tools.map((t) => t.name));
    },

    /** Not in the spec -- handy for poking at the page from the console. */
    getRegisteredTools() {
      return Array.from(registered.values());
    },

    async callTool(name, args = {}) {
      const tool = registered.get(name);
      if (!tool) throw new Error(`[webmcp-polyfill] unknown tool: ${name}`);
      log('callTool', name, args);
      const result = await tool.execute(args);
      log('callTool result', name, result);
      return result;
    },
  };
}

let installed = false;

export function installWebMcpPolyfill() {
  if (installed) return navigator.modelContext;
  installed = true;

  const native =
    typeof navigator !== 'undefined' && navigator.modelContext
      ? navigator.modelContext
      : null;

  if (native && typeof native.registerTool === 'function') {
    // eslint-disable-next-line no-console
    console.info(
      '%c[webmcp]%c native navigator.modelContext detected -- using it.',
      'color:#20b26c;font-weight:600',
      'color:inherit'
    );
  } else {
    const stub = createStub();
    try {
      Object.defineProperty(navigator, 'modelContext', {
        value: stub,
        configurable: true,
        writable: true,
      });
    } catch {
      // Some engines expose Navigator props as non-configurable getters.
      try {
        navigator.modelContext = stub;
      } catch {
        // Last resort: keep it on window so registerTool() below still works.
        window.modelContext = stub;
      }
    }
    // eslint-disable-next-line no-console
    console.info(
      '%c[webmcp]%c no native support -- polyfill installed. Tool traffic is logged here.',
      'color:#e8a33d;font-weight:600',
      'color:inherit'
    );
  }

  // Console playground: try `await webmcp.listTools()` in DevTools.
  window.webmcp = { listTools, executeTool, isNative: isNativeModelContext };

  return getModelContext();
}

function getModelContext() {
  return (
    (typeof navigator !== 'undefined' && navigator.modelContext) ||
    window.modelContext ||
    null
  );
}

export function isNativeModelContext() {
  const mc = getModelContext();
  return !!mc && !mc.__polyfilled;
}

/* ------------------------------------------------------------------ */
/* 2. Page side: registration (mirrored + forwarded to the browser)     */
/* ------------------------------------------------------------------ */

/**
 * Register one tool with the browser and with the local mirror.
 * Returns a handle with `unregister()` -- call it from a useEffect cleanup.
 */
export function registerTool(descriptor) {
  const { name, description, inputSchema, execute } = descriptor;

  if (!name || typeof execute !== 'function') {
    throw new Error('registerTool requires { name, execute }');
  }

  mirror.set(name, { name, description, inputSchema, execute });

  let nativeHandle = null;
  const mc = getModelContext();
  if (mc && typeof mc.registerTool === 'function') {
    try {
      nativeHandle = mc.registerTool({ name, description, inputSchema, execute });
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn('[webmcp] browser rejected registerTool', name, err);
    }
  }

  notifyRegistry();

  return {
    unregister() {
      mirror.delete(name);
      if (nativeHandle && typeof nativeHandle.unregister === 'function') {
        nativeHandle.unregister();
      } else if (mc && typeof mc.unregisterTool === 'function') {
        mc.unregisterTool(name);
      }
      notifyRegistry();
    },
  };
}

/* ------------------------------------------------------------------ */
/* 3. Agent side: discovery + invocation                               */
/* ------------------------------------------------------------------ */

/** What an agent sees: names, descriptions and JSON Schemas. No handlers. */
export function listTools() {
  return Array.from(mirror.values()).map(({ name, description, inputSchema }) => ({
    name,
    description,
    inputSchema,
  }));
}

/** Invoke a tool by name. Resolves to an MCP tool result. */
export async function executeTool(name, args = {}) {
  const tool = mirror.get(name);
  const id = ++callCounter;

  if (!tool) {
    const message = `Unknown tool: ${name}`;
    notifyTraffic({ id, name, args, phase: 'error', message });
    return toolError(message);
  }

  notifyTraffic({ id, name, args, phase: 'call' });

  try {
    const result = await tool.execute(args);
    notifyTraffic({ id, name, args, phase: 'result', result });
    return result;
  } catch (err) {
    const message = err?.message ?? String(err);
    notifyTraffic({ id, name, args, phase: 'error', message });
    return toolError(message);
  }
}

/* ------------------------------------------------------------------ */
/* 4. Observability (drives the on-screen inspector)                   */
/* ------------------------------------------------------------------ */

export function subscribeToRegistry(fn) {
  registryObservers.add(fn);
  fn(listTools());
  return () => registryObservers.delete(fn);
}

export function subscribeToTraffic(fn) {
  trafficObservers.add(fn);
  return () => trafficObservers.delete(fn);
}
