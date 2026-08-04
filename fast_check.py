#!/usr/bin/env python3
"""Experiment: does the cookie'd fast path work from a datacenter IP?"""
import gzip, json, os, re, urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
LABEL_RE = re.compile(r'aria-label="([^"]+)"')
CODE_RE = re.compile(r"\b[A-Z]{1,2}\d{1,2}$")
cookies = json.loads(os.environ["AMC_COOKIES"])
hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                if "amctheatres.com" in c["domain"])
ok = 0
for sid in ("144696961", "144696962", "144696966"):
    req = urllib.request.Request(
        f"https://www.amctheatres.com/showtimes/{sid}/seats",
        headers={"User-Agent": UA, "Cookie": hdr, "Accept-Encoding": "gzip",
                 "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                 "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
        html = raw.decode("utf-8", "replace")
        queued = "queue.amctheatres.com" in html or "queue-it" in html.lower()
        seats = [l for l in LABEL_RE.findall(html) if CODE_RE.search(l)]
        avail = [l for l in seats if not l.startswith("Occupied")]
        reg = [l for l in avail if "Wheelchair" not in l and "Companion" not in l]
        print(f"{sid}: status={r.status} len={len(html)} queued={queued} "
              f"seats={len(seats)} avail={len(avail)} regular={len(reg)}")
        if seats and not queued:
            ok += 1
    except Exception as e:
        print(f"{sid}: FAILED {e}")
print("VERDICT:", "COOKIES PORTABLE - cloud fast path works" if ok == 3
      else "cookies NOT portable from this IP")
