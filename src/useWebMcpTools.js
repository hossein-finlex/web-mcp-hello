import { useEffect, useRef } from 'react';
import { registerTool } from './webmcp-polyfill';

/**
 * Register a set of WebMCP tools for the lifetime of a component.
 *
 * THE TRAP THIS SOLVES
 * --------------------
 * The obvious implementation is wrong:
 *
 *     useEffect(() => {
 *       const h = registerTool({ name: 'x', execute: () => doThingWith(items) });
 *       return () => h.unregister();
 *     }, []);                     // <-- `items` is frozen at mount forever
 *
 * ...and re-registering on every state change is also wrong: the browser would
 * see the whole tool set churn on every keystroke, and an in-flight tool call
 * could be yanked out from under the agent.
 *
 * So: register ONCE with a stable indirection. The registered `execute` looks
 * up the handler in a ref that every render refreshes, so handlers always close
 * over current state while the *registration* stays stable.
 *
 * @param {Array<{name: string, description: string, inputSchema: object,
 *                execute: (args: object) => Promise<any>}>} tools
 *   Tool names and schemas must be static; handlers may change every render.
 */
export function useWebMcpTools(tools) {
  const latest = useRef(tools);
  latest.current = tools; // refreshed on every render -- always current state

  useEffect(() => {
    const handles = latest.current.map(({ name, description, inputSchema }) =>
      registerTool({
        name,
        description,
        inputSchema,
        // Late-bound: resolve the handler at call time, not at register time.
        execute: (args) => {
          const tool = latest.current.find((t) => t.name === name);
          if (!tool) throw new Error(`Tool "${name}" is no longer available`);
          return tool.execute(args ?? {});
        },
      })
    );

    return () => handles.forEach((h) => h.unregister());
    // Intentionally empty: registration is a mount/unmount concern.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
