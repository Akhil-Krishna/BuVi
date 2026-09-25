"use client";

import { sql } from "@codemirror/lang-sql";
import { keymap } from "@codemirror/view";
import { createTheme } from "@uiw/codemirror-themes";
import CodeMirror from "@uiw/react-codemirror";
import { tags as t } from "@lezer/highlight";

/**
 * CodeMirror 6 (Section 5's stack matrix leaves the SQL editor unnamed, but
 * a plain textarea has no line numbers or syntax highlighting -- both
 * expected of a "professional-looking" SQL Lab, matching the Stitch
 * reference's own code-editor chrome). Line numbers, bracket matching,
 * active-line highlight, and search all come from `basicSetup` (on by
 * default); only the theme is custom, built from this app's own tokens
 * rather than a bundled dark theme, since Section 5.1 is single-mode light.
 */
const buviLightTheme = createTheme({
  theme: "light",
  settings: {
    background: "#ffffff",
    foreground: "#12213a",
    caret: "#1e4fb8",
    selection: "#ebf3fc",
    selectionMatch: "#ebf3fc",
    lineHighlight: "#f5f7fa",
    gutterBackground: "#f5f7fa",
    gutterForeground: "#5b6b82",
    gutterBorder: "#d9dee4",
    fontFamily: "var(--font-mono)",
  },
  styles: [
    { tag: t.keyword, color: "#1e4fb8", fontWeight: "600" },
    { tag: [t.name, t.deleted, t.character, t.propertyName, t.macroName], color: "#12213a" },
    { tag: [t.function(t.variableName), t.labelName], color: "#0e7cd6" },
    { tag: [t.string, t.special(t.string)], color: "#1e8e5a" },
    { tag: [t.number], color: "#b7791f" },
    { tag: [t.comment], color: "#5b6b82", fontStyle: "italic" },
    { tag: [t.operator, t.punctuation], color: "#5b6b82" },
  ],
});

export function SqlEditor({
  value,
  onChange,
  onRun,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  onRun: () => void;
  placeholder?: string;
}) {
  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      theme={buviLightTheme}
      extensions={[
        sql(),
        keymap.of([
          {
            key: "Mod-Enter",
            run: () => {
              onRun();
              return true;
            },
          },
        ]),
      ]}
      placeholder={placeholder}
      height="240px"
      basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true }}
      className="text-sm"
    />
  );
}
