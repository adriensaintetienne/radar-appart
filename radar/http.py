"""Session HTTP commune : en-têtes de navigateur, délais polis, détection des pages anti-robot."""
import random
import time

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

_session = None
_last_call = {}


class Blocked(Exception):
    """Le site a renvoyé une page anti-robot / captcha / 403."""


def session():
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
        })
        _session = s
    return _session


BLOCK_MARKERS = ("captcha-delivery", "datadome", "cf-challenge", "challenge-platform", "px-captcha", "are you a robot", "access denied")


def get(url, **kwargs):
    return request("GET", url, **kwargs)


def post(url, **kwargs):
    return request("POST", url, **kwargs)


def request(method, url, *, min_delay=1.5, timeout=25, allow_blocked=False, **kwargs):
    """Requête avec un délai minimal entre deux appels au même domaine."""
    domain = requests.utils.urlparse(url).netloc
    wait = _last_call.get(domain, 0) + min_delay - time.time()
    if wait > 0:
        time.sleep(wait + random.uniform(0, 0.7))
    try:
        r = session().request(method, url, timeout=timeout, **kwargs)
    finally:
        _last_call[domain] = time.time()
    if not allow_blocked:
        head = r.text[:5000].lower() if "text" in r.headers.get("content-type", "") else ""
        if r.status_code in (401, 403, 429) or any(m in head for m in BLOCK_MARKERS):
            raise Blocked(f"{domain} a bloqué la requête (HTTP {r.status_code})")
    r.raise_for_status()
    return r
