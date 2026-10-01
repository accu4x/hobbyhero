"""Assemble the single-file artifact page from src/template.html, src/app.css, src/app.js,
src/engine.js, src/logo.datauri and snapshot.json -> dist/hobby-hero.html
(SPEC-artifact-v0 Phase 3, 2026-09-24; v0.2 adds the logo; SPEC-site-edition Phase 1,
2026-10-01, takes the style and the app script out of the template)."""
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"


def source(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8").strip("\n")


snap = json.loads((HERE / "snapshot.json").read_text(encoding="utf-8"))
# The snapshot goes in last, so nothing inside the data is ever read as a placeholder.
parts = {
    "/*__CSS__*/": source("app.css"),
    "/*__ENGINE__*/": source("engine.js"),
    "/*__APP__*/": source("app.js"),
    "/*__LOGO__*/": source("logo.datauri").strip(),
    "/*__SNAPSHOT__*/": json.dumps(snap, separators=(",", ":")).replace("</", "<\\/"),
}
page = (SRC / "template.html").read_text(encoding="utf-8")
for placeholder, value in parts.items():
    if page.count(placeholder) != 1:
        raise SystemExit(f"template.html must hold {placeholder} exactly once")
    page = page.replace(placeholder, value)
(HERE / "dist").mkdir(exist_ok=True)
out = HERE / "dist" / "hobby-hero.html"
out.write_text(page, encoding="utf-8")
print(out, out.stat().st_size // 1024, "KB")
