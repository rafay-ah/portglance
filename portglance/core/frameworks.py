"""Recognise well-known dev servers and services from their command line."""

from __future__ import annotations

import re
from collections.abc import Sequence

# (label, pattern) pairs, matched in order against "<comm> <cmdline>" in lower
# case. More specific tools come before the runtimes that host them.
_RULES: list[tuple[str, str]] = [
    # JavaScript / TypeScript
    ("Next.js", r"(?:/|\b)next(?:-server|-router-worker|/dist/|\s+(?:dev|start)\b)"),
    ("Nuxt", r"\bnuxi?\b"),
    ("Astro", r"(?:/|\b)astro(?:\s|/|$)"),
    ("Remix", r"(?:/|\b)remix(?:-serve)?(?:\s|/|$)"),
    ("Gatsby", r"(?:/|\b)gatsby(?:\s|/|$)"),
    ("Angular", r"\bng\s+serve\b|@angular/cli"),
    ("Storybook", r"storybook"),
    ("Docusaurus", r"docusaurus"),
    ("Eleventy", r"@11ty|(?:/|\b)eleventy(?:\s|/|$)"),
    ("SvelteKit", r"svelte-kit"),
    ("Vite", r"(?:/|\b)vite(?:\.js)?(?:\s|/|$)"),
    ("Create React App", r"react-scripts"),
    ("webpack", r"webpack"),
    ("Parcel", r"(?:/|\b)parcel(?:\s|/|$)"),
    ("Expo", r"(?:/|\b)expo(?:\s|/|$)"),
    ("Metro", r"react-native\s+start|(?:/|\b)metro(?:\s|/|$)"),
    ("NestJS", r"@nestjs|\bnest\s+start\b"),
    ("Wrangler", r"wrangler|workerd"),
    ("Firebase", r"firebase.*emulators"),
    ("json-server", r"json-server"),
    ("nodemon", r"nodemon"),
    ("esbuild", r"esbuild"),
    ("Bun", r"^bun\b"),
    ("Deno", r"^deno\b"),
    # Python
    ("FastAPI", r"\bfastapi\s+(?:dev|run)\b"),
    ("Django", r"manage\.py\s+runserver|django-admin\s+runserver|\bdaphne\b"),
    ("Uvicorn", r"uvicorn"),
    ("Gunicorn", r"gunicorn"),
    ("Hypercorn", r"hypercorn"),
    ("Granian", r"granian"),
    ("Flask", r"(?:/|\b)flask(?:\s|$)|-m\s+flask\b"),
    ("JupyterLab", r"jupyter[- ]lab|jupyterlab"),
    ("Jupyter kernel", r"ipykernel_launcher|ipykernel"),
    ("Jupyter", r"jupyter[- ](?:notebook|server)|jupyter_server|notebook\.app"),
    ("Streamlit", r"streamlit"),
    ("Gradio", r"gradio"),
    ("MkDocs", r"mkdocs"),
    ("Sphinx", r"sphinx-autobuild"),
    ("http.server", r"http\.server|simplehttpserver"),
    ("Celery Flower", r"\bflower\b"),
    # Other ecosystems
    ("Rails", r"\brails\s+(?:s|server)\b|bin/rails"),
    ("Puma", r"\bpuma\b"),
    ("Jekyll", r"jekyll"),
    ("Laravel", r"artisan\s+serve"),
    ("PHP", r"\bphp\b.*\s-s\s"),
    ("Hugo", r"(?:/|\b)hugo(?:\s|$)"),
    ("Phoenix", r"phx\.server|phoenix"),
    ("Spring Boot", r"spring-boot|springframework\.boot"),
    ("Quarkus", r"quarkus"),
    ("Trunk", r"(?:/|\b)trunk\s+serve"),
    ("Air", r"^air\b"),
    (".NET", r"^dotnet\b"),
    # Data stores and infrastructure
    ("PostgreSQL", r"^postgres\b|/postgres\b|postgresql"),
    ("MySQL", r"^mysqld\b"),
    ("MariaDB", r"^mariadbd?\b"),
    ("Redis", r"redis-server|^valkey-server"),
    ("MongoDB", r"^mongod\b"),
    ("Memcached", r"^memcached\b"),
    ("MinIO", r"^minio\b"),
    ("Mailpit", r"mailpit"),
    ("MailHog", r"mailhog"),
    ("Ollama", r"^ollama\b"),
    ("Caddy", r"^caddy\b"),
    ("nginx", r"^nginx\b"),
    ("Apache", r"^(?:apache2|httpd)\b"),
    ("kubectl port-forward", r"kubectl.*port-forward"),
    ("SSH tunnel", r"^ssh\b"),
    ("Docker", r"^docker-proxy\b|^rootlesskit\b|^rootlessport\b"),
    ("VS Code", r"^code\b"),
    # Generic runtimes last
    ("Node.js", r"^node(?:js)?\b"),
    ("Python", r"^python[\d.]*\b"),
    ("Ruby", r"^ruby\b"),
    ("Java", r"^java\b"),
    ("Go", r"^go\b"),
]

_COMPILED = [(label, re.compile(pattern)) for label, pattern in _RULES]

#: Services that speak something other than HTTP: no "open in browser" for these.
NON_HTTP_FRAMEWORKS = frozenset(
    {
        "Jupyter kernel",
        "MariaDB",
        "Memcached",
        "MongoDB",
        "MySQL",
        "PostgreSQL",
        "Redis",
        "SSH tunnel",
    }
)

#: Well-known ports of protocols that a browser cannot talk to.
NON_HTTP_PORTS = frozenset(
    {
        21, 22, 23, 25, 53, 110, 143, 389, 465, 587, 636, 993, 995,
        1025, 1433, 1521, 1883, 2049, 2181, 3306, 4369, 5432, 5433,
        5672, 6379, 6380, 7687, 8883, 9042, 9092, 9093, 11211,
        26257, 27017, 27018, 27019, 50051,
    }
)  # fmt: skip

#: Image name fragments of containers that do not serve HTTP on their main port.
NON_HTTP_IMAGES = ("postgres", "mysql", "mariadb", "redis", "valkey", "mongo", "memcached")


def detect_framework(name: str, cmdline: Sequence[str]) -> str | None:
    """Best-effort label such as ``"Vite"`` or ``"Django"`` for a process."""
    haystack = " ".join([name, *cmdline]).lower()
    for label, pattern in _COMPILED:
        if pattern.search(haystack):
            return label
    return None


def looks_like_http(
    port: int,
    proto: str,
    framework: str | None = None,
    image: str | None = None,
    container_port: int | None = None,
) -> bool:
    """Whether opening ``http://localhost:<port>`` in a browser makes sense."""
    if proto != "tcp":
        return False
    if framework in NON_HTTP_FRAMEWORKS:
        return False
    if port in NON_HTTP_PORTS or (container_port is not None and container_port in NON_HTTP_PORTS):
        return False
    if image and container_port is not None and container_port < 10000:
        lowered = image.lower()
        if any(fragment in lowered for fragment in NON_HTTP_IMAGES):
            return False
    return True
