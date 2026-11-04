"""The web page is complete: every file it references exists, the manifest is valid, the server serves exactly these files."""
import json, os, re

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "murmure", "web")
fails = 0


def check(label, ok):
    global fails
    if not ok:
        fails += 1
        print(f"KO {label}")


html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
for ref in re.findall(r'(?:src|href)="([^"#][^"]*)"', html):  # files only: "#id" is an inside reference
    check(f"index.html references an existing file: {ref}", os.path.exists(os.path.join(WEB, ref)))
for ref in re.findall(r'href="#(\w+)"', html):
    check(f"index.html references an existing symbol: #{ref}", f'id="{ref}"' in html)
manifest = json.load(open(os.path.join(WEB, "manifest.webmanifest"), encoding="utf-8"))
check("manifest has a name and icons", bool(manifest.get("name")) and bool(manifest.get("icons")))
server = open(os.path.join(os.path.dirname(WEB), "server.py"), encoding="utf-8").read()
for name in re.findall(r'": "([\w.]+)"', server[server.index("WEB_FILES"):server.index("WEB_TYPES")]):
    check(f"server serves an existing file: {name}", os.path.exists(os.path.join(WEB, name)))
js = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()
for el_id in re.findall(r'\$\("(\w+)"\)', js):
    check(f"app.js uses an element that exists: #{el_id}", f'id="{el_id}"' in html)

print("tous les tests OK" if not fails else f"{fails} failure(s)")
raise SystemExit(1 if fails else 0)
