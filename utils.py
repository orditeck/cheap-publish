import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from distutils.util import strtobool
from inspect import getmembers, isfunction
from os import environ
from pathlib import Path
from pprint import PrettyPrinter
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import quote, unquote

from requests.structures import CaseInsensitiveDict
from slugify import slugify
import yaml

import metadata_handlers

site_dir = Path(__file__).parent.absolute() / "build"
originals_dir = site_dir / "__originals"
formatted_dir = site_dir / "content"


# ---------------------------------------------------------------------------- #
#                                 General Utils                                #
# ---------------------------------------------------------------------------- #

def to_prerender_links(links: List[str]) -> str:
    """Converts links to prerender links"""
    x = ''.join([f'<link rel="prerender" href="{link}" as="document"/>\n' for link in links])
    print(x)
    return x


# Pretty printer
pp = PrettyPrinter(indent=4, compact=False).pprint


def convert_metadata_to_html(metadata: dict) -> str:
    """Convert yaml metadata to HTML depending on metadata type"""
    parsed_metadata = ""
    handlers = get_metadata_handlers()

    for metadata_key in metadata:
        if metadata_key.lower() in [name for name, _ in handlers]:
            func = [func for name, func in handlers if name.lower() == metadata_key.lower()][0]
            parsed_metadata += str(func(metadata[metadata_key])).strip().replace("\n", " ") + "\n"
    return parsed_metadata


def get_metadata_handlers():
    return [(name, func) for name, func in getmembers(metadata_handlers, isfunction) if not name.startswith("_")]


# print(convert_metadata_to_html({"modified": "2021-07-01 12:00:00", "tags": ["tag1", "tag2"], "button": "button1",
#                                 "source"  : "https://www.google.com"}))


def slugify_path(path: Union[str, Path], no_suffix: bool, lowercase = False, fix_md = False) -> Path:
    """Slugifies every component of a path. Note that '../xxx' will get slugified to '/xxx'. Always use absolute paths. `no_suffix=True` when path is URL or directory (slugify everything including extension)."""
    path = Path(str(path))  # .lower()
    if Settings.is_true("SLUGIFY"):
        if no_suffix:
            os_path = "/".join(slugify(item, lowercase=lowercase) for item in path.parts)
            name = ""
            suffix = ""
        else:
            os_path = "/".join(slugify(item, lowercase=lowercase) for item in str(path.parent).split("/"))
            if fix_md:
                path = Path(f"{str(path)}.md")
            name = ".".join(slugify(item, lowercase=lowercase) for item in path.stem.split("."))
            suffix = path.suffix

            if(fix_md):
                name = name.replace('.md', '')
                suffix = suffix.replace('.md', '')

        if name != "" and suffix != "":
            return Path(os_path) / f"{name}{suffix}"
        elif suffix == "":
            return Path(os_path) / name
        else:
            return Path(os_path)
    else:
        return path


# ---------------------------------------------------------------------------- #
#                               Document Classes                               #
# ---------------------------------------------------------------------------- #


@dataclass
class DocLink:
    """
    A class for internal links inside a Markdown document.
    [xxxx](yyyy<.md?>#zzzz)
    """

    combined: str
    title: str
    url: str
    md: str
    header: str

    @classmethod
    def get_links(cls, line: str) -> List["DocLink"]:
        r"""
        Factory method.
        Get non-http links [xxx](<!http>yyy<.md>#zzz).

        \[(.+?)\]: Captures title part (xxx).
        (?!http)(\S+?): Captures URL part (yyy) and discard URL that starts with http.
        (\.md)?: Captures ".md" extension (if any) to identify markdown files.
        (#\S+)?: Captures header part (#zzz).

        Returns:
            _type_: _description_
        """

        # Removed starting "[" and ending ")" such that we can identify inner links [...](...)

        return [
            cls(f"[{combined})", title, url, md, header)
            for combined, title, url, md, header in re.findall(
                r"\[((.*?)\]\((?!http)(\S*?)(\.md)?(#\S+)?)\)", line
            )
            if cls.no_inner_link(combined)
        ]

    @property
    def is_md(self) -> bool:
        """Link is a Markdown link."""
        return self.md != ""

    @staticmethod
    def no_inner_link(item: str) -> bool:
        """Check that capture link does not contain inner links."""
        return re.match(r"\[.*?\]\(\S*?\)", item) is None

    def abs_url(self, doc_path: "DocPath") -> str:
        """Returns an absolute URL based on quoted relative URL from obsidian-export."""

        if self.url is None or self.url == "":
            print(f"Empty link found: {doc_path.old_rel_path}")
            return "/404"

        try:
            new_rel_path = (
                (doc_path.new_path.parent / unquote(self.url))
                .resolve()
                .relative_to(formatted_dir)
            )
            print(f"new_rel_path1: {new_rel_path}")
            new_rel_path = quote(str(slugify_path(new_rel_path, False, False, self.is_md)))
            print(f"new_rel_path2: {new_rel_path}")

            return f"/{new_rel_path}"
        except Exception:
            print(f"Invalid link found: {doc_path.old_rel_path}")
            return "/404"

    @classmethod
    def parse(cls, line: str, doc_path: "DocPath") -> Tuple[str, List[str]]:
        """Parses and fixes all internal links in a line. Also returns linked paths for knowledge graph."""

        parsed = line
        linked: List[str] = []

        for link in cls.get_links(line):
            abs_url = link.abs_url(doc_path)

            if any(link.title.endswith(ext) for ext in (".webm", ".mp4")):
                # use shortcode for videos
                parsed = parsed.replace(
                    link.combined,
                    r"{{ " + f'video(url="{abs_url}", alt="{link.title}")' + r" }}",
                )
            else:
                parsed = parsed.replace(
                    link.combined, f"[{link.title}]({abs_url}{link.header})"
                )

            linked.append(abs_url)

        return parsed, linked


class DocPath:
    """
    A class for any path found in the exported Obsidian directory.
    Can be a section (folder), page (Markdown file) or resource (non-Markdown file).
    """

    def __init__(self, path: Path):
        """Path parsing."""
        self.old_path = path.resolve()
        self.old_rel_path = self.old_path.relative_to(originals_dir)
        new_rel_path = self.old_rel_path

        # Take care of cases where Markdown file has a sibling directory of the same name
        if self.is_md and (self.old_path.parent / self.old_path.stem).is_dir():
            print(f"Name collision with sibling folder, renaming: {self.old_rel_path}")
            new_rel_path = self.old_rel_path.parent / (
                    self.old_rel_path.stem + "-nested" + self.old_rel_path.suffix
            )

        self.new_rel_path = slugify_path(new_rel_path, not self.is_file)
        self.new_path = formatted_dir / str(self.new_rel_path)
        print(f"New path: {self.new_path}")

    # --------------------------------- Sections --------------------------------- #

    @property
    def section_title(self) -> str:
        """Gets the title of the section."""
        title = str(self.old_rel_path).replace('"', r"\"")
        return (
            title
            if (title != "" and title != ".")
            else Settings.options["ROOT_SECTION_NAME"] or "main"
        )

    @property
    def section_sidebar(self) -> str:
        """Gets the title of the section."""
        sidebar = str(self.old_rel_path)
        assert Settings.options["SUBSECTION_SYMBOL"] is not None
        section_symbol = Settings.options["SUBSECTION_SYMBOL"] if sidebar.count("/") > 0 else ""
        sidebar = (
                      section_symbol
                  ) + sidebar.split("/")[-1]

        print("sidebar", sidebar)
        return (
            sidebar
            if (sidebar != "" and sidebar != ".")
            else Settings.options["ROOT_SECTION_NAME"] or "main"
        )

    def write_to(self, child: str, content: Union[str, List[str]]):
        """Writes content to a child path under new path."""
        new_path = self.new_path / child
        new_path.parent.mkdir(parents=True, exist_ok=True)
        with open(new_path, "w") as f:
            if isinstance(content, str):
                f.write(content)
            else:
                f.write("\n".join(content))

    # ----------------------------------- Pages ---------------------------------- #

    @property
    def page_title(self) -> str:
        """Gets the title of the page."""

        # The replacement might not be necessary, filenames cannot contain double quotes
        title = " ".join(
            [
                #item if item[0].isupper() else item.title()
                item for item in self.old_path.stem.split(" ")
            ]
        ).replace('"', r"\"")
        return self.metadata("title") or title

    @property
    def is_md(self) -> bool:
        """Whether path points to a Markdown file."""
        return self.is_file and self.old_path.suffix == ".md"

    @property
    def created(self) -> datetime:
        """Gets first created time."""
        return self.metadata("created") or datetime.fromtimestamp(os.path.getmtime(self.old_path))

    @property
    def modified(self) -> datetime:
        """Gets last modified time."""
        return self.metadata("modified")

    @property
    def content(self) -> List[str]:
        """Gets the lines of the file but ignores the front matter."""
        with open(self.old_path, "r") as f:
            lines = f.readlines()
            if lines[0].startswith("---"):
                # find the end of the front matter
                for i, line in enumerate(lines[1:]):
                    if line.startswith("---"):
                        return lines[i + 2:]
            return lines
        # return [line for line in open(self.old_path, "r").readlines()]

    def metadata(self, __key: str) -> Union[str, Dict[str, str]]:
        """Gets the metadata of the file. Made up of the front matter and some file properties."""
        metadata = self.frontmatter
        return metadata.get(__key)

    @property
    def frontmatter(self) -> Dict[str, str]:
        """Gets the front matter of the file."""
        with open(self.old_path, "r") as f:
            lines = f.readlines()
            if lines[0].startswith("---"):
                # find the end of the front matter
                for i, line in enumerate(lines[1:]):
                    if line.startswith("---"):
                        return yaml.load("".join(lines[1:i + 1]),
                                         Loader=yaml.FullLoader)  # using yaml lib called pyyaml
            return {}
        # return [line for line in open(self.old_path, "r").readlines()]

    def write(self, content: Union[str, List[str]]):
        """Writes content to new path."""
        if not isinstance(content, str):
            content = "".join(content)
        self.new_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.new_path, "w") as f:
            f.write(content)

    # --------------------------------- Resources -------------------------------- #

    @property
    def is_file(self) -> bool:
        """Whether path points to a file."""
        return self.old_path.is_file()

    def copy(self):
        """Copies file from old path to new path."""
        self.new_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.old_path, self.new_path)

    # ----------------------------------- Graph ---------------------------------- #

    @property
    def abs_url(self) -> str:
        """Returns an absolute URL to the page."""
        assert self.is_md
        return quote(f"/{str(self.new_rel_path)[:-3]}")

    def edge(self, other: str) -> Tuple[str, str]:
        """Gets an edge from page's URL to another URL."""
        return tuple(sorted([self.abs_url, other]))


# ---------------------------------------------------------------------------- #
#                                   Settings                                   #
# ---------------------------------------------------------------------------- #


class Settings:
    """
    Changes to mutable class variable fields are broadcasted across all instances no matter where the change happens.
    The class object and all instances would receive the change no matter the setting method:
    - assign to Settings.default["xxx]
    - change cls.default["xxx"] inside class method
    - assign to instance.default["xxx"]
    - change self.default["xxx"] inside instance method
    """

    # Default options
    options: Dict[str, Optional[str]] = {
        "SITE_URL"             : None,
        "SITE_TITLE"           : "Someone's Second 🧠",
        "TIMEZONE"             : "Asia/Hong_Kong",
        "REPO_URL"             : None,
        "LANDING_PAGE"         : None,
        "LANDING_TITLE"        : "I love obsidian-zola! 💖",
        "SITE_TITLE_TAB"       : "",
        "LANDING_DESCRIPTION"  : "I have nothing but intelligence.",
        "LANDING_BUTTON"       : "Click to steal some👆",
        "SORT_BY"              : "title",
        "GANALYTICS"           : "",
        "SLUGIFY"              : "y",
        "HOME_GRAPH"           : "y",
        "PAGE_GRAPH"           : "y",
        "SUBSECTION_SYMBOL"    : "<div class='folder'><svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'><path d='M448 96h-172.1L226.7 50.75C214.7 38.74 198.5 32 181.5 32H64C28.65 32 0 60.66 0 96v320c0 35.34 28.65 64 64 64h384c35.35 0 64-28.66 64-64V160C512 124.7 483.3 96 448 96zM64 80h117.5c4.273 0 8.293 1.664 11.31 4.688L256 144h192c8.822 0 16 7.176 16 16v32h-416V96C48 87.18 55.18 80 64 80zM448 432H64c-8.822 0-16-7.176-16-16V240h416V416C464 424.8 456.8 432 448 432z' /></svg></div>",
        "LOCAL_GRAPH"          : "",
        "GRAPH_LINK_REPLACE"   : "",
        "STRICT_LINE_BREAKS"   : "",
        "SIDEBAR_COLLAPSED"    : "",
        "FOOTER"               : "",
        "ROOT_SECTION_NAME"    : "main",
        "COMMENTS_GISCUSS"     : "",
        "EDIT_PAGE"            : "",
        "EDIT_PAGE_BUTTON_TEXT": "Edit this page on Github",
        "REDIRECT_HOME"        : "",
        "GRAPH_OPTIONS"        : """
        {
        	nodes: {
        		shape: "dot",
        		color: isDark() ? "#8c8e91" : "#dee2e6",
        		font: {
        			face: "Inter",
        			color: isDark() ? "#c9cdd1" : "#616469",
        			strokeColor: isDark() ? "#c9cdd1" : "#616469",
        		},
        		scaling: {
        			label: {
        				enabled: true,
        			},
        		},
        	},
        	edges: {
        		color: { inherit: "both" },
        		width: 0.8,
        		smooth: {
        			type: "continuous",
        		},
        		hoverWidth: 4,
        	},
        	interaction: {
        		hover: true,
        	},
        	height: "100%",
        	width: "100%",
        	physics: {
        		stabilization: false,
        		solver: "repulsion",
        	},
        }
        """,
    }

    @classmethod
    def is_true(cls, key: str) -> bool:
        """Returns whether an option's string value is true."""
        val = cls.options[key]
        return bool(strtobool(val)) if val else False

    @classmethod
    def parse_env(cls):
        """
        Checks the env variables for required settings. Also stores the set variables.
        """

        for key in cls.options.keys():
            required = cls.options[key] is None

            if key in environ:
                cls.options[key] = environ[key]
            else:
                if required:
                    raise Exception(f"FATAL ERROR: build.environment.{key} not set!")
        if cls.options["SITE_TITLE_TAB"] == "":
            cls.options["SITE_TITLE_TAB"] = cls.options["SITE_TITLE"]
        print("Options:")
        pp(cls.options)

    @classmethod
    def sub_line(cls, line: str) -> str:
        """Substitutes variable placeholders in a line."""
        for key, val in cls.options.items():
            line = line.replace(f"___{key}___", val if val else "")
        return line

    @classmethod
    def sub_file(cls, path: Path):
        """Substitutes variable placeholders in a file."""
        content = "".join([cls.sub_line(line) for line in open(path, "r").readlines()])
        open(path, "w").write(content)


# ---------------------------------------------------------------------------- #
#                                Knowledge Graph                               #
# ---------------------------------------------------------------------------- #

PASTEL_COLORS = [
    # First tier
    "#FFADAD",
    "#FFD6A5",
    "#FDFFB6",
    "#CAFFBF",
    "#9BF6FF",
    "#A0C4FF",
    "#BDB2FF",
    "#FFC6FF",
    # Second tier
    "#FBF8CC",
    "#FDE4CF",
    "#FFCFD2",
    "#F1C0E8",
    "#CFBAF0",
    "#A3C4F3",
    "#90DBF4",
    "#8EECF5",
    "#98F5E1",
    "#B9FBC0",
    # Third tier
    "#EAE4E9",
    "#FFF1E6",
    "#FDE2E4",
    "#FAD2E1",
    "#E2ECE9",
    "#BEE1E6",
    "#F0EFEB",
    "#DFE7FD",
    "#CDDAFD",
]


def parse_graph(nodes: Dict[str, str], edges: List[Tuple[str, str]]):
    """
    Constructs a knowledge graph from given nodes and edges.
    """

    # Assign increasing ID value to each node
    node_ids = {k: i for i, k in enumerate(nodes.keys())}

    # Filter out edges that does not connect two known nodes (i.e. ghost pages)
    existing_edges = [
        edge for edge in set(edges) if edge[0] in node_ids and edge[1] in node_ids
    ]

    # Count the number of edges connected to each node
    edge_counts = {k: 0 for k in nodes.keys()}
    for i, j in existing_edges:
        edge_counts[i] += 1
        edge_counts[j] += 1

    # Node category if more than 2 nested level, take sub folder
    node_categories = set([key.split("/")[1 if key.count("/") < 5 else 2] for key in nodes.keys()])

    # Choose the most connected nodes to be colored
    top_nodes = {
        node_url: i
        for i, node_url in enumerate(
            list(node_categories)[: len(PASTEL_COLORS)]
        )
    }

    # Generate graph data
    graph_info = {
        "nodes": [
            {
                "id"     : node_ids[url],
                "label"  : title,
                "url"    : url,
                "color"  : PASTEL_COLORS[top_nodes[url.split("/")[1 if url.count("/") < 5 else 2]]] if url.split("/")[
                                                                                                           1 if url.count(
                                                                                                               "/") < 5 else 2] in top_nodes else None,
                "value"  : math.log10(edge_counts[url] + 1) + 1,
                "opacity": 0.1,
            }
            for url, title in nodes.items()
        ],
        "edges": [
            {"from": node_ids[edge[0]], "to": node_ids[edge[1]]}
            for edge in set(edges)
            if edge[0] in node_ids and edge[1] in node_ids
        ],
    }
    graph_info = json.dumps(graph_info)

    with open(site_dir / "static/js/graph_info.js", "w") as f:
        is_local = "true" if Settings.is_true("LOCAL_GRAPH") else "false"
        link_replace = "true" if Settings.is_true("GRAPH_LINK_REPLACE") else "false"
        f.write(
            "\n".join(
                [
                    f"var graph_data={graph_info}",
                    f"var graph_is_local={is_local}",
                    f"var graph_link_replace={link_replace}",
                ]
            )
        )


# ---------------------------------------------------------------------------- #
#                         Write Settings to Javascript                         #
# ---------------------------------------------------------------------------- #
def write_settings():
    """
    Writes settings to Javascript file.
    """

    with open(site_dir / "static/js/settings.js", "w") as f:
        sidebar_collapsed = "true" if Settings.is_true("SIDEBAR_COLLAPSED") else "false"
        f.write(
            "\n".join(
                [
                    f"var sidebar_collapsed={sidebar_collapsed}",
                ]
            )
        )
