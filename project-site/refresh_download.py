"""Refresh the static download button from published GitHub release metadata."""
import html
import json
from pathlib import Path
import re
import urllib.request

url = 'https://api.github.com/repos/furious-pixel/mw2-enhanced-renderer-mod/releases?per_page=100'
request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json'})
with urllib.request.urlopen(request, timeout=30) as response:
    releases = json.load(response)
release = max((r for r in releases if not r['draft'] and r['published_at']),
              key=lambda r: r['published_at'])
assets = [a for a in release['assets'] if re.search(r'windows[-_]x64\.zip$', a['name'], re.I)]
if len(assets) != 1:
    raise SystemExit('Newest release must have exactly one Windows x64 ZIP; review asset naming.')
asset = assets[0]
expected = 'https://github.com/furious-pixel/mw2-enhanced-renderer-mod/releases/download/'
if not asset['browser_download_url'].startswith(expected):
    raise SystemExit('Unexpected download URL')
label = f"Download {release['tag_name']} · Windows x64 ZIP"
if release['prerelease']:
    label += ' (prerelease)'
button = f'<a id="release-download" class="button" href="{html.escape(asset["browser_download_url"], quote=True)}">{html.escape(label)}</a>'
page = Path(__file__).parent / 'public/index.html'
text, count = re.subn(r'<a id="release-download"[^>]*>.*?</a>', lambda _: button,
                      page.read_text(encoding='utf-8'))
if count != 1:
    raise SystemExit('Expected exactly one download button')
page.write_text(text, encoding='utf-8', newline='\n')
print(label)
