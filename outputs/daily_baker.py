"""The Daily Baker: collect, draft, review, and export editorial satire."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
DEFAULT_STATE = ROOT.parent / "work" / "daily-baker"


class PlainText(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.parts = []
        self.feed(markup)

    def __str__(self):
        return " ".join(" ".join(self.parts).split())

    def handle_data(self, data):
        self.parts.append(data)


def collect(feeds):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    with ThreadPoolExecutor(max_workers=8) as workers:
        for articles in workers.map(lambda source: list(collect_source(source, cutoff)), feeds):
            yield from articles


def collect_source(source, cutoff):
    # Keep existing string-based feed configurations usable.
    if isinstance(source, str):
        source = {"url": source, "publisher": urlsplit(source).hostname}
    site = source.get("site")
    direct = source.get("url")
    fallback = "https://news.google.com/rss/search?" + urlencode({
        "q": f"site:{site} when:2d", "hl": "en", "gl": "US", "ceid": "US:en",
    })
    for feed in filter(None, (direct, fallback if site else None)):
        accepted = 0
        try:
            with urlopen(Request(feed, headers={"User-Agent": "TheDailyBaker/1.0"}), timeout=12) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000 or re.search(br"<!\s*(DOCTYPE|ENTITY)", raw, re.I):
                raise ValueError("Unsupported feed document")
            channel = ElementTree.fromstring(raw).find("channel")
            if channel is None:
                raise ValueError("Expected an RSS channel")
            for item in channel.findall("item"):
                link = (item.findtext("link") or "").strip()
                try:
                    published = parsedate_to_datetime(item.findtext("pubDate") or "")
                    if published.tzinfo is None:
                        continue
                    host = urlsplit(link).hostname
                except ValueError:
                    continue
                if not cutoff <= published <= datetime.now(timezone.utc) or not host or urlsplit(link).scheme != "https":
                    continue
                title = str(PlainText(item.findtext("title") or ""))
                summary = str(PlainText(item.findtext("description") or ""))
                if feed != direct:
                    attribution = item.find("source")
                    if attribution is None:
                        continue
                    origin = urlsplit(attribution.get("url", "")).hostname or ""
                    domain = site.split("/")[0]
                    if origin != domain and not origin.endswith("." + domain):
                        continue
                    label = (attribution.text or "").strip()
                    if label and title.endswith(f" - {label}"):
                        title = title[:-(len(label) + 3)]
                    summary = ""
                if not title:
                    continue
                accepted += 1
                yield {
                    "publisher": source["publisher"],
                    "channel": source.get("channel", source["publisher"]),
                    "url": link,
                    "title": title,
                    "summary": summary,
                }
        except (OSError, ValueError, ElementTree.ParseError) as error:
            print(f"Feed unavailable ({feed}): {error}")
        if accepted:
            return


def select(articles, topics):
    candidates = []
    sensitive = re.compile(r"\b(killed|deaths?|dead|massacre|shooting|earthquake|hostages?|genocide|war crimes?)\b", re.I)
    for topic in topics:
        sides = [[], []]
        for article in articles:
            story = f'{article["title"]} {article["summary"]}'
            if sensitive.search(story) or not re.search(topic["match"], story, re.I):
                continue
            matches = [i for i, (_, pattern) in enumerate(topic["sides"]) if re.search(pattern, story, re.I)]
            if len(matches) == 1:
                sides[matches[0]].append(article)
        pairs = [(a, b) for a in sides[0] for b in sides[1]
                 if a["publisher"] != b["publisher"] and (not a["url"] or a["url"] != b["url"])
                 and a["title"].casefold() != b["title"].casefold()]
        if pairs:
            coverage = len({item["publisher"] for side in sides for item in side})
            candidates.append((coverage, topic, pairs[0]))
    return max(candidates, key=lambda candidate: candidate[0], default=None)


def render(topic, sides, jokes, index, path, sources, solution):
    palettes = [
        ("#132527", "#aee9c5", "#f9ce87"),
        ("#242238", "#c8bbff", "#ffb9ce"),
        ("#272118", "#ffe098", "#bfe0ff"),
    ]
    background, left, right = palettes[index]
    image = Image.new("RGB", (1080, 1080), background)
    draw = ImageDraw.Draw(image)

    def centered(value, y, size, width, x=540, color="white"):
        font = ImageFont.load_default(size=size)
        words = value.split()
        lines = [words.pop(0)]
        for word in words:
            trial = f"{lines[-1]} {word}"
            if draw.textlength(trial, font=font) <= width:
                lines[-1] = trial
            else:
                lines.append(word)
        draw.multiline_text((x, y), "\n".join(lines), fill=color, font=font, anchor="mm", align="center", spacing=12)

    centered("THE DAILY BAKER   /   EDITORIAL SATIRE", 125, 33, 970)
    centered(topic, 266, 84, 960)
    for x, color, side, joke in zip((60, 550), (left, right), sides, jokes):
        draw.rounded_rectangle((x, 408, x + 470, 765), radius=30, fill=color)
        centered(side, 485, 34, 410, x + 235, background)
        centered(joke, 624, 54, 398, x + 235, background)
    if solution:
        draw.rounded_rectangle((60, 789, 1020, 956), radius=26, fill="#ffffff")
        centered("PROPOSED SOLUTION  /  THE DAILY BAKER", 831, 25, 900, color=background)
        centered(solution, 899, 36, 880, color=background)
    credits = "  /  ".join(source.get("channel", source["publisher"]) for source in sources)
    centered("Source coverage: " + credits, 995, 25, 960)
    image.save(path)


def demo():
    return [
        {"publisher": "Demo outlet A", "url": "", "title": "Congestion pricing cuts congestion",
         "summary": "Synthetic coverage for a local demo."},
        {"publisher": "Demo outlet B", "url": "", "title": "Congestion pricing is unfair to drivers",
         "summary": "Synthetic coverage for a local demo."},
    ]


def dashboard(db, state):
    sections = []
    for identifier, status, raw in db.execute("SELECT id, status, data FROM drafts ORDER BY rowid DESC"):
        data = json.loads(raw)
        sources = []
        for label, source in zip(data["sides"], data["sources"]):
            title = escape(source["title"])
            if source["url"]:
                title = f'<a href="{escape(source["url"], quote=True)}">{title}</a>'
            sources.append(f'<li><b>{escape(label)}</b> · {escape(source.get("channel", source["publisher"]))} · {title}<p>{escape(source["summary"])}</p></li>')
        solution = data.get("solution")
        solution_html = f'<p><b>Proposed solution</b> · {escape(solution)}</p>' if solution else ""
        mode = " · demo" if data["demo"] else ""
        sections.append(f'<article><img src="{identifier}.png" alt="{escape(data["alt"], quote=True)}">'
                        f'<div><h2>{escape(data["topic"])}</h2><code>{identifier}</code> · {status}{mode}'
                        f'<ul>{"".join(sources)}</ul><p>{escape(data["caption"])}</p>{solution_html}</div></article>')
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>The Daily Baker</title><style>body{font:17px system-ui;background:#f4efe7;color:#20282a;max-width:1050px;'
            'margin:50px auto;padding:0 20px}article{display:flex;flex-wrap:wrap;gap:26px;background:white;padding:22px;'
            'margin:22px 0;border-radius:16px}article div{flex:1;min-width:240px}img{width:min(100%,360px);'
            'align-self:start;border-radius:9px}p{line-height:1.45}</style><h1>The Daily Baker</h1>'
            '<p>Review the linked coverage and copy before using the approve or reject command.</p>'
            + ("".join(sections) or "<p>No drafts yet.</p>"))
    (state / "review.html").write_text(page, encoding="utf-8")


def generate(db, state, settings, is_demo):
    result = select(list(demo() if is_demo else collect(settings["feeds"])), settings["topics"])
    if result is None:
        print("No supported debate with opposing evidence from two publishers.")
        return
    _, topic, sources = result
    name = topic["name"]
    sides = [label for label, _ in topic["sides"]]
    solution = topic.get("solution", "")
    for index, jokes in enumerate(topic["jokes"]):
        data = {"topic": name, "sides": sides, "sources": sources, "demo": is_demo, "solution": solution,
                "caption": f"Editorial satire | {name} — {sides[0]} and {sides[1]}.",
                "alt": f"Two panels on {name}. " + " ".join(f"{side}: {joke}" for side, joke in zip(sides, jokes))}
        if solution:
            data["alt"] += f" Proposed solution from The Daily Baker: {solution}"
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
        identifier = sha256(f"{date.today()}:credits-v1:{index}:{raw}".encode()).hexdigest()[:16]
        if db.execute("SELECT 1 FROM drafts WHERE id = ?", (identifier,)).fetchone():
            continue
        render(name, sides, jokes, index, state / f"{identifier}.png", sources, solution)
        db.execute("INSERT INTO drafts VALUES (?, 'pending', ?)", (identifier, raw))
        print(f"Pending: {identifier}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest="command", required=True)
    generate_command = commands.add_parser("generate")
    generate_command.add_argument("--demo", action="store_true")
    for action in ("approve", "reject", "export"):
        commands.add_parser(action).add_argument("id")
    commands.add_parser("review")
    args = parser.parse_args()
    state = args.state.resolve()
    state.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(state / "queue.sqlite3", timeout=30) as db:
        db.execute("CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, status TEXT, data TEXT)")
        if args.command == "generate":
            settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
            db.execute("BEGIN IMMEDIATE")
            generate(db, state, settings, args.demo)
        elif args.command in ("approve", "reject"):
            status = {"approve": "approved", "reject": "rejected"}[args.command]
            changed = db.execute("UPDATE drafts SET status = ? WHERE id = ? AND status = 'pending'",
                                 (status, args.id))
            if not changed.rowcount:
                parser.exit(1, "Draft does not exist or was already reviewed.\n")
        elif args.command == "export":
            row = db.execute("SELECT data FROM drafts WHERE id = ? AND status = 'approved'", (args.id,)).fetchone()
            if not row:
                parser.exit(1, "Select an approved draft to export.\n")
            if json.loads(row[0])["demo"]:
                parser.exit(1, "Demo drafts are for review only.\n")
            data = json.loads(row[0])
            citations = [
                f'{source.get("channel", source["publisher"])} — {source["title"]}\n{source["url"]}'
                for source in data["sources"]
            ]
            post_copy = data["caption"]
            if data.get("solution"):
                post_copy += f'\n\nProposed solution from The Daily Baker\n{data["solution"]}'
            post_copy += "\n\nSource coverage\n" + "\n\n".join(citations) + "\n"
            import zipfile
            target = ROOT / f"post-{args.id}.zip"
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(state / f"{args.id}.png", "meme.png")
                archive.writestr("post.json", row[0])
                archive.writestr("caption.txt", post_copy)
            print(target)
        dashboard(db, state)
    print(state / "review.html")


if __name__ == "__main__":
    main()

