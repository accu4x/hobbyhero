"""Assemble the single-file artifact page from src/template.html, src/engine.js,
src/logo.datauri and snapshot.json -> dist/hobby-hero.html
(SPEC-artifact-v0 Phase 3, 2026-09-24; v0.2 adds the logo)."""
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
snap = json.loads((HERE / "snapshot.json").read_text(encoding="utf-8"))
page = (HERE / "src" / "template.html").read_text(encoding="utf-8")
engine = (HERE / "src" / "engine.js").read_text(encoding="utf-8")
logo = (HERE / "src" / "logo.datauri").read_text(encoding="utf-8").strip()
page = page.replace("/*__SNAPSHOT__*/", json.dumps(snap, separators=(",", ":")).replace("</", "<\\/"))
page = page.replace("/*__ENGINE__*/", engine)
page = page.replace("/*__LOGO__*/", logo)
(HERE / "dist").mkdir(exist_ok=True)
out = HERE / "dist" / "hobby-hero.html"
out.write_text(page, encoding="utf-8")
print(out, out.stat().st_size // 1024, "KB")
