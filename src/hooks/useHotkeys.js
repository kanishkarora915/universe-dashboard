import { useEffect } from "react";

/**
 * Global keyboard shortcuts.
 * @param {Object} handlers - { [combo]: fn }
 *   combo examples: "cmd+k", "ctrl+k", "escape", "1", "?", "cmd+shift+l", "f"
 */
export function useHotkeys(handlers, { enabled = true } = {}) {
  useEffect(() => {
    if (!enabled) return;

    const onKey = (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      const typing = tag === "input" || tag === "textarea" || e.target.isContentEditable;

      const key = e.key.toLowerCase();
      const mods = [];
      if (e.ctrlKey) mods.push("ctrl");
      if (e.metaKey) mods.push("cmd");
      if (e.shiftKey) mods.push("shift");
      if (e.altKey) mods.push("alt");

      // Prefer cmd over ctrl: if cmd held, don't register ctrl combo
      if (e.metaKey) {
        const idx = mods.indexOf("ctrl");
        if (idx >= 0) mods.splice(idx, 1);
      }

      const combo = [...mods, key].join("+");
      const comboAlt = [...mods.map((m) => (m === "cmd" ? "ctrl" : m)), key].join("+"); // cmd+k also matches ctrl+k

      // Find matching handler (exact match)
      let handler = handlers[combo] || handlers[comboAlt];

      // For plain keys (no modifiers), skip if typing in input
      if (handler && mods.length === 0 && typing) {
        // allow escape always
        if (key !== "escape") return;
      }

      if (handler) {
        e.preventDefault();
        handler(e);
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handlers, enabled]);
}
