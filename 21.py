#!/usr/bin/env python3
"""Mumbai 5.4 (24.py) - blitz.cloud (Docker) par chalne wala coding agent. Sirf Python stdlib.

Environment variables (blitz me daalo):
  JAAT_KEY  GEM_KEY  DS_KEY  GROQ_KEY  NV2_KEY  DAI_KEY     AI ki keys (jo key nahi di wo AI band rahega)
  NTFY_TOPIC        (optional) ntfy app ka topic, ya TG_TOKEN + TG_CHAT (Telegram): kaam khatam hone par phone par message
  BRAVE_KEY         (optional) @@SEARCH ke liye Brave Search; na do to DuckDuckGo se chalta hai
  MUMBAI_PASSWORD   (optional) login password, na do to 8888
  PORT              blitz khud deta hai (na ho to 8011)
  DATA_DIR          (optional) projects ka folder, default ~/mumbai_work
  ALLOWED_HOSTS     (optional) sirf ye domain maane, comma se alag

Login: 50 galat try par 1 din ke liye lock. /health bina password ke 'ok' deta hai (uptime bot ke liye).
Termux par bhi chalta hai:  python 21.py  ->  http://127.0.0.1:8011"""
import base64, difflib, io, json, os, re, shlex, shutil, signal, subprocess, sys
import contextlib, copy, threading, time, traceback, zipfile
import gzip, html.parser, ipaddress, socket, struct, tarfile, zlib
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import hashlib, hmac


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


# Saari APIs: naam aur model yahin likhe hain, sirf KEYS environment variable se aati hain.
# Naye options (har provider me):
#   group      same group ke providers me request baari-baari (rotation) se jaati hai
#   max_conc   ek saath kitni request (DeepSeek ke 2 account hain => 1)
#   min_gap    is provider par do request ke beech kam se kam itne second
#   rate_cool  429/limit aane par itne second aaram (Gemini ka lock ~2 ghante => 7200)
#   retry_429  429 aane par usi provider ko itni baar (ruk ke) dobara try karo, phir hi cooldown (default 1)
PRIORITY = ["JAAT", "GEM", "GEM2", "GEM3", "DS", "GROQ", "NV2", "DAI", "DSX"]
DISABLED = set()            # DSX wapas aa gaya: chalu (⚙ AI panel se chahe to band karo)
APIS = {
    "JAAT": {                      # JAAT (main, sabse zyada yahi chalega). Ek time par 1 request; 429 par 2 baar retry, cooldown bahut kam
        "type": "oai",
        "base": "https://jaat-api.haxnainbolte.workers.dev/v1",
        "key": _env("JAAT_KEY", "sk-jaat"),
        "models": ["jaat-default"],
        "timeout": 240,            # 4 min (beech ka server isse pehle hi timeout de deta tha)
        "total_timeout": 240,
        "group": "jaat",
        "no_system": True,         # JAAT 'System message must be at the beginning' 400 deta tha: system ko user message me jod do
        "max_conc": 1,
        "min_gap": 1,
        "rate_cool": 15,           # 429 ke baad sirf 15 sec aaram (lock nahi). 530 (Cloudflare) par sirf 3 sec ruk ke dobara: ask_oai dekho
        "retry_429": 2,            # 429 par 2 baar ruk ke dobara
        "max_ctx": 120000,         # JAAT ek jawab me ~37k char deta hai: input bhi bada lo (413 aaye to LEARN_CTX khud chhota kar leta hai)
    },
    "DSX": {                       # DeepSeek (ab sabse last). Key: environment me DSX_KEY, ya ⚙ se
        "type": "oai",
        "base": "https://my-ds-api.jaat.blitz.cloud/v1",
        "key": _env("DSX_KEY"),
        "models": ["deepseek-default"],
        "timeout": 900,            # bahut bada project padhne me pehla token aane tak 15 min tak
        "total_timeout": 2400,     # lamba jawab (bug list / bada plan) 40 min tak na kate
        "group": "deepseek",
        "max_conc": 1,
        "min_gap": 1,
        "rate_cool": 45,
        "max_ctx": int(_env("DSX_CTX", "1500000")),   # DSX ka context 1M token (~3M char) hai: itna bada project ek baar me padh leta hai (~1.5M char = ~400-500k token, margin ke saath; 413 aaye to khud chhota seekh leta hai)
        "max_tokens": int(_env("DSX_MAX_TOKENS", "64000")),   # DSX ka max output 384K hai; hum 64k tak maangte hain. Server 400 de to ask_oai error se asli hadd padh ke seekh leta hai (LEARN_MT)
    },
    "GEM": {                       # Gemini #1 (secondary)
        "type": "oai",
        "base": "https://gimmni.blitz.cloud/v1",
        "key": _env("GEM_KEY", "sk-gemini"),
        "models": ["gemini-3.7-flash"],
        "timeout": 180,
        "total_timeout": 400,
        "group": "gemini",
        "max_conc": 1,
        "min_gap": 10,
        "rate_cool": 7200,
    },
    "GEM2": {                      # Gemini #2 (workers.dev)
        "type": "oai",
        "base": "https://gemini.jaatbahi76.workers.dev/v1",
        "key": _env("GEM2_KEY", "sk-gemini"),
        "models": ["gemini-3.6-flash@think=0", "gemini-3.5-flash"],
        "timeout": 180,
        "total_timeout": 400,
        "group": "gemini",
        "max_conc": 1,
        "min_gap": 10,
        "rate_cool": 7200,
    },
    "GEM3": {                      # Gemini #3 (seedha IP, http)
        "type": "oai",
        "base": "http://129.225.83.244:8081/v1",
        "key": _env("GEM3_KEY", "sk-gemini"),
        "models": ["gemini-3.7-flash", "gemini-3.6-flash"],
        "timeout": 180,
        "total_timeout": 400,
        "group": "gemini",
        "max_conc": 1,
        "min_gap": 10,
        "rate_cool": 7200,
    },
    "DS": {
        "type": "proxy_get",
        "base": "https://anshapi.vercel.app/api/deepseek",
        "key": _env("DS_KEY", "ansh"),
        "models": ["deepseek-v4-flash"],
        "max_chars": 6500,
    },
    "GROQ": {
        "type": "oai",
        "base": "https://api.groq.com/openai/v1",
        "key": _env("GROQ_KEY"),
        "timeout": 5,              # Groq tez hai: 5s tak kuch na aaye to seedha agla AI (DS wagairah)
        "total_timeout": 30,       # poora jawab (stream) 30s se lamba nahi
        "min_gap": 2,
        "rate_cool": 60,           # 429 par 1 min aaram (lock nahi)
        "retry_429": 0,            # 429 par ruk ke dobara nahi, seedha agla AI
        "models": [
            "openai/gpt-oss-120b",
            "llama-3.3-70b-versatile",
            "qwen/qwen3-32b",
            "llama-3.1-8b-instant",
            "openai/gpt-oss-20b",
        ],
    },
    "NV2": {
        "type": "oai",
        "base": "https://integrate.api.nvidia.com/v1",
        "key": _env("NV2_KEY"),
        "models": ["nvidia/nemotron-3-ultra-550b-a55b", "poolside/laguna-xs-2.1"],
        "vision": "meta/llama-3.2-11b-vision-instruct",
    },
    "DAI": {
        "type": "deepai",
        "base": "https://api.deepai.org/hacking_is_a_serious_crime",
        "key": _env("DAI_KEY", "tryit-8803332334-5329fed31418c18274ac106f280207f3"),
        "models": ["standard"],
        "max_chars": 3000,
    },
}

# ---- bada jawab (token) + image dekhna: JAAT aur Gemini (conf se badal sakte ho) ----
for _n, _tok in (("JAAT", 10400), ("GEM", 60000), ("GEM2", 8192), ("GEM3", 60000)):   # JAAT ~10,600 token par kat jata hai; GEM2 32000 par 502 deta hai (test se pata chala)
    if _n in APIS:
        APIS[_n].setdefault("max_tokens", _tok)
        APIS[_n]["total_timeout"] = max(APIS[_n].get("total_timeout", 0), 600)   # lamba jawab beech me na kate
        APIS[_n].setdefault("vision", APIS[_n]["models"][0])                     # image dekhne ke liye wahi model
VISION_PREF = ("GEM", "GEM2", "GEM3", "JAAT", "NV2")                               # image: pehle Gemini, phir JAAT, phir NV2

DEFAULT_APIS = copy.deepcopy(APIS)
DEFAULT_PRIORITY = list(PRIORITY)

VERSION = "5.1"
PORT = int(os.environ.get("PORT") or 8011)
BIND = os.environ.get("BIND") or "0.0.0.0"
MAX_STEPS = 1000          # ek kaam me itne kadam
REPEAT_WAIT, REPEAT_WAITS = 15 * 60, 3   # atakne par 15 min ruko (max 3 baar), phir wahin se chalo
REPEAT_MAX = 15           # lagatar wahi command itni baar aaye tab kaam rokna (beech me har 3 baar par AI badalta hai)
MAX_TOKENS = int(os.environ.get("MAX_TOKENS") or 32000)         # AI ke jawab ki lambai (APIS me (19.py ke upar) cfg["max_tokens"] se badal sakte ho)
RUN_TIMEOUT = 120         # @@RUN kitni der tak
MAX_UPLOAD = 25 * 1024 * 1024
UA = "Mozilla/5.0 (Linux; Android 13; Mumbai) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
BOOT = str(int(time.time() * 1000))
ALLOWED_HOSTS = {h.strip() for h in (os.environ.get("ALLOWED_HOSTS") or "").split(",") if h.strip()}

ROOT = os.path.realpath(os.path.expanduser(os.environ.get("DATA_DIR") or "~/mumbai_work"))
_OLD = os.path.expanduser("~/paris_work")
if os.path.isdir(_OLD) and not os.path.exists(ROOT):
    os.makedirs(ROOT)
    shutil.move(_OLD, os.path.join(ROOT, "main"))
    print("purana ~/paris_work ab ~/mumbai_work/main hai")
os.makedirs(os.path.join(ROOT, ".mumbai"), exist_ok=True)
WORK = os.path.join(ROOT, "main")
os.makedirs(os.path.join(WORK, "uploads"), exist_ok=True)

# ---------- login: password + 50 galat par 1 din ka lock ----------
PASSWORD = _env("MUMBAI_PASSWORD", "8888")
MAX_TRIES, LOCK_SECS = 50, 24 * 3600
AUTH_TOKEN = hmac.new(("mumbai|" + _env("SESSION_SECRET", PASSWORD)).encode(), b"session", hashlib.sha256).hexdigest()
AUTH_FILE = os.path.join(ROOT, ".mumbai", "auth.json")
AUTH = {"fails": 0, "until": 0.0}
AUTH_LOCK = threading.Lock()


def auth_load():
    try:
        with open(AUTH_FILE, encoding="utf-8") as f:
            d = json.load(f)
        AUTH["fails"], AUTH["until"] = int(d.get("fails", 0)), float(d.get("until", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def auth_save():
    try:
        with open(AUTH_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(AUTH, f)
        os.replace(AUTH_FILE + ".tmp", AUTH_FILE)
    except OSError:
        pass


def auth_locked():
    """Lock ke kitne second baaki (0 = lock nahi). Lock khatam ho to ginti dobara 0."""
    left = AUTH["until"] - time.time()
    if left > 0:
        return int(left) + 1
    if AUTH["until"]:
        AUTH["fails"], AUTH["until"] = 0, 0.0
        auth_save()
    return 0


def check_password(pw):
    """(ok, message). Galat par ginti badhti hai; MAX_TRIES galat par LOCK_SECS ka lock (lock me sahi password bhi nahi chalta)."""
    with AUTH_LOCK:
        left = auth_locked()
        if left:
            return False, "🔒 lock hai, %d ghante %d min baad koshish karo" % (left // 3600, left % 3600 // 60)
        if hmac.compare_digest(str(pw).encode("utf-8", "replace"), PASSWORD.encode("utf-8", "replace")):
            if AUTH["fails"]:
                AUTH["fails"] = 0
                auth_save()
            return True, ""
        AUTH["fails"] += 1
        if AUTH["fails"] >= MAX_TRIES:
            AUTH["until"] = time.time() + LOCK_SECS
            auth_save()
            return False, "🔒 %d galat try ho gaye, 1 din ke liye lock" % MAX_TRIES
        auth_save()
        return False, "❌ galat password (%d/%d)" % (AUTH["fails"], MAX_TRIES)


auth_load()

SYSTEM = """Tum Mumbai ho, user ke phone par chalne wala coding aur sab-kaam ka sahayak (app, bot, website, script, PDF/docx/xlsx document, notes, research). Hinglish me chhota aur seedha bolo.
Agar user sirf baat kare, salaam kare ya sawal puche, to seedha normal jawab do. Koi command mat chalao.
Kaam ho to in commands se karo. Har command nayi line par sirf @@NAAM se shuru ho (bold ya backtick me mat lapeto):
@@PLAN                   agli lines me kaam ke kadam (ek line ek kadam), aakhir me @@END
@@CHECK <n>              kadam n poora hua
@@NOTE <ek line>         PROJECT.md me yaad-daasht jodo (faisle, file ke naam, kya baaki hai)
@@SPEC                   naye kaam ki jaanch-layak lines (S1: ..., S2: ...), aakhir me @@END
@@TREE                   poore project ki files ki list (.github jaise dot-folder bhi; files dhundhne ke liye find nahi, yahi use karo)
@@VERIFY                 project ki jaanch: syntax, XML, gradle, Android resources/manifest. Galtiyan list karta hai
@@GREP <text>            saari files me text dhundho
@@LS <folder>            ek folder ki list
@@READ <file> [a-b]      file padho (badi file ho to line range, jaise: @@READ app.py 100-220). zip, tar/gz, docx, xlsx, pptx, pdf aur image (Gemini/JAAT se andar ka text aur dikhawat) bhi padh leta hai\n@@UNZIP <file> [folder]  zip / tar / tgz / gz kholo\n@@SEARCH <sawal>         internet search (top 8 natije: title, link, saar). Phir kaam ka link @@WEB se padho\n@@NOTIFY <message>     user ke phone par message (kaam bahut lamba ho ya user ki zaroorat ho tab)\n@@WEB <https link>       web page ka text padho (sirf padhna; local/private address band). Library ka naya version ya docs dekhne ko
@@WRITE <file>           NAYI file, poora content, aakhir me @@END alag line par. Naam .pdf / .docx / .xlsx ho to asli file banti hai: body me "# bada heading", "## chhota heading", "- bullet", baaki paragraph (xlsx me har line ek row, comma se alag, "=SUM(A1:A3)" jaisa formula bhi chalta hai)
@@EDIT <file>            purani file me badlav, neeche wale format me, aakhir me @@END
@@WRITEB64 <file>        binary file (chhoti image wagairah), body base64, aakhir me @@END
@@RUN <shell command>    command chalao (2 minute tak, input nahi milta, isliye -y / --yes / -q jaise flags do)
@@BG <shell command>     lamba kaam background me (npm install, build, server). Ek id milti hai
@@LOG <id>               background kaam ka output aur status
@@KILL <id>              background kaam band karo
@@ZIP                    (Mumbai khud @@DONE ke baad banata hai, tum mat likho)
@@DEPS [naam]            gradle/package.json/requirements ki dependencies dikhao (naya library lagane se pehle dekho)
@@TEMPLATE android <package> <AppName>   Android ka poora skeleton (gradle, manifest, icon, workflow). Naye Android app par sabse pehle yahi
@@DONE <chhota saar>     kaam khatam
EDIT ka format (ek ya kai blocks):
<<<<<<< SEARCH
purani lines bilkul waise hi jaise file me hain
=======
nayi lines
>>>>>>> REPLACE
KAISE SOCHO (niyam kam, samajh zyada):
1. Pehle samjho. AUTO JAANKARI me SAMAJH hoti hai: user ka asli maqsad, kaam ka type aur DONE CRITERIA (kaam kab poora maana jayega). Wahi nishana hai: usse bahar ka kaam mat badhao, usme se kuch chhodo mat.
2. Banao, phir chala ya padh ke dekho. Bina saboot 'ho gaya' mat bolo. @@DONE hamesha AKELA likho (WRITE/RUN ke saath nahi); saar me har D-criteria ka saboot likho (kaunse @@RUN/@@READ ke natije se). Mumbai ek alag reviewer se ye saboot asli natije se milata hai.
3. Bade kadam: ek @@WRITE/@@EDIT me 600 line tak (.pdf/.docx/.md/.txt me bhi 600); poori file ek hi @@WRITE me likho, 600 se badi ho to 600-600 line ke hisson me. Purani file badalni ho to pehle @@READ, phir chhota @@EDIT (SEARCH text file jaisa bilkul waisa); poori file dobara @@WRITE mat karo. @@RUN/@@BG ke baad ruk jao aur natija dekho.
4. Type ke hisaab se: android/code ke naye kaam me pehle @@SPEC (BEHAVIOUR: user kya dekhe/kare, kahan toot sakta hai: khaali data, galat input, error par message) aur @@PLAN (ek file ya feature = ek kadam, har kadam ke baad @@CHECK n). Kitni lines chahiye wo AUTO JAANKARI me likha hai. light type (document, PDF, notes, likhna, sawal, chhota badlav) me seedha kaam karo: SPEC/PLAN mat likho, Android baatein mat jodo.
5. Reviewer ya jaanch ne mana kiya to wajah padho aur isi baar sahi karo. Wahi tareeka dohrao mat; 2 baar ke baad bhi na bane to asli jad dhundho aur naya raasta lo. User ki pasand ke bina sach me aage nahi badh sakte to @@ASK; chhote faisle khud lo.
6. Galti pakdi jaye (reviewer, error ya user se) to theek karne ke baad @@LEARN likho. AUTO JAANKARI ke SEEKHA me jo likha hai wo dobara mat karo.
Chhote niyam: content ``` me mat lapeto. File ke andar @@END chahiye to `@@WRITE file <<EOF_M` likho aur aakhir me alag line par EOF_M. Sirf project folder ke andar kaam karo (user ki files uploads/ me). @@ZIP mat likho (Mumbai khud banata hai). Test/build wala kadam @@VERIFY saaf aane par hi @@CHECK.
Android (APK): zaroori files: settings.gradle.kts, build.gradle.kts, gradle.properties (android.useAndroidX=true), app/build.gradle.kts (namespace, applicationId, sdk, dependencies), app/src/main/AndroidManifest.xml (xmlns:android, activity me android:exported), res/ (strings.xml, layout), .github/workflows/android.yml. Naye app par pehle @@TEMPLATE. Kotlin me R.string.x likhte hain. gradle ya ./gradlew @@RUN/@@BG me kabhi mat chalao (build GitHub Actions par hoga; yahan sirf @@VERIFY).
Document (PDF/docx/xlsx): seedha @@WRITE file.pdf me asli content (# heading, ## chhota heading, - bullet, baaki paragraph). PDF me sirf English/latin akshar chalte hain (Hindi '?' ban jata hai, isliye Hinglish Roman me likho). Banne ke baad @@LOOK file.pdf se layout dekho.
Udaharan:
- light: user "is note ka 1 page PDF banao" -> @@WRITE note.pdf ... -> @@LOOK note.pdf -> @@DONE note.pdf bana. D1: LOOK me 1 page, margin theek. D2: heading text me dikha.
- code: user "telegram bot jo /start par hello bole" -> @@SPEC (S1 /start par hello, S2 galat command par help, S3 token na ho to saaf error) -> @@PLAN (bot.py, config, README) -> kadam-kadam -> @@RUN python -m py_compile bot.py -> @@DONE.
- galti: reviewer kehta hai 'token khaali par crash' -> @@EDIT se check jodo -> @@LEARN token ya config khaali ho to crash nahi, saaf message -> aage badho."""

SYSTEM_MINI = """Tum Mumbai ho, coding assistant. Hinglish me chhota bolo. Sirf baat/sawal ho to seedha jawab do. Kaam ho to commands (har command nayi line par @@ se):
@@WRITE <file>  (content, aakhir me @@END alag line par)
@@EDIT <file>  (<<<<<<< SEARCH / ======= / >>>>>>> REPLACE, aakhir me @@END)
@@READ <file> <a-b>  (badi file ho to line range, jaise @@READ f.kt 1-400; ek baar me ~1000 line tak milti hain)   @@LS <dir>   @@TREE   @@GREP <text>   @@RUN <cmd>   @@VERIFY (project jaanch)   @@NOTE <ek line>   @@LEARN <sabak>   @@ASK <sawal>   @@LOOK <file.pdf ya image> [sawal]   @@CHECK <n>   @@DONE <saar>
Neeche PLAN dikhe to ek-ek kadam karo, har kadam ke baad @@CHECK n. Saare kadam ✅ hone par hi @@DONE.
@@DONE akela likho, @@VERIFY/RUN ka natija dekh ke. Test/build kadam @@VERIFY saaf aane par hi @@CHECK. Chhoti chhoti files likho. Ek reply me sirf EK command do aur uska natija dekh ke hi agla do. Jo file ka hissa padh chuke ho use dobara mat padho; natija kata ho to agli line range padho."""

STATE = {"running": False, "cancel": False, "msgs": [], "step": 0, "prov": "", "plan_items": [],
         "plan_ck": [], "worklog": [], "task": "", "proj": "main", "pending": [], "proc": None}
READ_FIRST, AUTO_CHECK, REVIEW = True, True, True
REVIEW_MAX, REVIEW_MIN_LINES = 3, 80
TRACK = {}


def track_reset():
    TRACK.clear()
    TRACK.update(seen=set(), warned=set(), edit_fail={}, reviewed={}, review_n=0,
                 spec=[], spec_rej=0, bugs={}, bug_rej={}, rounds={}, proof={}, final_rej=0, final_warn="",
                 size_rej={}, check_rej={}, since_check=0, polish=[], polish_done=False, flow_n=0,
                 pdfs=set(), looked=set(), pdf_rej=0, crit_rej=0, esc=0, asks=0, pivot1=False, last_reject="", img=None, learned=False)


track_reset()


UNDO_STACK = []            # har kaam ka ek dict: {rel_path: purane bytes ya None}
COOL = {}                  # provider -> kab tak chhod do
BG = {}                    # background kaam: id -> {"p", "cmd", "log"}
BG_N = [0]
EV, NEXT, LOCK, START = [], [0], threading.Lock(), threading.Lock()

BODY_KINDS = ("WRITE", "WRITEB64", "EDIT", "PLAN", "SPEC")
CTL = ("DONE", "PLAN", "CHECK", "NOTE", "SPEC", "LEARN", "ASK")
def _clean_env():
    """@@RUN wale shell ko keys/password mat dikhao (warna AI printenv se padh ke chat me daal deta)."""
    return {k: v for k, v in os.environ.items() if not re.search(r"(KEY|PASSWORD|SECRET|TOKEN)$", k, re.I)}


ENV = dict(_clean_env(), CI="1", npm_config_yes="true", PIP_NO_INPUT="1", DEBIAN_FRONTEND="noninteractive",
           PYTHONUNBUFFERED="1", NO_COLOR="1", TERM="dumb")


def log(s):
    print(time.strftime("[%H:%M:%S] ") + s, flush=True)


def ev(t, _log=True, **kw):
    """Chat me ek event jodta hai (user, ai, step, done, err, note, plan, check, reset)."""
    with LOCK:
        NEXT[0] += 1
        kw.update(t=t, id=NEXT[0])
        EV.append(kw)
        del EV[:-400]
    if _log and kw.get("text"):
        log(kw["text"][:120])


def ready(name):
    cfg = APIS.get(name)
    return bool(cfg) and name not in DISABLED and bool(str(cfg["key"]).strip()) and not str(cfg["key"]).startswith("YAHAN")


def models_of(cfg):
    return [m for m in cfg["models"] if not m.startswith("YAHAN")]


# ---------- provider ki sehat: kharab ko kuch der ke liye chhodo ----------
FAILS = {}                 # provider -> lagatar kitni baar kharab nikla


HARD_SECS = 600            # isse lamba cooldown = "lock": order() me aata hi nahi (locked provider ko dobara nahi thokte)
RATE_RE = re.compile(r"rate.?limit|quota|too many requests|resource.?exhausted|try again (?:later|in)", re.I)
ROT = {}                   # group -> ginti (baari-baari ke liye)
COOL_FILE = os.path.join(ROOT, ".mumbai", "cool.json")      # restart/rebuild ke baad bhi lock yaad rahe
CONF_FILE = os.path.join(ROOT, ".mumbai", "providers.json")  # bina rebuild ke provider badalne ki file
_CONF_MT = [0.0]
CONF_VER = 19              # purani providers.json ka priority (JAAT pehle, DSX last) naye order ko na bigaade
CONF_KEYS = ("type", "base", "key", "models", "timeout", "total_timeout", "max_chars", "max_url", "max_ctx",
             "max_tokens", "vision", "tier", "group", "max_conc", "min_gap", "rate_cool", "retry_429", "no_system")


def group_of(name):
    return (APIS.get(name) or {}).get("group") or name


def save_cool():
    now = time.time()
    try:
        with open(COOL_FILE + ".tmp", "w") as f:
            json.dump({k: v for k, v in COOL.items() if v > now}, f)
        os.replace(COOL_FILE + ".tmp", COOL_FILE)
    except OSError:
        pass


def load_cool():
    try:
        with open(COOL_FILE) as f:
            d = json.load(f)
        now = time.time()
        for k, v in d.items():
            if isinstance(v, (int, float)) and v > now:
                COOL[k] = float(v)
    except (OSError, ValueError, AttributeError):
        pass


load_cool()


def penalize(name, secs):
    FAILS[name] = FAILS.get(name, 0) + 1
    COOL[name] = time.time() + secs * min(FAILS[name], 6)
    if COOL[name] - time.time() > HARD_SECS:
        save_cool()


def lock(name, secs, why=""):
    """Limit lagi: itne second ke liye is provider ko bilkul mat chhedo (seedha, bina badhaye)."""
    COOL[name] = time.time() + secs
    FAILS[name] = FAILS.get(name, 0) + 1
    save_cool()
    ev("note", text="🔒 %s %s: %d min aaram, tab tak baaki AI se kaam chalega" % (name, why, max(1, secs // 60)))


def rotated(name):
    g = group_of(name)
    ROT[g] = ROT.get(g, 0) + 1


def cool_info():
    now = time.time()
    parts = ["%s %d min" % (n, (COOL[n] - now) // 60 + 1) for n in PRIORITY if ready(n) and COOL.get(n, 0) > now]
    return (" Cooldown me: " + ", ".join(parts) + " (⚙ me 🔓 se hata sakte ho)") if parts else ""


def order():
    """Group ke hisaab se: DeepSeek pehle, phir Gemini (teeno baari-baari), phir baaki. Locked wale bahar."""
    reload_conf()
    ok = [n for n in PRIORITY if ready(n)]
    now = time.time()
    live = [n for n in ok if COOL.get(n, 0) <= now]
    soft = sorted([n for n in ok if 0 < COOL.get(n, 0) - now <= HARD_SECS], key=lambda n: COOL[n])
    out, done = [], set()
    for n in live:
        g = group_of(n)
        if g in done:
            continue
        done.add(g)
        mem = [m for m in live if group_of(m) == g]
        k = ROT.get(g, 0) % len(mem)
        out += mem[k:] + mem[:k]
    return out + soft


def conf_status():
    now = time.time()
    return [{"name": n, "group": group_of(n), "ready": ready(n), "off": n in DISABLED, "cool": int(max(0, COOL.get(n, 0) - now))}
            for n in PRIORITY if n in APIS]


def conf_dump():
    prov = {}
    for n, c in APIS.items():
        d = {k: c[k] for k in CONF_KEYS if k in c}
        k = str(d.get("key") or "")
        if k:
            d["key"] = "••••" + k[-4:]
        if n in DISABLED:
            d["disabled"] = True
        prov[n] = d
    return json.dumps({"ver": CONF_VER, "priority": PRIORITY, "providers": prov}, indent=1, ensure_ascii=False)


def conf_write():
    out = {}
    for n, c in APIS.items():
        dflt = DEFAULT_APIS.get(n)
        d = {k: c[k] for k in CONF_KEYS if k in c and (dflt is None or c[k] != dflt.get(k))}
        if n in DISABLED:
            d["disabled"] = True
        if d:
            out[n] = d
    try:
        with open(CONF_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"ver": CONF_VER, "priority": PRIORITY, "providers": out}, f, indent=1, ensure_ascii=False)
        os.chmod(CONF_FILE + ".tmp", 0o600)
        os.replace(CONF_FILE + ".tmp", CONF_FILE)
        _CONF_MT[0] = os.path.getmtime(CONF_FILE)
    except OSError as e:
        log("providers.json save fail: %s" % e)


def conf_apply(text, write=False):
    """JSON text se providers/priority badalta hai (galat ho to ValueError, kuch nahi badalta)."""
    try:
        d = json.loads(text or "{}")
    except ValueError as e:
        raise ValueError("JSON galat: %s" % str(e)[:80])
    if not isinstance(d, dict):
        raise ValueError("sabse upar {} object chahiye")
    newp = {}
    for name, c in (d.get("providers") or {}).items():
        if not isinstance(c, dict) or not re.fullmatch(r"\w{1,20}", str(name)):
            raise ValueError("provider '%s' galat" % name)
        base, c = dict(APIS.get(name, {})), dict(c)
        if str(c.get("key", "")).startswith("••••"):
            c.pop("key")                         # masked key = badli nahi
        dis = bool(c.pop("disabled", False))
        base.update({k: v for k, v in c.items() if k in CONF_KEYS})
        if base.get("type") not in ("oai",) + tuple(PLAIN):
            raise ValueError("%s: type 'oai' (ya proxy_get/deepai) chahiye" % name)
        if not str(base.get("base", "")).startswith(("http://", "https://")):
            raise ValueError("%s: base http:// ya https:// se shuru ho" % name)
        if not isinstance(base.get("models"), list) or not base["models"]:
            raise ValueError("%s: models ki list chahiye" % name)
        base["key"] = str(base.get("key") or "")
        newp[name] = (base, dis)
    pr = d.get("priority")
    if pr is not None and not (isinstance(pr, list) and all(isinstance(x, str) for x in pr)):
        raise ValueError("priority naamon ki list ho")
    for name, (base, dis) in newp.items():
        APIS[name] = base
        (DISABLED.add if dis else DISABLED.discard)(name)
    if pr is not None:
        PRIORITY[:] = [n for n in pr if n in APIS]
    if write:
        conf_write()
    return "✅ save ho gaya, turant lagu (rebuild nahi chahiye)"


def reload_conf():
    """providers.json badli ho to bina restart ke dobara padh leta hai."""
    try:
        mt = os.path.getmtime(CONF_FILE)
    except OSError:
        return
    if mt == _CONF_MT[0]:
        return
    _CONF_MT[0] = mt
    try:
        with open(CONF_FILE, encoding="utf-8") as f:
            raw = f.read()
        d = json.loads(raw or "{}")
        if isinstance(d, dict) and int(d.get("ver") or 0) < CONF_VER:
            d.pop("priority", None)              # purana order chhodo, naya (JAAT pehle) rakho
            raw = json.dumps(d)
        conf_apply(raw)
        ev("note", text="⚙ providers.json load hui")
    except (OSError, ValueError, TypeError) as e:
        ev("note", text="⚠ providers.json padh nahi payi: %s" % e)


def nap(sec):
    """Ruk ke intezaar, Stop dabe to False."""
    end = time.time() + sec
    while time.time() < end:
        if STATE["cancel"]:
            return False
        time.sleep(0.3)
    return not STATE["cancel"]


def retry_after(e):
    try:
        return max(1.0, min(20.0, float(e.headers.get("Retry-After"))))
    except (TypeError, ValueError, AttributeError):
        return 5.0


# ---------- DS / DeepAI (OpenAI jaise nahi, isliye alag) ----------
def http(url, data=None, headers=None, timeout=90):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def flat_text(msgs):
    parts = []
    for m in msgs:
        c = m.get("content")
        if isinstance(c, list):
            c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
        parts.append("%s: %s" % (m.get("role", "user"), c or ""))
    return "\n".join(parts)


def tail(s, n):
    return s if len(s) <= n else "..." + s[-n:]


def keep_head(s, n):
    """Natija lamba ho to shuru ka 70% + aakhir ka 30% rakhta hai (READ me file ka upar ka hissa chahiye)."""
    if len(s) <= n:
        return s
    h = int(n * 0.7)
    return s[:h] + "\n...(kata gaya; aage ke liye @@READ <file> <a-b> chhoti range me)...\n" + s[-(n - h):]


def mini_context(room):
    """Chhote AI ke liye plan + PROJECT.md + file list ka chhota saar (ye sab use aur kahin se dikhta nahi)."""
    parts = []
    try:
        parts.append(mode_note(True))
    except Exception as e:
        log("mode_note galti: %s" % e)
    items = STATE["plan_items"]
    if items:
        pl = "\n".join("%d %s %s" % (i + 1, "✅" if (i + 1) in STATE["plan_ck"] else "⬜", x[:70])
                       for i, x in enumerate(items))
        parts.append("PLAN:\n" + clip(pl, max(200, room // 3)))
    try:
        with open(os.path.join(WORK, "PROJECT.md"), encoding="utf-8", errors="replace") as f:
            notes = f.read().strip()
    except OSError:
        notes = ""
    if notes:
        parts.append("PROJECT.md:\n" + clip(notes, max(200, room // 3)))
    fs = ", ".join(list_files(60))
    if fs:
        n = max(150, room // 4)
        parts.append("Files: " + (fs if len(fs) <= n else fs[:n] + " ..."))
    try:
        if cur_mode() == "android":
            errs = [m for lv, m in verify_project(force_android=True) if lv == "E"]
            parts.append("ANDROID baaki: " + (clip("; ".join(errs[:3]), max(150, room // 4)) if errs else "sab theek"))
    except Exception:
        pass
    return ("\n\n" + "\n".join(parts)) if parts else ""


def flat_short(msgs, limit):
    """Chhote-limit wale AI ke liye: chhota system + plan/notes/files + user ka ASLI kaam + aakhri natija."""
    sysm = SYSTEM_MINI
    task = STATE.get("task") or ""
    last = msgs[-1]["content"] if isinstance(msgs[-1].get("content"), str) else ""
    room = max(400, limit - len(sysm) - 80)
    ctx = mini_context(min(room // 2, 2400))
    room = max(250, room - len(ctx))
    if len(msgs) <= 2 or last == task:
        return sysm + ctx + "\n\nKaam: " + tail(task or last, room)
    wl = tail("\n".join(STATE["worklog"][-5:]), 250)
    room -= len(wl)
    tr = min(len(task), max(120, room // 3))
    return "%s%s\n\nUser ka asli kaam: %s\n\nAb tak:\n%s\n\nAbhi ka natija:\n%s" % (
        sysm, ctx, task[:tr], wl, keep_head(last, max(150, room - tr)))


REPLY_KEYS = ("response", "reply", "answer", "text", "result", "content", "message", "data", "output")


def dig(j):
    """JSON me kitna bhi andar ho, AI ka jawab (string) dhundhta hai. Jaise {"data": {"response": "..."}}."""
    if isinstance(j, str):
        return j
    if isinstance(j, dict):
        for k in REPLY_KEYS:
            if k in j:
                r = dig(j[k])
                if r and r.strip():
                    return r
        for v in j.values():
            if isinstance(v, (dict, list)):
                r = dig(v)
                if r and r.strip():
                    return r
    if isinstance(j, list):
        for x in j:
            r = dig(x)
            if r and r.strip():
                return r
    return None


def extract(out):
    try:
        j = json.loads(out)
    except ValueError:
        return out
    if isinstance(j, dict) and j.get("status") is False:
        raise IOError("provider error: %s" % str(j)[:100])
    return dig(j) if isinstance(j, (dict, list)) else out


def call_proxy_get(cfg, model, text):
    # ANDAZA: parameter ka naam 'prompt' maana hai. Na chale to yahin badlo.
    out = http(cfg["base"] + "?" + urllib.parse.urlencode({"prompt": text, "model": model}))
    return extract(out)


def call_deepai(cfg, model, text):
    # ANDAZA: DeepAI chat ke form fields. Na chale to yahin badlo.
    hist = json.dumps([{"role": "user", "content": text}])
    body = urllib.parse.urlencode({"chat_style": "chat", "chatHistory": hist, "model": model}).encode()
    return extract(http(cfg["base"], body, {"api-key": cfg["key"]}))


PLAIN = {"proxy_get": call_proxy_get, "deepai": call_deepai}


def completion(text):
    return json.dumps({
        "id": "chatcmpl-mumbai", "object": "chat.completion", "created": int(time.time()), "model": "mumbai",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}).encode()


def sse(text):
    def ch(delta, fin):
        return "data: " + json.dumps({
            "id": "chatcmpl-mumbai", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": "mumbai", "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}) + "\n\n"
    return (ch({"role": "assistant", "content": text}, None) + ch({}, "stop") + "data: [DONE]\n\n").encode()


def fold_system(msgs):
    """system message ko pehle user message ke andar jod deta hai (kuch server 'System message must be at the beginning' 400 dete hain)."""
    sys_t = "\n\n".join(str(m.get("content") or "") for m in msgs if m.get("role") == "system")
    rest = [dict(m) for m in msgs if m.get("role") != "system"]
    if not sys_t:
        return rest
    for m in rest:
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                m["content"] = [{"type": "text", "text": sys_t + "\n\n"}] + c
            else:
                m["content"] = sys_t + "\n\n" + str(c or "")
            return rest
    return [{"role": "user", "content": sys_t}] + rest


def upstream(cfg, model, body, timeout=None):
    b = dict(body)
    if cfg.get("no_system") and b.get("messages"):
        b["messages"] = fold_system(b["messages"])
    b["model"] = model
    req = urllib.request.Request(
        cfg["base"].rstrip("/") + "/chat/completions", json.dumps(b).encode(),
        {"Content-Type": "application/json", "Authorization": "Bearer " + cfg["key"], "User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout or cfg.get("timeout", 60))


# ---------- AI se poochna ----------
class Cancelled(Exception):
    pass


_SLOT_LOCK = threading.Lock()
SEMS, LAST_START = {}, {}


@contextlib.contextmanager
def slot(name):
    """Provider par ek saath max_conc request, aur do request ke beech min_gap second."""
    cfg = APIS.get(name) or {}
    mc, gap = int(cfg.get("max_conc") or 0), float(cfg.get("min_gap") or 0)
    sem = None
    if mc:
        with _SLOT_LOCK:
            sem = SEMS.get((name, mc))
            if sem is None:
                sem = SEMS[(name, mc)] = threading.Semaphore(mc)
        while not sem.acquire(timeout=0.5):
            if STATE["cancel"]:
                raise Cancelled()
    try:
        if gap:
            with _SLOT_LOCK:
                at = max(time.time(), LAST_START.get(name, 0.0) + gap)
                LAST_START[name] = at
            wait = at - time.time()
            if wait > 0 and not nap(wait):
                raise Cancelled()
        yield
    finally:
        if sem:
            sem.release()


def fit(msgs, budget):
    """Bahut lamba context kaat-ta hai (purane beech ke messages hatakar), taaki AI atke nahi."""
    def size(m):
        return sum(len(x.get("content") or "") for x in m)
    if size(msgs) <= budget:
        return msgs
    head, rest = msgs[:2], msgs[2:]
    while len(rest) > 2 and size(head + rest) > budget:
        rest = rest[1:]
    while len(rest) > 1 and rest[0]["role"] != "user":
        rest = rest[1:]
    out = head + rest
    if size(out) > budget:
        out = [out[0]] + [dict(x, content=clip(x.get("content") or "", 4000)) for x in out[1:]]
    return out


LAST_RAW = [""]            # khali jawab ki asli wajah (server ne kya bheja)


def _err_text(o):
    e = o.get("error") if isinstance(o, dict) else None
    if e:
        return str(e.get("message") if isinstance(e, dict) else e)[:200]
    return ""


def cancellable(fn):
    """fn(dead) ko alag thread me chalata hai. Stop dabte hi TURANT Cancelled uthata hai (server ka jawab aane ka intezaar nahi).
    Der se aaya jawab fenk diya jata hai, chat me kabhi nahi aata."""
    box, done, dead = {}, threading.Event(), threading.Event()

    def work():
        try:
            box["res"] = fn(dead)
        except BaseException as e:
            box["err"] = e
        finally:
            done.set()
    threading.Thread(target=work, daemon=True).start()
    while not done.wait(0.2):
        if STATE["cancel"]:
            dead.set()
            raise Cancelled()
    if dead.is_set():
        raise Cancelled()
    if "err" in box:
        raise box["err"]
    return box["res"]


def stream_call(cfg, model, body, total):
    def job(dead):
        r = None
        try:
            r = upstream(cfg, model, body)
            if dead.is_set():
                raise Cancelled()
            return read_reply(r, total, dead)
        finally:
            try:
                if r is not None:
                    r.close()
            except Exception:
                pass
    return cancellable(job)


def read_reply(r, total, dead=None):
    """(jawab, finish_reason). Stream ho to tukde jodta hai; timeout sirf 'kuch nahi aaya' par lagta hai."""
    LAST_RAW[0] = ""
    if "event-stream" not in r.headers.get("Content-Type", ""):
        body = r.read()
        try:
            j = json.loads(body)
            ch = j["choices"][0]
            return ch["message"].get("content") or "", ch.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError):
            LAST_RAW[0] = body.decode("utf-8", "replace")[:250].replace("\n", " ")
            return "", None
    parts, fin, t0, extra = [], None, time.time(), []
    for raw in r:
        if STATE["cancel"] or (dead is not None and dead.is_set()):
            raise Cancelled()
        if time.time() - t0 > total:
            raise IOError("bahut der lagi (%ds)" % total)
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            if line and len(extra) < 4:
                extra.append(line[:160])
            continue
        d = line[5:].strip()
        if d == "[DONE]":
            break
        try:
            obj = json.loads(d)
            ch = (obj.get("choices") or [{}])[0]
        except (ValueError, AttributeError):
            continue
        if _err_text(obj) and len(extra) < 4:
            extra.append(_err_text(obj))
        parts.append((ch.get("delta") or {}).get("content") or (ch.get("message") or {}).get("content") or "")
        fin = ch.get("finish_reason") or fin
    if not "".join(parts).strip():
        LAST_RAW[0] = " | ".join(extra) if extra else "(stream bilkul khali aaya)"
    return "".join(parts), fin


CTX_GUESS = (("groq", 14000), ("cerebras", 24000), ("github", 20000))   # APIS me (19.py ke upar) "max_ctx" likho to wahi chalega
LEARN_CTX = {}                     # 413 aane par seekha hua chhota limit (provider -> char)
LEARN_MT = {}                      # 400 "max_tokens" aane par seekhi hui jawab ki hadd (provider -> token)
BIG_RE = re.compile(r"too large|too long|context length|maximum context|reduce the length|request too big", re.I)


def ctx_limit(name, cfg):
    if name in LEARN_CTX:
        return LEARN_CTX[name]
    if cfg.get("max_ctx"):
        return cfg["max_ctx"]
    hay = (name + " " + str(cfg.get("base", ""))).lower()
    for k, v in CTX_GUESS:
        if k in hay:
            return v
    return 48000


SMALL_NUDGE = ("\n\n[Mumbai: pichhla jawab lamba hone se khali gaya. Ab SIRF EK chhota kadam karo: ek file ya ek feature, 150-200 line se kam, "
               "kam se kam soch-vichar. Reviewer ho to sirf chhota JSON, lambi vyakhya nahi.]")


CF530_WAIT, CF530_TRIES = int(_env("CF530_WAIT", "3")), 3        # 530 aaye to itne second ruko, itni baar tak


def ask_oai(name, cfg, msgs):
    budget = ctx_limit(name, cfg)
    saw_empty, last_err = False, 0
    for model in models_of(cfg):
        use_mt, use_stream, n429 = True, True, 0
        n530 = 0                                  # Cloudflare 530: sirf 530 ke baad 3 sec ruko, har request ke baad nahi
        mt, empty_n, shrunk = min(cfg.get("max_tokens", MAX_TOKENS), LEARN_MT.get(name, 10 ** 9)), 0, False
        for _ in range(8):
            if STATE["cancel"]:
                return None
            body = {"messages": fit(msgs, budget), "temperature": 0.2}
            log("→ %s/%s ~%d char" % (name, model, sum(len(x.get("content") or "") for x in body["messages"])))
            if use_mt:
                body["max_tokens"] = mt
            if use_stream:
                body["stream"] = True
            try:
                with slot(name):
                    out, fin = stream_call(cfg, model, body, cfg.get("total_timeout", 300))
                if out.strip():
                    return out, fin == "length"
                saw_empty, empty_n = True, empty_n + 1
                ev("note", text="%s/%s: khali jawab (finish=%s, stream=%s). Server ne bheja: %s" % (name, model, fin, use_stream, LAST_RAW[0] or "-"))
                if re.search(r"\b530\b", (LAST_RAW[0] or "")[:200]) and n530 < CF530_TRIES:
                    n530 += 1
                    ev("note", text="%s: Cloudflare 530, %ds ruk ke dobara (%d/%d)" % (name, CF530_WAIT, n530, CF530_TRIES))
                    if not nap(CF530_WAIT):
                        return None
                    continue
                if fin == "length" and not shrunk:
                    shrunk = True            # token badhane ki jagah kaam chhota karwao
                    msgs = list(msgs)
                    last = dict(msgs[-1])
                    last["content"] = (last.get("content") or "") + SMALL_NUDGE
                    msgs[-1] = last
                    ev("note", text="%s: jawab token me kat ke khali gaya, kaam chhota karke dobara" % name)
                    continue
                if use_stream and empty_n == 1 and fin != "length":
                    use_stream = False       # shayad stream khali aaya
                    continue
                break                        # is model se nahi, agla model
            except Cancelled:
                return None
            except urllib.error.HTTPError as e:
                code = e.code
                try:
                    err_body = e.read().decode("utf-8", "replace")[:600]
                except Exception:
                    err_body = ""
                if (code == 413 or (code in (400, 429) and BIG_RE.search(err_body))) and budget > 3000:
                    budget = max(3000, budget // 2)      # context is provider ke liye bada tha
                    LEARN_CTX[name] = budget
                    ev("note", text="%s: context bada tha, %d char tak chhota karke dobara" % (name, budget))
                    continue
                rc = cfg.get("rate_cool", 0)
                if rc >= HARD_SECS and (code == 429 or (code in (400, 403) and RATE_RE.search(err_body))):
                    lock(name, rc, "limit (HTTP %s)" % code)      # lock: dobara thokna bekaar
                    return None
                if code == 530 and n530 < CF530_TRIES:
                    n530 += 1
                    ev("note", text="%s: Cloudflare 530, %ds ruk ke dobara (%d/%d)" % (name, CF530_WAIT, n530, CF530_TRIES))
                    if not nap(CF530_WAIT):
                        return None
                    continue
                if code == 429 and n429 < int(cfg.get("retry_429", 1)):
                    n429, w = n429 + 1, retry_after(e)
                    ev("note", text="%s: 429 (bheed), %.0fs ruk ke dobara (%d/%d)" % (name, w, n429, int(cfg.get("retry_429", 1))))
                    if not nap(w):
                        return None
                    continue
                ev("note", text="%s/%s HTTP %s %s" % (name, model, code, err_body[:150].replace("\n", " ")))
                if code == 400 and use_mt and "max_tokens" in err_body.lower() and mt > 4096:
                    _lim = [int(x) for x in re.findall(r"\d{4,6}", err_body) if 1024 <= int(x) < mt]
                    mt = max(_lim) if _lim else max(4096, mt // 2)   # error me hadd likhi ho (jaise <= 8192) to wahi, warna aadha: dobara 400 mat khao
                    LEARN_MT[name] = mt
                    ev("note", text="%s: jawab ki hadd %d token par laayi" % (name, mt), _log=False)
                    continue
                if code == 400 and use_stream:
                    use_stream = False   # shayad stream pasand nahi
                    continue
                if code == 400 and use_mt:
                    use_mt = False       # shayad max_tokens pasand nahi
                    continue
                if code in (400, 404):
                    break                # agla model
                if code >= 500:
                    last_err = code
                    break                # server ki dikkat: provider ka agla model try karo
                if code == 429 and rc and rc < HARD_SECS:
                    COOL[name] = time.time() + rc          # chhota cooldown: lock/FAILS badhana nahi
                    ev("note", text="%s: 429 baar-baar, %ds aaram" % (name, rc))
                elif code == 429 and rc:
                    lock(name, rc, "429")
                else:
                    penalize(name, {429: 60, 401: 600, 403: 600}.get(code, 90))
                return None
            except Exception as e:
                ev("note", text="%s fail: %s" % (name, str(e)[:80]))
                penalize(name, 90)
                return None
    if last_err or saw_empty:
        penalize(name, 90 if last_err else 30)
    return None


def plain_prompt(cfg, msgs, scale=1.0):
    """Chhote AI ka prompt. proxy_get me URL me encode hone ke BAAD ki lambai bhi dekhta hai (414 se bachne ko)."""
    lim = int(cfg.get("max_chars", 3000) * scale)
    text = flat_short(msgs, lim)
    if cfg["type"] == "proxy_get":
        cap = int(cfg.get("max_url", 6000) * scale)      # APIS me (19.py ke upar) "max_url" se badal sakte ho
        for _ in range(4):
            enc = len(urllib.parse.quote(text, safe=""))
            if enc <= cap:
                break
            lim = max(500, int(lim * cap / enc * 0.95))
            text = flat_short(msgs, lim)
    return text


def ask_plain(name, cfg, msgs):
    out = ""
    for scale in (1.0, 0.5):
        try:
            _txt = plain_prompt(cfg, msgs, scale)
            out = cancellable(lambda dead: PLAIN[cfg["type"]](cfg, models_of(cfg)[0], _txt))
            break
        except Cancelled:
            return None
        except urllib.error.HTTPError as e:
            if e.code in (413, 414) and scale == 1.0:
                ev("note", text="%s: HTTP %s (prompt lamba), chhota karke dobara" % (name, e.code))
                continue
            ev("note", text="%s HTTP %s" % (name, e.code))
            penalize(name, {429: 60, 401: 600, 403: 600}.get(e.code, 90))
            return None
        except Exception as e:
            ev("note", text="%s fail: %s" % (name, str(e)[:80]))
            penalize(name, 90)
            return None
    if out and out.strip():
        return out, False
    ev("note", text="%s: khali jawab, agla AI" % name)
    penalize(name, 30)
    return None


REFUSE_RE = re.compile(r"^\W*(i\s+(cannot|can't|can\s?not|am\s+unable\s+to|won't)\s+(fulfill|help|assist|comply|do\s+that|provide)|"
                       r"i'?m\s+(sorry|unable)[^.\n]{0,40}(can't|cannot|unable)|sorry,?\s+(but\s+)?i\s+(can't|cannot))", re.I)


def is_refusal(text):
    """Chhota 'I cannot fulfill this request' jaisa jawab (koi @@ command nahi): ise history me mat daalo, agla AI try karo."""
    t = (text or "").strip()
    return bool(t) and len(t) < 200 and "@@" not in t and bool(REFUSE_RE.search(t))


def ask(msgs, role="write"):
    """(naam, jawab, kat_gaya?) ya None. role: plan = sabse mazboot pehle, write = sasta/tez pehle."""
    refused = None
    for name in role_order(role):
        if STATE["cancel"]:
            return None
        cfg, t = APIS[name], time.time()
        res = None
        for attempt in range(3):
            try:
                res = ask_oai(name, cfg, msgs) if cfg["type"] == "oai" else ask_plain(name, cfg, msgs)
            except Exception as e:
                ev("note", text="%s fail: %s" % (name, str(e)[:80]))
                penalize(name, 90)
                res = None
            if res or STATE["cancel"] or COOL.get(name, 0) - time.time() > 20:
                break          # provider abhi kharab nikla: usi ko 3 baar mat thoko, agla AI
            if attempt < 2:
                ev("note", text="↻ %s dobara (%d/3)" % (name, attempt + 2))
                if not nap(2 * (attempt + 1)):
                    return None
        if res and is_refusal(res[0]):
            ev("note", text="↷ %s ne mana kar diya (%s), agla AI try kar raha hoon" % (name, res[0].strip()[:50]))
            refused = refused or (name, res[0], res[1])
            res = None
        if res:
            if COOL.pop(name, None) is not None:
                save_cool()
            FAILS.pop(name, None)
            rotated(name)
            log("← %s %.0fs%s" % (name, time.time() - t, " (kata hua)" if res[1] else ""))
            return name, res[0], res[1]
    return refused


# ================= FORMATS: har type ki file padho/banao (sirf stdlib; pypdf ho to PDF padhne me wo bhi) =================
CONVERT_EXT = (".zip", ".docx", ".pptx", ".xlsx", ".pdf", ".tar", ".tgz", ".gz", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".odt")
MAKE_EXT = (".pdf", ".docx", ".xlsx")
FMT_MAX = 12000
READ_CAP = int(os.environ.get("READ_CAP") or 40000)   # @@READ ek baar me itne char (~1000 line) dikhata hai (pehle 12000 = ~300 line, beech se kat jata tha)


def _xml_text(node):
    out = []
    for el in node.iter():
        t = el.tag.rsplit("}", 1)[-1]
        if t == "t" and el.text:
            out.append(el.text)
        elif t in ("tab",):
            out.append("\t")
        elif t in ("br", "cr"):
            out.append("\n")
    return "".join(out)


def _paras(xml_bytes):
    root = ET.fromstring(xml_bytes)
    lines = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] in ("p",):
            lines.append(_xml_text(el))
    return lines


def _read_docx(z):
    names = ["word/document.xml"] + sorted(n for n in z.namelist() if re.match(r"word/(header|footer)\d*\.xml$", n))
    out = []
    for n in names:
        if n in z.namelist():
            out += [l for l in _paras(z.read(n)) if l.strip()] if n != "word/document.xml" else _paras(z.read(n))
    return "\n".join(out)


def _read_pptx(z):
    slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                    key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    out = []
    for i, n in enumerate(slides, 1):
        out.append("--- Slide %d ---" % i)
        out += [l for l in _paras(z.read(n)) if l.strip()]
    return "\n".join(out) or "(koi slide text nahi mila)"


def _col_idx(ref):
    m = re.match(r"([A-Z]+)", ref or "")
    n = 0
    for ch in (m.group(1) if m else "A"):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _read_xlsx(z):
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
            shared.append(_xml_text(si))
    sheets = []
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rmap = {r.get("Id"): r.get("Target") for r in rels}
        for s in wb.iter():
            if s.tag.rsplit("}", 1)[-1] == "sheet":
                rid = [v for k, v in s.attrib.items() if k.endswith("}id")]
                tgt = rmap.get(rid[0]) if rid else None
                if tgt:
                    tgt = tgt.lstrip("/")
                    sheets.append((s.get("name"), tgt if tgt.startswith("xl/") else "xl/" + tgt))
    except (KeyError, ET.ParseError):
        pass
    if not sheets:
        sheets = [(n, n) for n in sorted(z.namelist()) if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
    out = []
    for name, path in sheets:
        out.append("=== Sheet: %s ===" % name)
        root = ET.fromstring(z.read(path))
        rows = 0
        for row in root.iter():
            if row.tag.rsplit("}", 1)[-1] != "row":
                continue
            cells = {}
            for c in row:
                if c.tag.rsplit("}", 1)[-1] != "c":
                    continue
                t, val = c.get("t"), ""
                v = next((x for x in c if x.tag.rsplit("}", 1)[-1] == "v"), None)
                if t == "s" and v is not None and v.text and v.text.isdigit() and int(v.text) < len(shared):
                    val = shared[int(v.text)]
                elif t == "inlineStr":
                    val = _xml_text(c)
                elif v is not None and v.text is not None:
                    val = v.text
                if val == "":
                    f = next((x for x in c if x.tag.rsplit("}", 1)[-1] == "f"), None)
                    if f is not None and f.text:
                        val = "=" + f.text            # formula (Excel kholne par value bharegi)
                cells[_col_idx(c.get("r"))] = val
            if cells:
                out.append("\t".join(cells.get(i, "") for i in range(max(cells) + 1)))
                rows += 1
            if rows >= 300:
                out.append("...(300 se zyada rows, baaki nahi dikhayi)")
                break
    return "\n".join(out)


def _read_odt(z):
    return "\n".join(_paras(z.read("content.xml")))


def _pdf_unescape(s):
    s = re.sub(r"\\([0-7]{1,3})", lambda m: chr(int(m.group(1), 8)), s)
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "r": "", "t": "\t"}.get(m.group(1), m.group(1)), s, flags=re.S)


def _read_pdf(raw, path):
    try:
        import pypdf
        r = pypdf.PdfReader(io.BytesIO(raw))
        out = []
        for i, pg in enumerate(r.pages, 1):
            out.append("--- Page %d ---\n%s" % (i, (pg.extract_text() or "").strip()))
        txt = "\n".join(out)
        if txt.replace("--- Page", "").strip():
            return txt
    except ImportError:
        pass
    except Exception as e:
        log("pypdf fail: %s" % e)
    parts = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        data = m.group(1)
        try:
            data = zlib.decompress(data)
        except Exception:
            pass
        t = data.decode("latin-1", "replace")
        if "BT" not in t:
            continue
        for blk in re.findall(r"BT(.*?)ET", t, re.S):
            line = []
            for tj in re.finditer(r"\[(.*?)\]\s*TJ|\((.*?)(?<!\\)\)\s*(?:Tj|'|\")", blk, re.S):
                if tj.group(1) is not None:
                    line.append("".join(_pdf_unescape(x) for x in re.findall(r"\((.*?)(?<!\\)\)", tj.group(1), re.S)))
                else:
                    line.append(_pdf_unescape(tj.group(2)))
            if line:
                parts.append(" ".join(line))
    if parts:
        return "(stdlib se padha, adhura ya bigda ho sakta hai; sahi padhne ko Dockerfile me pypdf lagao)\n" + "\n".join(parts)
    return "(PDF me text nahi mila: scan/image wali PDF ho sakti hai, ya pypdf chahiye)"


def _img_info(raw, ext):
    try:
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", raw[16:24])
            return "PNG image %dx%d, %d bytes" % (w, h, len(raw))
        if raw[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", raw[6:10])
            return "GIF image %dx%d, %d bytes" % (w, h, len(raw))
        if raw[:2] == b"\xff\xd8":
            i = 2
            while i < len(raw) - 9:
                if raw[i] != 0xFF:
                    i += 1
                    continue
                mk = raw[i + 1]
                if mk in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                    return "JPEG image %dx%d, %d bytes" % (w, h, len(raw))
                i += 2 + struct.unpack(">H", raw[i + 2:i + 4])[0]
    except Exception:
        pass
    return "image file, %d bytes (size nahi padh payi)" % len(raw)


def read_any(path, raw):
    """Binary document ko text me badalta hai. Is type ki nahi hai to None."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in CONVERT_EXT:
        return None
    try:
        if ext in (".png", ".jpg", ".jpeg", ".gif"):
            return look_image(path, raw) or _img_info(raw, ext)
        if ext == ".pdf":
            return _read_pdf(raw, path)
        if ext == ".gz" and not path.lower().endswith(".tar.gz"):
            return gzip.decompress(raw).decode("utf-8", "replace")
        if ext in (".tar", ".tgz", ".gz"):
            with tarfile.open(fileobj=io.BytesIO(raw)) as t:
                ms = t.getmembers()
                return "archive: %d entries\n%s" % (len(ms), "\n".join("%s (%d B)" % (m.name + ("/" if m.isdir() else ""), m.size) for m in ms[:300]))
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if ext == ".docx":
                return _read_docx(z)
            if ext == ".pptx":
                return _read_pptx(z)
            if ext == ".xlsx":
                return _read_xlsx(z)
            if ext == ".odt":
                return _read_odt(z)
            infos = z.infolist()
            return "zip: %d entries\n%s%s" % (len(infos), "\n".join("%s (%d B)" % (i.filename, i.file_size) for i in infos[:300]),
                                              "\n...(300 se zyada)" if len(infos) > 300 else "")
    except Exception as e:
        return "(%s padhne me dikkat: %s)" % (ext, str(e)[:120])


# ---------- UNZIP ----------
def do_unzip(arg):
    parts = arg.split()
    if not parts:
        return "ERROR: @@UNZIP <file> [folder]", False, False, None
    src = safe(parts[0])
    if not os.path.isfile(src):
        return "ERROR: file nahi mili: %s" % parts[0], False, False, None
    stem = re.sub(r"(\.tar\.gz|\.tgz|\.tar|\.zip|\.gz)$", "", os.path.basename(src), flags=re.I) or "out"
    dest_rel = parts[1] if len(parts) > 1 else os.path.join(os.path.dirname(os.path.relpath(src, WORK)), stem)
    dest = safe(dest_rel)
    os.makedirs(dest, exist_ok=True)
    n, total, skipped = 0, 0, 0

    def put(name, data):
        nonlocal n, total, skipped
        t = os.path.realpath(os.path.join(dest, name))
        if not (t == dest or t.startswith(dest + os.sep)) or os.path.isdir(t):
            skipped += 1
            return
        if n >= 3000 or total + len(data) > 150 * 1024 * 1024:
            raise ValueError("bahut bada archive (3000 files ya 150MB se zyada)")
        os.makedirs(os.path.dirname(t), exist_ok=True)
        snap(t)
        with open(t, "wb") as f:
            f.write(data)
        n += 1
        total += len(data)

    low = src.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(src) as z:
            for i in z.infolist():
                if not i.is_dir():
                    if i.file_size > 100 * 1024 * 1024:
                        skipped += 1
                        continue
                    put(i.filename, z.read(i))
    elif low.endswith((".tar", ".tgz", ".tar.gz")):
        with tarfile.open(src) as t:
            for m in t.getmembers():
                if m.isfile() and m.size <= 100 * 1024 * 1024:
                    put(m.name, t.extractfile(m).read())
                elif m.isfile():
                    skipped += 1
    elif low.endswith(".gz"):
        with gzip.open(src) as g:
            put(stem, g.read(100 * 1024 * 1024))
    else:
        return "ERROR: sirf .zip .tar .tgz .tar.gz .gz khulte hain", False, False, None
    return "khola: %d files -> %s (%d bytes)%s" % (n, os.path.relpath(dest, WORK), total, (", %d chhodi (unsafe/bahut badi)" % skipped) if skipped else ""), n > 0, True, None


# ---------- WEB (sirf padhna) ----------
class _TextOut(html.parser.HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "pre", "table", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "head"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, d):
        if not self.skip and d.strip():
            self.out.append(d)


def _public_host(host):
    try:
        for fam, _, _, _, sa in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(sa[0].split("%")[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return False
        return True
    except Exception:
        return False


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _public_host(urllib.parse.urlparse(newurl).hostname or ""):
            raise urllib.error.URLError("redirect private address par ja raha hai, roka")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def do_web(arg):
    url = arg.strip().split()[0] if arg.strip() else ""
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return "ERROR: @@WEB https://... (poora link do)", False, False, None
    if not _public_host(u.hostname):
        return "ERROR: ye address private/local hai, allowed nahi", False, False, None
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json,text/plain,*/*"})
    def _fetch(dead):
        with urllib.request.build_opener(_SafeRedirect).open(req, timeout=25) as r:
            return r.headers.get("Content-Type", ""), r.read(2 * 1024 * 1024)
    try:
        ctype, raw = cancellable(_fetch)
    except Cancelled:
        return "⏹ roka gaya", False, False, None
    if not re.search(r"text|json|xml|javascript", ctype, re.I) and b"\0" in raw[:2000]:
        return "(%s, %d bytes: ye text nahi hai, padha nahi gaya)" % (ctype or "binary", len(raw)), False, True, None
    text = raw.decode("utf-8", "replace")
    if "html" in ctype.lower() or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        p = _TextOut()
        try:
            p.feed(text)
        except Exception:
            pass
        text = re.sub(r"[ \t]+", " ", "".join(p.out))
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return "[%s]\n%s" % (url, clip(text, FMT_MAX) if len(text) > FMT_MAX else text), False, True, None


# ---------- WRITE se PDF / DOCX / XLSX banana ----------
def _blocks(text):
    """'# heading', '## heading', '- bullet', baaki paragraph, khaali line = gap."""
    out = []
    for ln in text.split("\n"):
        s = ln.rstrip()
        if s.startswith("## "):
            out.append(("h2", s[3:].strip()))
        elif s.startswith("# "):
            out.append(("h1", s[2:].strip()))
        elif re.match(r"\s*[-*] ", s):
            out.append(("li", re.sub(r"^\s*[-*] ", "", s)))
        elif not s.strip():
            out.append(("gap", ""))
        else:
            out.append(("p", s.strip()))
    return out


def make_docx(text):
    def esc(t):
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = []
    for k, t in _blocks(text):
        if k == "gap":
            body.append("<w:p/>")
            continue
        rpr = {"h1": '<w:rPr><w:b/><w:sz w:val="36"/></w:rPr>', "h2": '<w:rPr><w:b/><w:sz w:val="28"/></w:rPr>'}.get(k, "")
        if k == "li":
            t = "\u2022 " + t
        body.append('<w:p><w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (rpr, esc(t)))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr></w:body></w:document>' % "".join(body))
    ct = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def _col_name(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def make_xlsx(text):
    import csv
    lines = [l for l in text.split("\n") if l.strip()]
    delim = "\t" if lines and "\t" in lines[0] else ("|" if lines and "|" in lines[0] and "," not in lines[0] else ",")
    rows = []
    for r in csv.reader(lines, delimiter=delim):
        rows.append([c.strip() for c in r])

    def esc(t):
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    xr = []
    for ri, row in enumerate(rows, 1):
        cs = []
        for ci, v in enumerate(row):
            ref = "%s%d" % (_col_name(ci), ri)
            if v == "":
                continue
            if v.startswith("="):
                cs.append('<c r="%s"><f>%s</f></c>' % (ref, esc(v[1:])))
            elif re.fullmatch(r"-?\d+(\.\d+)?", v) and not (len(v) > 1 and v.startswith("0") and not v.startswith("0.")):
                cs.append('<c r="%s"><v>%s</v></c>' % (ref, v))
            else:
                cs.append('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, esc(v)))
        xr.append('<row r="%d">%s</row>' % (ri, "".join(cs)))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>' % "".join(xr))
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wrels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    ct = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wrels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def make_pdf(text):
    """Simple A4 PDF (Helvetica, latin-1 akshar). Devanagari/emoji '?' ban jate hain."""
    W, H, M = 595, 842, 50
    pages, cur, y = [], [], H - M

    def width(s, size):
        return len(s) * size * 0.52

    def wrap(s, size, maxw):
        words, lines, ln = s.split(" "), [], ""
        for w in words:
            t = (ln + " " + w).strip()
            if width(t, size) <= maxw or not ln:
                ln = t
            else:
                lines.append(ln)
                ln = w
        lines.append(ln)
        return lines

    def enc(s):
        return s.encode("cp1252", "replace").decode("cp1252").encode("latin-1", "replace")

    bad = [0]

    def emit(font, size, x, s):
        nonlocal y, cur
        if y - size < M:
            pages.append(cur)
            cur, y = [], H - M
        e = enc(s)
        bad[0] += e.count(b"?") - s.count("?")
        e = e.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        cur.append(b"BT /%s %d Tf %d %d Td (" % (font.encode(), size, x, y - size) + e + b") Tj ET")
        y -= int(size * 1.35)

    for k, t in _blocks(text):
        if k == "gap":
            y -= 7
            continue
        font, size, x = {"h1": ("F2", 20, M), "h2": ("F2", 15, M), "li": ("F1", 11, M + 14)}.get(k, ("F1", 11, M))
        if k in ("h1", "h2"):
            y -= 4
        if k == "li":
            t = "- " + t
        for ln in wrap(t, size, W - M - x):
            emit(font, size, x, ln)
    pages.append(cur)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None,
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    kids = []
    for pg in pages:
        content = b"\n".join(pg)
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        cid = len(objs)
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (W, H, cid))
        kids.append(b"%d 0 R" % len(objs))
    objs[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % len(kids)
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out), bad[0]


def make_binary(ext, text):
    """(bytes, note). ext: .pdf/.docx/.xlsx"""
    if ext == ".docx":
        return make_docx(text), ""
    if ext == ".xlsx":
        return make_xlsx(text), ""
    data, bad = make_pdf(text)
    return data, (" (%d akshar '?' ban gaye: PDF me sirf English/latin akshar chalte hain)" % bad) if bad else ""

# ================= SEARCH + NOTIFY =================
def _strip_tags(t):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", t or ""))).strip()


def do_search(q):
    q = q.strip()
    if not q:
        return "ERROR: @@SEARCH <kya dhundhna hai>", False, False, None
    res = []
    bk = _env("BRAVE_KEY")
    if bk:                                   # Brave Search API (free plan), sabse bharosemand
        req = urllib.request.Request("https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": q, "count": 8}),
                                     headers={"Accept": "application/json", "X-Subscription-Token": bk, "User-Agent": UA})
        def _brave(dead):
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        try:
            j = cancellable(_brave)
        except Cancelled:
            return "⏹ roka gaya", False, False, None
        for x in (j.get("web") or {}).get("results", [])[:8]:
            res.append((_strip_tags(x.get("title")), x.get("url", ""), _strip_tags(x.get("description"))))
    else:                                    # bina key: DuckDuckGo ka html page
        req = urllib.request.Request("https://html.duckduckgo.com/html/", urllib.parse.urlencode({"q": q}).encode(),
                                     {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
        def _ddg(dead):
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read(1500000).decode("utf-8", "replace")
        try:
            page = cancellable(_ddg)
        except Cancelled:
            return "⏹ roka gaya", False, False, None
        links = [(m.group(0), m.end()) for m in re.finditer(r"<a\b[^>]*result__a[^>]*>.*?</a>", page, re.S)]
        for i, (a, end) in enumerate(links[:8]):
            h = re.search(r'href="([^"]+)"', a)
            url = html.unescape(h.group(1)) if h else ""
            u = re.search(r"uddg=([^&]+)", url)
            if u:
                url = urllib.parse.unquote(u.group(1))
            nxt = links[i + 1][1] if i + 1 < len(links) else len(page)
            sn = re.search(r"result__snippet[^>]*>(.*?)</(?:a|td|div)>", page[end:nxt], re.S)
            res.append((_strip_tags(a), url, _strip_tags(sn.group(1)) if sn else ""))
        if not res and re.search(r"anomaly|captcha|blocked", page, re.I):
            return "ERROR: DuckDuckGo ne roka (bahut request). Thodi der baad, ya BRAVE_KEY environment me daalo", False, False, None
    if not res:
        return "(kuch nahi mila)", False, True, None
    return "\n\n".join("%d. %s\n%s\n%s" % (i, t, u, sn[:250]) for i, (t, u, sn) in enumerate(res, 1)) + \
        "\n\n(kisi link ka poora text padhne ko @@WEB <link>)", False, True, None


def notify(text):
    """ntfy.sh (NTFY_TOPIC) AUR Telegram (TG_TOKEN + TG_CHAT), jo-jo set ho sabhi par message. Kam se kam ek gaya to True."""
    text = (text or "")[:900]
    sent = False
    topic, tok, chat = _env("NTFY_TOPIC"), _env("TG_TOKEN"), _env("TG_CHAT")
    if topic:
        try:
            urllib.request.urlopen(urllib.request.Request("https://ntfy.sh/" + urllib.parse.quote(topic), text.encode("utf-8"),
                                                          {"Title": "Mumbai", "User-Agent": UA}), timeout=15).read()
            sent = True
        except Exception as e:
            log("ntfy fail: %s" % str(e)[:80])
    if tok and chat:
        try:
            urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % tok,
                                   urllib.parse.urlencode({"chat_id": chat, "text": "Mumbai: " + text}).encode(), timeout=15).read()
            sent = True
        except Exception as e:
            log("telegram fail: %s" % str(e)[:80])
    return sent


def notify_bg(text):
    threading.Thread(target=notify, args=(text,), daemon=True).start()


def do_notify(msg):
    if not (_env("NTFY_TOPIC") or (_env("TG_TOKEN") and _env("TG_CHAT"))):
        return "ERROR: notify set nahi. Environment me NTFY_TOPIC (ntfy app) ya TG_TOKEN + TG_CHAT (Telegram) daalo", False, False, None
    ok = notify(msg or "Mumbai ka kaam")
    return ("message bheja" if ok else "ERROR: message nahi gaya"), False, ok, None



# ---------- commands padhna ----------
CMD_NAMES = "SPEC|TEMPLATE|DEPS|WRITEB64|WRITE|EDIT|PLAN|CHECK|VERIFY|TREE|GREP|LS|READ|RUN|BG|LOG|KILL|UNZIP|WEB|SEARCH|NOTIFY|ZIP|NOTE|LEARN|ASK|LOOK|DONE"
CMD_RE = re.compile(r"^[\s*`>_#\-]*@@(%s)\b[\s*`]*(.*)$" % CMD_NAMES)
# "...baat.@@PLAN": command line ke beech me chipka ho to alag line bana do (kisi bhi space/backtick/quote ke baad wala nahi)
GLUE_RE = re.compile(r"(?<=[^\s`*_\"'(\[>#\-])@@(?:%s)\b" % CMD_NAMES)
# "[Assistant]:" / "assistant:" jaisa tag line ke shuru me
TAG_RE = re.compile(r"^\s*(?:\[\s*(?:assistant|ai|mumbai|model|bot)\s*\]\s*:?|(?:assistant|mumbai)\s*:)\s*", re.I)
# AI ki English "soch" (The user wants..., I need to...) jo galti se jawab me aa jati hai
LEAK_RE = re.compile(r"^\s*(?:The user\b|User (?:wants|is asking|asked|said|has|needs)\b|I (?:need|should|must|have) to\b|"
                     r"I(?:'ll| will)\b|Let me\b|Let's\b|Wait[,.]|Hmm\b|Looking at\b|My (?:plan|approach)\b|"
                     r"Since the user\b|Now,? I\b|First,? I\b)", re.I)
THINK_RE = re.compile(r"<(think|thinking|thought|reasoning)>.*?</\1>", re.S | re.I)


def clean_reply(text):
    """<think>...</think> hata deta hai (uske andar likhe @@ commands chal na jayein)."""
    return THINK_RE.sub("", text or "").strip("\n")


def parse(text):
    """(commands, baaki baat). command = (naam, arg, body, poora_aaya?)."""
    cmds, prose, dropped, lines, i = [], [], [], text.split("\n"), 0
    leaking = False
    while i < len(lines):
        line = TAG_RE.sub("", lines[i], count=1)
        g = GLUE_RE.search(line)
        if g:                                   # "baat.@@PLAN" -> do alag lines
            lines[i:i + 1] = [line[:g.start()], line[g.start():]]
            continue
        m = CMD_RE.match(line)
        if not m and re.fullmatch(r"[\s*`]*@@END[\s*`]*", line):
            i += 1                              # bhatka hua @@END chat me na dikhe
            continue
        if not m:
            if not line.strip():
                leaking = False
            elif leaking or LEAK_RE.match(line):
                leaking = True
                dropped.append(line)
                i += 1
                continue
            prose.append(line)
            i += 1
            continue
        leaking = False
        k, a = m.group(1), m.group(2).strip().strip("*`").strip()
        i += 1
        if k in BODY_KINDS:
            term, custom = "@@END", False
            h = re.search(r"\s*<<\s*['\"]?(\w+)['\"]?\s*$", a)
            if h:
                term, a, custom = h.group(1), a[:h.start()].strip(), True
            body, complete = [], False
            while i < len(lines):
                if lines[i].strip() == term:
                    complete = True
                    i += 1
                    break
                if k in ("PLAN", "SPEC") and not custom and CMD_RE.match(lines[i]):
                    complete = True      # @@END bhool gaya: agla @@command plan ko yahin band kar deta hai
                    break
                body.append(lines[i])
                i += 1
            if k in ("PLAN", "SPEC"):
                complete = True          # plan chhota hota hai, @@END na ho to bhi maan lo
            if body and body[0].strip().startswith("```"):
                body = body[1:]
            if body and body[-1].strip() == "```":
                body = body[:-1]
            cmds.append((k, a, "\n".join(body) + "\n", complete))
        else:
            cmds.append((k, a, "", True))
    out = "\n".join(prose).strip()
    if not out and not cmds and dropped:        # poora jawab hi English tha: mat chhupao
        out = "\n".join(dropped).strip()
    return cmds, out


# ---------- files ----------
def safe(p):
    p = p.strip().strip("`'\"") or "."
    full = os.path.realpath(os.path.join(WORK, p))
    if full != WORK and not full.startswith(WORK + os.sep):
        raise ValueError("path workspace ke bahar hai")
    return full


def clip(s, n=3000):
    """Lambe text me shuru aur AAKHIR dono rakhta hai (error aksar aakhir me hota hai)."""
    if len(s) <= n:
        return s
    head = n // 3
    return s[:head] + "\n...(beech ka hissa kata gaya)...\n" + s[-(n - head):]


def snap(p):
    if not UNDO_STACK:
        return
    cur, rel = UNDO_STACK[-1], os.path.relpath(p, WORK)
    if rel in cur:
        return
    if os.path.isfile(p):
        if os.path.getsize(p) > 5 * 1024 * 1024:
            return
        with open(p, "rb") as f:
            cur[rel] = f.read()
    else:
        cur[rel] = None


def prune(d):
    while d != WORK and d.startswith(WORK + os.sep):
        try:
            if os.listdir(d):
                break
            os.rmdir(d)
        except OSError:
            break
        d = os.path.dirname(d)


SKIP_DIRS = {".git", ".gradle", ".idea", ".bg", ".mumbai", ".cache", ".venv", "venv", "__pycache__", "node_modules"}


def list_files(limit=1000):
    """Saari project files. Dot-folder (.github jaise) shamil hain; sirf kachra (.git, .gradle, node_modules...) chhoota hai."""
    out = []
    for root, dirs, files in os.walk(WORK):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            out.append(os.path.relpath(os.path.join(root, f), WORK))
    out.sort()
    return out[:limit] if limit else out


def list_files_recent(limit=1000):
    """(files nayi-pehle, {file: kitne second pehle badli}) - 📁 me nayi/badli files upar dikhen."""
    fs, now, age = list_files(None), time.time(), {}

    def mt(f):
        try:
            return os.path.getmtime(os.path.join(WORK, f))
        except OSError:
            return 0
    fs.sort(key=mt, reverse=True)
    fs = fs[:limit]
    for f in fs:
        age[f] = int(max(0, now - mt(f)))
    return fs, age


def manifest():
    m = {}
    for f in list_files(None):
        try:
            st = os.stat(os.path.join(WORK, f))
            m[f] = (st.st_mtime, st.st_size)
        except OSError:
            pass
    return m


def diff_manifest(before):
    """RUN ke baad: nayi files undo me daalta hai. (nayi, badli) lautata hai."""
    after = manifest()
    new = [f for f in after if f not in before]
    mod = [f for f in after if f in before and after[f] != before[f]]
    if UNDO_STACK:
        for f in new:
            UNDO_STACK[-1].setdefault(f, None)
    return new, mod


INDENT_EXT = {".py", ".pyi", ".pyx", ".yml", ".yaml", ".coffee", ".haml", ".slim", ".sass", ".styl", ".nim", ".mk"}
MDLINK_RE = re.compile(r"\[([^\]\n]{1,300})\]\(<?([^)\s>]{1,600})>?\)")


def unlink(s):
    """AI ke markdown link [http://x](http://x) ko seedha http://x bana deta hai (file me link-syntax nahi jani chahiye)."""
    if "](" not in s:
        return s

    def norm(u):
        return re.sub(r"^(https?://|mailto:)", "", u.strip().strip("<>")).rstrip("/").lower()

    def fix(m):
        t, h = m.group(1), m.group(2)
        if re.match(r"(?i)(https?://|www\.)", t.strip()) or norm(t) == norm(h):
            return h
        return m.group(0)
    return MDLINK_RE.sub(fix, s)


def find_replace(content, s, r, strict=False):
    """strict=True (Python/YAML jaisi indentation wali file): indentation bilkul file jaisi chahiye, fuzzy nahi."""
    n = content.count(s)
    if n == 1:
        return content.replace(s, r, 1), None
    if n > 1:
        return None, "SEARCH %d jagah mila, thoda aur aas-paas ki lines jodo" % n
    cl, sl, rl = content.split("\n"), s.split("\n"), r.split("\n")
    while sl and not sl[-1].strip():
        sl.pop()
    while sl and not sl[0].strip():
        sl.pop(0)
    if not sl:
        return None, "SEARCH khali hai"

    def ind(x):
        return len(x) - len(x.lstrip())

    if strict:
        norms = (lambda x: x.rstrip(), lambda x: x[:ind(x)] + " ".join(x.split()))
    else:
        norms = (lambda x: x.rstrip(), lambda x: x.strip(), lambda x: " ".join(x.split()))
    sfx = " (is file me indentation bilkul waisi honi chahiye jaisi file me hai)" if strict else ""
    for step, norm in enumerate(norms):
        nsl, ncl = [norm(x) for x in sl], [norm(x) for x in cl]
        idx = [i for i in range(len(cl) - len(sl) + 1) if ncl[i:i + len(sl)] == nsl]
        if step > 0 and not strict:
            # sirf wahi jagah jahan har line ka indentation-fark ek jaisa ho (warna galat block ho sakta hai)
            idx = [i for i in idx if len({ind(cl[i + j]) - ind(sl[j]) for j in range(len(sl)) if sl[j].strip()}) <= 1]
        if len(idx) > 1:
            return None, "SEARCH %d jagah mila (space ka fark chhod ke), thoda aur aas-paas ki lines jodo" % len(idx)
        if len(idx) == 1:
            i0, new = idx[0], rl
            if step > 0 and not strict:              # indentation ka fark theek karo
                d = ind(cl[i0]) - ind(sl[0])
                if d > 0:
                    new = [(" " * d + x) if x.strip() else x for x in rl]
                elif d < 0:
                    new = [x[min(-d, ind(x)):] if x.strip() else x for x in rl]
            cl[i0:i0 + len(sl)] = new
            return "\n".join(cl), None
    first = sl[0].strip()
    hit = [i for i, l in enumerate(cl) if first and first in l]
    if not hit:
        close = difflib.get_close_matches(first, [l.strip() for l in cl], n=1, cutoff=0.6)
        hit = [i for i, l in enumerate(cl) if close and l.strip() == close[0]]
    if hit:
        i = hit[0]
        return None, ("SEARCH ki pehli line line %d ke paas milti hai: '%s'. Aage ki lines alag hain. "
                      "@@READ file %d-%d se asli text dekh ke copy karo%s" % (
                          i + 1, cl[i].strip()[:80], max(1, i - 1), i + len(sl) + 3, sfx))
    return None, "SEARCH file me nahi mila. @@READ se asli text dekh ke bilkul waisa hi copy karo" + sfx


def apply_edit(content, body, strict=False):
    blocks = re.findall(r"<{5,9} SEARCH\n(.*?)\n={5,9}\n(.*?)\n?>{5,9} REPLACE", body, re.S)
    if not blocks:
        return None, "koi SEARCH/REPLACE block nahi mila"
    for i, (s, r) in enumerate(blocks, 1):
        if not s.strip():
            return None, "block %d: SEARCH khali hai" % i
        content, err = find_replace(content, s, r, strict)
        if err:
            return None, "block %d: %s" % (i, err)
    return content, None


# ---------- shell ----------
BLOCKED = {"sudo", "su", "mkfs", "shutdown", "reboot", "poweroff", "halt"}
TOUCHY = {"rm", "mv", "chmod", "chown", "shred", "truncate", "rmdir", "dd", "ln", "cp"}


WRAPPERS = {"env", "command", "nohup", "time", "exec", "nice", "builtin", "stdbuf", "timeout"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
INTERP_RE = re.compile(r"^(python[\d.]*|node(js)?|perl|ruby|php|deno|bun)$")
INLINE_FLAGS = {"-c", "-e", "-E", "-p", "-r", "--eval", "--print"}
INLINE_DANGER = re.compile(
    r"rmtree|os\.(?:remove|unlink|rmdir|removedirs|rename|replace|system|popen|exec\w*|kill)|shutil\.(?:move|rmtree)|"
    r"subprocess|\.unlink\(|\.rmdir\(|\bfs\.\w*(?:rm|unlink|rmdir|rename)\w*|child_process|File\.delete|FileUtils|"
    r"\bexec\s*\(|\bsystem\s*\(|\.(?:rmSync|rmdirSync|unlinkSync|renameSync|rmdir|unlink|rename|rm)\(")


def split_cmd(cmd):
    """Command ko ; && || | & aur nayi line par todta hai, par quotes ("..." '...') ke andar nahi."""
    segs, cur, q, i = [], [], None, 0
    while i < len(cmd):
        ch = cmd[i]
        if q:
            cur.append(ch)
            if ch == "\\" and q == '"' and i + 1 < len(cmd):
                i += 1
                cur.append(cmd[i])
            elif ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            cur.append(ch)
        elif ch == "\\" and i + 1 < len(cmd):
            cur.append(ch)
            i += 1
            cur.append(cmd[i])
        elif ch in ";&|\n":
            segs.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    segs.append("".join(cur))
    return segs


def where(x, cwd):
    """(poora path, project folder ke andar hai?)"""
    full = os.path.realpath(os.path.join(cwd, os.path.expandvars(os.path.expanduser(x))))
    return full, (full == WORK or full.startswith(WORK + os.sep))


def risky(cmd, cwd=None, depth=0):
    """Khatarnak command ki motti jaanch. Ye asli sandbox NAHI hai, bas galti se bachav hai."""
    cwd = cwd or WORK
    if depth > 3:
        return "command bahut ulja hua hai"
    if re.search(r"(curl|wget)[^|;]*\|\s*(ba|z)?sh\b", cmd):
        return "curl/wget se seedha sh chalana allowed nahi"
    for seg in split_cmd(cmd):
        bare = re.sub(r"'[^']*'|\"[^\"]*\"", "''", seg)                     # quotes ke andar ka > mat dekho
        for m in re.finditer(r"(?<![<\d&>])>>?\s*([^\s|&;<>()]+)", bare):      # > file, >> file
            tgt = m.group(1).strip("'\"")
            if tgt.startswith("/dev/") or tgt.startswith("&"):
                continue
            if not where(tgt, cwd)[1]:
                return "'> %s' project folder ke bahar likh raha hai" % tgt[:40]
        try:
            t = shlex.split(seg)
        except ValueError:
            continue
        while t:
            t[0] = t[0].lstrip("({")
            if not t[0] or re.match(r"^\w+=", t[0]):
                t.pop(0)
            elif os.path.basename(t[0]) in WRAPPERS:
                t.pop(0)
                while t and (t[0].startswith("-") or re.match(r"^\d+[smhd]?$", t[0]) or re.match(r"^\w+=", t[0])):
                    t.pop(0)
            else:
                break
        if not t:
            continue
        c = os.path.basename(t[0])
        if c in BLOCKED:
            return "'%s' allowed nahi hai" % c
        if c in ("cd", "pushd"):                       # cd .. && rm -rf x jaisi chaal
            tgt = t[1] if len(t) > 1 else "~"
            if tgt == "-":
                return "'cd -' allowed nahi hai"
            full, inside = where(tgt, cwd)
            if not inside:
                return "'cd %s' project folder ke bahar jata hai" % tgt[:40]
            cwd = full
            continue
        if c in SHELLS and "-c" in t[1:]:              # bash -c "..." ke andar bhi dekho
            k = t.index("-c")
            if len(t) > k + 1:
                r = risky(t[k + 1], cwd, depth + 1)
                if r:
                    return r
            continue
        if c == "eval":
            r = risky(" ".join(t[1:]), cwd, depth + 1)
            if r:
                return r
            continue
        if INTERP_RE.match(c) and any(x in INLINE_FLAGS for x in t[1:]):
            if INLINE_DANGER.search(" ".join(t[1:])):
                return "inline code (-c/-e) me file hatane/badalne wala kaam allowed nahi, script file me likho"
            continue
        if c == "xargs":
            sub = [x for x in t[1:] if not x.startswith("-")]
            if sub and os.path.basename(sub[0]) in BLOCKED:
                return "'%s' allowed nahi hai" % os.path.basename(sub[0])
            continue
        if c == "find" and any(x in ("-delete", "-exec", "-execdir", "-ok") for x in t):
            paths = []
            for x in t[1:]:
                if x.startswith("-") or x in ("(", "!"):
                    break
                paths.append(x)
            for x in paths or ["."]:
                full, inside = where(x, cwd)
                if not inside:
                    return "'find %s ... -delete/-exec' project folder ke bahar hai" % x[:40]
                if full == WORK and "-delete" in t:
                    return "poore project folder par find -delete mat chalao"
            continue
        if c in TOUCHY:
            args = [x for x in t[1:] if not x.startswith("-")]
            if c == "dd":
                args = [x.split("=", 1)[1] for x in t[1:] if "=" in x]
            elif c == "cp":
                args = args[-1:]
            for x in args:
                full, inside = where(x, cwd)
                if not inside:
                    return "'%s %s' project folder ke bahar hai" % (c, x[:40])
                if full == WORK and c in ("rm", "rmdir", "mv", "shred"):
                    return "poora project folder mat hatao/hilao"
    return None


def kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            p.kill()
        except OSError:
            pass


def kill_proc():
    p = STATE.get("proc")
    if p and p.poll() is None:
        kill_group(p)


def kill_bg():
    for j in BG.values():
        if j["p"].poll() is None:
            kill_group(j["p"])


def run_shell(cmd, timeout=RUN_TIMEOUT):
    """(exit_code, output, kyun_roka: None/'timeout'/'cancel'). stdin band, timeout par bacche bhi maare jate hain."""
    p = subprocess.Popen(cmd, shell=True, cwd=WORK, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, env=ENV, start_new_session=True)
    STATE["proc"] = p
    t0, why, out = time.time(), None, b""
    try:
        while True:
            try:
                out, _ = p.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if STATE["cancel"]:
                    why = "cancel"
                elif time.time() - t0 > timeout:
                    why = "timeout"
                if why:
                    kill_group(p)
                    try:
                        out, _ = p.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        out = b""
                    break
    finally:
        STATE["proc"] = None
    return p.returncode, out.decode("utf-8", "replace"), why


def bg_start(cmd):
    BG_N[0] += 1
    jid = "b%d" % BG_N[0]
    os.makedirs(os.path.join(WORK, ".bg"), exist_ok=True)
    path = os.path.join(WORK, ".bg", jid + ".log")
    lf = open(path, "wb")
    p = subprocess.Popen(cmd, shell=True, cwd=WORK, stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                         env=ENV, start_new_session=True)
    lf.close()
    BG[jid] = {"p": p, "cmd": cmd, "log": path}
    return jid


def bg_status(jid, n=3000):
    j = BG.get(jid.strip())
    if not j:
        return "ERROR: aisi id nahi: %s (chalu: %s)" % (jid, ", ".join(BG) or "koi nahi")
    code = j["p"].poll()
    try:
        with open(j["log"], "rb") as f:
            txt = f.read().decode("utf-8", "replace")
    except OSError:
        txt = ""
    st = "chal raha hai" if code is None else "khatam, exit %d" % code
    return "[%s] %s\ncmd: %s\n%s" % (jid, st, j["cmd"][:100], clip(txt, n) or "(abhi output nahi)")


# ---------- project notes ----------
def append_pm(lines, task=""):
    p = os.path.join(WORK, "PROJECT.md")
    snap(p)
    new = not os.path.exists(p)
    with open(p, "a", encoding="utf-8") as f:
        if new:
            f.write("# %s\n\n## Kaam\n%s\n" % (STATE["proj"], (task or STATE["task"])[:500]))
        f.write("\n".join(lines) + "\n")


def clean_plan_item(l):
    """'1. x', '1) x', '1 x', '- x', '**x**' sab se sirf kadam ka text nikalta hai."""
    l = re.sub(r"^\s*[-*•–—]\s+", "", l)
    l = re.sub(r"[*`]", "", l)
    l = re.sub(r"^\s*(?:\d+\s*[.):]\s*|\d+\s+)", "", l)
    l = re.sub(r"^\s*[-–—]\s+", "", l)
    return l.strip()


def plan_pm(items, task=""):
    """PROJECT.md me purana '## Plan' hata ke naya plan likhta hai (@@NOTE ki lines jaisi ki taisi rehti hain)."""
    p = os.path.join(WORK, "PROJECT.md")
    snap(p)
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")
    except OSError:
        lines = ["# %s" % STATE["proj"], "", "## Kaam", (task or STATE["task"])[:500]]
    out, i = [], 0
    while i < len(lines):
        if lines[i].strip() == "## Plan":
            i += 1
            while i < len(lines) and (not lines[i].strip() or re.match(r"^\s*\d+[.)]\s", lines[i])):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    text = "\n".join(out).rstrip("\n") + "\n\n## Plan\n" + "\n".join(
        "%d. %s" % (n + 1, x) for n, x in enumerate(items)) + "\n"
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def context_block():
    p = ["", "", "--- AUTO JAANKARI (har kadam naya) ---", "Project: %s" % STATE["proj"]]
    try:
        p.append(mode_note())
    except Exception as e:
        log("mode_note galti: %s" % e)
    try:
        if STATE.get("goal"):
            if STATE.get("brief"):
                p.append(STATE["brief"])
            p.append("SAMAJH: " + STATE["goal"] + ("\nDONE CRITERIA (@@DONE par inka saboot do):\n" + "\n".join(STATE["criteria"]) if STATE.get("criteria") else ""))
        sk = seekha_text()
        if sk:
            p.append("SEEKHA (pehle ki galtiyan, dobara mat karo):\n" + sk)
    except Exception as e:
        log("samajh/seekha galti: %s" % e)
    notes = ""
    try:
        with open(os.path.join(WORK, "PROJECT.md"), encoding="utf-8", errors="replace") as f:
            notes = f.read().strip()
    except OSError:
        pass
    p.append("PROJECT.md:\n" + clip(notes, 3500) if notes else "PROJECT.md abhi nahi hai (@@NOTE se ban jayegi).")
    items = STATE["plan_items"]
    if items:
        p.append("PLAN:\n" + "\n".join("%d %s %s" % (i + 1, "✅" if (i + 1) in STATE["plan_ck"] else "⬜", x)
                                       for i, x in enumerate(items)))
    fs = list_files(120)
    p.append("Files: " + (", ".join(fs) + (" ..." if len(fs) >= 120 else "") if fs else "(abhi koi nahi)"))
    try:
        if cur_mode() == "android":
            errs = [m for lv, m in verify_project(force_android=True) if lv == "E"]
            p.append("ANDROID JAANCH (Mumbai ne khud ki, har kadam naya): " + (
                "sab theek ✅" if not errs else "%d baaki:\n" % len(errs) + "\n".join("- " + clip(e, 170) for e in errs[:6])))
    except Exception:
        pass
    try:
        sc = spec_context()
        if sc:
            p.append(sc)
    except Exception as e:
        log("spec_context galti: %s" % e)
    return "\n".join(p)


def build_msgs():
    m = STATE["msgs"]
    return [{"role": "system", "content": SYSTEM + context_block()}] + m[1:]


# ---------- project jaanch (verify) ----------
# AI ke bharose nahi: DONE se pehle Mumbai khud project ki jaanch karta hai aur galtiyan wapas bhejta hai.
ANDROID_NS = "http://schemas.android.com/apk/res/android"
ANDROID_TASK_RE = re.compile(r"android|\bapk\b|kotlin|gradle", re.I)
LIB_PFX = ("abc_", "mtrl_", "m3_", "material_", "design_", "appcompat_", "notification_", "mr_", "androidx_")
LIB_STYLE = ("Theme.Material", "Theme.AppCompat", "Widget.", "TextAppearance.", "ThemeOverlay.", "Base.", "Platform.",
             "ShapeAppearance", "Animation.", "Theme.Holo", "Theme.DeviceDefault")
VALUE_TAGS = {"string": "string", "color": "color", "style": "style", "dimen": "dimen", "integer": "integer",
              "bool": "bool", "string-array": "array", "integer-array": "array", "array": "array",
              "plurals": "plurals", "drawable": "drawable"}
REF_TYPES = {"string", "drawable", "mipmap", "layout", "color", "dimen", "menu", "xml", "anim", "bool", "integer",
             "array", "plurals", "raw", "font", "style", "id"}
GRADLE_NAMES = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
SRC_EXT = (".kt", ".java")
TRANSITIVE_MATERIAL = {"appcompat", "recyclerview", "cardview", "viewpager2", "constraintlayout"}
DEP_MAP = (("androidx.recyclerview", "recyclerview"), ("com.google.android.material", "material"),
           ("androidx.appcompat", "appcompat"), ("androidx.constraintlayout", "constraintlayout"),
           ("androidx.cardview", "cardview"), ("androidx.viewpager2", "viewpager2"),
           ("androidx.swiperefreshlayout", "swiperefreshlayout"), ("androidx.documentfile", "documentfile"))
LINTED_EXT = (".py", ".json", ".xml", ".kt", ".java", ".kts", ".gradle", ".yml", ".yaml")
VERIFY_ITEM_RE = re.compile(r"\b(?:test|build|verify|compile)\b|jaanch", re.I)
VERIFY_CMD_RE = re.compile(r"py_compile|node\s+--check|node\s+\S*test\S*\.[cm]?js|pytest|unittest|gradle|assemble|\btsc\b|npm\s+(?:test|run\s+build)|flake8|ruff|mypy|"
                           r"cargo\s+(?:check|build|test)|go\s+(?:build|test|vet)|\bmake\b|javac|kotlinc|xmllint|json\.tool", re.I)
LAZY_RE = re.compile(r"rest of (?:the )?(?:code|file)|baaki (?:code|file)|\.\.\.\s*\(?(?:existing|baaki|same)\b|TODO: implement", re.I)


def _read(rel, limit=600000):
    try:
        with open(os.path.join(WORK, rel), encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _nc(text):
    """Comments hatao (XML <!-- --> aur // /* */)."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", text)


def _strip_lits(text):
    """Strings/char literals hatao (bracket ginti ke liye)."""
    text = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    text = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', text)
    return re.sub(r"'(?:\\.|[^'\\\n])'", "''", text)


def _in_string(line, pos):
    return line[:pos].replace('\\"', "").count('"') % 2 == 1


def is_android_project(files):
    for f in files:
        b = os.path.basename(f)
        if b == "AndroidManifest.xml":
            return True
        if b in GRADLE_NAMES and ("com.android" in _read(f, 20000) or "android {" in _read(f, 20000)):
            return True
    return False


def res_index(files):
    """Project me kaun-kaun se resource maujood hain: {type: {naam}}."""
    have = {}

    def add(t, n):
        have.setdefault(t, set()).add(n.replace(".", "_"))
    for f in files:
        parts = f.replace("\\", "/").split("/")
        if "res" not in parts:
            continue
        i = parts.index("res")
        if len(parts) < i + 3:
            continue
        typ, fn = parts[i + 1].split("-")[0], parts[-1]
        if typ == "values":
            if not fn.endswith(".xml"):
                continue
            try:
                root = ET.parse(os.path.join(WORK, f)).getroot()
            except (ET.ParseError, OSError):
                continue
            for el in root.iter():
                nm = el.get("name")
                if not nm:
                    continue
                if el.tag == "item" and el.get("type"):
                    add(el.get("type"), nm)
                elif el.tag in VALUE_TAGS:
                    add(VALUE_TAGS[el.tag], nm)
        else:
            add(typ, re.sub(r"\.9$", "", fn.rsplit(".", 1)[0]))
            if fn.endswith(".xml"):
                for n in re.findall(r"@\+id/(\w+)", _read(f)):
                    add("id", n)
    return have


def _missing_hint(typ, name):
    if typ in ("mipmap", "drawable"):
        return "res/%s*/%s.xml ya .png banao (chhota vector xml chalega), ya jaha likha hai wo icon line hata do" % (typ, name)
    if typ in ("string", "color", "dimen", "bool", "integer", "style"):
        return "res/values/ ki xml me <%s name=\"%s\"> jodo" % (typ, name)
    if typ == "id":
        return "layout me android:id=\"@+id/%s\" do" % name
    return "res/%s/%s.xml banao" % (typ, name)


def _lib_skip(typ, name):
    if name.startswith(LIB_PFX):
        return True
    return typ == "style" and name.startswith(tuple(s.replace(".", "_") for s in LIB_STYLE) + LIB_STYLE)


def check_gradle_files(files, items, task):
    E, W = "E", "W"
    gr = {f: _read(f, 100000) for f in files if os.path.basename(f) in GRADLE_NAMES}
    alltxt = "\n".join(gr.values())
    names = set(files)
    if not any(os.path.basename(f).startswith("settings.gradle") for f in gr):
        items.append((E, "settings.gradle.kts nahi hai (root me banao: rootProject.name aur include(\":app\") ke saath)"))
    else:
        st = "\n".join(v for f, v in gr.items() if os.path.basename(f).startswith("settings.gradle"))
        if not re.search(r"include\s*\(?\s*[\"']:", st):
            items.append((E, "settings.gradle.kts me include(\":app\") nahi hai"))
    if not any(os.path.dirname(f) == "" and os.path.basename(f).startswith("build.gradle") for f in gr):
        items.append((E, "root build.gradle.kts nahi hai (plugins { id(\"com.android.application\") version \"8.5.2\" apply false ... })"))
    apps = [f for f, v in gr.items() if os.path.basename(f).startswith("build.gradle")
            and "com.android.application" in v and re.search(r"\bandroid\s*\{", v)]
    if not apps:
        items.append((E, "koi module 'com.android.application' plugin ke saath nahi mila (app/build.gradle.kts banao)"))
    if "google()" not in alltxt:
        items.append((E, "gradle me google() repository nahi hai (settings.gradle.kts ke pluginManagement/dependencyResolutionManagement me daalo)"))
    props = _read("gradle.properties")
    if not props:
        items.append((E, "gradle.properties nahi hai (usme android.useAndroidX=true likho)"))
    elif not re.search(r"^\s*android\.useAndroidX\s*=\s*true", props, re.M):
        items.append((E, "gradle.properties me android.useAndroidX=true nahi hai"))
    srcs = [f for f in files if f.endswith(SRC_EXT)]
    if any(f.endswith(".kt") for f in srcs) and not re.search(
            r"org\.jetbrains\.kotlin\.android|kotlin-android|kotlin\(\s*[\"']android[\"']\s*\)", alltxt):
        items.append((E, "Kotlin files hain par kotlin plugin nahi (id(\"org.jetbrains.kotlin.android\") root aur app dono me)"))
    for f in apps:
        v = gr[f]
        mod = os.path.dirname(f)
        for pat, msg in ((r"namespace\s*=?\s*[\"'][\w.]+[\"']", "namespace"), (r"applicationId\s*=?\s*[\"']", "applicationId"),
                         (r"compileSdk", "compileSdk"), (r"minSdk", "minSdk"), (r"targetSdk", "targetSdk")):
            if not re.search(pat, v):
                items.append((E, "%s me %s nahi hai" % (f, msg)))
        # code me jo library use hui, uski dependency
        mod_files = [x for x in files if (x.startswith(mod + "/") if mod else True)]
        code = "\n".join(_read(x, 60000) for x in mod_files
                         if x.endswith(SRC_EXT) or "/res/layout" in x or x.endswith("AndroidManifest.xml"))
        for pref, art in DEP_MAP:
            if pref in code and art not in v and not (art in TRANSITIVE_MATERIAL and "material" in v):
                items.append((E, "code me %s use hua par %s me '%s' dependency nahi" % (pref, f, art)))
        # manifest + activity
        mp = os.path.join(mod, "src", "main", "AndroidManifest.xml")
        mtxt = _read(mp)
        if not mtxt:
            items.append((E, "%s nahi hai" % mp))
            continue
        try:
            for el in ET.parse(os.path.join(WORK, mp)).getroot().iter():
                if el.tag in ("activity", "service", "receiver") and any(c.tag == "intent-filter" for c in el) \
                        and not any(k.split("}")[-1] == "exported" for k in el.attrib):
                    nm = next((x for k, x in el.attrib.items() if k.split("}")[-1] == "name"), "?")
                    items.append((E, "%s me <%s %s> me intent-filter hai par android:exported=\"true\" nahi likha (Android 12+ me build fail)" % (
                        mp, el.tag, nm)))
        except (ET.ParseError, OSError):
            pass
        ns_m = re.search(r"namespace\s*=?\s*[\"']([\w.]+)[\"']", v)
        ns = ns_m.group(1) if ns_m else ""
        for act in re.findall(r"<activity[^>]*?android:name\s*=\s*\"([^\"]+)\"", mtxt):
            fq = ns + act if act.startswith(".") else (ns + "." + act if "." not in act else act)
            simple, pkg = fq.rsplit(".", 1)[-1], fq.rsplit(".", 1)[0] if "." in fq else ""
            hit = None
            for x in srcs:
                if re.search(r"\bclass\s+%s\b" % re.escape(simple), _read(x, 60000)):
                    hit = x
                    break
            if not hit:
                items.append((E, "manifest me activity %s hai par class %s kisi .kt/.java me nahi" % (act, simple)))
                continue
            pm = re.search(r"^\s*package\s+([\w.]+)", _read(hit, 4000), re.M)
            if pm and pkg and pm.group(1) != pkg:
                items.append((E, "%s ka package '%s' hai par manifest/namespace '%s' chahta hai" % (hit, pm.group(1), pkg)))
    # workflow / wrapper
    wf = [f for f in files if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))]
    wrapper = "gradlew" in names and "gradle/wrapper/gradle-wrapper.properties" in names
    wants_apk = bool(re.search(r"apk|build", task or "", re.I))
    if not wf:
        items.append((E if wants_apk else W,
                      ".github/workflows/android.yml nahi hai (phone par gradle nahi chalta; GitHub Actions APK banata hai. "
                      "Steps: checkout, setup-java 17, gradle/actions/setup-gradle, `gradle assembleDebug`, upload-artifact)"))
    for f in wf:
        w = _read(f)
        if "./gradlew" in w and not wrapper:
            items.append((E, "%s me ./gradlew hai par gradlew/gradle-wrapper project me nahi; `gradle assembleDebug` likho" % f))
        if not re.search(r"assemble|bundle", w):
            items.append((E, "%s me assembleDebug wala build step nahi hai" % f))
        if "upload-artifact" not in w:
            items.append((W, "%s me upload-artifact step nahi (APK download kaise hogi?)" % f))
        if "setup-java" not in w:
            items.append((W, "%s me setup-java (17) step nahi" % f))


def check_android_code(files, items):
    E = "E"
    have = res_index(files)
    scan = [f for f in files if f.endswith(".xml") and ("/res/" in "/" + f or f.endswith("AndroidManifest.xml"))]
    seen = set()
    for f in scan:
        txt = _nc(_read(f))
        for typ, nm in re.findall(r"@(?!\+)(\w+)/([\w.]+)", txt):
            if typ not in REF_TYPES or _lib_skip(typ, nm) or (typ, nm, f) in seen:
                continue
            seen.add((typ, nm, f))
            if nm.replace(".", "_") not in have.get(typ, ()):
                items.append((E, "%s me @%s/%s hai par wo bana nahi: %s" % (f, typ, nm, _missing_hint(typ, nm))))
    for f in [x for x in files if x.endswith(SRC_EXT)]:
        raw = _read(f, 200000)
        for n, line in enumerate(raw.split("\n"), 1):
            for m in re.finditer(r"(?<![\w\"'])@?android:(drawable|string|color|id|layout|mipmap)/\w+", line):
                if not _in_string(line, m.start()) and not line.lstrip().startswith(("//", "*", "/*")):
                    items.append((E, "%s:%d Kotlin/Java me '%s' nahi chalta; android.R.%s.<naam> likho" % (
                        f, n, m.group(0), m.group(1))))
        txt = _nc(raw)
        for typ, nm in sorted(set(re.findall(r"(?<![\w.])R\.(\w+)\.(\w+)", txt))):
            if typ in REF_TYPES and not _lib_skip(typ, nm) and nm not in have.get(typ, ()):
                items.append((E, "%s me R.%s.%s hai par wo resource bana nahi: %s" % (f, typ, nm, _missing_hint(typ, nm))))


def js_syntax_error(txt, ext=".js"):
    """Node ho to asli `node --check`; import/export ho to module ki tarah (Cloudflare Worker .js me bhi yahi hota hai). '' = theek ya node nahi."""
    node = shutil.which("node")
    if not node:
        return ""
    is_mod = ext == ".mjs" or bool(re.search(r"^\s*(?:import\b|export\b)", txt, re.M))
    import tempfile
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".mjs" if is_mod else ".cjs", delete=False, encoding="utf-8") as fh:
            fh.write(txt)
            tmp = fh.name
        r = subprocess.run([node, "--check", tmp], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return ""
        lines = [l for l in (r.stderr or "").splitlines() if l.strip() and tmp not in l]
        return clip(" | ".join(lines[:3]) or "syntax galat", 220)
    except Exception:
        return ""
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def check_generic(files, items):
    E, W = "E", "W"
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        try:
            if os.path.getsize(os.path.join(WORK, f)) > 1024 * 1024:
                continue
        except OSError:
            continue
        if ext in (".py", ".json", ".xml", ".yml", ".yaml", ".kt", ".java", ".kts", ".gradle", ".js", ".html", ".md", ".txt"):
            txt = _read(f)
        else:
            continue
        if ext in (".kt", ".java", ".kts", ".gradle", ".py", ".js") and LAZY_RE.search(txt):
            items.append((W, "%s me 'baaki code' jaisa placeholder lagta hai; poora likho" % f))
        if ext == ".py":
            try:
                compile(txt, f, "exec")
            except SyntaxError as e:
                items.append((E, "%s:%s Python syntax galti: %s" % (f, e.lineno, e.msg)))
        elif ext in (".js", ".mjs", ".cjs"):
            err = js_syntax_error(txt, ext)
            if err:
                items.append((E, "%s JS syntax galti (node): %s" % (f, err)))
        elif ext == ".json":
            try:
                json.loads(txt)
            except ValueError as e:
                items.append((E, "%s JSON galat: %s" % (f, str(e)[:80])))
        elif ext == ".xml":
            try:
                ET.parse(os.path.join(WORK, f))
            except ET.ParseError as e:
                items.append((E, "%s XML toota hua hai: %s" % (f, str(e)[:80])))
        elif ext in (".yml", ".yaml"):
            if re.search(r"^\t", txt, re.M):
                items.append((E, "%s me tab se indent hai; YAML me sirf space chalte hain" % f))
        elif ext in (".kt", ".java", ".kts", ".gradle"):
            s = _strip_lits(_nc(txt))
            for o, c in (("{", "}"), ("(", ")"), ("[", "]")):
                if s.count(o) != s.count(c):
                    items.append((E, "%s me %s aur %s barabar nahi (%d vs %d); file adhuri kati ho sakti hai" % (
                        f, o, c, s.count(o), s.count(c))))
                    break
    # xmlns:android URL (manifest aur layouts)
    for f in files:
        if f.endswith(".xml") and "android:" in _read(f, 60000):
            m = re.search(r"xmlns:android\s*=\s*\"([^\"]*)\"", _read(f, 60000))
            if m and m.group(1) != ANDROID_NS:
                items.append((E, "%s me xmlns:android galat hai: %s ; sahi: %s" % (f, m.group(1), ANDROID_NS)))
            elif not m and f.endswith("AndroidManifest.xml"):
                items.append((E, "%s me xmlns:android nahi hai" % f))


def verify_project(force_android=False):
    """[(level, msg)] level E = rukawat, W = chetavni. Khali list = sab theek."""
    files = list_files(None)
    items = []
    check_generic(files, items)
    try:
        check_docs(files, items)
    except Exception as e:
        log("check_docs galti: %s" % e)
    if force_android or is_android_project(files):
        check_gradle_files(files, items, STATE.get("task") or "")
        check_android_code(files, items)
    try:
        items.extend(cross_check(files))
    except Exception as e:
        log("cross_check galti: %s" % e)
    seen, out = set(), []
    for lvl, msg in sorted(items, key=lambda x: x[0] != "E"):     # pehle E, phir W; dohraav nahi
        if msg not in seen:
            seen.add(msg)
            out.append((lvl, msg))
    return out


def format_report(items, n=12):
    errs = [m for lv, m in items if lv == "E"]
    warns = [m for lv, m in items if lv == "W"]
    lines = ["❌ %d galti, ⚠ %d chetavni" % (len(errs), len(warns))] if items else ["✅ jaanch me koi galti nahi mili"]
    lines += ["E%d. %s" % (i + 1, m) for i, m in enumerate(errs[:n])]
    if len(errs) > n:
        lines.append("...(%d galtiyan aur; pehle ye theek karo, phir @@VERIFY)" % (len(errs) - n))
    lines += ["W%d. %s" % (i + 1, m) for i, m in enumerate(warns[:4])]
    return "\n".join(lines)


def doc_text(rel, limit=20000):
    """PDF/docx/xlsx ka text (reviewer aur jaanch ke liye)."""
    try:
        with open(os.path.join(WORK, rel), "rb") as fh:
            raw = fh.read(5000000)
        return (read_any(rel, raw) or "")[:limit]
    except Exception as e:
        return "(padhne me dikkat: %s)" % str(e)[:100]


def check_docs(files, items):
    """PDF/docx/xlsx asli aur khuli hain? E = kharab/khali, W = chetavni."""
    for f in files:
        if os.path.splitext(f)[1].lower() not in DOC_BIN_EXT or f.split(os.sep, 1)[0] == "uploads":
            continue
        ext = os.path.splitext(f)[1].lower()
        p = os.path.join(WORK, f)
        try:
            size = os.path.getsize(p)
            with open(p, "rb") as fh:
                head = fh.read(8)
        except OSError:
            continue
        if size == 0:
            items.append(("E", "%s khali hai (0 bytes)" % f))
            continue
        if ext == ".pdf":
            if not head.startswith(b"%PDF"):
                items.append(("E", "%s asli PDF nahi hai (shuru me %%PDF nahi)" % f))
                continue
        elif not zipfile.is_zipfile(p):
            items.append(("E", "%s asli %s nahi hai (zip format toota hua)" % (f, ext)))
            continue
        txt = doc_text(f, 4000)
        if "padhne me dikkat" in txt[:80]:
            items.append(("E", "%s khul nahi payi: %s" % (f, txt[:120])))
        elif not txt.strip() or txt.startswith("(PDF me text nahi mila"):
            items.append(("W", "%s se text nahi nikla (khali ya scan wali ho sakti hai); @@READ %s se dekho" % (f, f)))


def doc_summary():
    """VERIFY ke natije ke neeche document files ka asli saboot (size, page, akshar)."""
    out = []
    for f in list_files(None):
        if f.lower().endswith(DOC_BIN_EXT) and f.split(os.sep, 1)[0] != "uploads":
            try:
                size = os.path.getsize(os.path.join(WORK, f))
                t = doc_text(f, 200000)
                pages = len(re.findall(r"--- Page \d+ ---", t))
                if not t.strip() or t.startswith("(PDF me text nahi mila") or "padhne me dikkat" in t[:80]:
                    out.append("📄 %s: %d bytes, text nahi nikla / file khuli nahi" % (f, size))
                else:
                    out.append("📄 %s: %d bytes, %s%d akshar text" % (f, size, ("%d page, " % pages) if pages else "", len(t.strip())))
            except Exception:
                pass
    return ("\n" + "\n".join(out[:8])) if out else ""


def want_android():
    """Kaam Android ka lagta hai? (task ke shabd ya files se)"""
    return bool(ANDROID_TASK_RE.search(STATE.get("task") or "")) or is_android_project(list_files(None))



# ---------- Claude-jaisa loop: padho -> badlo -> jaancho ----------
def rel_of(a):
    """Command ke naam wale arg se project-relative path. Galat ho to None."""
    try:
        return os.path.relpath(safe(a), WORK)
    except Exception:
        return None


def split_range(a):
    """'app.py 100-220' -> ('app.py', '100-220')."""
    parts = a.rsplit(None, 1)
    if len(parts) == 2 and re.fullmatch(r"\d+-\d+", parts[1]):
        return parts[0], parts[1]
    return a, None


def is_strict_file(a):
    ap = a.strip().strip("`'\"")
    return os.path.splitext(ap)[1].lower() in INDENT_EXT or os.path.basename(ap) in ("Makefile", "makefile")


def excerpt_for(rel, body="", width=150):
    """File ka wo hissa jo model ko dikhana hai: chhoti file poori, badi file me SEARCH ki pehli line ke aas-paas."""
    txt = _read(rel, 400000)
    if not txt:
        return ""
    lines = txt.split("\n")
    if len(txt) <= 30000:
        return "(poori file, %d lines)\n%s" % (len(lines), txt)
    first = ""
    m = re.search(r"<{5,9} SEARCH\n(.*?)\n={5,9}\n", body or "", re.S)
    if m:
        first = next((l.strip() for l in m.group(1).split("\n") if l.strip()), "")
    idx = 0
    if first:
        hit = [i for i, l in enumerate(lines) if first in l]
        if not hit:
            close = difflib.get_close_matches(first, [l.strip() for l in lines], n=1, cutoff=0.5)
            hit = [i for i, l in enumerate(lines) if close and l.strip() == close[0]]
        idx = hit[0] if hit else 0
    a, b = max(0, idx - 40), min(len(lines), idx + width)
    return "(lines %d-%d, kul %d; baaki ke liye @@READ %s <a-b>)\n%s" % (a + 1, b, len(lines), rel, "\n".join(lines[a:b]))


def read_gate(k, a, body):
    """None = kaam karne do. Warna text: pehle file dekho. Har file par sirf ek baar roakta hai (phir jaane deta hai),
    aur agar EDIT ka SEARCH file se match kar raha hai to bilkul nahi roakta."""
    if not READ_FIRST or k not in ("EDIT", "WRITE"):
        return None
    rel = rel_of(a)
    if not rel:
        return None
    p = os.path.join(WORK, rel)
    if not os.path.isfile(p) or rel in TRACK["seen"] or rel in TRACK["warned"] or os.path.getsize(p) == 0:
        return None
    if TRACK["edit_fail"].get(rel, 0) >= 3:
        return None
    if k == "EDIT":
        try:
            with open(p, encoding="utf-8", errors="surrogateescape", newline="") as f:
                old = f.read()
        except OSError:
            return None
        strict, clean = is_strict_file(a), unlink(body)
        new, err = apply_edit(old, clean, strict)
        if err and clean != body:
            new, err = apply_edit(old, body, strict)
        if not err:
            return None
        TRACK["warned"].add(rel)
        return ("ROKA: %s tumne abhi padhi nahi aur tumhara SEARCH file se match nahi hua (%s). Asli file ye rahi, "
                "isse dekh ke @@EDIT dobara do:\n%s" % (rel, err, excerpt_for(rel, body)))
    TRACK["warned"].add(rel)
    return ("ROKA: %s pehle se hai (%d bytes) aur tumne padhi nahi; poori @@WRITE se purana content ud jayega. "
            "Pehle @@READ karo aur @@EDIT se badlo. Sach me poori badalni ho to wahi @@WRITE dobara do, ab chal jayegi.\n"
            "Abhi file ka shuru:\n%s" % (rel, os.path.getsize(p), excerpt_for(rel)))


ERR_LOC_RES = (re.compile(r'File "([^"\n]+)", line (\d+)'),
               re.compile(r"([\w./\\:\-]+?\.(?:kt|java|xml|py|js|ts|tsx|jsx|kts|gradle|json|yml|yaml|html|css|go|rs|c|cpp|h)):(\d+)"))


def error_files(text):
    """Error output se [(project-file, line)]. Sirf wahi jo project me sach me hain."""
    found, seen, allf = [], set(), None
    for rx in ERR_LOC_RES:
        for m in rx.finditer(text or ""):
            raw, line, cand = m.group(1).replace("file://", ""), int(m.group(2)), None
            if os.path.isabs(raw):
                ap = os.path.realpath(raw)
                if ap.startswith(WORK + os.sep) and os.path.isfile(ap):
                    cand = os.path.relpath(ap, WORK)
            else:
                ap = os.path.realpath(os.path.join(WORK, raw))
                if ap.startswith(WORK + os.sep) and os.path.isfile(ap):
                    cand = os.path.relpath(ap, WORK)
                else:
                    if allf is None:
                        allf = list_files(None)
                    suf = raw.lstrip("./")
                    hits = [f for f in allf if f == suf or f.endswith("/" + suf)]
                    cand = hits[0] if len(hits) == 1 else None
            if cand and (cand, line) not in seen:
                seen.add((cand, line))
                found.append((cand, line))
            if len(found) >= 4:
                return found
    return found


def err_hint(text):
    """Fail hue command ke output se: kaunsi file/line me galti, aur un files ko dobara padhna zaroori."""
    locs = error_files(text)
    if not locs:
        return ""
    for f, _ in locs:
        TRACK["seen"].discard(f)
        TRACK["warned"].discard(f)
    return "\n[Hint] Galti in jagah dikh rahi hai. Pehle padho, phir @@EDIT:\n" + "\n".join(
        "@@READ %s %d-%d" % (f, max(1, n - 8), n + 8) for f, n in locs[:3])


def auto_check(rels):
    """Abhi badli files ki turant jaanch (poore project ki nahi; wo @@VERIFY/@@DONE par hoti hai)."""
    rels = sorted(r for r in set(rels) if r and os.path.isfile(os.path.join(WORK, r)))
    if not AUTO_CHECK or not rels:
        return ""
    items = []
    try:
        check_generic(rels, items)
    except Exception as e:
        log("auto_check galti: %s" % e)
        return ""
    errs = [m for lv, m in items if lv == "E"]
    if not errs:
        return ""
    return "[AUTO-JAANCH] abhi likhi files me %d galti (turant theek karo):\n%s" % (
        len(errs), "\n".join("- " + clip(e, 200) for e in errs[:5]))


# ---------- @@DEPS ----------
DEP_LINE_RE = re.compile(r"\b(?:implementation|api|kapt|ksp|classpath|compileOnly|runtimeOnly|testImplementation|androidTestImplementation)\b"
                         r"|\bid\s*[(\"']|\bkotlin\s*\(|namespace|compileSdk|minSdk|targetSdk|applicationId|jvmTarget|gradle-version")


def deps_summary(filt=""):
    filt, out = (filt or "").strip().lower(), []
    for f in list_files(None):
        b, lines = os.path.basename(f), []
        if b in GRADLE_NAMES:
            lines = [l.strip() for l in _read(f, 100000).split("\n") if DEP_LINE_RE.search(l)]
        elif b == "package.json":
            try:
                j = json.loads(_read(f))
                for sec in ("dependencies", "devDependencies", "scripts"):
                    lines += ["%s: %s %s" % (sec, k, v) for k, v in (j.get(sec) or {}).items()]
            except ValueError:
                lines = ["(package.json JSON galat)"]
        elif b.startswith("requirements") and b.endswith(".txt"):
            lines = [l.strip() for l in _read(f).split("\n") if l.strip() and not l.strip().startswith("#")]
        elif b in ("pyproject.toml", "Cargo.toml", "go.mod", "Gemfile", "pubspec.yaml"):
            lines = [l.rstrip() for l in _read(f, 20000).split("\n") if l.strip()][:40]
        else:
            continue
        if filt:
            lines = [l for l in lines if filt in l.lower()]
        if lines:
            out.append("== %s ==\n%s" % (f, "\n".join(lines[:40])))
    return clip("\n\n".join(out), 4000) if out else (
        "(dependency file nahi mili%s. Dekhta hoon: build.gradle(.kts), package.json, requirements.txt, pyproject.toml, Cargo.toml, go.mod)" % (
            " ya '%s' nahi mila" % filt if filt else ""))


# ---------- @@TEMPLATE android ----------
def template_tip():
    try:
        fs = list_files(None)
        if cur_mode() == "android" and not any(os.path.basename(f) == "AndroidManifest.xml" for f in fs):
            return "TIP: Android project abhi bana nahi. Pehle @@SPEC likho, phir @@TEMPLATE android <package> <AppName> chalao; wo saari zaroori files (gradle, manifest, icon, workflow) bana deta hai."
    except Exception:
        pass
    return ""


def android_template(pkg, app):
    ns = "http://schemas.android.com/apk/res/android"
    pd = pkg.replace(".", "/")
    F = {}
    F["settings.gradle.kts"] = (
        'pluginManagement {\n    repositories {\n        google()\n        mavenCentral()\n        gradlePluginPortal()\n    }\n}\n'
        'dependencyResolutionManagement {\n    repositories {\n        google()\n        mavenCentral()\n    }\n}\n'
        'rootProject.name = "%s"\ninclude(":app")\n' % app.replace('"', ""))
    F["build.gradle.kts"] = (
        'plugins {\n    id("com.android.application") version "8.5.2" apply false\n'
        '    id("org.jetbrains.kotlin.android") version "1.9.24" apply false\n}\n')
    F["gradle.properties"] = (
        "org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8\nandroid.useAndroidX=true\nandroid.nonTransitiveRClass=true\nkotlin.code.style=official\n")
    F[".gitignore"] = "build/\n.gradle/\nlocal.properties\n*.iml\n.idea/\n"
    F["app/build.gradle.kts"] = (
        'plugins {\n    id("com.android.application")\n    id("org.jetbrains.kotlin.android")\n}\n\n'
        'android {\n    namespace = "%s"\n    compileSdk = 34\n\n    defaultConfig {\n        applicationId = "%s"\n'
        '        minSdk = 26\n        targetSdk = 34\n        versionCode = 1\n        versionName = "1.0"\n    }\n\n'
        '    buildTypes {\n        release {\n            isMinifyEnabled = false\n        }\n    }\n\n'
        '    compileOptions {\n        sourceCompatibility = JavaVersion.VERSION_17\n        targetCompatibility = JavaVersion.VERSION_17\n    }\n'
        '    kotlinOptions {\n        jvmTarget = "17"\n    }\n}\n\n'
        'dependencies {\n    implementation("androidx.core:core-ktx:1.12.0")\n    implementation("androidx.appcompat:appcompat:1.6.1")\n'
        '    implementation("com.google.android.material:material:1.11.0")\n    implementation("androidx.recyclerview:recyclerview:1.3.2")\n'
        '    implementation("androidx.constraintlayout:constraintlayout:2.1.4")\n}\n' % (pkg, pkg))
    F["app/src/main/AndroidManifest.xml"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n<manifest xmlns:android="%s">\n\n    <application\n        android:allowBackup="true"\n'
        '        android:icon="@mipmap/ic_launcher"\n        android:roundIcon="@mipmap/ic_launcher_round"\n        android:label="@string/app_name"\n'
        '        android:supportsRtl="true"\n        android:theme="@style/Theme.App">\n        <activity\n            android:name=".MainActivity"\n'
        '            android:exported="true">\n            <intent-filter>\n                <action android:name="android.intent.action.MAIN" />\n'
        '                <category android:name="android.intent.category.LAUNCHER" />\n            </intent-filter>\n        </activity>\n'
        '    </application>\n\n</manifest>\n' % ns)
    F["app/src/main/java/%s/MainActivity.kt" % pd] = (
        'package %s\n\nimport android.os.Bundle\nimport android.widget.TextView\nimport androidx.appcompat.app.AppCompatActivity\n\n'
        'class MainActivity : AppCompatActivity() {\n    override fun onCreate(savedInstanceState: Bundle?) {\n        super.onCreate(savedInstanceState)\n'
        '        setContentView(R.layout.activity_main)\n        findViewById<TextView>(R.id.title).text = getString(R.string.app_name)\n    }\n}\n' % pkg)
    F["app/src/main/res/layout/activity_main.xml"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n<LinearLayout xmlns:android="%s"\n    android:layout_width="match_parent"\n'
        '    android:layout_height="match_parent"\n    android:orientation="vertical">\n\n    <TextView\n        android:id="@+id/title"\n'
        '        android:layout_width="match_parent"\n        android:layout_height="wrap_content"\n        android:padding="16dp"\n'
        '        android:textSize="20sp"\n        android:text="@string/app_name" />\n\n    <androidx.recyclerview.widget.RecyclerView\n'
        '        android:id="@+id/list"\n        android:layout_width="match_parent"\n        android:layout_height="0dp"\n'
        '        android:layout_weight="1" />\n\n</LinearLayout>\n' % ns)
    F["app/src/main/res/values/strings.xml"] = '<resources>\n    <string name="app_name">%s</string>\n</resources>\n' % (
        app.replace("&", "&amp;").replace("<", "&lt;"))
    F["app/src/main/res/values/colors.xml"] = (
        '<resources>\n    <color name="primary">#FF3F51B5</color>\n    <color name="white">#FFFFFFFF</color>\n'
        '    <color name="ic_launcher_background">#FF3F51B5</color>\n</resources>\n')
    F["app/src/main/res/values/themes.xml"] = (
        '<resources>\n    <style name="Theme.App" parent="Theme.MaterialComponents.DayNight.NoActionBar">\n'
        '        <item name="colorPrimary">@color/primary</item>\n        <item name="colorOnPrimary">@color/white</item>\n    </style>\n</resources>\n')
    F["app/src/main/res/drawable/ic_launcher_foreground.xml"] = (
        '<vector xmlns:android="%s"\n    android:width="108dp"\n    android:height="108dp"\n    android:viewportWidth="108"\n'
        '    android:viewportHeight="108">\n    <path\n        android:fillColor="#FFFFFF"\n        android:pathData="M30,38h18l6,8h24v30h-48z" />\n</vector>\n' % ns)
    icon = ('<?xml version="1.0" encoding="utf-8"?>\n<adaptive-icon xmlns:android="%s">\n'
            '    <background android:drawable="@color/ic_launcher_background" />\n'
            '    <foreground android:drawable="@drawable/ic_launcher_foreground" />\n</adaptive-icon>\n' % ns)
    F["app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml"] = icon
    F["app/src/main/res/mipmap-anydpi-v26/ic_launcher_round.xml"] = icon
    F[".github/workflows/android.yml"] = (
        "name: Android APK\non:\n  push:\n  workflow_dispatch:\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n      - uses: actions/setup-java@v4\n        with:\n          distribution: temurin\n          java-version: 17\n"
        "      - uses: gradle/actions/setup-gradle@v4\n        with:\n          gradle-version: 8.7\n"
        "      - name: Build debug APK\n        run: gradle assembleDebug --no-daemon\n"
        "      - uses: actions/upload-artifact@v4\n        with:\n          name: app-debug-apk\n          path: app/build/outputs/apk/debug/*.apk\n")
    return F


def do_template(a):
    """(natija, badla?, theek?, code)."""
    parts = a.split()
    if not parts or parts[0].lower() != "android":
        return "ERROR: abhi sirf ek template hai: @@TEMPLATE android <package> <AppName>  (jaise: @@TEMPLATE android com.example.filemanager File Manager)", False, False, None
    rest, pkg = parts[1:], ""
    if rest and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+", rest[0]):
        pkg, rest = rest[0].lower(), rest[1:]
    app = re.sub(r"[^A-Za-z0-9 _-]", "", " ".join(rest)).strip() or "My App"
    if not pkg:
        slug = re.sub(r"[^a-z0-9]", "", app.lower())
        pkg = "com.example." + (slug if slug[:1].isalpha() else "app" + slug)
    made, kept = [], []
    for rel, content in android_template(pkg, app).items():
        p = safe(rel)
        if os.path.exists(p):
            kept.append(rel)
            continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        snap(p)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        made.append(rel)
    for rel in made:
        if rel.endswith(("MainActivity.kt", "activity_main.xml", "strings.xml")):
            TRACK["seen"].add(rel)
    show = "\n".join("--- %s ---\n%s" % (r, _read(r)) for r in made if r.endswith(("MainActivity.kt", "activity_main.xml")))
    res = ("Android template ban gaya: package %s, app '%s' (%d nayi files%s).\n"
           "Ye build-layak hai: gradle, manifest, icon, dark-mode theme, .github/workflows/android.yml sab taiyar (minSdk 26). "
           "Ab sirf app ki logic badlo: MainActivity.kt, res/layout, res/values/strings.xml. "
           "Naya R.id/R.string use karo to uska resource bhi banao. Settings/gradle/icon dobara mat likho.\n"
           "Files: %s\n%s" % (pkg, app, len(made), (", %d pehle se the, chhode" % len(kept)) if kept else "", ", ".join(made) or "-", show))
    return clip(res, 6000), bool(made), True, None


# ---------- doosri AI se review ----------
REVIEW_SYS = ("You are a strict code reviewer. Find only REAL bugs that would break compile or run: syntax errors, undefined names, "
              "missing imports, wrong types or API use, R.id/R.string/R.layout names missing from the given resource list, null-safety errors, "
              "unclosed brackets. Ignore style, naming, comments and missing features. Report a problem only if you are more than 80% sure. "
              "Reply exactly 'OK' if there is no real bug. Otherwise reply with at most 5 lines, each like: - line N: problem -> fix. No other text.")
REVIEW_EXT = (".kt", ".java", ".py", ".js", ".ts", ".go", ".rs", ".c", ".cpp")


def res_summary():
    have = res_index(list_files(None))
    return "; ".join("%s: %s" % (t, ", ".join(sorted(have[t])[:40])) for t in ("id", "string", "layout", "drawable", "mipmap", "color", "style") if have.get(t))


def run_review(rel, writer):
    """Badi code file ka doosri AI se bug-review. Dikkat/timeout/khaali jawab par chup-chaap "" lautata hai."""
    if not REVIEW or TRACK["review_n"] >= REVIEW_MAX or not rel.lower().endswith(REVIEW_EXT):
        return ""
    txt = _read(rel, 60000)
    if txt.count("\n") + 1 < REVIEW_MIN_LINES or TRACK["reviewed"].get(rel) == hash(txt):
        return ""
    cands = sorted([n for n in order() if APIS[n]["type"] == "oai"], key=lambda n: n == writer)
    if not cands or STATE["cancel"]:
        return ""
    TRACK["reviewed"][rel] = hash(txt)
    ctx = ("Project resources -> " + res_summary()) if rel.endswith((".kt", ".java")) else ""
    for name in cands[:2]:
        cfg = dict(APIS[name], max_tokens=600, total_timeout=90)
        body = "File: %s\nProject files: %s\n%s\n\n```\n%s\n```" % (rel, ", ".join(list_files(60)), ctx, txt)
        if len(body) + len(REVIEW_SYS) > ctx_limit(name, cfg) - 1500:
            continue
        TRACK["review_n"] += 1
        ev("note", text="🔍 %s ka review (%s se)..." % (rel, name))
        try:
            res = ask_oai(name, cfg, [{"role": "system", "content": REVIEW_SYS}, {"role": "user", "content": body}])
        except Exception as e:
            log("review fail: %s" % e)
            continue
        if not res or not res[0].strip():
            continue
        out = clean_reply(res[0]).strip()
        if len(out) < 40 and re.match(r"^\W*ok\b", out, re.I):
            return ""
        bullets = [l.strip() for l in out.split("\n") if l.strip().startswith(("-", "*", "•"))][:5]
        found = "\n".join(bullets) if bullets else (out[:600] if len(out) < 600 else "")
        return ("[REVIEW %s] doosri AI ne ye dikhaya (pakka nahi). Sahi lage to @@EDIT se theek karo, galat lage to ignore karke aage badho:\n%s" % (
            rel, clip(found, 900))) if found else ""
    return ""


# ================= 8.py: SPEC -> chhota part -> jaanch -> reviewer(JSON) -> fix-loop -> final saboot =================
SPEC_GATE, REVIEW_LOOP = True, True
SPEC_REJECT_MAX, REVIEW_ROUNDS, FINAL_REJECT_MAX = 4, 3, 4
REVIEWER_TRIES, REVIEWER_TIMEOUT, REVIEWER_TOKENS = 3, 300, 16000
REVIEWER_PREF = "JAAT"
REVIEW_GEMINI = True    # review me Gemini bhi, par sabse LAST me (baari-baari); pehle JAAT, Groq, NV2, DS chalte hain taaki Gemini ka limit kam kharch ho
REVIEW_EXT = (".kt", ".java", ".py", ".js", ".ts", ".go", ".rs", ".c", ".cpp", ".xml", ".html", ".css", ".kts", ".gradle")
SPEC_FILE = "SPEC.md"
_LAST_BUSY = [0.0]


# ---------- SPEC ----------
def parse_spec(body):
    items = []
    for l in (body or "").split("\n"):
        l = l.strip().strip("*`")
        if not l or l.startswith("```"):
            continue
        l = re.sub(r"^(?:[-*•]\s*|S?\d+\s*[.:)\-]\s*)", "", l).strip()
        if len(l) >= 8:
            items.append(l[:300])
    return items[:25]


def save_spec(items):
    TRACK["spec"] = ["S%d: %s" % (i + 1, x) for i, x in enumerate(items)]
    TRACK["proof"] = {}
    p = os.path.join(WORK, SPEC_FILE)
    snap(p)
    with open(p, "w", encoding="utf-8") as f:
        f.write("# SPEC (har line jaanch-layak; @@DONE par reviewer har line ka saboot dega)\n\n" + "\n".join(TRACK["spec"]) + "\n")


def load_spec():
    try:
        with open(os.path.join(WORK, SPEC_FILE), encoding="utf-8", errors="replace") as f:
            lines = [re.sub(r"\s+(?:✅|❌).*$", "", l.strip()) for l in f if re.match(r"^S\d+:", l.strip())]
        TRACK["spec"] = lines
    except OSError:
        TRACK["spec"] = []


def spec_gate(k, a):
    """None = theek. Warna text: pehle @@SPEC likho."""
    if not SPEC_GATE or k not in ("WRITE", "WRITEB64", "EDIT", "TEMPLATE") or TRACK["spec"] or not prof()["need_spec"]:
        return None
    if k != "TEMPLATE":
        rel = rel_of(a)
        if rel in (SPEC_FILE, "PROJECT.md"):
            return None
    if TRACK["spec_rej"] >= SPEC_REJECT_MAX:
        return None
    TRACK["spec_rej"] += 1
    return ("ROKA: pehle @@SPEC likho (bina SPEC ke @@WRITE/@@EDIT/@@TEMPLATE nahi chalega). Kam se kam %d line, har line jaanch-layak: "
            "kya chalna chahiye aur kahan toot sakta hai (khaali data, galat input, error par message%s). "
            "Format:\n@@SPEC\nS1: ...\nS2: ...\n@@END") % (max(3, prof()["spec_min"]), ", permission, rotate, dark mode" if cur_mode() == "android" else "")


def bug_gate(k, a):
    """Khule bugs ho aur model kisi aur file par bhaag raha ho to ek baar roko."""
    if not REVIEW_LOOP or k not in ("WRITE", "EDIT", "WRITEB64"):
        return None
    open_b = {f: b for f, b in TRACK["bugs"].items() if b}
    if not open_b:
        return None
    rel = rel_of(a)
    if rel in open_b or TRACK["bug_rej"].get(rel, 0) >= 1:
        return None
    TRACK["bug_rej"][rel] = TRACK["bug_rej"].get(rel, 0) + 1
    return ("ROKA: pehle reviewer ke khule bugs band karo:\n%s\nBug dusri file (layout/resource) me theek hota ho to wahi @@EDIT dobara do, ab chal jayega."
            % bug_text(open_b))


def bug_text(d, n=12):
    out = []
    for f, bl in d.items():
        for b in bl:
            out.append("- %s:%s [%s] %s -> %s" % (b.get("file") or f, b.get("line", "?"), b.get("spec_id") or "-",
                                                  clip(str(b.get("bug", "")), 220), clip(str(b.get("fix", "")), 200)))
    more = len(out) - n
    return "\n".join(out[:n]) + ("\n...(%d bug aur)" % more if more > 0 else "")


# ---------- outline (baaki files ki sirf function/id list) ----------
KT_SIG = re.compile(r"^\s*(?:(?:private|public|internal|protected|override|suspend|data|open|abstract|sealed|lateinit|const|inline)\s+)*"
                    r"(?:fun|class|object|interface)\s+[^{=\n]{1,110}")
PY_SIG = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+[^\n:]{1,110}")
JS_SIG = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+\w+[^{\n]{0,80}|class\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>)")


def outline(exclude=None, limit=3500):
    out = []
    for f in list_files(None):
        if f == exclude or f in (SPEC_FILE, "PROJECT.md") or f.split(os.sep, 1)[0] == "uploads":
            continue
        ext, sigs = os.path.splitext(f)[1].lower(), []
        txt = _read(f, 120000) if ext in REVIEW_EXT else ""
        if not txt:
            continue
        if ext in (".kt", ".java"):
            sigs = [KT_SIG.match(l).group(0).strip() for l in txt.split("\n") if KT_SIG.match(l)]
        elif ext == ".py":
            sigs = [PY_SIG.match(l).group(0).strip() for l in txt.split("\n") if PY_SIG.match(l)]
        elif ext in (".js", ".ts"):
            sigs = [JS_SIG.match(l).group(0).strip() for l in txt.split("\n") if JS_SIG.match(l)]
        elif ext == ".xml":
            sigs = ["id/" + x for x in re.findall(r"@\+id/(\w+)", txt)] + ["name/" + x for x in re.findall(r'<(?:string|color|style|dimen)\s+name="([\w.]+)"', txt)]
        elif ext == ".html":
            sigs = ["#" + x for x in re.findall(r'\bid\s*=\s*["\']([\w-]+)', txt)] + [x.strip() for x in re.findall(r"function\s+\w+\s*\([^)]*\)", txt)]
        if sigs:
            out.append("%s: %s" % (f, "; ".join(sigs[:25])))
    return clip("\n".join(out), limit) if out else ""


def spec_context():
    p = []
    if TRACK["spec"]:
        p.append("SPEC (har line ka saboot @@DONE par chahiye):\n" + "\n".join(
            "%s %s" % (s, "✅" if s.split(":", 1)[0] in TRACK["proof"] else "⬜") for s in TRACK["spec"]))
    open_b = {f: b for f, b in TRACK["bugs"].items() if b}
    if open_b:
        p.append("REVIEWER KE KHULE BUGS (inhe band karo):\n" + bug_text(open_b))
    if TRACK.get("polish"):
        p.append("POLISH LIST (har ek banao ya wajah likh ke mana karo):\n" + "\n".join(TRACK["polish"]))
    try:
        o = outline()
        if o:
            p.append("FILES KA NAKSHA (function/id):\n" + o)
    except Exception:
        pass
    return "\n".join(p)


# ---------- cross-file jaanch (Mumbai khud, AI ke bina) ----------
def _py_arity(files, items):
    import ast
    trees = {}
    for f in files:
        if f.endswith(".py"):
            try:
                trees[f] = ast.parse(_read(f, 400000))
            except SyntaxError:
                pass
    defs = {}
    for f, tr in trees.items():
        cnt = {}
        for n in tr.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cnt[n.name] = cnt.get(n.name, 0) + 1
                if n.decorator_list or n.args.vararg or n.args.kwarg:
                    cnt[n.name] += 99
                defs.setdefault(f, {})[n.name] = n
        defs[f] = {k: v for k, v in defs.get(f, {}).items() if cnt.get(k) == 1}
    mods = {os.path.splitext(os.path.basename(f))[0]: f for f in trees}
    for f, tr in trees.items():
        known = dict(defs.get(f, {}))
        for n in ast.walk(tr):
            if isinstance(n, ast.ImportFrom) and n.module and n.level == 0 and n.module.split(".")[0] in mods and n.module in mods:
                src = mods[n.module]
                top = {getattr(x, "name", None) for x in trees[src].body if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
                top |= {t.id for x in trees[src].body if isinstance(x, ast.Assign) for t in x.targets if isinstance(t, ast.Name)}
                for al in n.names:
                    if al.name != "*" and al.name not in top:
                        items.append(("E", "[CROSS] %s:%d `from %s import %s` — %s me %s hai hi nahi" % (f, n.lineno, n.module, al.name, src, al.name)))
                    elif al.name in defs.get(src, {}):
                        known[al.asname or al.name] = defs[src][al.name]
        for n in ast.walk(tr):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in known):
                continue
            fn = known[n.func.id]
            if any(isinstance(a, ast.Starred) for a in n.args) or any(k.arg is None for k in n.keywords):
                continue
            a = fn.args
            pos = [x.arg for x in a.posonlyargs + a.args]
            kwonly = [x.arg for x in a.kwonlyargs]
            names = set(pos + kwonly)
            bad = [k.arg for k in n.keywords if k.arg not in names]
            required = pos[:len(pos) - len(a.defaults)] + [x.arg for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]
            given = set(pos[:len(n.args)]) | {k.arg for k in n.keywords}
            missing = [r for r in required if r not in given]
            if bad or missing or len(n.args) > len(pos):
                items.append(("E", "[CROSS] %s:%d %s() galat bulaya: %s" % (
                    f, n.lineno, n.func.id, ("anjaan keyword %s; " % bad if bad else "") + ("kam arg %s; " % missing if missing else "") +
                    ("zyada positional (%d > %d)" % (len(n.args), len(pos)) if len(n.args) > len(pos) else ""))))


def _android_cross(files, items):
    src = [f for f in files if f.endswith((".kt", ".java"))]
    if not src:
        return
    alltxt = "\n".join(_read(f, 200000) for f in src)
    pkg = ""
    for f in files:
        if os.path.basename(f) in ("build.gradle.kts", "build.gradle"):
            m = re.search(r"namespace\s*=?\s*[\"']([\w.]+)[\"']", _read(f, 20000))
            if m:
                pkg = m.group(1)
                break
    if not pkg:
        return
    for f in src:
        for n, line in enumerate(_read(f, 200000).split("\n"), 1):
            m = re.match(r"\s*import\s+(%s\.[\w.]+)\s*$" % re.escape(pkg), line)
            if m and not m.group(1).endswith(".R"):
                last = m.group(1).rsplit(".", 1)[1]
                if not re.search(r"\b(?:class|object|interface|fun|val|var|typealias|enum class)\s+%s\b" % re.escape(last), alltxt):
                    items.append(("E", "[CROSS] %s:%d import %s — project me ye class/function nahi mili" % (f, n, m.group(1))))
    for f in files:
        if not (f.endswith(".xml") and "/res/layout" in "/" + f):
            continue
        t = _read(f, 100000)
        for name in set(re.findall(r'android:onClick\s*=\s*"(\w+)"', t)):
            if not re.search(r"\bfun\s+%s\s*\(|\bvoid\s+%s\s*\(" % (name, name), alltxt):
                items.append(("E", "[CROSS] %s me onClick=\"%s\" hai par koi `fun %s(` nahi" % (f, name, name)))
        for fq in set(re.findall(r"<(%s\.[\w.]+)" % re.escape(pkg), t)):
            if not re.search(r"\bclass\s+%s\b" % re.escape(fq.rsplit(".", 1)[1]), alltxt):
                items.append(("E", "[CROSS] %s me custom view %s hai par class nahi mili" % (f, fq)))


def _web_cross(files, items):
    fs = set(files)
    htmls = [f for f in files if f.endswith(".html")]
    if not htmls:
        return
    all_ids, all_js = set(), ""
    for f in htmls:
        t = _read(f, 300000)
        all_ids |= set(re.findall(r'\bid\s*=\s*["\']([\w-]+)', t))
        all_js += t
        base = os.path.dirname(f)
        for ref in re.findall(r'<script[^>]+src\s*=\s*["\']([^"\':]+?)["\']|<link[^>]+href\s*=\s*["\']([^"\':]+?)["\']', t):
            r = ref[0] or ref[1]
            if r and not r.startswith(("//", "#", "data")) and os.path.normpath(os.path.join(base, r.split("?")[0])) not in fs:
                items.append(("E", "[CROSS] %s me `%s` link hai par ye file project me nahi hai" % (f, r)))
    for f in files:
        if f.endswith((".js", ".ts")):
            all_js += _read(f, 300000)
    for f in files:
        if f.endswith((".js", ".ts")):
            base = os.path.dirname(f)
            for r in re.findall(r"(?:require\(|from\s+)[\"'](\.[^\"']+)[\"']", _read(f, 300000)):
                cand = os.path.normpath(os.path.join(base, r))
                if not any(x in fs for x in (cand, cand + ".js", cand + ".ts", cand + "/index.js")):
                    items.append(("E", "[CROSS] %s import `%s` — file nahi mili" % (f, r)))
    for i in sorted(set(re.findall(r"getElementById\(\s*[\"']([\w-]+)[\"']\s*\)", all_js)) - all_ids):
        items.append(("E", "[CROSS] JS getElementById('%s') karta hai par HTML me id=\"%s\" nahi" % (i, i)))
    for fn in sorted(set(re.findall(r'\bon(?:click|change|input|submit)\s*=\s*["\'](\w+)\s*\(', all_js))):
        if not re.search(r"function\s+%s\b|\b%s\s*=\s*(?:async\s*)?(?:function|\()|(?:const|let|var)\s+%s\b" % (fn, fn, fn), all_js):
            items.append(("E", "[CROSS] HTML me onclick=\"%s()\" hai par ye function kahin define nahi" % fn))


def cross_check(files=None):
    files = files or list_files(None)
    items = []
    for fn in (_py_arity, _android_cross, _web_cross):
        try:
            fn(files, items)
        except Exception as e:
            log("cross %s galti: %s" % (fn.__name__, e))
    return items


# ---------- reviewer (naya khaali context, JSON) ----------
def _numbered(txt):
    return "\n".join("%d| %s" % (i, l) for i, l in enumerate(txt.split("\n"), 1))


def parse_json_reply(text):
    t = clean_reply(text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.M).strip()
    for cand in (t,):
        try:
            return json.loads(cand)
        except ValueError:
            pass
    for o, c in (("{", "}"), ("[", "]")):
        s, e = t.find(o), t.rfind(c)
        if s != -1 and e > s:
            try:
                return json.loads(t[s:e + 1])
            except ValueError:
                continue
    return None


def reviewer_names(writer=None):
    """Review ka order: JAAT, baaki oai (Groq, NV2...), phir DS (chhoti file par), sabse last Gemini (baari-baari)."""
    alln = [n for n in order() if APIS[n]["type"] in ("oai", "proxy_get")]
    gem = [n for n in alln if group_of(n) == "gemini"]
    oai = [n for n in alln if APIS[n]["type"] == "oai" and n not in gem]
    plain = [n for n in alln if APIS[n]["type"] != "oai"]
    if REVIEWER_PREF in oai:
        oai.remove(REVIEWER_PREF)
        oai.insert(0, REVIEWER_PREF)
    names = oai + plain + (gem if REVIEW_GEMINI else [])
    return names or gem


def ask_review(name, cfg, system, user):
    """(jawab, kata?) ya None. oai ho to ask_oai; DS jaise chhote proxy par sirf tab jab prompt uski limit me aaye."""
    if cfg["type"] == "oai":
        return ask_oai(name, cfg, [{"role": "system", "content": system}, {"role": "user", "content": user}])
    text = system + "\n\n" + user
    if len(text) > cfg.get("max_chars", 3000) or (
            cfg["type"] == "proxy_get" and len(urllib.parse.quote(text, safe="")) > cfg.get("max_url", 6000)):
        log("⏭ review: %s ke liye prompt bada (%d char)" % (name, len(text)))
        return None
    try:
        out = cancellable(lambda dead: PLAIN[cfg["type"]](cfg, models_of(cfg)[0], text))
    except Cancelled:
        return None
    except Exception as e:
        ev("note", text="%s review fail: %s" % (name, str(e)[:80]))
        penalize(name, 90)
        return None
    return (out, False) if out and out.strip() else None


def reviewer_call(system, user):
    """(text, provider) ya (None, None). Har provider par 3 koshish, phir agla."""
    for name in reviewer_names():
        for t in range(REVIEWER_TRIES):
            if STATE["cancel"]:
                return None, None
            cfg = dict(APIS[name], max_tokens=REVIEWER_TOKENS, timeout=max(APIS[name].get("timeout", 60), 30), total_timeout=min(REVIEWER_TIMEOUT, max(APIS[name].get("total_timeout", REVIEWER_TIMEOUT), 120)))
            try:
                res = ask_review(name, cfg, system, user)
            except Exception as e:
                log("reviewer %s: %s" % (name, e))
                res = None
            if res and res[0].strip():
                return res[0], name
            if COOL.get(name, 0) - time.time() > 500 or not nap(2 * (t + 1)):
                break
    return None, None


REVIEW_SYS8 = ("You are a strict senior code reviewer. Find REAL bugs that break compile or runtime, or that violate the SPEC lines: "
               "syntax errors, undefined names, missing imports, wrong types/API use, R.id/R.string/R.layout missing from the resource list, "
               "null-safety errors, unclosed brackets, unhandled empty/null data, permission flows that cannot work, crashes without a message. "
               "Ignore style and naming. Report only if >80% sure; give the exact line number from the numbered listing. "
               "Reply with ONLY a JSON array, no prose, no code fence: "
               '[{"file":"path","line":N,"spec_id":"S3 or empty","bug":"what is wrong","fix":"exact change"}]. Reply [] if no real bug.')


def _norm_bugs(data, rel):
    if isinstance(data, dict):
        data = data.get("bugs", [])
    if not isinstance(data, list):
        return None
    out = []
    for b in data[:12]:
        if isinstance(b, dict) and b.get("bug"):
            b.setdefault("file", rel)
            out.append(b)
    return out


def review_file(rel, writer=None):
    """([bug,...], 'model, model') - khaali list = saaf. (None, '') = reviewer nahi mila / round khatam: chup raho."""
    if not REVIEW_LOOP or not rel.lower().endswith(REVIEW_EXT):
        return None, ""
    txt = _read(rel, 60000)
    if not txt.strip():
        return None, ""
    if TRACK["rounds"].get(rel, 0) >= REVIEW_ROUNDS:
        return None, ""
    TRACK["rounds"][rel] = TRACK["rounds"].get(rel, 0) + 1
    android = cur_mode() == "android"
    ctx = ("Project resources -> " + res_summary()) if rel.endswith((".kt", ".java", ".xml")) and android else ""
    user = "SPEC:\n%s\n\n%s\n\nOTHER FILES (outline):\n%s\n%s\n\nFILE: %s\n%s" % (
        "\n".join(TRACK["spec"]) or "(none)", all_files_line(), outline(exclude=rel, limit=3500) or "(none)", ctx, rel, _numbered(txt))
    ev("note", text="🔍 %s ka review (round %d)..." % (rel, TRACK["rounds"][rel]))
    _rs = REVIEW_SYS8 + FILES_NOTE + (ANDROID_PITFALLS if android else "")
    outs = scan_call(_rs, user, rel) if (CODE_FLOW and cur_mode() in ("code", "android")) else dual_call(_rs, user)
    lists, provs = [], []
    for t, lab in outs:
        b = _norm_bugs(parse_json_reply(t), rel)
        if b is not None:
            lists.append(drop_phantom(b))
            provs.append(lab)
    if not lists:
        ev("note", text="⚠ %s: reviewer nahi mila, agle @@EDIT/@@WRITE par dobara review hoga" % rel)
        TRACK["rounds"][rel] = max(0, TRACK["rounds"].get(rel, 1) - 1)      # fail hua round ginti me nahi
        return None, ""
    return merge_bugs(lists), ", ".join(provs)


def review_hook(rel, writer=None):
    bugs, prov = review_file(rel, writer)
    rnd = TRACK["rounds"].get(rel, 1)
    if bugs is None:
        TRACK["bugs"].pop(rel, None)
        if rnd >= REVIEW_ROUNDS:
            return "[REVIEW %s] %d round ho chuke, ab is file ka review band (baaki bug @@DONE ke aakhri review me dikhenge)" % (rel, REVIEW_ROUNDS)
        return ""
    if not bugs:
        TRACK["bugs"].pop(rel, None)
        show_review(rel, True, "koi bug nahi mila (round %d)" % rnd, prov)
        return "[REVIEW %s] ✅ reviewer ko koi bug nahi mila (round %d)" % (rel, rnd)
    TRACK["bugs"][rel] = bugs
    show_review(rel, False, "%d bug (round %d/%d)\n%s" % (len(bugs), rnd, REVIEW_ROUNDS, bug_text({rel: bugs})), prov)
    return ("[REVIEW %s] %d bug (round %d/%d). Har bug @@EDIT se theek karo (galat lage to wajah likh ke ignore karo), phir aage badho:\n%s" % (
        rel, len(bugs), rnd, REVIEW_ROUNDS, bug_text({rel: bugs}))
        + (("\n" + pivot_text("Is file ka review %d baar bug laya." % rnd)) if rnd >= 3 else ""))


# ---------- @@DONE se pehle poore project ka saboot ----------
FINAL_SYS = ("You are a strict release reviewer. You get the SPEC lines and the full project files (with line numbers). "
             "For EVERY spec line decide pass=true only if the code clearly implements it AND handles its failure case; evidence must cite file:line. "
             "Also list real bugs (compile/runtime/cross-file mismatch). Reply with ONLY JSON, no prose, no fence: "
             '{"specs":[{"id":"S1","pass":true,"evidence":"file:line what"}],"bugs":[{"file":"path","line":N,"spec_id":"","bug":"","fix":""}]}')


PAYLOAD_EXT = REVIEW_EXT + (".yml", ".yaml", ".properties", ".json", ".toml", ".txt", ".pro")
FILES_NOTE = (" The line 'ALL PROJECT FILES' lists every file that really exists (dot-folders like .github included). "
              "NEVER report a file as missing, absent or not found if its path is in that list; read its content in the listing instead.")


def all_files_line():
    fs = [f for f in list_files(None) if f.split(os.sep, 1)[0] != "uploads" and f not in (SPEC_FILE, "PROJECT.md")]
    return "ALL PROJECT FILES (%d): %s" % (len(fs), ", ".join(fs[:150]))


MISSING_RE = re.compile(r"missing|absent|not found|not exist|doesn'?t exist|does not exist|nahi hai|nahi mil|gayab|no such", re.I)
PATH_RE = re.compile(r"[\w./\-]*[\w\-]\.[A-Za-z0-9]{1,6}")


def phantom(text):
    """True agar text kisi aisi file ko seedha 'missing' bolta hai jo disk par hai ("X missing" / "missing file X")."""
    t = str(text or "")
    if not MISSING_RE.search(t):
        return False
    have = set(list_files(None))
    for tok in PATH_RE.findall(t):
        tok = tok[2:] if tok.startswith("./") else tok.lstrip("/")
        if not tok or not (tok in have or any(f.endswith("/" + tok) for f in have)):
            continue
        q = re.escape(tok)
        if re.search(r"(?:%s)\s*(?:file\s*)?[:=]?\s*['\"`(]?%s" % (MISSING_RE.pattern, q), t, re.I) or \
           re.search(r"%s['\"`)]?\s*(?:file\s*)?(?:is |are |was |seems |appears )*(?:%s)" % (q, MISSING_RE.pattern), t, re.I):
            return True
    return False


NOT_BUG_RE = re.compile(r"not a (?:real )?(?:code )?bug|no (?:real )?(?:bug|issue|problem)s?\b|nothing to fix|no change (?:is )?(?:needed|required)|"
                        r"verify .{0,60}(?:dashboard|deploy)|cross-file:? no issue|looks (?:good|correct|fine)", re.I)


def drop_phantom(bugs):
    return [b for b in (bugs or [])
            if not phantom("%s %s" % (b.get("bug", ""), b.get("fix", "")))
            and not NOT_BUG_RE.search("%s %s" % (b.get("bug", ""), b.get("fix", "")))]


def _final_payload(name):
    budget = max(20000, min(PAYLOAD_MAX, ctx_limit(name, APIS[name]) - 9000))
    parts, used, rest = [], 0, []
    for f in list_files(None):
        if f in (SPEC_FILE, "PROJECT.md") or f.split(os.sep, 1)[0] == "uploads":
            continue
        if f.lower().endswith(DOC_BIN_EXT):
            block = "=== %s (document ka nikala hua text) ===\n%s" % (f, doc_text(f, 6000))
        elif not f.lower().endswith(PAYLOAD_EXT):
            continue
        else:
            t = _read(f, 60000)
            block = "=== %s ===\n%s" % (f, _numbered(t))
        if used + len(block) <= budget:
            parts.append(block)
            used += len(block)
        else:
            rest.append(f)
    tail = ("\n\n(jagah kam thi, ye files sirf naam se: %s)\n%s" % (", ".join(rest), outline(limit=2500))) if rest else ""
    return "SPEC:\n%s\n\n%s\n\nFILES:\n%s%s" % ("\n".join(TRACK["spec"]) or "(none)", all_files_line(), "\n\n".join(parts), tail)


def _write_spec_marks(proof):
    try:
        with open(os.path.join(WORK, SPEC_FILE), "w", encoding="utf-8") as f:
            f.write("# SPEC\n\n" + "\n".join("%s %s" % (l, ("✅ " + proof[l.split(":", 1)[0]]) if l.split(":", 1)[0] in proof else "❌")
                                              for l in TRACK["spec"]) + "\n")
    except OSError:
        pass


def _final_gate_core():
    """"" = DONE chalne do. Warna text: kya baaki hai.
    Kadam: (1) SPEC saboot + bugs (2 model) -> (2) user-flow simulate (2 model) -> (3) polish top-10 (ek baar)."""
    if not REVIEW_LOOP or (not TRACK["spec"] and not TRACK["bugs"]):
        return ""
    if TRACK["final_rej"] >= FINAL_REJECT_MAX:
        TRACK["final_warn"] = "\n⚠ reviewer ke sawaal %d baar ke baad bhi baaki the (upar dekho)" % FINAL_REJECT_MAX
        return ""
    slots = reviewer_slots()
    if not slots:
        return ""
    ev("note", text="🧪 poore project ka aakhri review (SPEC ke khilaf, 2 model se)...")
    small = min(slots, key=lambda s: ctx_limit(s[0], APIS[s[0]]))[0]
    payload = _final_payload(small)
    outs = dual_call(FINAL_SYS + FILES_NOTE, payload)
    datas = [(parse_json_reply(t), lab) for t, lab in outs]
    datas = [(d, lab) for d, lab in datas if isinstance(d, dict)]
    if not datas:
        TRACK["final_warn"] = "\n⚠ aakhri review nahi ho paya (reviewer jawab nahi de paya)"
        show_review("Aakhri review", False, "reviewer jawab nahi de paya", "")
        return ""
    provs = ", ".join(lab for _, lab in datas)
    per = [{str(s.get("id", "")).strip(): s for s in d.get("specs", []) if isinstance(s, dict)} for d, _ in datas]
    fails, proof = [], {}
    for line in TRACK["spec"]:
        sid = line.split(":", 1)[0]
        vs = [sp.get(sid) for sp in per]
        good = [v for v in vs if v and str(v.get("evidence", "")).strip() and (v.get("pass") is True or phantom(v.get("evidence")))]
        if len(good) == len(vs):                       # jitne reviewer bole, sab ne pass kiya
            proof[sid] = str(good[0]["evidence"])[:200]
        else:
            bad = next((v for v in vs if not (v and v.get("pass") is True)), None) or {}
            fails.append("%s %s -> %s" % (sid, line.split(":", 1)[1].strip()[:120], bad.get("evidence") or "saboot nahi mila"))
    TRACK["proof"] = proof
    _write_spec_marks(proof)
    bugs = merge_bugs([drop_phantom(_norm_bugs(d.get("bugs", []), "") or []) for d, _ in datas])
    byfile = {}
    for b in bugs:
        byfile.setdefault(b.get("file") or "?", []).append(b)
    TRACK["bugs"] = byfile
    total = len(TRACK["spec"])
    if fails or bugs:
        TRACK["final_rej"] += 1
        show_review("Aakhri review", False, "SPEC %d/%d pass\n%s%s" % (
            len(proof), total, "\n".join("✗ " + x for x in fails[:10]), ("\n" + bug_text(byfile)) if bugs else ""), provs)
        msg = "DONE mana (aakhri review %d/%d):" % (TRACK["final_rej"], FINAL_REJECT_MAX)
        if fails:
            msg += "\nSPEC ke ye lines pass nahi hue:\n" + "\n".join("- " + x for x in fails[:12])
        if bugs:
            msg += "\nReviewer ke bugs:\n" + bug_text(byfile)
        return msg + "\nInhe @@EDIT se theek karo, @@VERIFY chalao, phir @@DONE dobara."
    show_review("Aakhri review", True, "SPEC %d/%d pass, koi bug nahi\n%s" % (
        len(proof), total, "\n".join("%s ✅ %s" % (k, v) for k, v in list(proof.items())[:12])), provs)
    # (2) user-flow simulate
    if TRACK["flow_n"] < FLOW_MAX and prof()["flow"]:
        TRACK["flow_n"] += 1
        ev("note", text=("🚶 user-flow simulate (app kholo, permission mana, tap, rotate, lock, khaali list, kharab file)..." if cur_mode() == "android"
                         else "🚶 user-flow simulate (chalao, galat/khaali input, config ya API ki galti, dobara chalao)..."))
        fb, fprov = flow_pass(payload)
        if fb:
            for b in fb:
                b["file"] = b.get("file") or "?"
            fby = {}
            for b in fb:
                fby.setdefault(b["file"], []).append(b)
            TRACK["bugs"] = fby
            TRACK["final_rej"] += 1
            show_review("User-flow", False, "%d jagah flow toota\n%s" % (len(fb), bug_text(fby)), fprov)
            return ("DONE mana (user-flow simulate, %d/%d): reviewer ne asli user ke raste par ye jagah toot-ti dekhi:\n%s\n"
                    "Har bug @@EDIT se theek karo, @@VERIFY chalao, phir @@DONE dobara." % (TRACK["final_rej"], FINAL_REJECT_MAX, bug_text(fby)))
        show_review("User-flow", True, "flow chalaye, koi jagah nahi tooti" if fb is not None else "flow reviewer nahi mila", fprov)
    # (3) polish: top-10, sirf ek baar
    if not TRACK["polish_done"] and prof()["polish"]:
        TRACK["polish_done"] = True
        ev("note", text="✨ polish: achhe asli app me kya-kya hota hai jo yahan nahi...")
        pl = polish_list(payload)
        if pl:
            TRACK["polish"] = pl
            show_review("Polish top-10", False, "\n".join(pl), "")
            return ("POLISH (DONE abhi ruka): reviewer ke hisaab se is type ke achhe asli app me ye cheezein hoti hain jo yahan nahi hain:\n%s\n"
                    "Har ek ko banao (@@WRITE/@@EDIT me, 300 line tak) YA ek line me wajah likh ke mana karo (jaise 'N3: nahi banaya kyunki ...'). "
                    "Phir @@VERIFY aur @@DONE." % "\n".join(pl))
    return ""


# ================= 9.py: dohra reviewer, user-flow, polish, size-limit, gradle-gate =================
REVIEW_PARALLEL, REVIEW_GAP, PAYLOAD_MAX = False, 3, 60000   # reviewer ek-ek karke, beech me 3s, payload 60k char tak
MAX_BODY_LINES, BODY_REJECT_MAX = int(_env("MAX_BODY_LINES", "600")), 4          # ek @@WRITE/@@EDIT me itni lines tak
SPEC_MIN, PLAN_MIN = 10, 12                      # sirf android ke liye; baaki type ke liye PROFILES dekho
DOC_BIN_EXT = (".pdf", ".docx", ".xlsx")
DOC_BODY_EXT = DOC_BIN_EXT + (".md", ".txt", ".csv")
DOC_BODY_LINES = 600                             # document files me ek @@WRITE ki hadd

# ---------- 20.py: kaam ka type -> kitni jaanch ----------
# android = poori sakht jaanch (purana tareeka); code = bot/website/script/tool (halki); light = document/notes/chhota kaam (bina SPEC/PLAN ke)
PROFILES = {
    "android": {"spec_min": 10, "plan_min": 12, "need_spec": True,  "spec_review": True,  "check_review": True,  "flow": True,  "polish": True},
    "code":    {"spec_min": 4,  "plan_min": 4,  "need_spec": True,  "spec_review": True,  "check_review": True,  "flow": True,  "polish": False},
    "light":   {"spec_min": 0,  "plan_min": 0,  "need_spec": False, "spec_review": False, "check_review": False, "flow": False, "polish": False},
}
MODE_FORCE_RE = re.compile(r"^\s*/(android|code|light)\b", re.I)
CODE_WORD_RE = re.compile(r"\b(?:bot|script|api|server|backend|frontend|website|web\s?site|web\s?app|webapp|app|application|game|flask|django|fastapi|"
                          r"express|react|vue|angular|nextjs|node|nodejs|html|css|javascript|typescript|python|java|golang|rust|php|telegram|discord|"
                          r"whatsapp|scraper|crawler|extension|plugin|database|sql|sqlite|cli|software|program|code|coding|function|class|docker|"
                          r"github|repo|deploy|bug|compile|build|login|dashboard|calculator|tool|module|library|package|py|js|ts)\b", re.I)
DOC_WORD_RE = re.compile(r"\b(?:pdf|docx|xlsx|pptx|excel|sheet|document|doc|notes?|report|essay|letter|resume|cv|ppt|presentation|slides?|article|"
                         r"summary|summari[sz]e|translate|translation|poem|story|email|blog|assignment|worksheet|syllabus|table|explain|research|"
                         r"compare|likh\w*|nibandh|kahani|kavita|jaankari|samjha\w*)\b", re.I)


def classify_task(task):
    """Task ke shabdon se type: '/light', '/code', '/android' likho to wahi; warna code shabd zyada hon to 'code', nahi to 'light'."""
    t = task or ""
    m = MODE_FORCE_RE.match(t)
    if m:
        return m.group(1).lower()
    c = len(CODE_WORD_RE.findall(t))
    d = len(DOC_WORD_RE.findall(t))
    return "code" if (c and c >= d) or build_intent(t) else "light"


def cur_mode():
    t = STATE.get("task") or ""
    m = MODE_FORCE_RE.match(t)
    if m:
        return m.group(1).lower()
    u = STATE.get("utype")
    if u in PROFILES:                      # 21.py: model ki "samajh" ne jo type bataya
        if u == "code" and is_android_project(list_files(None)):
            return "android"
        return u
    if ANDROID_TASK_RE.search(t):
        return "android"
    k = classify_task(t)
    if k == "code" and is_android_project(list_files(None)):
        return "android"
    return k


SMALL_LEVEL = int(_env("SMALL_LEVEL", "3"))      # itne level tak ka kaam = chhota = tez raasta (0 likho to band)


def is_small():
    """Model ne level <= SMALL_LEVEL bataya aur user ne /code ya /android zabardasti nahi likha."""
    try:
        if MODE_FORCE_RE.match(STATE.get("task") or ""):
            return False
        return STATE.get("utype") == "code" and 0 < int(STATE.get("level") or 0) <= SMALL_LEVEL
    except Exception:
        return False


def prof():
    if is_small():
        return PROFILES["light"]
    return PROFILES.get(cur_mode()) or PROFILES["code"]


def mode_note(short=False):
    m = cur_mode()
    p = PROFILES[m]
    if m == "android":
        if short:
            return "KAAM KA TYPE: android - SPEC %d+ line, PLAN %d+ kadam." % (p["spec_min"], p["plan_min"])
        return "KAAM KA TYPE: android - SPEC kam se kam %d line, PLAN kam se kam %d chhote kadam." % (p["spec_min"], p["plan_min"])
    if m == "code":
        if short:
            return "KAAM KA TYPE: code - SPEC %d+ line, PLAN %d+ kadam. Android baatein mat jodo." % (p["spec_min"], p["plan_min"])
        return ("KAAM KA TYPE: code (bot/website/script/tool) - SPEC kam se kam %d line (behaviour: galat input, khaali data, error par message), "
                "PLAN kam se kam %d kadam. Android wali baatein (permission, rotate, screen lock, back button) mat jodo." % (p["spec_min"], p["plan_min"]))
    if short:
        return "KAAM KA TYPE: light - SPEC aur bada PLAN zaroori nahi, seedha kaam karo."
    return ("KAAM KA TYPE: light (document/PDF/notes/likhna/chhota kaam) - SPEC zaroori nahi, seedha kaam karo. PLAN tabhi jab kaam bada ho "
            "(2-5 kadam, har kadam me asli file ya command; 'soch/plan' jaisa kadam nahi). Android/permission/rotate/storage jaisi baatein mat jodo.")
SPEC_REVIEW_MAX, CHECK_REVIEW_MAX, FLOW_MAX = 2, 2, 2
REVIEW_MODELS = ("jaat",)  # review pehle JAAT se; doosra model jo bhi (Gemini nahi) mile
ANDROID_PITFALLS = (" Also check these real-world Android pitfalls when relevant: MediaStore DATA column / raw file paths fail on Android 10+ "
                    "(use content URIs via ContentUris.withAppendedId); MediaPlayer.prepare() on the main thread (use prepareAsync); crash on a "
                    "corrupt or missing file (need OnErrorListener / try-catch / message); non-music audio (ringtones, notifications) listed "
                    "(filter IS_MUSIC / duration); playback state lost on screen rotation; playback stops in background or on screen lock "
                    "(needs foreground Service + notification / MediaSession); missing seekbar, next/previous, auto-next on completion, "
                    "play/pause state; permission denied or 'don't ask again' with no path to app settings; API 33+ READ_MEDIA_AUDIO vs "
                    "READ_EXTERNAL_STORAGE and POST_NOTIFICATIONS; no empty-list message; MediaPlayer not released in onDestroy.")


def reviewer_slots():
    """[(provider, model), (provider, model)] - 2 alag model (pehle REVIEW_MODELS, kam pade to jo bhi mile)."""
    names, slots = reviewer_names(), []
    for want in REVIEW_MODELS:
        for n in names:
            ms = models_of(APIS[n])
            m = next((x for x in ms if x == want), None) or next((x for x in ms if want in x), None)
            if m and (n, m) not in slots:
                slots.append((n, m))
                break
    for n in names:
        for m in models_of(APIS[n]):
            if len(slots) < 2 and (n, m) not in slots:
                slots.append((n, m))
    return slots[:2]


def call_slot(name, model, system, user):
    """Ek khaas model se poochho (3 koshish). Text ya None."""
    for t in range(REVIEWER_TRIES):
        if STATE["cancel"]:
            return None
        cfg = dict(APIS[name], models=[model], max_tokens=REVIEWER_TOKENS, timeout=max(APIS[name].get("timeout", 60), 30), total_timeout=min(REVIEWER_TIMEOUT, max(APIS[name].get("total_timeout", REVIEWER_TIMEOUT), 120)))
        try:
            res = ask_review(name, cfg, system, user)
        except Exception as e:
            log("reviewer %s/%s: %s" % (name, model, e))
            res = None
        if res and res[0].strip():
            return res[0]
        if COOL.get(name, 0) - time.time() > 500 or not nap(2 * (t + 1)):
            break
    return None


def dual_call(system, user):
    """[(text, 'model')] - dono model saath-saath. Dono fail ho to purana single reviewer_call."""
    slots = reviewer_slots()
    res = [None] * len(slots)

    def work(i):
        res[i] = call_slot(slots[i][0], slots[i][1], system, user)
    if REVIEW_PARALLEL:
        ths = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(len(slots))]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
    else:                          # ek provider par ek saath 2 badi requests se limit/502 aata hai
        for i in range(len(slots)):
            if i and not nap(REVIEW_GAP):
                break
            work(i)
    out = [(r, slots[i][1]) for i, r in enumerate(res) if r]
    if not out and not STATE["cancel"]:
        txt, prov = reviewer_call(system, user)
        if txt:
            out = [(txt, prov or "?")]
    return out


def merge_bugs(lists):
    out, seen = [], set()
    for lst in lists:
        for b in lst or []:
            key = (str(b.get("file")), str(b.get("line")))
            if key in seen and str(b.get("line")).isdigit():
                continue
            seen.add(key)
            out.append(b)
    return out[:15]


def show_review(title, ok, text, prov=""):
    """Reviewer ka jawab chat me saaf dikhe (✅ ya 🐞)."""
    ev("review", title=title, ok=bool(ok), text=clip(text, 3000), prov=prov)


# ---------- SPEC ka review: "kya user ke raste ke kaam chhoot rahe hain?" ----------
SPEC_SYS = ("You review a SPEC (checkable requirement lines) written for a software task. Ask: 'are the real user's flows missing?' "
            "A good SPEC is about BEHAVIOUR (what the user sees/does, failure cases: permission denied, empty data, rotate, lock screen, bad input, "
            "back button), not only setup (gradle, manifest). Judge ok=false if less than 70% of lines are behaviour or important flows of this kind "
            "of app are missing. Reply ONLY JSON, no prose: {\"ok\":true,\"missing\":[\"short missing flow\", up to 8]}")


SPEC_SYS_CODE = ("You review a SPEC (checkable requirement lines) written for a software task (bot, script, website, API or tool - NOT necessarily a mobile app). "
                 "Ask: 'are the real user's main flows and failure cases missing?' A good SPEC is about BEHAVIOUR (what the user sees/does; failure cases that fit "
                 "THIS kind of software: bad or empty input, missing config or API key, network/API error, restart/state, large input), not only setup. "
                 "Do NOT ask for mobile-only things (permission dialogs, rotation, lock screen, back button) unless the TASK itself is a mobile app. "
                 "Judge ok=false only if more than 40% of lines are setup-only or an important flow of this kind of software is clearly missing. "
                 'Reply ONLY JSON, no prose: {"ok":true,"missing":["short missing flow", up to 5]}')


def spec_review(items):
    slots = reviewer_slots()
    if not slots:
        return None, ""
    user = "TASK:\n%s\n\nSPEC:\n%s" % ((STATE["task"] or "")[:1500], "\n".join("S%d: %s" % (i + 1, x) for i, x in enumerate(items)))
    t = call_slot(slots[0][0], slots[0][1], SPEC_SYS if cur_mode() == "android" else SPEC_SYS_CODE, user)
    d = parse_json_reply(t) if t else None
    return (d if isinstance(d, dict) else None), slots[0][1]


# ---------- CHECK ka saboot ----------
CHECK_SYS = ("A coding agent claims a plan step is finished. Judge from the project files whether that step's behaviour is REALLY implemented "
             "and wired (a step is not done just because it exists in a template or stub). "
             "Reply ONLY JSON: {\"done\":true,\"evidence\":\"file:line what\",\"missing\":\"what is still missing\"}")
LAZY_CHECK_RE = re.compile(r"pehle se|already|template|no change|kuch nahi|nahi badla|zaroorat nahi|skip", re.I)


def check_claim(item, note):
    """(True/False/None, text, model). None = reviewer nahi mila (maan lo)."""
    slots = reviewer_slots()
    if not slots:
        return None, "", ""
    n, m = slots[0]
    t = call_slot(n, m, CHECK_SYS + FILES_NOTE, "PLAN STEP: %s\nAGENT NOTE: %s\n\n%s" % (item, note or "-", _final_payload(n)))
    d = parse_json_reply(t) if t else None
    if not isinstance(d, dict) or "done" not in d:
        return None, "", m
    done = d["done"] is True or str(d["done"]).strip().lower() in ("true", "yes", "1")
    why = (d.get("evidence") or d.get("missing") or "") if done else (d.get("missing") or d.get("evidence") or "")
    return done, str(why)[:300], m


# ---------- user-flow simulate + polish ----------
FLOW_SYS = ("You are a QA engineer who SIMULATES real users on the code. Walk these flows step by step through the actual code path: "
            "(1) open the app fresh; (2) deny the permission (also 'don't ask again'); (3) do the main action (e.g. tap an item); "
            "(4) rotate the screen in the middle; (5) lock the screen / send app to background; (6) empty list / no data; "
            "(7) corrupt or missing file / bad input; (8) back button then reopen; (9) next/previous/repeat of the main action; (10) process death. "
            "Write a bug at EVERY place where a flow breaks (crash, stuck UI, lost state, silent failure, a feature that flow needs but is missing). "
            "Use exact file and line from the numbered listing. Reply ONLY a JSON array, no prose: "
            '[{"file":"path","line":N,"spec_id":"","bug":"flow name: what happens","fix":"exact change"}]. Reply [] if every flow works.')
FLOW_SYS_CODE = ("You are a QA engineer who SIMULATES real use of this software (bot, script, website, API or tool - NOT a mobile app) on the code. "
                 "Walk these flows step by step through the actual code path: (1) start it fresh with normal valid input; (2) do the main action; "
                 "(3) empty or missing input; (4) malformed or unexpected input; (5) missing config, env var or API key; (6) network / API / file error; "
                 "(7) run it a second time or restart (state, duplicates); (8) very large input. "
                 "Do NOT report mobile-only things (permission dialogs, rotation, lock screen). "
                 "Write a bug at EVERY place where a flow breaks (crash, hang, silent failure, wrong output, a feature the flow needs but is missing). "
                 "Use exact file and line from the numbered listing. Reply ONLY a JSON array, no prose: "
                 '[{"file":"path","line":N,"spec_id":"","bug":"flow name: what happens","fix":"exact change"}]. Reply [] if every flow works.')
POLISH_SYS = ("You are a product reviewer. From the task, SPEC and files, list the TOP 10 features/behaviours that a good real-world app of this "
              "type has but this project lacks (skip what is already implemented; skip pure styling). Reply ONLY a JSON array, no prose: "
              '[{"item":"short feature","why":"why users expect it"}]')


def flow_pass(payload):
    outs = dual_call((FLOW_SYS if cur_mode() == "android" else FLOW_SYS_CODE) + FILES_NOTE + (ANDROID_PITFALLS if cur_mode() == "android" else ""), payload)
    lists, provs = [], []
    for t, lab in outs:
        b = _norm_bugs(parse_json_reply(t), "")
        if b is not None:
            lists.append(drop_phantom(b))
            provs.append(lab)
    return (merge_bugs(lists) if lists else None), ", ".join(provs)


def polish_list(payload):
    slots = reviewer_slots()
    if not slots:
        return []
    t = call_slot(slots[0][0], slots[0][1], POLISH_SYS, payload)
    d = parse_json_reply(t) if t else None
    if isinstance(d, dict):
        d = d.get("items") or d.get("features") or []
    out = []
    for i, x in enumerate(d if isinstance(d, list) else [], 1):
        if isinstance(x, dict) and x.get("item"):
            out.append("N%d: %s - %s" % (i, clip(str(x["item"]), 90), clip(str(x.get("why", "")), 110)))
    return out[:10]


# ---------- gates ----------
def is_gradle_cmd(cmd):
    for seg in re.split(r"&&|\|\||;|\||\n", cmd or ""):
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        toks = [t for t in toks if not re.match(r"^\w+=", t)]
        while toks and toks[0] in ("sh", "bash", "exec", "nohup", "time", "env", "command", "timeout"):
            toks = toks[1:]
        if toks and os.path.basename(toks[0]) in ("gradle", "gradlew", "gradlew.bat"):
            return True
    return False


def gradle_gate(k, a):
    if k in ("RUN", "BG") and is_gradle_cmd(a):
        return ("ROKA: phone/Termux par ./gradlew aur gradle dono nahi chalte. Build GitHub Actions par hoga "
                "(.github/workflows/android.yml). Yahan @@VERIFY se jaancho, aur user se bolo: 'build GitHub par hoga'. Phir aage badho.")
    return None


def size_gate(k, a, body):
    """Ek @@WRITE/@@EDIT ki line hadd (MAX_BODY_LINES) se zyada par roko, alag files me todne ko kaho."""
    if k not in ("WRITE", "EDIT"):
        return None
    rel = rel_of(a)
    if not rel or rel in (SPEC_FILE, "PROJECT.md"):
        return None
    n = len([l for l in (body or "").split("\n") if l.strip()])
    lim = DOC_BODY_LINES if rel.lower().endswith(DOC_BODY_EXT) else MAX_BODY_LINES
    if n <= lim or TRACK["size_rej"].get(rel, 0) >= BODY_REJECT_MAX:
        return None
    TRACK["size_rej"][rel] = TRACK["size_rej"].get(rel, 0) + 1
    return ("ROKA: ek @@%s me %d line hain, hadd %d hai. Chhota karo: bada kaam alag files me todo (jaise model, logic, screen/page alag) "
            "ya pehle chhoti @@WRITE, phir kai chhote @@EDIT (300-600 line). Document ho to alag-alag part files me likho. "
            "Har file ke baad reviewer dekhega." % (k, n, lim))


# ---------- chhote sudhar ----------
def busy_note_ok():
    now = time.time()
    if now - _LAST_BUSY[0] < 8:
        return False
    _LAST_BUSY[0] = now
    return True


def drop_old_zips(keep):
    for f in os.listdir(WORK):
        if f.lower().endswith(".zip") and f != keep and os.path.isfile(os.path.join(WORK, f)):
            p = os.path.join(WORK, f)
            snap(p)
            try:
                os.remove(p)
            except OSError:
                pass


# ---------- commands chalana ----------
def run_cmd(k, a, body="", complete=True):
    """(natija, kuch badla?, theek?, exit_code)"""
    try:
        if k in BODY_KINDS and not complete:
            return ("ERROR: @@END nahi aaya, jawab beech me kat gaya, isliye %s nahi kiya. "
                    "Chhote hisson me do (chhoti @@WRITE + @@EDIT), ya file ke andar @@END ho to `<<EOF_M` tareeka." % k), False, False, None
        if k == "LS":
            p = safe(a)
            names = sorted(n + ("/" if os.path.isdir(os.path.join(p, n)) else "") for n in os.listdir(p))
            return "\n".join(names) or "(khali)", False, True, None
        if k == "TREE":
            fs = list_files(None)
            txt = "\n".join("%s (%d B)" % (f, os.path.getsize(os.path.join(WORK, f))) for f in fs[:400])
            if len(fs) > 400:
                txt += "\n...(%d files aur; @@LS <folder> ya @@GREP use karo)" % (len(fs) - 400)
            return txt or "(khali)", False, True, None
        if k == "GREP":
            q, out = a.lower(), []
            for f in list_files(None):
                fp = os.path.join(WORK, f)
                try:
                    if os.path.getsize(fp) > 1024 * 1024:
                        continue
                    with open(fp, encoding="utf-8", errors="ignore") as fh:
                        for n, line in enumerate(fh, 1):
                            if q in line.lower():
                                out.append("%s:%d: %s" % (f, n, line.strip()[:120]))
                except OSError:
                    pass
                if len(out) >= 100:
                    break
            txt = "\n".join(out[:100]) or "(kuch nahi mila)"
            return txt + ("\n...(sirf pehle 100 dikhaye)" if len(out) >= 100 else ""), False, True, None
        if k == "READ":
            rng = None
            parts = a.rsplit(None, 1)
            if len(parts) == 2 and re.fullmatch(r"\d+-\d+", parts[1]):
                a, rng = parts[0], tuple(int(x) for x in parts[1].split("-"))
            p = safe(a)
            with open(p, "rb") as f:
                raw = f.read()
            conv = read_any(p, raw)
            if conv is not None:
                text = conv
            elif b"\0" in raw[:4096]:
                return "(binary file, %d bytes) padha nahi ja sakta" % len(raw), False, True, None
            else:
                text = raw.decode("utf-8", "replace")
            lines = text.split("\n")
            if rng:
                s, e = max(1, rng[0]), max(rng[0], rng[1])
                return "(lines %d-%d, kul %d)\n%s" % (s, min(e, len(lines)), len(lines), clip("\n".join(lines[s - 1:e]), READ_CAP)), False, True, None
            if len(text) > READ_CAP:
                cut = text[:READ_CAP]
                return ("%s\n...(kata gaya: %d lines me se %d dikhi. Aage ke liye @@READ %s %d-%d)" % (
                    cut, len(lines), cut.count("\n") + 1, a, cut.count("\n") + 1, cut.count("\n") + 900)), False, True, None
            return text, False, True, None
        if k in ("WRITE", "WRITEB64"):
            p = safe(a)
            if os.path.isdir(p):
                return "ERROR: ye folder hai, file ka naam do", False, False, None
            if k == "WRITEB64":
                data = base64.b64decode("".join(body.split()))
            else:
                data = unlink(body).encode("utf-8")
            note = ""
            ext = os.path.splitext(p)[1].lower()
            if k == "WRITE" and ext in MAKE_EXT:
                data, note = make_binary(ext, unlink(body))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            snap(p)
            with open(p, "wb") as f:
                f.write(data)
            return "likha: %s (%d bytes)%s" % (a, len(data), note), True, True, None
        if k == "EDIT":
            p = safe(a)
            if not os.path.isfile(p):
                return "ERROR: file nahi mili: %s (nayi file ke liye @@WRITE)" % a, False, False, None
            with open(p, encoding="utf-8", errors="surrogateescape", newline="") as f:
                old = f.read()
            ap = a.strip().strip("`'\"")
            strict = os.path.splitext(ap)[1].lower() in INDENT_EXT or os.path.basename(ap) in ("Makefile", "makefile")
            clean = unlink(body)
            new, err = apply_edit(old, clean, strict)
            if err and clean != body:                  # file me sach me link-syntax ho to asli body se try
                new2, err2 = apply_edit(old, body, strict)
                if not err2:
                    new, err = new2, None
            if err:
                return "ERROR: " + err, False, False, None
            snap(p)
            with open(p, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
                f.write(new)
            return "badlav ho gaya: %s" % a, True, True, None
        if k == "UNZIP":
            return do_unzip(a)
        if k == "WEB":
            return do_web(a)
        if k == "SEARCH":
            return do_search(a)
        if k == "NOTIFY":
            return do_notify(a)
        if k == "NOTE":
            if ("- " + a) in _read("PROJECT.md"):
                return "ye note pehle se PROJECT.md me hai. Dobara mat likho: agla kadam karo, ya jawab chat me likho / @@DONE.", False, True, None
            append_pm(["- " + a])
            return "PROJECT.md me likha. NOTE jawab dene ke liye nahi hai: ab agla kadam karo ya jawab chat me likho.", False, True, None
        if k == "LEARN":
            TRACK["learned"] = True
            return ("seekha me likha (har project me yaad rahega)" if seekha_add(a) else "ye sabak pehle se likha hai"), False, True, None
        if k == "LOOK":
            return do_look(a)
        if k == "TEMPLATE":
            return do_template(a)
        if k == "DEPS":
            return deps_summary(a), False, True, None
        if k == "VERIFY":
            probs = verify_project(force_android=(cur_mode() == "android"))
            return format_report(probs) + doc_summary(), False, not any(lv == "E" for lv, _ in probs), None
        if k == "ZIP":
            name = clean_name((a.split() or [""])[0] or STATE["proj"])
            if not name.endswith(".zip"):
                name += ".zip"
            p = os.path.join(WORK, name)
            drop_old_zips(name)
            snap(p)
            data = make_zip(skip={name})
            with open(p, "wb") as f:
                f.write(data)
            return "zip bani: %s (%d files, %d bytes). 📁 se download karo" % (name, len(zip_files({name})), len(data)), True, True, None
        if k == "BG":
            why = risky(a)
            if why:
                return "ERROR: " + why, False, False, None
            jid = bg_start(a)
            return "background me shuru: id %s. Dekhne ke liye @@LOG %s" % (jid, jid), False, True, None
        if k == "LOG":
            txt = bg_status(a)
            return txt, False, not txt.startswith("ERROR"), None
        if k == "KILL":
            j = BG.get(a.strip())
            if not j:
                return "ERROR: aisi id nahi: %s" % a, False, False, None
            kill_group(j["p"])
            return "band kiya: %s" % a, False, True, None
        if k == "RUN":
            why = risky(a)
            if why:
                return "ERROR: " + why + ". Sirf project folder ke andar kaam karo.", False, False, None
            before = manifest()
            code, text, stop = run_shell(a)
            new, mod = diff_manifest(before)
            res = "exit %s\n%s" % (code, clip(text, 4000))
            if stop == "timeout":
                res += "\n⏱ %ds ho gaye, command band kar di. Lamba kaam ho to @@BG use karo." % RUN_TIMEOUT
            elif stop == "cancel":
                res += "\n⏹ user ne roka, command band kar di."
            if new or mod:
                res += "\n(nayi: %s | badli: %s)" % (", ".join(new[:8]) or "-", ", ".join(mod[:8]) or "-")
            return res, bool(new or mod), code == 0 and not stop, code
    except Exception as e:
        return "ERROR: %s" % e, False, False, None
    return "", False, True, None


def do_undo():
    if STATE["running"]:
        return "⏳ pehle ⏹ se roko"
    if not UNDO_STACK:
        return "↩ wapas karne ko kuch nahi"
    cur = UNDO_STACK.pop()
    n = 0
    for rel, orig in cur.items():
        try:
            p = safe(rel)
            if orig is None:
                if os.path.isfile(p):
                    os.remove(p)
                prune(os.path.dirname(p))
            else:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(orig)
            n += 1
        except (OSError, ValueError):
            pass
    save_state()
    return "↩ pichhle kaam ki %d file wapas pehle jaisi kar di (@@RUN se hui purani files ka delete/badlav wapas nahi hota)" % n


# ---------- yaad-daasht ----------
def add(role, content):
    if not STATE["msgs"]:
        STATE["msgs"] = [{"role": "system", "content": SYSTEM}]
    STATE["msgs"].append({"role": role, "content": content})


def tell_agent(text):
    if STATE["running"]:
        STATE["pending"].append(text)       # agent agle kadam par padh lega
    else:
        add("user", text)
        save_state()


def wl(line):
    STATE["worklog"].append(line[:140])
    del STATE["worklog"][:-200]


def trim():
    m = STATE["msgs"]
    for x in m[1:-8]:                       # purane bade messages chhote karo (file to disk par hai)
        c = x.get("content")
        if isinstance(c, str) and len(c) > 1500:
            x["content"] = c[:800] + "\n...(purana hissa kata gaya)...\n" + c[-400:]
    if len(m) > 30:
        first = next((x for x in m[1:] if x["role"] == "user"), None)
        tl = m[-16:]
        while tl and tl[0]["role"] != "user":
            tl = tl[1:]
        mem = {"role": "user", "content": "[Yaad-daasht] Ab tak ka kaam:\n" + "\n".join(STATE["worklog"][-40:])}
        STATE["msgs"] = [m[0]] + ([first] if first and first not in tl else []) + [mem] + tl


def save_state():
    try:
        with LOCK:
            evs = list(EV)
        d = {"msgs": STATE["msgs"], "ev": evs, "plan_items": STATE["plan_items"], "plan_ck": STATE["plan_ck"],
             "worklog": STATE["worklog"], "task": STATE["task"],
             "utype": STATE.get("utype", ""), "goal": STATE.get("goal", ""), "criteria": STATE.get("criteria", [])}
        path = os.path.join(ROOT, ".mumbai", STATE["proj"] + ".json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except Exception as e:
        log("state save fail: %s" % e)


def reset_chat_state():
    STATE.update(msgs=[], plan_items=[], plan_ck=[], worklog=[], task="", pending=[], step=0, prov="",
                 utype="", goal="", criteria=[], asking=None, answer=None, strong_n=0)
    UNDO_STACK.clear()


def load_state():
    """Disk se is project ka chat wapas laata hai. Purane events ki list lautata hai."""
    try:
        with open(os.path.join(ROOT, ".mumbai", STATE["proj"] + ".json"), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    STATE.update(msgs=d.get("msgs") or [], plan_items=d.get("plan_items") or [], plan_ck=d.get("plan_ck") or [],
                 worklog=d.get("worklog") or [], task=d.get("task") or "",
                 utype=d.get("utype") or "", goal=d.get("goal") or "", criteria=d.get("criteria") or [])
    return d.get("ev") or []


def replay(evs):
    for e in evs:
        e = dict(e)
        t = e.pop("t", None)
        e.pop("id", None)
        if t and t != "reset":
            ev(t, _log=False, **e)


def clean_proj(n):
    return re.sub(r"[^\w\-]", "_", (n or "").strip())[:40].strip("_")


def set_project(name):
    global WORK
    WORK = os.path.realpath(os.path.join(ROOT, name))
    os.makedirs(os.path.join(WORK, "uploads"), exist_ok=True)
    STATE["proj"] = name
    with open(os.path.join(ROOT, ".mumbai", "last"), "w") as f:
        f.write(name)


def list_projects():
    return sorted(d for d in os.listdir(ROOT) if not d.startswith(".") and os.path.isdir(os.path.join(ROOT, d)))


def new_chat():
    """Naya chat = naya khaali folder. Purana chat/files alag project me safe rehte hain."""
    if not STATE["msgs"] and not list_files(1):
        return "🆕 ye pehle se naya aur khaali hai"
    name = time.strftime("chat_%m%d_%H%M%S")
    switch_project(name)
    return "🆕 naya chat (folder: %s). Purana kaam title dabake wapas mil jayega" % name


def enter_project(name):
    set_project(name)
    reset_chat_state()
    old = load_state()
    with LOCK:
        EV.clear()
    ev("reset")
    replay(old)


def switch_project(name):
    name = clean_proj(name)
    if not name:
        return "❌ project ka naam galat"
    save_state()
    kill_bg()
    BG.clear()
    track_reset()
    enter_project(name)
    return "📂 project: %s" % name


def project_dir(name):
    d = os.path.realpath(os.path.join(ROOT, name))
    if os.path.dirname(d) != ROOT or name.startswith("."):
        raise ValueError("project ka naam galat")
    return d


def delete_project(name):
    name = clean_proj(name)
    if name not in list_projects():
        raise ValueError("aisa project nahi hai")
    d, cur = project_dir(name), name == STATE["proj"]
    if cur:
        kill_bg()
        BG.clear()
    shutil.rmtree(d, ignore_errors=True)
    try:
        os.remove(os.path.join(ROOT, ".mumbai", name + ".json"))
    except OSError:
        pass
    if cur:
        rest = list_projects()
        enter_project(rest[0] if rest else "main")
    return "🗑 project '%s' hat gaya" % name


def rename_project(name, to):
    a, b = clean_proj(name), clean_proj(to)
    if not b:
        raise ValueError("naya naam galat")
    if a not in list_projects():
        raise ValueError("aisa project nahi hai")
    if a == b:
        return "naam wahi hai"
    if os.path.exists(os.path.join(ROOT, b)):
        raise ValueError("is naam ka project pehle se hai")
    da, cur = project_dir(a), a == STATE["proj"]
    if cur:
        save_state()
        kill_bg()
        BG.clear()
    os.rename(da, os.path.join(ROOT, b))
    ja, jb = (os.path.join(ROOT, ".mumbai", x + ".json") for x in (a, b))
    if os.path.exists(ja):
        os.replace(ja, jb)
    if cur:
        enter_project(b)
    return "✏️ project '%s' → '%s'" % (a, b)


# ---------- agent ----------
CONT_KEYS = {"aage", "age", "badho", "badhao", "continue", "resume", "jaari", "jari", "chalu", "dobara", "retry",
             "agla", "next", "again"}
CONT_WORDS = CONT_KEYS | {"ok", "okay", "haan", "han", "yes", "ji", "please", "pls", "karo", "kar", "do", "chalo",
                          "rakho", "se", "phir", "fir", "kaam", "jao", "go", "on", "carry", "raho", "bhai", "yaar",
                          "theek", "hai", "ab", "to", "toh", "iske", "baad", "bad"}


ANALYZE_RE = re.compile(r"analy|review|audit|jaanch|janch|check|dekh|deep|bug|galti|btao|batao|bata\b|samjha|explain|sahi hai", re.I)


def project_has_files():
    return any(f not in ("PROJECT.md", "SPEC.md") and f.split(os.sep, 1)[0] != "uploads" for f in list_files(None))


def is_continue(t):
    """'aage badho' jaisa chhota message (naya kaam nahi, purana hi jaari rakhna)."""
    ws = re.findall(r"[a-z]+", t.lower())
    return 0 < len(ws) <= 6 and all(w in CONT_WORDS for w in ws) and any(w in CONT_KEYS for w in ws)


def plan_left():
    done = set(STATE["plan_ck"])
    return [i for i in range(1, len(STATE["plan_items"]) + 1) if i not in done]


def begin():
    with START:
        if STATE["running"]:
            return False
        STATE.update(running=True, cancel=False, step=0, prov="")
        return True


def start_agent(task):
    if not begin():
        return False
    ev("user", text=task)
    threading.Thread(target=agent, args=(task,), daemon=True).start()
    return True


def agent(task):
    try:
        run_agent(task)
    except Exception as e:
        log(traceback.format_exc())
        ev("err", text="❌ andar ki galti: %s: %s" % (type(e).__name__, str(e)[:200]))
    finally:
        if STATE["cancel"]:
            STATE["pending"].clear()
        STATE["running"] = False
        STATE["asking"] = None
        trim()
        save_state()


def run_agent(task):
    track_reset()
    UNDO_STACK.append({})
    del UNDO_STACK[:-10]
    if is_continue(task) and STATE["task"]:
        # "aage badho" par asli kaam wahi rahe, warna chhota AI purana kaam bhool jata hai
        add("user", task + "\n[Asli kaam jo chal raha hai: %s]" % STATE["task"][:700])
        load_spec()
    else:
        STATE["task"] = task
        try:
            tip = template_tip()
        except Exception:
            tip = ""
        samajh = ""
        STATE.update(utype="", goal="", criteria=[])
        try:
            samajh = do_understand(task)
        except Exception as e:
            log("samajh galti: %s" % e)
        if STATE["cancel"]:
            ev("note", text="⏹ roka gaya")
            return
        add("user", task + samajh + (("\n[" + tip + "]") if tip else "") +
            ("\n[KAAM YAAD RAKHO: user ne ye maanga hai: %s. Hello/'kaise madad karoon' mat likho, seedha kaam shuru karo.]" % task[:400]
             if STATE.get("utype") in ("code", "android") or build_intent(task) else ""))
    offtask = 0
    acted, dirty, last_fail, nudges = 0, False, False, 0
    wrote, verified, zip_made, zip_stale = False, False, False, False
    rej = {"done": 0, "verify": 0, "fail": 0, "plan": 0, "lint": 0, "zip": 0, "check": 0, "bugchk": 0, "specmin": 0, "specrev": 0, "planmin": 0}
    repeat, last_sig, forced, readnudge, waits = 0, None, 0, 0, 0

    def finish(summary, name, step, errs=()):
        left = plan_left()
        warn = ("\n⚠ plan ke kadam %s @@CHECK nahi hue the" % ", ".join(map(str, left))) if left else ""
        if errs:
            warn += "\n⚠ jaanch me abhi bhi %d galti baaki:\n%s" % (len(errs), "\n".join("- " + clip(e, 160) for e in errs[:6]))
        if wrote:            # zip Mumbai khud banata hai, saboot ke BAAD
            try:
                zr, _c, zok, _x = run_cmd("ZIP", STATE["proj"])
            except Exception as ex:
                zr, zok = "ERROR: %s" % ex, False
            wl("ZIP %s" % STATE["proj"])
            ev("step", kind="ZIP", arg=STATE["proj"], out=zr, ok=zok, prov="mumbai")
        try:
            if TRACK.get("last_reject") and not TRACK.get("learned"):
                fl = next((l.strip("- ").strip() for l in TRACK["last_reject"].split("\n") if l.startswith("- ")), "")
                seekha_add("[auto] aakhri review ne rok diya tha: %s" % (fl[:110] or "details upar chat me"))
        except Exception as e:
            log("auto seekha galti: %s" % e)
        ev("done", text=(summary or "Kaam poora") + warn + TRACK.get("final_warn", ""), prov=name, steps=step, zip=bool(wrote), proj=STATE["proj"])
        notify_bg("✅ %s: %s" % (STATE["proj"], (summary or "Kaam poora")[:300]))

    for step in range(1, MAX_STEPS + 1):
        STATE["step"] = step
        while STATE["pending"]:
            add("user", STATE["pending"].pop(0))
        trim()
        if STATE["cancel"]:
            ev("note", text="⏹ roka gaya")
            return
        got = ask(build_msgs(), role=cur_role())
        if STATE["cancel"]:
            ev("note", text="⏹ roka gaya")
            return
        if not got:
            ev("err", text="❌ koi AI jawab nahi de paya." + (cool_info() or " Upar 🩺 Test dabake dekho kaun sa chal raha hai.") + " Thodi der baad 'aage badho' likho.")
            return
        name, reply, cut = got
        STATE["prov"] = name
        reply = clean_reply(reply)
        _c0, _p0 = parse(reply)
        if (not _c0 and not acted and offtask < 3 and len(reply) < 500 and "@@" not in reply
                and (STATE.get("utype") in ("code", "android") or build_intent(task)) and not is_continue(task)):
            offtask += 1                     # kaam ki jagah 'Hello! kaise madad karoon' jaisa jawab: usse mat maano
            penalize(name, 180)
            ev("note", text="↷ %s ne kaam ka jawab nahi diya (%s), agle AI se kara raha hoon (%d/3)" % (name, reply.strip()[:40].replace("\n", " "), offtask))
            continue
        add("assistant", reply)
        cmds, prose = parse(reply)
        need_read = bool(not cmds and not acted and readnudge < 2 and ANALYZE_RE.search(STATE["task"] or task)
                         and project_has_files() and not TRACK["seen"])
        if prose and not need_read:
            ev("ai", text=prose, prov=name)
        if need_read:
            readnudge += 1
            add("user", "ROKA: tumne koi file padhi hi nahi aur analysis/jawab de diya. Bina padhe ✅ ya 'sab sahi hai' mat likho. "
                        "Pehle @@TREE, phir zaroori files @@READ (badi file ho to line range) karo. Har baat ke saath file:line ka saboot do.")
            ev("note", text="↻ bina file padhe jawab tha, AI ko pehle @@READ karne ko kaha")
            continue
        if not cmds:
            stuck = bool(acted and plan_left())      # plan adhura hai par AI ne command nahi diya
            if acted and wrote and not cut and "@@" not in reply and not stuck and TRACK["spec"] and forced < 4:
                # @@DONE nahi aaya par kaam ho chuka: baat par mat ruko, saboot-review zabardasti chalao
                forced += 1
                ev("note", text="🧪 @@DONE nahi aaya par kaam ho chuka hai, isliye aakhri saboot-review zabardasti chala raha hoon")
                fmsg = done_gate()
                if fmsg:
                    add("user", "Natija:\n" + fmsg + "\n(Tumne @@DONE nahi likha tha, isliye Mumbai ne khud aakhri review chalaya. Baat par mat ruko: theek karo, phir @@DONE.)")
                    continue
                finish((prose or "")[:200], name, step)
                return
            if nudges < 3 and (cut or "@@" in reply or stuck):
                nudges += 1
                if cut:
                    add("user", "Tumhara jawab limit par beech me kat gaya. Kaam chhote hisson me karo (chhoti @@WRITE, phir @@EDIT).")
                elif "@@" in reply:
                    add("user", "Tumhare jawab me @@ command sahi format me nahi mila. Har command nayi line par sirf @@NAAM se shuru ho "
                                "(bold/backtick nahi). Kaam baaki ho to command do, poora ho to @@DONE <saar>.")
                else:
                    add("user", "Plan ke kadam %s abhi baaki hain. Agla kadam command se karo (@@READ/@@WRITE/@@EDIT/@@RUN). "
                                "Kadam ho chuka ho to @@CHECK n likho." % ", ".join(map(str, plan_left())))
                ev("note", text="↻ AI ko sahi command likhne ko kaha")
                continue
            if cut or "@@" in reply or stuck:
                ev("err", text="⚠ AI teen baar bhi sahi command nahi de paya, kaam ruk gaya. 'aage badho' likh ke dobara chalao.")
            return          # sirf baat thi, jawab upar dikh chuka hai
        nudges = 0          # command mile, ginti dobara shuru
        sig = tuple((k, a, b) for k, a, b, _ in cmds if k not in CTL) or tuple(
            (k, a, "") for k, a, _b, _ in cmds if k in ("NOTE", "LEARN", "CHECK"))
        repeat = repeat + 1 if sig and sig == last_sig else 1
        last_sig = sig
        if repeat >= REPEAT_MAX:
            if waits >= REPEAT_WAITS:
                ev("err", text="⚠ AI %d baar ruk-ruk ke bhi wahi command dohra raha, maine roka. Kaam aur saaf likh ke dobara try karo." % repeat)
                return
            waits += 1
            COOL.clear()
            FAILS.clear()
            ev("note", text="⏸ AI %d baar wahi command dohra chuka. 15 min ruk raha hoon (%d/%d), phir wahin se chalega. ⏹ dabao to ruk jayega." % (
                repeat, waits, REPEAT_WAITS))
            if not nap(REPEAT_WAIT):
                ev("note", text="⏹ roka gaya")
                return
            repeat, last_sig = 0, None
            add("user", "[Mumbai: 15 min ruk ke dobara shuru. Pehle AI wahi command dohra rahe the. Natija upar hai; ab dusra tareeka socho ya agla kadam karo.]")
            save_state()
            continue
        if repeat >= 3 and repeat % 3 == 0:
            # wahi command dohra raha hai: is AI ko kuch der ke liye hatao, agla AI wahi kadam kare
            penalize(name, 300)
            ev("note", text="🔁 %s wahi command dohra raha tha (%d baar), ab agle AI se kara raha hoon" % (name, repeat))
            add("user", "[Mumbai: pichhle AI ne wahi command dohraya. Natija pehle hi upar hai, use dekh ke ISI kadam ka agla hissa karo. "
                        "Wahi command mat dohrao; file ka kata hissa chahiye to chhoti line range padho.]")
            save_state()
            continue
        has_action = any(k not in CTL for k, _, _, _ in cmds)
        out, stop_batch = [], False
        probs, errs = [], []
        if any(k == "DONE" for k, _, _, _ in cmds) and not has_action:
            # DONE par Mumbai khud project jaanchta hai (AI ke bharose nahi)
            try:
                empty = not [f for f in list_files(None) if f != "PROJECT.md"]
                probs = verify_project(force_android=bool(ANDROID_TASK_RE.search(STATE["task"] or "") and (wrote or empty)))
            except Exception as e:
                log("verify galti: %s" % e)
            errs = [m for lv, m in probs if lv == "E"]
        for k, a, body, complete in cmds:
            if STATE["cancel"]:
                ev("note", text="⏹ roka gaya")
                return
            if k == "DONE":
                if has_action:
                    out.append("DONE abhi nahi maana: DONE hamesha akela likho, pichhle commands ka natija dekhne ke baad.")
                elif not acted and rej["done"] < 1:
                    rej["done"] += 1
                    out.append("DONE mana: abhi tak koi kaam hua hi nahi. Pehle command se kaam karo.")
                elif plan_left() and rej["plan"] < 2:
                    rej["plan"] += 1
                    out.append("DONE mana: plan ke kadam %s abhi @@CHECK nahi hue. Baaki kaam karo aur har kadam ke baad @@CHECK n. "
                               "Agar koi kadam sach me zaroori nahi to bhi @@CHECK n likh ke aage badho." % ", ".join(map(str, plan_left())))
                elif zip_made and zip_stale and rej["zip"] < 2:
                    rej["zip"] += 1
                    out.append("DONE mana: ZIP banne ke BAAD files badli hain, isliye zip purani hai. Saara kaam khatam karke @@ZIP dobara do.")
                elif errs and rej["lint"] < 4:
                    rej["lint"] += 1
                    out.append("DONE mana: Mumbai ki apni jaanch me %d galti mili (tumhare @@RUN se ye nahi dikhti). "
                               "Inhe theek karo, phir @@VERIFY, saaf aaye tab @@DONE.\n%s" % (len(errs), format_report(probs)))
                elif dirty and not errs and not any(f.endswith(LINTED_EXT) for f in list_files(None)) and rej["verify"] < 1:
                    rej["verify"] += 1
                    out.append("DONE mana: aakhri badlav ke baad koi check nahi hua. Ek @@RUN (py_compile/node --check/test) ya @@READ karo, phir @@DONE.")
                elif last_fail and rej["fail"] < 1:
                    rej["fail"] += 1
                    out.append("DONE mana: aakhri @@RUN fail hua tha (exit != 0). Theek karo, ya @@DONE me saaf likho kyun theek hai.")
                else:
                    fmsg = done_gate()
                    if fmsg:
                        out.append(fmsg)
                        continue
                    finish(a, name, step, errs)
                    return
                continue
            if k == "SPEC":
                sitems = parse_spec(body)
                _pf = prof()
                _need = 3 if _pf["need_spec"] else 1
                if len(sitems) < _need:
                    out.append("[SPEC] kam hai. Kam se kam %d jaanch-layak lines likho (S1: ...), aakhir me @@END." % _need)
                    continue
                if len(sitems) < _pf["spec_min"] and rej["specmin"] < 5:
                    rej["specmin"] += 1
                    out.append("[SPEC] mana: sirf %d line hain, kam se kam %d chahiye. Sirf setup nahi, BEHAVIOUR likho: %s. @@SPEC dobara do." % (
                        len(sitems), _pf["spec_min"],
                        "permission mana, khaali list, rotate, screen lock, kharab file/input, back button" if cur_mode() == "android"
                        else "galat ya khaali input, config/API ki galti, error par message"))
                    continue
                if rej["specrev"] < SPEC_REVIEW_MAX and REVIEW_LOOP and _pf["spec_review"]:
                    ev("note", text="🔍 SPEC ka review (kya user ke raste ke kaam chhoot rahe hain?)...")
                    sr, sprov = spec_review(sitems)
                    if sr is not None and sr.get("ok") is False and sr.get("missing"):
                        rej["specrev"] += 1
                        miss = "\n".join("- " + clip(str(x), 160) for x in sr["missing"][:8])
                        show_review("SPEC", False, "ye raste/kaam chhoot rahe hain:\n" + miss, sprov)
                        out.append("[SPEC] mana: reviewer kehta hai user ke ye raste SPEC me nahi hain:\n%s\n"
                                   "Inhe jodkar poora @@SPEC dobara do (S1 se)." % miss)
                        continue
                    if sr is not None:
                        show_review("SPEC", True, "SPEC me user ke raste theek dikhe (%d lines)" % len(sitems), sprov)
                save_spec(sitems)
                ev("note", text="📐 SPEC (%d lines):\n%s" % (len(sitems), "\n".join(TRACK["spec"])))
                out.append("[SPEC] theek hai (%d lines, SPEC.md me). Ab chhote parts me kaam karo (ek file/feature, phir jaanch). "
                           "Har file ke baad reviewer bug batayega; @@DONE par har S line ka saboot chahiye." % len(sitems))
                continue
            if k == "PLAN":
                items = [x for x in (clean_plan_item(l) for l in body.split("\n") if l.strip()) if x][:25]
                if not items:
                    out.append("[PLAN] khali tha. Kadam ek-ek line me likho aur aakhir me @@END.")
                    continue
                if len(items) < prof()["plan_min"] and rej["planmin"] < 4:
                    rej["planmin"] += 1
                    out.append("[PLAN] mana: %d bade kadam hain, kam se kam %d chhote chahiye. Ek file ya ek feature = ek kadam "
                               "(jaise: data model, ek feature ka logic, ek screen/page, input ki jaanch, test). "
                               "\"UI aur logic ek mein\" nahi. Har kadam me asli file/command ka kaam ho. @@PLAN dobara do." % (len(items), prof()["plan_min"]))
                    continue
                STATE.update(plan_items=items, plan_ck=[])
                ev("plan", items=items)
                plan_pm(items, STATE["task"])
                out.append("[PLAN] theek hai. Kadam 1 se shuru karo, har kadam ke baad @@CHECK n.")
                continue
            if k == "CHECK":
                scan_dirty_files()          # file poori hui: 4 AI scan, clean to Groq ka memory card
                if any(TRACK["bugs"].values()) and rej["bugchk"] < 3:
                    rej["bugchk"] += 1
                    out.append("[CHECK %s] mana: reviewer ke bug khule hain, pehle inhe band karo:\n%s" % (a, bug_text({f: b for f, b in TRACK["bugs"].items() if b})))
                    continue
                try:
                    n = int(a.split()[0])
                except (ValueError, IndexError):
                    n = len(STATE["plan_ck"]) + 1
                item = STATE["plan_items"][n - 1] if 1 <= n <= len(STATE["plan_items"]) else ""
                if VERIFY_ITEM_RE.search(item) and not verified and n not in STATE["plan_ck"] and rej["check"] < 3:
                    rej["check"] += 1
                    out.append("[CHECK %s] mana: ye kadam jaanch/build ka hai, par abhi tak @@VERIFY (ya test/compile ka @@RUN) "
                               "saaf nahi aaya, ya uske baad file badli hai. Pehle @@VERIFY chalao, galti ho to theek karo, phir @@CHECK." % a)
                    continue
                note = a.split(None, 1)[1] if len(a.split(None, 1)) > 1 else ""
                lazy = TRACK["since_check"] == 0 or LAZY_CHECK_RE.search(note)
                if (lazy and item and not VERIFY_ITEM_RE.search(item) and n not in STATE["plan_ck"]
                        and TRACK["check_rej"].get(n, 0) < CHECK_REVIEW_MAX and REVIEW_LOOP and prof()["check_review"]):
                    ev("note", text="🔍 CHECK %s: kadam sach me hua? reviewer se puch raha hoon..." % n)
                    okc, why, cprov = check_claim(item, note)
                    if okc is False:
                        TRACK["check_rej"][n] = TRACK["check_rej"].get(n, 0) + 1
                        show_review("CHECK %s" % n, False, "kadam poora nahi: %s\n(%s)" % (item[:80], why), cprov)
                        out.append("[CHECK %s] mana: reviewer kehta hai ye kadam sach me nahi hua: %s\nPehle ye banao, phir @@CHECK." % (a, why)
                                   + (("\n" + pivot_text("Is kadam ko reviewer 2 baar mana kar chuka.")) if TRACK["check_rej"][n] >= 2 else ""))
                        continue
                    if okc:
                        show_review("CHECK %s" % n, True, "%s\n%s" % (item[:80], why), cprov)
                TRACK["since_check"] = 0
                if n not in STATE["plan_ck"]:
                    STATE["plan_ck"].append(n)
                ev("check", n=n)
                out.append("[CHECK %s] ok" % a)
                continue
            if k == "ZIP":
                out.append("[ZIP] mana: zip Mumbai khud @@DONE ke saboot ke BAAD banayega. Tum mat banao.")
                continue
            if stop_batch and k not in ("NOTE", "LEARN"):
                out.append("[%s %s] SKIP: pichhle @@RUN/@@BG ka natija dekhe bina aage nahi badha. Natija dekh ke dobara do." % (k, a[:40]))
                continue
            if k == "ASK":
                TRACK["asks"] += 1
                if TRACK["asks"] > 3:
                    out.append("[ASK] mana: is kaam me 3 sawal ho chuke. Ab khud sabse saada tareeka chuno aur aage badho.")
                    continue
                ans, how = ask_user(a or "Aage kya karun?", ctx=(body or "")[:600])
                if how == "stop":
                    return
                out.append("[ASK] %s:\n%s\n(Isi ke hisaab se aage badho.)" % ("User ka jawab" if how == "user" else "User nahi aaya, 3 AI ne mil ke tay kiya", ans))
                continue
            gate = (spec_gate(k, a) or gradle_gate(k, a) or bug_gate(k, a) or size_gate(k, a, body) or read_gate(k, a, body)) if complete else None
            if gate:
                wl("%s %s (roka)" % (k, a[:70]))
                ev("step", kind=k, arg=a[:120], out=clip(gate, 4000), ok=False, prov=name)
                out.append("[%s %s]\n%s" % (k, a[:60], gate))
                continue
            res, changed, ok, code = run_cmd(k, a, body, complete)
            try:
                rel = rel_of(split_range(a)[0] if k == "READ" else a) if k in ("READ", "WRITE", "WRITEB64", "EDIT") else None
                if k == "READ" and ok and rel:
                    TRACK["seen"].add(rel)
                elif k in ("WRITE", "WRITEB64", "EDIT") and rel:
                    if ok:
                        TRACK["seen"].add(rel)
                        TRACK["edit_fail"].pop(rel, None)
                        if rel.lower().endswith(".pdf"):
                            TRACK["pdfs"].add(rel)
                            TRACK["looked"].discard(rel)
                        extra = auto_check([rel])
                        if not extra and k != "WRITEB64":
                            extra = review_hook(rel, name)
                        if extra:
                            res += "\n" + extra
                    elif k == "EDIT":
                        TRACK["edit_fail"][rel] = TRACK["edit_fail"].get(rel, 0) + 1
                        if TRACK["edit_fail"][rel] >= 3:
                            res += "\n[Note] Is file par 3 baar EDIT fail hua. Naya tareeka socho: @@READ se file dobara dekho ya chhoti file ho to @@WRITE."
                elif k == "RUN" and not ok:
                    res += err_hint(res)
            except Exception as e:
                log("hook galti: %s" % e)
            if k not in ("NOTE", "VERIFY", "LEARN"):
                acted += 1
            if k in ("WRITE", "WRITEB64", "EDIT", "TEMPLATE") and ok:
                dirty, last_fail, verified, wrote = True, False, False, True
                TRACK["since_check"] += 1
            if k in ("RUN", "READ"):
                dirty = False
            if k == "RUN":
                last_fail = not ok
                verified = bool(ok and VERIFY_CMD_RE.search(a))
                wrote = wrote or bool(changed)
            if k == "VERIFY":
                verified = ok
            if k == "ZIP" and ok:
                zip_made, zip_stale = True, False
            elif zip_made and ok and (k in ("WRITE", "WRITEB64", "EDIT", "TEMPLATE") or (k == "RUN" and changed)):
                zip_stale = True
            if k in ("RUN", "BG"):
                stop_batch = True
            wl("%s %s%s" % (k, a[:70], "" if ok else " (fail)"))
            show = (body + (("\n\n" + res) if ("[REVIEW" in res or "[AUTO" in res) else "")) if k in ("WRITE", "WRITEB64") and ok else (
                res + "\n\n" + clip(body, 3000) if k == "EDIT" else res)
            ev("step", kind=k, arg=a[:120], out=clip(show, 4000), ok=ok, prov=name, img=TRACK.pop("img", None))
            out.append("[%s %s]\n%s" % (k, a[:60], res))
        if repeat == 2 and any(o.startswith("[EDIT") and "ERROR" in o for o in out):
            out.append("[Note] Wahi EDIT dobara fail hua. Pehle @@READ karke asli text dekho, ya file chhoti ho to poori @@WRITE karo.")
        if cut:
            out.append("[Note] Tumhara jawab limit par kat gaya tha; adhuri commands chalayi nahi gayi. Chhote hisson me do.")
        add("user", "Natija:\n" + "\n\n".join(out))
        save_state()
    ev("err", text="⚠ %d kadam ho gaye, ruk gaya. Aage badhana ho to 'aage badho' likho." % MAX_STEPS)
    notify_bg("⚠ %s: %d kadam ho gaye, ruk gaya. Aage badhna ho to chat me 'aage badho' likho." % (STATE["proj"], MAX_STEPS))


# ================= 21.py: samajh, done-criteria, naya raasta, user se poochna, seekha, PDF dekhna =================
ASK_WAIT = float(_env("ASK_WAIT", "30"))           # user ke jawab ka intezaar (second, 10 min). Na aaye to 3 AI faisla karte hain
CRIT_REJECT_MAX, ESC_AT, ESC_MAX = 2, 4, 2          # criteria 2 baar mana; aakhri review 4 baar mana -> user se poochho; ek kaam me max 2 baar
SPLIT_ROLES = _env("SPLIT_ROLES", "1") != "0"       # plan/review mazboot model se, likhna sasta/tez model se (0 = band)
TIER = {"JAAT": "strong", "NV2": "strong", "GEM": "strong", "GEM2": "strong", "GEM3": "strong", "DSX": "strong",
        "DS": "cheap", "GROQ": "cheap", "DAI": "cheap"}          # providers.json me "tier": "strong"/"cheap" se badal sakte ho
SEEKHA_FILE = os.path.join(ROOT, ".mumbai", "seekha.md")
PREVIEW_DIR = os.path.join(ROOT, ".mumbai", "preview")
STATE.update(asking=None, answer=None, utype="", goal="", criteria=[], strong_n=0)


def tier(name):
    return APIS[name].get("tier") or TIER.get(name, "strong")


LIGHT_PREF = ("DS", "GROQ", "GEM", "GEM2", "GEM3")      # chhote (light) kaam me pehle ye, JAAT/baaki baad me


def role_order(role):
    names = order()
    try:
        _m = cur_mode()
        if _m == "light":
            pref = [n for n in LIGHT_PREF if n in names]
            return pref + [n for n in names if n not in pref]
        if _m in ("code", "android") and CODE_FLOW:
            pref = [n for n in (CODE_PLAN_PREF if role == "plan" else CODE_WRITE_PREF) if n in names]
            rest = [n for n in names if n not in pref]
            if role != "plan":      # likhne me Gemini sabse last (uska limit bachao)
                gem = [n for n in rest if group_of(n) == "gemini"]
                rest = [n for n in rest if n not in gem] + gem
            return pref + rest
    except Exception:
        pass
    if not SPLIT_ROLES:
        return names
    if role == "write":
        cheap = [n for n in names if tier(n) == "cheap" and APIS[n]["type"] == "oai"]     # chhote plain proxy bade prompt nahi jhelte
        return cheap + [n for n in names if n not in cheap]
    return [n for n in names if tier(n) == "strong"] + [n for n in names if tier(n) != "strong"]


def cur_role():
    if STATE.get("strong_n", 0) > 0:
        STATE["strong_n"] -= 1
        return "plan"
    if cur_mode() in ("code", "android") and not STATE["plan_items"] and not TRACK["spec"] and not TRACK.get("wrote_any"):
        return "plan"
    return "write"


# ---------- seekha: global yaad-daasht (har project me) ----------
def seekha_lines():
    try:
        with open(SEEKHA_FILE, encoding="utf-8") as f:
            return [l.strip() for l in f.read().split("\n") if l.strip()]
    except OSError:
        return []


def seekha_add(line):
    line = re.sub(r"\s+", " ", (line or "").strip().lstrip("-* "))[:220]
    if len(line) < 8:
        return False
    cur = seekha_lines()
    key = re.sub(r"\W+", "", line.lower())[:50]
    if any(re.sub(r"\W+", "", c.lower())[:50] == key for c in cur):
        return False
    cur = (cur + [line])[-40:]
    try:
        with open(SEEKHA_FILE + ".tmp", "w", encoding="utf-8") as f:
            f.write("\n".join(cur) + "\n")
        os.replace(SEEKHA_FILE + ".tmp", SEEKHA_FILE)
    except OSError as e:
        log("seekha save fail: %s" % e)
        return False
    return True


def seekha_text(n=12):
    return "\n".join("- " + l for l in seekha_lines()[-n:])


# ---------- samajh step ----------
UNDERSTAND_SYS = ("You read ONE task from a user of a phone coding/document assistant and write a short understanding. Reply ONLY JSON, no prose: "
                  '{"goal":"one line: what the user really wants","type":"light|code|android|chat","done":["3-6 finish conditions"],"question":""}. '
                  "type: android = Android app/APK; code = app, bot, website, script, tool; light = document/PDF/notes/writing/research/small edit; "
                  "chat = just talking or a simple question (then done=[]). Each done item must be checkable by reading a file or running a command "
                  "(e.g. 'python -m py_compile bot.py passes', 'note.pdf exists, 1 page, heading visible'), not vague ('works well'). "
                  "question: ONE short question ONLY if the task cannot even be started without the answer (which of two very different things); otherwise empty. "
                  "Write goal and done in the user's language style (Hinglish ok).")


def _do_understand_orig(task):
    """Naye kaam par 3 line: maqsad, type, done-criteria. Text jo AI ke pehle message me judta hai ('' = kuch nahi)."""
    if len(task.split()) < 4:
        return ""
    txt, prov = reviewer_call(UNDERSTAND_SYS, "TASK:\n%s\n\nFILES: %s" % (task[:1500], ", ".join(list_files(40)) or "(none)"))
    d = parse_json_reply(txt) if txt else None
    if not isinstance(d, dict):
        return ""
    typ = str(d.get("type", "")).strip().lower()
    goal = clip(str(d.get("goal", "")).strip(), 220)
    done = [clip(str(x).strip(), 160) for x in (d.get("done") or []) if str(x).strip()][:6] if typ != "chat" else []
    ques = str(d.get("question") or "").strip()[:300]
    if typ == "chat":
        typ = "light"
    if typ in PROFILES:
        STATE["utype"] = typ
    STATE["goal"] = goal
    STATE["criteria"] = ["D%d: %s" % (i + 1, x) for i, x in enumerate(done)]
    ev("understand", goal=goal, kind=typ or "?", done=STATE["criteria"], prov=prov or "")
    extra = ""
    if ques and not MODE_FORCE_RE.match(task):
        ans, how = ask_user(ques, ctx="Task: " + task[:500])
        if how == "stop":
            return ""
        extra = "\n[User se poochha tha: %s -> %s: %s]" % (ques, "jawab" if how == "user" else "3 AI ka faisla", ans)
        STATE["task"] = task + extra
    return extra + ("\n[SAMAJH: %s | type=%s%s]" % (goal, typ or "?", (" | " + " ; ".join(STATE["criteria"])) if STATE["criteria"] else ""))


# ---------- done-criteria ka saboot ----------
CRIT_SYS = ("You check whether a task is REALLY finished. You get DONE CRITERIA and EVIDENCE (real outputs of commands and file reads from this session, plus project files). "
            "For EACH criterion pass=true only if the evidence shows it. 'evidence' must be a short EXACT quote (max 120 chars) copied from the EVIDENCE text; "
            "'via' is READ, RUN or FILE. If no exact quote proves it, pass=false and 'missing' says what to run or read. "
            'Reply ONLY JSON: {"criteria":[{"id":"D1","pass":true,"via":"RUN","evidence":"...","missing":""}]}')


def _norm_ws(t):
    return re.sub(r"\s+", " ", str(t or "")).strip().lower()


def _evidence_blob():
    parts = [clip(m["content"], 2500) for m in STATE["msgs"][-60:] if m.get("role") == "user" and str(m.get("content", "")).startswith("Natija:")]
    blob = "\n".join(parts[-12:])
    for f in list_files(60):
        if f.lower().endswith(PAYLOAD_EXT + (".md", ".html", ".txt", ".csv")) and f != "PROJECT.md":
            t = _read(f, 3000)
            if t.strip():
                blob += "\n\nFILE %s:\n%s" % (f, t[:2500])
        if len(blob) > PAYLOAD_MAX:
            break
    return blob[:PAYLOAD_MAX]


def criteria_gate():
    crit = STATE.get("criteria") or []
    if not crit or not REVIEW_LOOP or TRACK["crit_rej"] >= CRIT_REJECT_MAX or not TRACK.get("acted_any", True):
        return ""
    ev("note", text="🧾 done-criteria ka saboot asli natije se mila raha hoon...")
    blob = _evidence_blob()
    txt, prov = reviewer_call(CRIT_SYS, "DONE CRITERIA:\n%s\n\nEVIDENCE:\n%s" % ("\n".join(crit), blob))
    d = parse_json_reply(txt) if txt else None
    items = d.get("criteria") if isinstance(d, dict) else None
    if not isinstance(items, list):
        TRACK["final_warn"] = TRACK.get("final_warn", "") + "\n⚠ done-criteria ka saboot-review nahi ho paya"
        return ""
    nb, got, fails = _norm_ws(blob), {}, []
    for it in items:
        if isinstance(it, dict):
            got[str(it.get("id", "")).strip().upper()] = it
    lines = []
    for c in crit:
        cid = c.split(":", 1)[0].strip().upper()
        it = got.get(cid) or {}
        evd = _norm_ws(str(it.get("evidence", "")).strip(" .…"))[:60]
        if it.get("pass") is True and evd and evd.rstrip(".") in nb:
            lines.append("%s ✅ %s: %s" % (cid, it.get("via", "?"), str(it.get("evidence"))[:100]))
        else:
            why = str(it.get("missing") or ("saboot asli natije me nahi mila" if it.get("pass") is True else "saboot nahi mila"))[:140]
            fails.append("%s %s -> %s" % (cid, c.split(":", 1)[-1].strip()[:100], why))
    if fails:
        TRACK["crit_rej"] += 1
        show_review("Done-criteria", False, "%d/%d ka saboot nahi\n%s" % (len(fails), len(crit), "\n".join("✗ " + x for x in fails)), prov or "")
        return ("DONE mana (done-criteria %d/%d): in criteria ka saboot asli @@RUN/@@READ natije me nahi mila:\n%s\n"
                "Pehle asli @@RUN ya @@READ se saboot lao (ya kaam poora karo), phir @@DONE me har D ka saboot likho." % (
                    TRACK["crit_rej"], CRIT_REJECT_MAX, "\n".join("- " + x for x in fails)))
    show_review("Done-criteria", True, "\n".join(lines), prov or "")
    return ""


# ---------- haar par tareeka badlo ----------
def pivot_text(why):
    STATE["strong_n"] = 2           # agle 2 jawab sabse mazboot model se
    return ("NAYA RAASTA ZAROORI: %s Wahi tareeka dohrao mat. (1) Ek line me likho ki asli jad kya hai (natije ya file:line se, andaze se nahi). "
            "(2) Pichhla tareeka chhodo aur alag tareeka lo (file chhoti karke saaf likho, alag structure, ya jo cheez baar-baar toot rahi hai use hata do). "
            "(3) Do raaste barabar hon aur user ki pasand matter kare to @@ASK <sawal, a/b/c options ke saath>." % why)


QUESTION_SYS = ("A coding assistant is stuck: a reviewer keeps rejecting its work. Write ONE short question for the user (Hinglish, max 2 lines) with 2-3 short options "
                "that decide the way forward (e.g. simplify the feature, keep it and accept a limitation, use a different approach). "
                'Reply ONLY JSON: {"question":"...","options":["a ...","b ...","c ..."]}')


def escalate_final():
    TRACK["esc"] += 1
    ctx = "TASK: %s\nREVIEWER KYA KEHTA HAI:\n%s" % ((STATE.get("task") or "")[:600], (TRACK.get("last_reject") or "")[:1800])
    txt, _ = reviewer_call(QUESTION_SYS, ctx)
    d = parse_json_reply(txt) if txt else None
    if isinstance(d, dict) and d.get("question"):
        q, opts = str(d["question"])[:400], [str(x)[:120] for x in (d.get("options") or [])][:3]
    else:
        q, opts = "Reviewer %d baar mana kar chuka. Kaam ko saada karke aage badhun, ya jaisa hai waisa chhod doon?" % TRACK["final_rej"], ["a) saada karo", "b) jaisa hai chhod do"]
    ans, how = ask_user(q, opts, ctx)
    if how == "stop":
        return "Roka gaya."
    TRACK["final_rej"] = ESC_AT - 1
    STATE["strong_n"] = 2
    return ("FAISLA (%s): %s\nIs faisle ke hisaab se kaam badlo (reviewer ke bugs ab bhi theek karne hain jahan faisle se mel khate hain), "
            "@@VERIFY chalao, phir @@DONE." % ("user ka" if how == "user" else "user nahi aaya, 3 AI ne mil ke tay kiya", ans))


def final_gate():
    if TRACK["final_rej"] >= ESC_AT and TRACK["esc"] < ESC_MAX and TRACK["final_rej"] < FINAL_REJECT_MAX and REVIEW_LOOP:
        return escalate_final()
    msg = _final_gate_core()
    if msg.startswith("DONE mana"):
        TRACK["last_reject"] = msg
        if TRACK["final_rej"] >= 2 and not TRACK["pivot1"]:
            TRACK["pivot1"] = True
            msg += "\n" + pivot_text("Aakhri review 2 baar reject ho chuka hai.")
    return msg


def done_gate():
    return final_gate() or pdf_gate() or criteria_gate()


# ---------- user se poochna: 10 min, phir 3 AI ----------
TG_OFF = [None]


def tg_updates():
    """Telegram me aaye naye messages (sirf TG_CHAT ke). Jawab Telegram se bhi chalta hai."""
    tok, chat = _env("TG_TOKEN"), _env("TG_CHAT")
    if not (tok and chat):
        return []
    try:
        url = "https://api.telegram.org/bot%s/getUpdates?timeout=0" % tok + (("&offset=%d" % TG_OFF[0]) if TG_OFF[0] else "")
        j = json.loads(http(url, timeout=15))
        out = []
        for u in j.get("result", []):
            TG_OFF[0] = int(u["update_id"]) + 1
            m = u.get("message") or {}
            if str((m.get("chat") or {}).get("id")) == chat and m.get("text"):
                out.append(m["text"])
        return out
    except Exception as e:
        log("tg getUpdates fail: %s" % str(e)[:80])
        return []


COUNCIL_SYS = ("You are one of 3 independent advisors. A coding assistant needs a decision because the user did not reply in time. "
               "Pick what is best for the user's goal: prefer the simplest choice that still satisfies the task and is easy to undo. "
               "If OPTIONS are given, 'choice' must be copied exactly from one option. "
               'Reply ONLY JSON: {"choice":"short decision","why":"one line"}')
MERGE_SYS = ("Three advisors answered differently. Pick or merge into ONE final decision that best serves the task (simplest, easy to undo). "
             'Reply ONLY JSON: {"decision":"short final decision"}')


def council_slots(n=3):
    names, slots = reviewer_names(), []
    for nm in names:
        ms = models_of(APIS[nm])
        if ms and (nm, ms[0]) not in slots:
            slots.append((nm, ms[0]))
    for nm in names:
        for m in models_of(APIS[nm])[1:]:
            if len(slots) < n and (nm, m) not in slots:
                slots.append((nm, m))
    return slots[:n]


def council(q, opts, ctx=""):
    """3 AI mil kar faisla: majority, sab alag hon to sabse mazboot model se merge."""
    slots = council_slots(3)
    user = "QUESTION: %s\nOPTIONS: %s\nTASK: %s\nCONTEXT:\n%s" % (q, " | ".join(opts) if opts else "(none, answer freely)", (STATE.get("task") or "")[:500], (ctx or "")[:1500])
    votes = []
    for i, (nm, m) in enumerate(slots):
        if STATE["cancel"] or (i and not nap(REVIEW_GAP)):
            break
        t = call_slot(nm, m, COUNCIL_SYS, user)
        d = parse_json_reply(t) if t else None
        if isinstance(d, dict) and str(d.get("choice", "")).strip():
            votes.append((str(d["choice"]).strip()[:200], str(d.get("why", ""))[:140], "%s/%s" % (nm, m)))
    if not votes:
        pick = opts[0] if opts else "Sabse saada tareeka chuno aur aage badho"
        ev("note", text="🗳 koi AI jawab nahi de paya, sabse saada raasta le raha hoon: %s" % pick)
        return pick
    nz = lambda x: re.sub(r"\W+", " ", x.lower()).strip()
    cnt = {}
    for c, _, _ in votes:
        cnt[nz(c)] = cnt.get(nz(c), 0) + 1
    best, n = max(cnt.items(), key=lambda kv: kv[1])
    pick = next(c for c, _, _ in votes if nz(c) == best)
    if n < 2 and len(votes) >= 3:
        t = call_slot(slots[0][0], slots[0][1], MERGE_SYS, user + "\nANSWERS:\n" + "\n".join("- %s (%s)" % (c, w) for c, w, _ in votes))
        d = parse_json_reply(t) if t else None
        if isinstance(d, dict) and str(d.get("decision", "")).strip():
            pick = str(d["decision"]).strip()[:300]
    ev("note", text="🗳 %d AI ne mil ke tay kiya: %s\n(%s)" % (len(votes), pick, " | ".join("%s: %s" % (w, c) for c, _, w in votes)))
    return pick


def ask_user(question, options=None, ctx=""):
    """(jawab, kisne) kisne = 'user' | 'council' | 'stop'. Kaam jaisa hai waisa rukta hai (file nahi badalti).
    Telegram/ntfy par message jata hai; Telegram me reply bhi chalta hai. ASK_WAIT (10 min) me jawab na aaye to 3 AI faisla karte hain."""
    q = clip((question or "").strip(), 500) or "Aage kya karun?"
    opts = [str(o)[:120] for o in (options or [])][:4]
    STATE["answer"] = None
    STATE["asking"] = {"q": q, "t": time.time()}
    ev("ask", text=q, opts=opts, wait=int(ASK_WAIT))
    notify_bg("❓ %s%s\n(App me ya yahin Telegram me jawab do. %d min me jawab na aaya to 3 AI mil ke tay kar lenge, kaam tab tak ruka hai.)" % (
        q, ("\n" + "\n".join(opts)) if opts else "", int(ASK_WAIT // 60)))
    tg_updates()                                   # purane messages jawab na ban jayein
    ans, end, last_tg = None, time.time() + ASK_WAIT, 0.0
    remind = time.time() + ASK_WAIT / 2
    try:
        while time.time() < end:
            if STATE["cancel"]:
                return None, "stop"
            if STATE.get("answer"):
                ans = STATE["answer"]
                break
            now = time.time()
            if now - last_tg > 8:
                last_tg = now
                got = tg_updates()
                if got:
                    ans = got[-1]
                    ev("user", text="(Telegram) " + ans)
                    break
            if now > remind:
                remind = end + 1
                notify_bg("⏰ Mumbai abhi bhi jawab ka intezaar kar raha hai: %s" % q[:200])
            time.sleep(0.4)
    finally:
        STATE["asking"] = None
    if ans:
        ev("ask_done", how="user", text=ans)
        return ans.strip(), "user"
    ev("note", text="⌛ user ka jawab nahi aaya, ab 3 AI mil ke tay karenge...")
    dec = council(q, opts, ctx)
    ev("ask_done", how="council", text=dec)
    notify_bg("🗳 Jawab nahi aaya, 3 AI ne tay kiya: %s" % dec[:300])
    return dec, "council"


# ---------- PDF ko image bana ke dekhna ----------
_DARK = bytes(range(200))
_INK = re.compile(rb"[\x00-\xc7]")


def look_ok():
    return bool(shutil.which("pdftoppm"))


def _pgm(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s", raw)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    data = raw[m.end():]
    return (w, h, data) if len(data) >= w * h else None


def page_metrics(w, h, data):
    """Kinaron ka khaali hissa (%), bhara hissa (%). Sirf stdlib."""
    top = bot = None
    left, right, ink = w, -1, 0
    for i in range(h):
        row = data[i * w:(i + 1) * w]
        mm = _INK.search(row)
        if not mm:
            continue
        top = i if top is None else top
        bot = i
        left = min(left, mm.start())
        right = max(right, w - 1 - _INK.search(row[::-1]).start())
        ink += len(row) - len(row.translate(None, _DARK))
    if top is None:
        return {"blank": True, "ink": 0.0}
    return {"blank": False, "ink": 100.0 * ink / (w * h), "L": 100.0 * left / w, "R": 100.0 * (w - 1 - right) / w,
            "T": 100.0 * top / h, "B": 100.0 * (h - 1 - bot) / h}


def _pdf_text_pages(p):
    try:
        if shutil.which("pdftotext"):
            r = subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, timeout=30)
            return r.stdout.decode("utf-8", "replace").split("\f")[:-1] or [""]
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        return [(pg.extract_text() or "") for pg in PdfReader(p).pages]
    except Exception:
        return None


IMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
_IMG_CACHE = {}


def vision_ask(prompt, images, max_tokens=1500):
    """images = [(mime, bytes)]. (text, provider) ya (None, None). Pehle Gemini, phir JAAT, phir NV2. Stop par turant."""
    content = [{"type": "text", "text": prompt}]
    for mime, raw in images[:3]:
        content.append({"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())}})
    tried = []
    for nm in list(VISION_PREF) + [n for n in order() if n not in VISION_PREF]:
        if STATE["cancel"]:
            return None, None
        cfg = APIS.get(nm)
        if not cfg or cfg["type"] != "oai" or not ready(nm) or nm in tried or COOL.get(nm, 0) > time.time():
            continue
        model = cfg.get("vision") or (models_of(cfg) or [None])[0]
        if not model:
            continue
        tried.append(nm)
        body = {"messages": [{"role": "user", "content": content}], "max_tokens": max_tokens, "temperature": 0.2}

        def job(dead, cfg=cfg, model=model, body=body, nm=nm):
            with slot(nm):
                r = upstream(cfg, model, body, timeout=120)
                try:
                    return json.loads(r.read().decode("utf-8", "replace"))
                finally:
                    r.close()
        try:
            j = cancellable(job)
            t = ((j.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
            if t:
                return t, nm
        except Cancelled:
            return None, None
        except Exception as e:
            log("vision %s fail: %s" % (nm, str(e)[:80]))
            ev("note", text="👁 %s image nahi dekh paya, agla AI (%s)" % (nm, str(e)[:60]))
    return None, None


def look_image(path, raw, ask_text=None):
    """Image ke andar ki cheez (text, error, UI) Gemini/JAAT se padhta hai. Text lautata hai."""
    ext = os.path.splitext(path)[1].lower()
    mime = IMG_MIME.get(ext)
    if not mime:
        return None
    key = (path, len(raw), ask_text or "")
    if key in _IMG_CACHE:
        return _IMG_CACHE[key]
    info = _img_info(raw, ext)
    if len(raw) > 7 * 1024 * 1024:
        return info + "\n(image 7MB se badi hai, AI ko nahi bheji; chhoti karke dobara do)"
    prompt = ask_text or ("Describe this image for a coding assistant. First transcribe ALL visible text exactly (error messages, logs, code, "
                          "button labels, numbers) in a block, then describe the layout/UI and anything wrong. Be precise, no guessing.")
    txt, prov = vision_ask(prompt, [(mime, raw)])
    if not txt:
        return info + "\n(image AI se dekhi nahi ja saki: koi vision AI chala nahi. Gemini/JAAT ka cooldown ya key dekho)"
    out = "%s\nAI-ankh (%s):\n%s" % (info, prov, txt)
    _IMG_CACHE[key] = out
    if len(_IMG_CACHE) > 40:
        _IMG_CACHE.pop(next(iter(_IMG_CACHE)))
    return out


def _vision_look(pngs, name_hint):
    prompt = ("These are page images of a generated PDF (%s). Check LAYOUT only: text cut off or overlapping, big empty areas, broken headings/bullets, "
              "'?' or boxes instead of letters, margins. Answer in max 5 short lines: start with OK or PROBLEM, then what is wrong." % name_hint)
    imgs = []
    for pth in pngs[:2]:
        with open(pth, "rb") as f:
            imgs.append(("image/png", f.read()))
    t, nm = vision_ask(prompt, imgs, max_tokens=600)
    return (t[:700], nm) if t else (None, None)


def do_look(arg):
    parts = arg.split()
    if not parts:
        return "ERROR: @@LOOK <file.pdf> [page]", False, False, None
    p = safe(parts[0])
    if not os.path.isfile(p):
        return "ERROR: file nahi mili: %s" % parts[0], False, False, None
    if os.path.splitext(p)[1].lower() in IMG_MIME:
        with open(p, "rb") as f:
            _raw = f.read()
        _q = " ".join(parts[1:]) or None
        return (look_image(p, _raw, _q) or "ERROR: image nahi padhi"), False, True, None
    if not p.lower().endswith(".pdf"):
        return "ERROR: @@LOOK sirf PDF ya image (png/jpg/webp/gif) ke liye hai", False, False, None
    if not look_ok():
        return "ERROR: PDF image banane ka tool (pdftoppm / poppler-utils) nahi hai. Dockerfile me poppler-utils jodo. Tab tak @@READ file.pdf se text dekho.", False, False, None
    rel = os.path.relpath(p, WORK)
    d = os.path.join(PREVIEW_DIR, STATE["proj"])
    os.makedirs(d, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", rel)
    for old in os.listdir(d):
        if old.startswith(stem + "."):
            try:
                os.remove(os.path.join(d, old))
            except OSError:
                pass
    first = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    try:
        subprocess.run(["pdftoppm", "-r", "40", "-gray", "-l", "30", p, os.path.join(d, stem + ".g")], capture_output=True, timeout=90, check=True)
        subprocess.run(["pdftoppm", "-r", "70", "-png", "-f", str(first), "-l", str(first + 2), p, os.path.join(d, stem + ".v")], capture_output=True, timeout=90, check=True)
    except Exception as e:
        return "ERROR: PDF render nahi hua (%s). File kharab ho sakti hai." % str(e)[:100], False, False, None
    grays = sorted(f for f in os.listdir(d) if f.startswith(stem + ".g") and f.endswith(".pgm"))
    pngs = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.startswith(stem + ".v") and f.endswith(".png")]
    texts = _pdf_text_pages(p)
    n = len(grays)
    lines, warns = ["%s: %d page%s (pehle 30 jaanche)" % (rel, n, "" if n == 1 else "s")], 0
    for i, gf in enumerate(grays):
        g = _pgm(os.path.join(d, gf))
        if not g:
            continue
        mt = page_metrics(*g)
        flags = []
        tx = (texts[i] if texts and i < len(texts) else None)
        if mt["blank"]:
            flags.append("⚠ page khaali hai")
        else:
            if min(mt["L"], mt["R"]) < 3.5 or mt["T"] < 3:
                flags.append("⚠ text kinare se chipka (kat sakta hai)")
            if mt["B"] > 60 and i < n - 1:
                flags.append("⚠ page aadha khaali, agla page jaldi shuru")
            if mt["ink"] > 35:
                flags.append("⚠ bahut bhara hua (overlap?)")
        if tx is not None:
            body_chars = len(re.sub(r"\s", "", tx))
            if body_chars and tx.count("?") / max(1, body_chars) > 0.05:
                flags.append("⚠ '?' bahut hain (Hindi/emoji akshar ban gaye?)")
            if not body_chars and not mt["blank"]:
                flags.append("ℹ text nahi nikla (sirf image?)")
        warns += sum(1 for f in flags if f.startswith("⚠"))
        mtxt = "khaali" if mt["blank"] else "bhara %.0f%% · kinare L%.0f R%.0f T%.0f B%.0f%%" % (mt["ink"], mt["L"], mt["R"], mt["T"], mt["B"])
        lines.append("P%d: %s %s" % (i + 1, mtxt, "; ".join(flags) if flags else "✓"))
    vis, vprov = _vision_look(pngs, rel) if pngs else (None, None)
    if vis:
        lines.append("AI-ankh (%s): %s" % (vprov, vis))
        if vis.upper().lstrip().startswith("PROBLEM"):
            warns += 1
    lines.append("Natija: %s" % ("sab theek dikha ✅" if not warns else "%d jagah dhyan dene layak ⚠ (theek karke dobara @@LOOK)" % warns))
    if pngs:
        TRACK["img"] = "@preview/%s/%s" % (STATE["proj"], os.path.basename(pngs[0]))
    TRACK["looked"].add(rel)
    return "\n".join(lines), False, True, None


def pdf_gate():
    if not TRACK.get("pdfs") or not look_ok() or TRACK["pdf_rej"] >= 1:
        return ""
    miss = sorted(f for f in TRACK["pdfs"] if f not in TRACK["looked"])
    if not miss:
        return ""
    TRACK["pdf_rej"] += 1
    return ("DONE mana: PDF ka layout abhi dekha nahi (%s). @@LOOK %s chalao; margin, khaali page ya '?' ki galti dikhe to theek karke dobara dekho, phir @@DONE." % (
        ", ".join(miss), miss[0]))




def selftest():
    try:
        for name in PRIORITY:
            if STATE["cancel"]:
                ev("note", text="⏹ test roka gaya")
                return
            if not ready(name):
                ev("note", text="⏭ %s: band ya key baaki" % name)
                continue
            if COOL.get(name, 0) - time.time() > HARD_SECS:
                ev("note", text="🔒 %s: aaram me (%d min baaki), test nahi kiya taaki lock na badhe" % (name, (COOL[name] - time.time()) // 60 + 1))
                continue
            cfg = APIS[name]
            model = models_of(cfg)[0]
            t = time.time()
            try:
                if cfg["type"] == "oai":
                    r = json.loads(upstream(cfg, model, {"messages": [{"role": "user", "content": "ping"}]}, timeout=30).read())
                    r = r["choices"][0]["message"]["content"] or ""
                else:
                    r = PLAIN[cfg["type"]](cfg, model, "ping")
                COOL.pop(name, None)
                ev("note", text="💚 %s theek (%.1fs): %s" % (name, time.time() - t, clip(r.strip(), 40)))
            except Exception as e:
                ev("note", text="💔 %s fail: %s" % (name, str(e)[:80]))
    except Exception as e:
        ev("err", text="❌ test me galti: %s" % str(e)[:150])
    finally:
        STATE["running"] = False


def zip_files(skip=None):
    """Zip me kya jayega: PROJECT.md, uploads/ aur purani .zip files nahi."""
    out = []
    for f in list_files(None):
        if (skip and f in skip) or f in ("PROJECT.md", "SPEC.md") or f.split(os.sep, 1)[0] == "uploads" or f.lower().endswith(".zip"):
            continue
        out.append(f)
    return out


def make_zip(skip=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in zip_files(skip):
            z.write(os.path.join(WORK, f), f)
    return buf.getvalue()


def clean_name(n):
    n = re.sub(r"[^\w.\-]", "_", os.path.basename(n or "")).lstrip(".")
    return n or "file"


LOGIN = r"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Mumbai 🔒</title><style>
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#faf9f5;color:#1f1e1d;font:16px system-ui,sans-serif}
@media(prefers-color-scheme:dark){body{background:#262624;color:#eceae4}}
.box{width:min(320px,90vw);text-align:center}
input{width:100%;box-sizing:border-box;padding:12px;font-size:18px;border-radius:12px;border:1px solid #8886;background:transparent;color:inherit;text-align:center}
button{margin-top:10px;width:100%;padding:12px;font-size:16px;border:0;border-radius:12px;background:#c96442;color:#fff}
#m{min-height:1.4em;margin-top:10px;color:#c0392b;font-size:14px}
</style></head><body><div class=box><h2>Mumbai 🌇</h2>
<input id=p type=password autocomplete=current-password placeholder="Password" autofocus>
<button id=b>Login</button><div id=m></div></div>
<script>
var p=document.getElementById('p'),m=document.getElementById('m');
function go(){m.textContent='';
 fetch('/login',{method:'POST',body:JSON.stringify({password:p.value})}).then(function(r){
  if(r.ok){location.reload();return}
  return r.json().then(function(j){m.textContent=(j.error&&j.error.message)||'galat'},function(){m.textContent='galat'})
 }).catch(function(){m.textContent='Server se baat nahi hui'})}
document.getElementById('b').onclick=go;
p.addEventListener('keydown',function(e){if(e.key=='Enter')go()});
</script></body></html>"""


PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Mumbai</title><style>
:root{--bg:#fff;--fg:#111827;--mut:#6b7280;--card:#f3f4f6;--bd:#e5e7eb;--acc:#2563eb}
:root{color-scheme:light}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:2px;padding:6px 8px;border-bottom:1px solid var(--bd);background:var(--bg)}
header b{flex:1 1 auto;min-width:0;max-width:52%;display:flex;align-items:center;gap:6px;font-size:16px;font-weight:600;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:7px 12px;border-radius:12px}
header b:active{background:var(--card)}
header b i{font-style:normal;color:var(--mut);font-size:12px}
header .sp{flex:0 0 0}
.ib{background:none;border:0;color:var(--mut);border-radius:10px;padding:7px 9px;font-size:17px;flex:none;white-space:nowrap}
.ib:active{background:var(--card)}
#csheet .ib,#sheet .ib,#psheet .ib{border:1px solid var(--bd);color:var(--fg);font-size:14px}
#chat{flex:1;overflow-y:auto;padding:14px 14px 6px;scroll-behavior:smooth}
#chat>*{max-width:760px;margin-left:auto;margin-right:auto}
.msg{margin:14px auto;white-space:pre-wrap;word-wrap:break-word;line-height:1.6}
.user{background:var(--card);border-radius:20px;padding:10px 15px;margin-left:auto;margin-right:0;width:fit-content;max-width:86%}
.chip{display:inline-block;font-size:11px;color:var(--mut);border:1px solid var(--bd);border-radius:8px;padding:0 6px;margin-left:6px}
.step{border:1px solid var(--bd);border-radius:12px;margin:6px auto;background:transparent}
.step summary{padding:7px 12px;font-size:13px;color:var(--mut);cursor:pointer;word-break:break-all;list-style:none}
.step summary::-webkit-details-marker{display:none}
.step[open]{background:var(--card)}
.step pre{margin:0;padding:8px 12px;border-top:1px solid var(--bd);font-size:12px;white-space:pre-wrap;word-break:break-all}
.bad summary{color:#c0392b}
.done{border-left:3px solid #2e9e5b;padding-left:10px}
.err{color:#c0392b}.note{color:var(--mut);font-size:13px;text-align:center;margin:6px 0}
#hint{color:var(--mut);text-align:center;margin-top:30vh;padding:0 20px}
#bar{display:none;align-items:center;gap:8px;padding:6px 16px;font-size:14px;color:var(--mut);max-width:760px;width:100%;margin:0 auto}
#bar .st{color:var(--acc);font-size:17px;display:inline-block;animation:spin 2.4s linear infinite}
#bar .tx{background:linear-gradient(90deg,var(--mut) 30%,var(--fg) 50%,var(--mut) 70%);background-size:200% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:shim 1.6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes shim{from{background-position:200% 0}to{background-position:-200% 0}}
footer{display:flex;gap:6px;align-items:flex-end;padding:8px 10px calc(10px + env(safe-area-inset-bottom));max-width:780px;width:100%;margin:0 auto}
footer textarea{flex:1;max-height:130px;resize:none;border:1px solid var(--bd);border-radius:22px;padding:11px 16px;font:16px system-ui;background:var(--card);color:var(--fg);outline:none}
#send{background:var(--acc);border:0;color:#fff;border-radius:50%;width:42px;height:42px;font-size:18px;flex:none}
#sheet,#psheet,#csheet{position:fixed;inset:0;background:var(--bg);display:none;flex-direction:column;z-index:5}
#sheet .top,#psheet .top,#csheet .top{display:flex;gap:6px;align-items:center;padding:10px;border-bottom:1px solid var(--bd)}
#sheet .top b,#psheet .top b,#csheet .top b{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}
#fl,#pl{flex:1;overflow:auto;padding:6px 12px}
#fq{margin:6px 12px 0;padding:8px 12px;border:1px solid var(--bd);border-radius:12px;background:var(--bg);color:var(--fg);font:16px system-ui}
.age{font-size:11px;color:var(--mut);white-space:nowrap;flex:none}
.rev{border-left:3px solid #2e9e5b;padding-left:10px;font-size:14px}.rev.badr{border-left-color:#c0392b}
.row{display:flex;gap:10px;align-items:center;padding:9px 0;border-bottom:1px solid var(--bd)}
.row span{flex:1;word-break:break-all}.row a{color:var(--acc);text-decoration:none;font-size:20px;cursor:pointer}
#cta{flex:1;max-height:none;margin:6px 12px 10px;font:12px/1.4 monospace;border-radius:10px;white-space:pre;border:1px solid var(--bd);background:var(--bg);color:var(--fg);padding:8px}
.md{white-space:normal}.md p{margin:0 0 .7em}.md p:last-child{margin-bottom:0}
.md h1,.md h2,.md h3{margin:.9em 0 .4em;line-height:1.3}.md h1{font-size:1.3em}.md h2{font-size:1.15em}.md h3{font-size:1.02em}
.md ul,.md ol{margin:0 0 .7em;padding-left:1.4em}.md li{margin:.15em 0}
.md code{background:var(--card);border:1px solid var(--bd);border-radius:6px;padding:0 5px;font:13px ui-monospace,monospace}
.md pre{position:relative;background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:10px 12px;overflow-x:auto;margin:0 0 .7em}
.md pre code{background:none;border:0;padding:0;white-space:pre}
.md a{color:var(--acc)}.md blockquote{margin:0 0 .7em;padding-left:10px;border-left:3px solid var(--bd);color:var(--mut)}
.cp{position:absolute;top:4px;right:4px;background:var(--bg);border:1px solid var(--bd);color:var(--mut);border-radius:8px;font-size:11px;padding:2px 8px}
.tb{display:flex;flex-direction:column;align-items:center;gap:1px;line-height:1;padding:5px 7px}.tb small{font-size:10px}
.badge{font-size:10px;border-radius:8px;padding:1px 7px;border:1px solid var(--bd);color:var(--mut);margin-left:6px;flex:none}
.badge.code{color:#2d6cdf;border-color:#2d6cdf}.badge.android{color:#2e9e5b;border-color:#2e9e5b}.badge.light{color:var(--mut)}
.chk{border-style:dashed}.chk.hasbad summary{color:var(--acc)}.chk .ln{padding:6px 12px;font-size:12px;border-top:1px solid var(--bd);white-space:pre-wrap;word-break:break-word}
.chk .ln.b{color:#c0392b}.chk .ln.g{color:#2e9e5b}
.pbar{height:5px;border-radius:3px;background:var(--bd);margin:6px 0 2px;overflow:hidden}.pbar i{display:block;height:100%;width:0;background:#2e9e5b;transition:width .3s}
.plan summary{font-size:14px;color:var(--fg)}.plan .it{padding:2px 12px;font-size:14px}.plan .it.ok{color:var(--mut)}.plan .pb{padding:0 12px 8px}
.dl{display:inline-block;margin-top:8px;background:var(--acc);color:#fff!important;border-radius:12px;padding:8px 14px;font-size:14px;text-decoration:none}
#fh{display:none;align-items:center;gap:6px;padding:6px 12px;border-top:1px solid var(--bd)}#fh b{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;font-size:13px}
#fm{display:none;max-width:100%;max-height:60vh;margin:8px auto}#fp{display:none;width:100%;height:55vh;border:0}
#fv{display:none;margin:0;padding:10px 12px;border-top:1px solid var(--bd);font-size:12px;white-space:pre-wrap;word-break:break-all;max-height:45%;overflow:auto}
header{position:relative;padding:10px 14px}
header b{max-width:none;font-size:17px;font-weight:600;letter-spacing:-.2px}
#mnb{font-size:22px;line-height:1;padding:6px 12px;color:var(--fg)}
#menu{display:none;position:absolute;right:10px;top:52px;background:#fff;border:1px solid var(--bd);border-radius:14px;box-shadow:0 8px 28px rgba(17,24,39,.14);padding:6px;z-index:9;min-width:170px}
#menu button{display:block;width:100%;background:none;border:0;padding:12px 14px;font-size:15px;color:var(--fg);border-radius:10px;text-align:left}
#menu button:active{background:var(--card)}
body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.user{background:#111827;color:#fff;border-radius:18px 18px 4px 18px}
.step{border-radius:10px;font-size:13px}
.step.plan{background:#fff;border-color:var(--bd)}
.step.plan .it{padding:5px 12px;font-size:13px;color:var(--fg)}
.step.plan .it.ok{color:var(--mut)}
.done{border:1px solid #bbf7d0;background:#f0fdf4;border-left:3px solid #16a34a;border-radius:12px;padding:12px 14px}
.note{font-size:12px}
footer textarea{background:#fff;box-shadow:0 1px 3px rgba(17,24,39,.08)}
#send{background:#111827;width:44px;height:44px;font-size:17px;touch-action:manipulation;display:flex;align-items:center;justify-content:center}
#send.stop{background:#dc2626;font-size:15px}
#send.busy{opacity:.6}
#cfm{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:20;padding:24px;backdrop-filter:blur(2px)}
#cfm .cbox{background:var(--bg);color:var(--fg);border-radius:22px;padding:22px 20px 16px;width:100%;max-width:340px;text-align:center;box-shadow:0 12px 40px rgba(0,0,0,.3)}
#cfm .ci{width:48px;height:48px;border-radius:50%;background:#fee2e2;color:#dc2626;font-size:20px;display:flex;align-items:center;justify-content:center;margin:0 auto 10px}
#cfm h3{margin:0 0 6px;font-size:18px}#cfm p{margin:0 0 16px;color:var(--mut);font-size:14px;line-height:1.4}
#cfm button{display:block;width:100%;border:0;border-radius:14px;padding:13px;font-size:15px;margin-top:8px}
#cfm .cgo{background:var(--acc);color:#fff;font-weight:600}#cfm .cstop{background:transparent;color:#dc2626;border:1px solid var(--bd)}
.pchk{margin:8px 0;border:1px solid var(--bd);border-radius:12px;font-size:13px;color:var(--mut);overflow:hidden}
.pchk summary{padding:8px 12px;cursor:pointer}.pchk ul{margin:0;padding:2px 14px 10px 30px;color:var(--fg)}.pchk li{margin:3px 0}
.fgrp{display:flex;align-items:center;gap:8px;padding:12px 4px 6px;font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);cursor:pointer}
.fgrp .n{font-weight:400}.frow{display:flex;align-items:center;gap:12px;padding:8px 4px;border-radius:12px}
.frow:active{background:var(--card)}.fic{flex:none;width:38px;height:38px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;background:#64748b}
.fnm{flex:1;min-width:0;cursor:pointer}.fnm b{display:block;font-weight:500;font-size:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fnm small{color:var(--mut);font-size:12px}.fkb{flex:none;background:none;border:0;color:var(--mut);font-size:20px;padding:6px 10px}
.fact{display:none;gap:8px;padding:2px 4px 10px 54px}.fact.on{display:flex}
.fact a,.fact button{border:1px solid var(--bd);background:var(--card);color:var(--fg);border-radius:999px;padding:6px 14px;font-size:13px;text-decoration:none;cursor:pointer}
html,body{max-width:100%;overflow-x:hidden;overscroll-behavior-x:none}
#chat{min-width:0;width:100%;overflow-x:hidden;touch-action:pan-y;overscroll-behavior-x:none}
#chat>*{min-width:0;max-width:min(760px,100%);box-sizing:border-box}
.msg,.step,.step summary,.step .it,.step pre,.note,.ln,.md,.md *{overflow-wrap:anywhere;word-break:break-word}
.step{overflow:hidden}
.md pre,.md code{white-space:pre-wrap}
.md pre{overflow-x:auto;max-width:100%}
.md table{display:block;max-width:100%;overflow-x:auto}
.md img,.step img{max-width:100%;height:auto}
.user{background:#f3f4f6;color:#111827;border:1px solid var(--bd);border-radius:18px 18px 4px 18px}
#chat>.user{margin-left:auto;margin-right:max(0px,calc((100% - 760px)/2));max-width:86%}
.step,.step[open],.step.plan{background:#fff}
.md code{background:#f3f4f6}
.step.plan summary{pointer-events:none;padding:8px 12px;font-size:13px}
.step.plan .it{display:none}
.step.act{border-style:dashed}
.step.act>summary{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:10px 12px}
.actb{padding:6px 8px;border-top:1px solid var(--bd);max-height:50vh;overflow:auto}
.actb .step{margin:4px 0}
#send{width:48px;height:48px}
#send.stop{background:#f87171;font-size:22px;font-weight:300}
footer .attach{width:44px;height:44px;border-radius:50%;background:var(--card);display:flex;align-items:center;justify-content:center;color:var(--mut);padding:0;flex:none}
#fbtn{display:flex;align-items:center;color:var(--fg);padding:8px 10px}
footer textarea{min-height:54px;max-height:200px;padding:15px 18px;line-height:1.4}
</style></head><body>
<header><b id=ttl onclick="openProjects()" title="Project / chat badlo">🌇 Mumbai <i>▾</i></b><span class=sp></span>
<button class=ib id=fbtn type=button onclick="openFiles()" aria-label=Files><svg width=22 height=22 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.8 stroke-linecap=round stroke-linejoin=round><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></button>
<button class=ib id=mnb type=button onclick="toggleMenu(event)" aria-label=Menu>&#8943;</button>
<div id=menu><button type=button onclick="mnu('/test')">Test</button><button type=button onclick="mnu('conf')">AI settings</button><button type=button onclick="mnu('/undo')">Undo</button><button type=button onclick="mnu('/new')">New chat</button></div></header>
<div id=chat><div id=hint>Kaam likho, jaise "ek todo app banao html me".<br>Main files bana ke dunga. 📁 me dekh aur download kar sakte ho. Upar project ka naam (▾) dabao to dusra project / chat khul jayega.</div></div>
<div id=bar><span class=st>✻</span><span class=tx id=bt>Soch raha hoon…</span></div>
<footer>
<button class="ib attach" type=button onclick="document.getElementById('up').click()" aria-label=Attach><svg width=22 height=22 viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.9 stroke-linecap=round stroke-linejoin=round><path d="M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/></svg></button>
<textarea id=t rows=1 placeholder="Kaam likho..."></textarea>
<button id=send onclick="sendBtn()">➤</button>
<input type=file id=up multiple style="display:none" onchange="upload(this.files)"></footer>
<div id=cfm onclick="if(event.target==this)cfmNo()"><div class=cbox><div class=ci>⏹</div><h3>Kaam rok dein?</h3><p id=cfmt></p>
<button class=cgo type=button onclick="cfmNo()">Chalne do</button><button class=cstop type=button onclick="cfmYes()">Haan, roko</button></div></div>
<div id=psheet><div class=top><b>🗂 Projects</b>
<button class=ib onclick="newProject()">➕ Naya</button>
<button class=ib onclick="closeProjects()">✕</button></div>
<div id=pl></div></div>
<div id=sheet><div class=top><b>📁 Files</b>
<button class=ib onclick="document.getElementById('up').click()">⬆ Upload</button>
<a class=ib href="/zip" style="text-decoration:none">⬇ Sab zip</a>
<button class=ib onclick="closeFiles()">✕</button></div>
<input id=fq placeholder="🔎 file dhundho" oninput="drawFiles()">
<div id=fl></div><div id=fh><b id=fn></b><button class=ib onclick="copyFile()" id=fc>📋 Copy</button><a class=ib id=fo target=_blank style="text-decoration:none">↗ Kholo</a><button class=ib onclick="hideFv()">✕</button></div><img id=fm><iframe id=fp></iframe><pre id=fv></pre></div>
<div id=csheet><div class=top><b>⚙ AI providers</b>
<button class=ib onclick="saveConf()">💾 Save</button>
<button class=ib onclick="unlockAll()">🔓</button>
<button class=ib onclick="document.getElementById('csheet').style.display='none'">✕</button></div>
<div id=cst style="padding:6px 12px;display:flex;flex-wrap:wrap;gap:4px"></div>
<textarea id=cta spellcheck=false></textarea></div>
<script>
var lastKind='',last=0,running=false,sending=false,polling=false,redo=false,boot='',chat=document.getElementById('chat'),sheet=document.getElementById('sheet'),psheet=document.getElementById('psheet');
function el(tag,cls,txt){var e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e}
var ICON={LS:'📂',READ:'📖',WRITE:'📝',WRITEB64:'🖼',EDIT:'✏️',TREE:'🌳',GREP:'🔎',RUN:'⚙️',BG:'🚀',LOG:'📜',KILL:'🛑',ZIP:'📦',UNZIP:'📂',WEB:'🌐',SEARCH:'🔍',NOTIFY:'🔔',NOTE:'🗒',VERIFY:'🛡',LOOK:'👁',LEARN:'🧠',ASK:'❓'},plan=[];
function toast(t){chat.appendChild(el('div','msg err',t));chat.scrollTop=chat.scrollHeight}
var chk=null,chkN=[0,0],planBox=null;
function esc(t){return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function inl(t){t=esc(t);var codes=[];
 t=t.replace(/`([^`\n]+)`/g,function(_,c){codes.push(c);return '\u0001'+(codes.length-1)+'\u0002'});
 t=t.replace(/\*\*([^*\n]+)\*\*/g,'<b>$1</b>').replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:]|$)/g,'$1<i>$2</i>');
 t=t.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
 t=t.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,'$1<a href="$2" target="_blank" rel="noopener">$2</a>');
 return t.replace(/\u0001(\d+)\u0002/g,function(_,i){return '<code>'+codes[+i]+'</code>'})}
function md(src){var L=String(src||'').replace(/\r/g,'').split('\n'),out=[],i=0,para=[];
 function flush(){if(para.length){out.push('<p>'+para.map(inl).join('<br>')+'</p>');para=[]}}
 while(i<L.length){var l=L[i],m;
  if((m=l.match(/^\s*```/))){flush();var code=[];i++;while(i<L.length&&!/^\s*```/.test(L[i])){code.push(L[i]);i++}i++;
   out.push('<pre><button class=cp>Copy</button><code>'+esc(code.join('\n'))+'</code></pre>');continue}
  if((m=l.match(/^(#{1,3})\s+(.*)$/))){flush();out.push('<h'+m[1].length+'>'+inl(m[2])+'</h'+m[1].length+'>');i++;continue}
  if(/^\s*[-*]\s+/.test(l)){flush();var li=[];while(i<L.length&&/^\s*[-*]\s+/.test(L[i])){li.push('<li>'+inl(L[i].replace(/^\s*[-*]\s+/,''))+'</li>');i++}out.push('<ul>'+li.join('')+'</ul>');continue}
  if(/^\s*\d+[.)]\s+/.test(l)){flush();var lo=[];while(i<L.length&&/^\s*\d+[.)]\s+/.test(L[i])){lo.push('<li>'+inl(L[i].replace(/^\s*\d+[.)]\s+/,''))+'</li>');i++}out.push('<ol>'+lo.join('')+'</ol>');continue}
  if(/^>\s?/.test(l)){flush();var q=[];while(i<L.length&&/^>\s?/.test(L[i])){q.push(L[i].replace(/^>\s?/,''));i++}out.push('<blockquote>'+q.map(inl).join('<br>')+'</blockquote>');continue}
  if(!l.trim()){flush();i++;continue}
  para.push(l);i++}
 flush();return out.join('')}
function mdEl(cls,txt){var d=el('div',cls+' md');d.innerHTML=md(txt);
 d.querySelectorAll('.cp').forEach(function(b){b.onclick=function(){var c=b.parentNode.querySelector('code').textContent;copyText(c,b)}});return d}
function copyText(t,btn){var ok=function(){if(btn){var o=btn.textContent;btn.textContent='✓';setTimeout(function(){btn.textContent=o},1200)}};
 if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(t).then(ok,function(){fb()});else fb();
 function fb(){var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();try{document.execCommand('copy');ok()}catch(x){}a.remove()}}
function inner(line,bad){ /* andar ki jaanch: sab review/SPEC/CHECK ek band line me */
 if(!chk){chk=el('details','step chk');chk.appendChild(el('summary',null,''));chkN=[0,0];actBox().appendChild(chk);actSay('Andar ki jaanch')}
 var l=el('div','ln'+(bad===true?' b':bad===false?' g':''),line);chk.appendChild(l);
 if(bad===true)chkN[1]++;else if(bad===false)chkN[0]++;
 chk.querySelector('summary').textContent='🔍 Andar ki jaanch'+(chkN[0]||chkN[1]?' · '+chkN[0]+' theek'+(chkN[1]?' · '+chkN[1]+' sudhar':''):'');
 chk.classList.toggle('hasbad',chkN[1]>0)}
var INNER=/^(🔍|🧪|🚶|✨|📐|🧾)/,askBox=null;
function drawPlan(){if(!planBox)return;var done=planBox.items.filter(function(x){return x.ok}).length,n=planBox.items.length;
 planBox.sum.textContent='Plan  '+done+'/'+n;planBox.bar.style.width=(n?100*done/n:0)+'%';
 planBox.items.forEach(function(x){x.d.textContent=(x.ok?'\u2713  ':'\u25CB  ')+x.t;x.d.className='it'+(x.ok?' ok':'')});
 if(done==n&&n)planBox.c.open=false}
function toggleMenu(ev){ev.stopPropagation();var m=document.getElementById('menu');m.style.display=m.style.display=='block'?'none':'block'}
function mnu(a){document.getElementById('menu').style.display='none';if(a=='conf')openConf();else if(a=='files')openFiles();else actPost(a)}
document.addEventListener('click',function(){var m=document.getElementById('menu');if(m)m.style.display='none'});
var NOISE=/(HTTP\s?\d{3}|\b429\b|rate.?limit|bheed|aaram|ruk ke dobara|org_\w+|cooldown)/i,PROV=/^(JAAT|GEM\d?|GROQ|DS|NV2|DAI)\b/;
var act=null,actBody=null,actN=0,lastAi=null,doneShown=false,stopping=false,curLevel=0,curPlan=[0,0];
function actBox(){if(!act){act=el('details','step act');act.appendChild(el('summary',null,'Kaam chal raha hai'));actBody=el('div','actb');act.appendChild(actBody);actN=0;chat.appendChild(act)}return actBody}
function actSay(t){if(!act)return;actN++;act.firstChild.textContent=t+(actN>1?'  \u00b7  '+actN+' kadam':'')}
function noisy(e){var x=e.text||'';return (e.t=='note'&&NOISE.test(x))||(e.t=='err'&&PROV.test(x)&&NOISE.test(x))}
function cleanStep(s){var V={EDIT:'Badlo',WRITE:'Banao',WRITEB64:'Banao',VERIFY:'Jaanch',RUN:'Chalao',READ:'Padho',CHECK:'Jaanch'};
 return String(s).replace(/^@@(\w+)\s*/,function(m,c){return (V[c]||c)+': '}).replace(/\s*\u2192\s*/g,' \u2014 ')}
function stopNow(){stopping=true;sendIcon();toast('Ruk raha hoon...');var n=0;
 (function go(){post('/stop').catch(function(){});if(++n<5&&running)setTimeout(go,1500)})()}
function render(e){
 if(noisy(e))return;
 var h=document.getElementById('hint');if(h)h.style.display='none';
 if(e.t!='review'&&!(e.t=='note'&&INNER.test(e.text||'')))chk=null;
 if(e.t=='user'){act=null;lastAi=null;doneShown=false;chat.appendChild(el('div','msg user',e.text))}
 else if(e.t=='ai'){var d=mdEl('msg',e.text);if(e.prov)d.appendChild(el('span','chip',e.prov));if(act){actBody.appendChild(d);lastAi=d;actSay('Soch raha hoon')}else chat.appendChild(d)}
 else if(e.t=='step'){lastKind=e.kind;var s=el('details','step'+(e.ok?'':' bad'));
  s.appendChild(el('summary',null,(ICON[e.kind]||'•')+' '+e.kind+' '+e.arg));
  if(e.out)s.appendChild(el('pre',null,e.out));
  if(e.img){var im=el('img');im.src='/raw?p='+encodeURIComponent(e.img);im.style.cssText='max-width:100%;display:block;margin:6px 12px 10px;border:1px solid var(--bd);border-radius:8px';s.appendChild(im);s.open=true}
  actBox().appendChild(s);actSay((ICON[e.kind]||'•')+' '+e.kind+' '+e.arg)}
 else if(e.t=='done'){doneShown=true;lastAi=null;if(act)act.firstChild.textContent='Kaam ke kadam'+(actN?' \u00b7 '+actN:'');act=null;var d=mdEl('msg done','✅ '+e.text);
  d.appendChild(el('span','chip',e.prov+' · '+e.steps+' kadam'));
  if(e.zip){var a=el('a','dl','⬇ '+(e.proj||'project')+'.zip download');a.href='/zip';d.appendChild(document.createElement('br'));d.appendChild(a)}
  chat.appendChild(d)}
 else if(e.t=='plan'){var c=el('details','step plan');c.open=true;var sm=el('summary',null,'');c.appendChild(sm);
  var pb=el('div','pb'),bar=el('div','pbar');bar.appendChild(el('i'));pb.appendChild(bar);c.appendChild(pb);
  planBox={c:c,sum:sm,bar:bar.firstChild,items:[]};
  e.items.forEach(function(x){var d=el('div','it');c.appendChild(d);planBox.items.push({t:cleanStep(x),d:d,ok:false})});
  chat.appendChild(c);drawPlan()}
 else if(e.t=='check'){if(planBox&&planBox.items[e.n-1]){planBox.items[e.n-1].ok=true;drawPlan()}}
 else if(e.t=='review')inner((e.ok?'✅ ':'🐞 ')+e.title+(e.prov?' ('+e.prov+')':'')+'\n'+e.text,!e.ok);
 else if(e.t=='understand'){var u=el('details','step');u.appendChild(el('summary',null,'🧠 Samajh · '+(e.kind||'?')+(e.done&&e.done.length?' · '+e.done.length+' criteria':'')));
  var ub=el('div','ln',(e.goal||'')+(e.done&&e.done.length?'\n\nKaam kab poora:\n'+e.done.join('\n'):''));ub.style.cssText='padding:8px 12px;font-size:13px;white-space:pre-wrap;border-top:1px solid var(--bd)';u.appendChild(ub);actBox().appendChild(u);actSay('Samajh raha hoon')}
 else if(e.t=='ask'){var a=el('div','step ask');a.style.cssText='padding:10px 12px;border-color:var(--acc)';
  a.appendChild(el('div',null,'❓ '+e.text));
  (e.opts||[]).forEach(function(o){var b=el('button','ib',o);b.style.cssText='border:1px solid var(--bd);margin:6px 6px 0 0;color:var(--fg);font-size:14px';b.onclick=function(){post('/run',{task:o}).then(poll)};a.appendChild(b)});
  var st=el('div','note','⏳ jawab likho (neeche box me ya Telegram me). '+Math.round((e.wait||0)/60)+' min me na aaya to 3 AI mil ke tay karenge.');st.style.textAlign='left';a.appendChild(st);
  askBox={c:a,st:st};chat.appendChild(a)}
 else if(e.t=='ask_done'){if(askBox){askBox.st.textContent=(e.how=='user'?'✅ aapka jawab: ':'🗳 3 AI ne tay kiya: ')+e.text;askBox.c.style.borderColor='var(--bd)';askBox=null}}
 else if(e.t=='err')chat.appendChild(el('div','msg err',e.text));
 else if(e.t=='note'&&/^🔎PLANCHK/.test(e.text||'')){var pl=e.text.split('\n');var dt=el('details','pchk');dt.appendChild(el('summary',null,'🔎 Plan check: '+(pl.length-2)+' baat'));var ul=el('ul');pl.slice(2).forEach(function(x){ul.appendChild(el('li',null,x))});dt.appendChild(ul);(act?actBody:chat).appendChild(dt)}
 else if(e.t=='note'){if(INNER.test(e.text||''))inner(e.text,null);else(/^(⏳|⚠|❌|Ruk)/.test(e.text||'')||!act?chat:actBody).appendChild(el('div','note',e.text))}
}
function fresh(){act=null;lastAi=null;doneShown=false;plan=[];chk=null;planBox=null;askBox=null;chat.innerHTML='';var h=el('div',null,'Naya chat shuru. Kaam likho.');h.id='hint';chat.appendChild(h)}
function poll(){if(polling)return;polling=true;
 fetch('/events?after='+last).then(function(r){if(r.status==401){location.reload();throw 0}return r.json()}).then(function(j){
  if(boot&&j.boot!==boot){boot=j.boot;last=0;fresh();redo=true;return}
  boot=j.boot;
  var near=chat.scrollHeight-chat.scrollTop-chat.clientHeight<120;
  j.events.forEach(function(e){if(e.id<=last)return;last=e.id;
   if(e.t=='reset')fresh();else render(e)});
  running=j.running;curLevel=j.level||0;curPlan=[j.plan_done||0,j.plan_n||0];if(!running)stopping=false;if(!running&&lastAi&&!doneShown){chat.appendChild(lastAi);lastAi=null}var b=document.getElementById('bar');
  b.style.display=running?'flex':'none';
  var W={READ:'Padh raha hoon',WRITE:'Likh raha hoon',WRITEB64:'Likh raha hoon',EDIT:'Badal raha hoon',RUN:'Chala ke dekh raha hoon',BG:'Background me chala raha hoon',VERIFY:'Jaanch raha hoon',GREP:'Dhundh raha hoon',TREE:'Files dekh raha hoon',WEB:'Web padh raha hoon',SEARCH:'Search kar raha hoon',UNZIP:'Khol raha hoon',LOOK:'PDF dekh raha hoon'};
  document.getElementById('bt').textContent=stopping?'Ruk raha hoon…':(W[lastKind]||'Soch raha hoon')+'… '+(j.plan_n?'('+j.plan_done+'/'+j.plan_n+')':'');
  sendIcon();document.getElementById('t').placeholder=j.asking?'Jawab likho...':(running?'Beech me bhejo (queue)...':'Kaam likho...');
  document.getElementById('ttl').innerHTML='';var tt=document.getElementById('ttl');tt.appendChild(document.createTextNode('📂 '+j.proj+' '));tt.appendChild(el('i',null,'▾'));if(j.mode)tt.appendChild(el('span','badge '+j.mode,j.mode));
  if(near&&j.events.length)chat.scrollTop=chat.scrollHeight
 }).catch(function(){}).then(function(){polling=false;if(redo){redo=false;poll()}})}
setInterval(poll,1000);poll();
function post(p,b){return fetch(p,{method:'POST',body:JSON.stringify(b||{})})}
function actPost(p){post(p).then(poll)}
function act2(p,b){return post(p,b).then(function(r){if(!r.ok)return r.text().then(function(t){alert(t)})}).catch(function(e){alert('Server se baat nahi hui: '+e.message)})}
function sendIcon(){var v=document.getElementById('t').value.trim();var sb=document.getElementById('send'),st=running&&!v;sb.textContent=st?(stopping?'⏳':'✕'):'➤';sb.classList.toggle('stop',st);sb.classList.toggle('busy',st&&stopping)}
function askStop(){var d=document.getElementById('cfm'),p=curPlan;
 document.getElementById('cfmt').textContent=p[1]?(p[0]+'/'+p[1]+' kadam ho chuke hain. Rokne par aadha kaam reh jayega.'):'Kaam abhi chal raha hai. Rokne par aadha kaam reh jayega.';
 d.style.display='flex'}
function cfmNo(){document.getElementById('cfm').style.display='none'}
function cfmYes(){cfmNo();stopNow()}
function sendBtn(){var t=document.getElementById('t'),v=t.value.trim();
 if(running&&!v){if(stopping)return;if(curLevel>3)askStop();else stopNow();return}
 if(sending||!v)return;
 sending=true;running=true;
 t.value='';t.style.height='auto';
 post('/run',{task:v}).catch(function(e){toast('❌ bheja nahi ja saka: '+e.message)}).then(function(){sending=false;poll()})}
document.getElementById('t').addEventListener('input',function(){sendIcon();this.style.height='auto';this.style.height=Math.min(this.scrollHeight,200)+'px'});
function upload(fs){var inp=document.getElementById('up'),list=Array.prototype.slice.call(fs),i=0;
 function next(){
  if(i>=list.length){inp.value='';if(sheet.style.display=='flex')openFiles();return}
  var f=list[i++];
  fetch('/upload?name='+encodeURIComponent(f.name),{method:'POST',body:f}).then(function(r){
   if(!r.ok)return r.text().then(function(t){throw new Error(t.slice(0,120)||('HTTP '+r.status))})
  }).catch(function(e){toast('❌ upload fail ('+f.name+'): '+e.message)}).then(next)}
 next()}
var FILES=[],AGE={};
function ago(s){if(s<90)return 'abhi';if(s<3600)return Math.floor(s/60)+' min';if(s<86400)return Math.floor(s/3600)+' ghante';return Math.floor(s/86400)+' din'}
function openFiles(){sheet.style.display='flex';
 fetch('/files').then(r=>r.json()).then(function(j){FILES=j.files;AGE=j.age||{};drawFiles();document.getElementById('fl').scrollTop=0})}
var OPENF={},FCOL={py:'#3776ab',js:'#ca8a04',ts:'#2563eb',html:'#e34f26',css:'#0ea5e9',json:'#64748b',md:'#475569',txt:'#64748b',pdf:'#dc2626',png:'#16a34a',jpg:'#16a34a',kt:'#7c3aed',java:'#ea580c',xml:'#0d9488',yml:'#0d9488',yaml:'#0d9488',sh:'#334155',zip:'#92400e'};
function fileRow(n,d,showDir){var ext=(n.split('.').pop()||'').toLowerCase(),base=n.split('/').pop(),dir=n.indexOf('/')>=0?n.slice(0,n.lastIndexOf('/')):'';
 var w=el('div');var r=el('div','frow');var ic=el('div','fic',(n.indexOf('.')>=0?ext:'file').slice(0,4).toUpperCase());ic.style.background=FCOL[ext]||'#64748b';r.appendChild(ic);
 var m=el('div','fnm');m.appendChild(el('b',null,base));var sm=[];if(showDir&&dir)sm.push(dir);if(AGE[n]!=null)sm.push(ago(AGE[n]));if(sm.length)m.appendChild(el('small',null,sm.join('  ·  ')));
 m.onclick=function(){show(n)};r.appendChild(m);
 var ac=el('div','fact');var kb=el('button','fkb','⋮');kb.onclick=function(){ac.classList.toggle('on')};r.appendChild(kb);
 var rn=el('button',null,'Naam badlo');rn.onclick=function(){var to=prompt('Naya naam:',n);if(to&&to!==n)act2('/rename',{p:n,to:to}).then(openFiles)};
 var dw=el('a',null,'Download');dw.href='/download?p='+encodeURIComponent(n);dw.setAttribute('download','');
 var dl=el('button',null,'Delete');dl.style.color='#dc2626';dl.onclick=function(){if(confirm(n+' delete karu?'))act2('/delete',{p:n}).then(openFiles)};
 ac.appendChild(rn);ac.appendChild(dw);ac.appendChild(dl);w.appendChild(r);w.appendChild(ac);d.appendChild(w)}
function drawFiles(){var q=(document.getElementById('fq').value||'').toLowerCase(),d=document.getElementById('fl');d.innerHTML='';
 var list=FILES.filter(function(n){return !q||n.toLowerCase().indexOf(q)>=0});
 if(!list.length){d.textContent=FILES.length?'(kuch nahi mila)':'(abhi koi file nahi)';return}
 if(q){list.forEach(function(n){fileRow(n,d,true)});return}
 var root=[],grp={};list.forEach(function(n){var k=n.indexOf('/')>=0?n.split('/')[0]:'';if(k)(grp[k]=grp[k]||[]).push(n);else root.push(n)});
 root.forEach(function(n){fileRow(n,d,false)});
 Object.keys(grp).sort().forEach(function(k){var open=OPENF[k]!==false;var h=el('div','fgrp');h.appendChild(el('span',null,open?'▾':'▸'));h.appendChild(el('span',null,k));h.appendChild(el('span','n',String(grp[k].length)));
  h.onclick=function(){OPENF[k]=!open;drawFiles()};d.appendChild(h);if(open)grp[k].forEach(function(n){fileRow(n,d,true)})})}
function closeFiles(){sheet.style.display='none';hideFv()}
var curFile='',curText='';
function hideFv(){['fv','fh','fm','fp'].forEach(function(i){document.getElementById(i).style.display='none'})}
function show(n){curFile=n;curText='';hideFv();
 var ext=(n.split('.').pop()||'').toLowerCase(),raw='/raw?p='+encodeURIComponent(n),fh=document.getElementById('fh');
 document.getElementById('fn').textContent=n;fh.style.display='flex';
 var fo=document.getElementById('fo'),fc=document.getElementById('fc');fo.href=raw;
 if(/^(png|jpe?g|gif|webp|svg)$/.test(ext)){fc.style.display='none';fo.style.display='';var m=document.getElementById('fm');m.src=raw;m.style.display='block';return}
 if(ext=='pdf'){fc.style.display='none';fo.style.display='';var p=document.getElementById('fp');p.src=raw;p.style.display='block';return}
 fc.style.display='';fo.style.display='none';
 fetch('/file?p='+encodeURIComponent(n)).then(r=>r.text()).then(function(t){curText=t;
  var v=document.getElementById('fv');v.style.display='block';v.textContent=t})}
function copyFile(){copyText(curText,document.getElementById('fc'))}
function openProjects(){psheet.style.display='flex';
 fetch('/projects').then(r=>r.json()).then(function(j){var d=document.getElementById('pl');d.innerHTML='';
  j.projects.forEach(function(n){var r=el('div','row');var s=el('span',null,(n==j.cur?'📂 ':'📁 ')+n);
   s.onclick=function(){if(n==j.cur){closeProjects();return}act2('/project',{name:n}).then(function(){closeProjects();poll()})};r.appendChild(s);
   var rn=el('a',null,'✏️');rn.onclick=function(){var to=prompt('Project ka naya naam:',n);if(to&&to.trim()&&to.trim()!==n)act2('/rename_project',{name:n,to:to.trim()}).then(function(){openProjects();poll()})};r.appendChild(rn);
   var dl=el('a',null,'🗑');dl.onclick=function(){if(confirm('"'+n+'" project poora (files + chat) delete karu? Wapas nahi aayega.'))act2('/delete_project',{name:n}).then(function(){openProjects();poll()})};r.appendChild(dl);
   d.appendChild(r)})})}
function newProject(){var n=prompt('Naye project ka naam:');if(n&&n.trim())act2('/project',{name:n.trim()}).then(function(){closeProjects();poll()})}
function closeProjects(){psheet.style.display='none'}
function openConf(){document.getElementById('csheet').style.display='flex';
 fetch('/config').then(function(r){return r.json()}).then(function(j){document.getElementById('cta').value=j.conf;
  var s=document.getElementById('cst');s.innerHTML='';
  var h=el('div','note','👆 chip dabao: 🔒 ho to cooldown hatega, warna ON/OFF');h.style.width='100%';s.appendChild(h);
  j.status.forEach(function(x){var c=el('div','note',(x.off?'⛔':(x.ready?(x.cool?'🔒':'🟢'):'⚪'))+' '+x.name+' · '+x.group+(x.cool?' · '+(x.cool<90?x.cool+' sec':Math.ceil(x.cool/60)+' min'):''));
   c.style.cursor='pointer';c.onclick=function(){post(x.cool&&!x.off?'/unlock':'/toggle',{name:x.name}).then(function(){openConf()})};s.appendChild(c)})})}
function saveConf(){post('/config',{conf:document.getElementById('cta').value}).then(function(r){return r.text()}).then(function(t){alert(t);openConf()})}
function unlockAll(){post('/unlock').then(function(){openConf()})}
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def _send(self, code, data, ctype="application/json", hdrs=None):
        if isinstance(data, str):
            data = data.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (hdrs or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _guard(self, post=False):
        """Dusri website se chhupke aayi request ko rokta hai (Origin/Host jaanch)."""
        host = self.headers.get("Host", "")
        fwd = self.headers.get("X-Forwarded-Host", "")
        mine = {host, fwd} | ALLOWED_HOSTS
        if ALLOWED_HOSTS and host not in ALLOWED_HOSTS and fwd not in ALLOWED_HOSTS:
            self._send(403, '{"error":{"message":"host galat"}}')
            return False
        org = self.headers.get("Origin")
        if post and org and urllib.parse.urlparse(org).netloc not in mine:
            self._send(403, '{"error":{"message":"origin galat"}}')
            return False
        return True

    def _cookie_ok(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "mb" and hmac.compare_digest(v.encode("utf-8", "replace"), AUTH_TOKEN.encode()):
                return True
        return False

    def _authed(self):
        """Login cookie, ya OpenCode jaise tool ke liye 'Authorization: Bearer <password>' (galat par ginti badhti hai)."""
        if self._cookie_ok():
            return True
        h = self.headers.get("Authorization") or ""
        if h.startswith("Bearer "):
            return check_password(h[7:].strip())[0]
        return False

    def do_HEAD(self):                      # uptime bot aksar HEAD bhejte hain
        code = 200 if urllib.parse.urlparse(self.path).path == "/health" else 401
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._guard():
            return
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/health":             # bina password, uptime bot ke liye
            return self._send(200, "ok", "text/plain")
        if not self._authed():
            if u.path in ("/events", "/files", "/projects", "/file", "/raw", "/download", "/zip", "/config") or u.path.rstrip("/").endswith("/models"):
                return self._send(401, '{"error":{"message":"login chahiye"}}')
            return self._send(200, LOGIN, "text/html; charset=utf-8")
        if u.path.rstrip("/").endswith("/models"):
            self._send(200, json.dumps({"object": "list", "data": [{"id": "mumbai", "object": "model"}]}))
        elif u.path == "/events":
            try:
                after = int((q.get("after") or ["0"])[0] or 0)
            except ValueError:
                after = 0
            with LOCK:
                evs = [e for e in EV if e["id"] > after]
            self._send(200, json.dumps({"events": evs, "running": STATE["running"], "step": STATE["step"],
                                        "max": MAX_STEPS, "prov": STATE["prov"], "boot": BOOT, "proj": STATE["proj"],
                                        "plan_n": len(STATE["plan_items"]), "plan_done": len(STATE["plan_ck"]),
                                        "mode": cur_mode() if STATE.get("task") else "",
                                        "asking": bool(STATE.get("asking")), "queued": len(STATE["pending"]),
                                        "level": int(STATE.get("level") or 0) if STATE["running"] else 0}))
        elif u.path == "/config":
            self._send(200, json.dumps({"conf": conf_dump(), "status": conf_status()}))
        elif u.path == "/files":
            fs, age = list_files_recent(1000)
            self._send(200, json.dumps({"files": fs, "age": age}))
        elif u.path == "/projects":
            self._send(200, json.dumps({"projects": list_projects(), "cur": STATE["proj"]}))
        elif u.path == "/raw":
            try:
                rp = (q.get("p") or [""])[0]
                if rp.startswith("@preview/"):
                    p = os.path.realpath(os.path.join(PREVIEW_DIR, rp[9:]))
                    if not p.startswith(os.path.realpath(PREVIEW_DIR) + os.sep):
                        raise ValueError("galat path")
                else:
                    p = safe(rp)
                if not os.path.isfile(p):
                    raise ValueError("file nahi mili")
                ct = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                      ".webp": "image/webp", ".svg": "image/svg+xml", ".pdf": "application/pdf"}.get(os.path.splitext(p)[1].lower())
                if not ct:
                    raise ValueError("preview nahi")
                with open(p, "rb") as f:
                    data = f.read(15000000)
                self._send(200, data, ct, {"Content-Disposition": "inline", "Content-Security-Policy": "sandbox"})
            except Exception as e:
                self._send(404, "ERROR: %s" % e, "text/plain; charset=utf-8")
        elif u.path in ("/file", "/download"):
            try:
                p = safe((q.get("p") or [""])[0])
                if not os.path.isfile(p):
                    raise ValueError("file nahi mili")
                with open(p, "rb") as f:
                    data = f.read()
                if u.path == "/file":
                    if b"\0" in data[:4096]:
                        self._send(200, "(binary file, %d bytes)" % len(data), "text/plain; charset=utf-8")
                    else:
                        self._send(200, clip(data.decode("utf-8", "replace"), 20000), "text/plain; charset=utf-8")
                else:
                    self._send(200, data, "application/octet-stream",
                               {"Content-Disposition": 'attachment; filename="%s"' % clean_name(p)})
            except Exception as e:
                self._send(404, "ERROR: %s" % e, "text/plain; charset=utf-8")
        elif u.path == "/zip":
            self._send(200, make_zip(), "application/zip",
                       {"Content-Disposition": 'attachment; filename="%s.zip"' % STATE["proj"]})
        else:
            self._send(200, PAGE, "text/html; charset=utf-8")

    def _err(self, msg, code=400):
        self._send(code, msg, "text/plain; charset=utf-8")

    def do_POST(self):
        if not self._guard(True):
            return
        n = int(self.headers.get("Content-Length") or 0)
        u = urllib.parse.urlparse(self.path)
        if u.path == "/login":
            try:
                lb = json.loads(self.rfile.read(min(n, 2048)) or b"{}")
            except ValueError:
                lb = {}
            ok, msg = check_password(lb.get("password", "") if isinstance(lb, dict) else "")
            if not ok:
                return self._send(403, json.dumps({"error": {"message": msg}}))
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return self._send(200, '{"ok":true}', hdrs={
                "Set-Cookie": "mb=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000%s" % (AUTH_TOKEN, secure)})
        if not self._authed():
            return self._send(401, '{"error":{"message":"login chahiye"}}')
        if u.path == "/upload":
            if n > MAX_UPLOAD:
                return self._send(413, '{"error":{"message":"file bahut badi (25MB se zyada)"}}')
            name = clean_name((urllib.parse.parse_qs(u.query).get("name") or [""])[0])
            try:
                os.makedirs(os.path.join(WORK, "uploads"), exist_ok=True)
                with open(os.path.join(WORK, "uploads", name), "wb") as f:
                    f.write(self.rfile.read(n))
            except OSError as e:
                return self._err("upload fail: %s" % e, 500)
            ev("note", text="📎 upload hui: uploads/%s" % name)
            tell_agent("[Note] User ne file upload ki hai: uploads/%s" % name)
            return self._send(200, json.dumps({"path": "uploads/" + name}))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, '{"error":{"message":"bad json"}}')
        if not isinstance(body, dict):
            body = {}
        p = u.path
        if p.endswith("/chat/completions"):
            return self.proxy(body)
        if p == "/stop":
            STATE["cancel"] = True
            STATE["pending"].clear()          # beech me queue kiye message bhi nahi chalenge
            kill_proc()
            t_stop = time.time()
            while STATE["running"] and time.time() - t_stop < 5:      # agent thread nikalne tak ruko (ab turant nikalta hai)
                time.sleep(0.1)
            STATE["asking"] = None
            ev("note", text="⏹ roka gaya" if not STATE["running"] else "Ruk raha hoon...")
        elif p == "/undo":
            _m = do_undo()
            if not _m.startswith("⏳") or busy_note_ok():
                ev("note", text=_m)
        elif p in ("/new", "/project", "/delete", "/rename", "/delete_project", "/rename_project") and STATE["running"]:
            if busy_note_ok():
                ev("note", text="⏳ pehle ⏹ se roko")
        elif p == "/new":
            ev("note", text=new_chat())
        elif p == "/project":
            ev("note", text=switch_project(str(body.get("name", ""))))
        elif p == "/delete":
            try:
                rel = str(body.get("p", ""))
                fp = safe(rel)
                if not os.path.isfile(fp):
                    raise ValueError("file nahi mili")
                os.remove(fp)
                prune(os.path.dirname(fp))
                tell_agent("[Note] User ne %s delete kar di." % rel)
                ev("note", text="🗑 %s delete" % rel)
            except Exception as e:
                return self._err("delete nahi hua: %s" % e)
        elif p == "/rename":
            try:
                rel, to = str(body.get("p", "")), str(body.get("to", ""))
                a, b = safe(rel), safe(to)
                if not os.path.isfile(a):
                    raise ValueError("file nahi mili")
                if os.path.exists(b):
                    raise ValueError("is naam ki file/folder pehle se hai")
                os.makedirs(os.path.dirname(b), exist_ok=True)
                os.rename(a, b)
                prune(os.path.dirname(a))
                tell_agent("[Note] User ne %s ka naam badal ke %s kar diya." % (rel, to))
                ev("note", text="✏️ %s → %s" % (rel, to))
            except Exception as e:
                return self._err("rename nahi hua: %s" % e)
        elif p == "/delete_project":
            try:
                ev("note", text=delete_project(str(body.get("name", ""))))
            except Exception as e:
                return self._err("project delete nahi hua: %s" % e)
        elif p == "/rename_project":
            try:
                ev("note", text=rename_project(str(body.get("name", "")), str(body.get("to", ""))))
            except Exception as e:
                return self._err("project rename nahi hua: %s" % e)
        elif p == "/run":
            task = str(body.get("task", "")).strip()
            if task and STATE.get("asking"):
                STATE["answer"] = task
                ev("user", text=task)
            elif task and STATE["running"]:
                ev("user", text=task)
                tell_agent("[User ne kaam ke beech me ye bheja, agle kadam me isko dhyan me lo: %s]" % task)
                ev("note", text="📨 message queue me hai, agle kadam par padhunga")
            elif task and not start_agent(task):
                ev("note", text="⏳ abhi kaam chal raha hai")
        elif p == "/config":
            try:
                return self._send(200, conf_apply(str(body.get("conf", "")), write=True), "text/plain; charset=utf-8")
            except ValueError as e:
                return self._err("❌ " + str(e))
        elif p == "/unlock":
            nm = str(body.get("name", ""))
            if nm in APIS:
                COOL.pop(nm, None)
                FAILS.pop(nm, None)
                save_cool()
                ev("note", text="🔓 %s ka cooldown hata diya" % nm)
            else:
                COOL.clear()
                FAILS.clear()
                save_cool()
                ev("note", text="🔓 saare cooldown hata diye")
        elif p == "/toggle":
            nm = str(body.get("name", ""))
            if nm in APIS:
                if nm in DISABLED:
                    DISABLED.discard(nm)
                    ev("note", text="🟢 %s chalu kar diya" % nm)
                else:
                    DISABLED.add(nm)
                    ev("note", text="⛔ %s band kar diya (wapas dabao to chalu)" % nm)
                conf_write()
        elif p == "/test":
            if begin():
                threading.Thread(target=selftest, daemon=True).start()
            else:
                ev("note", text="⏳ abhi kaam chal raha hai")
        self._send(200, "ok", "text/plain")

    # ---------- OpenCode ke liye proxy (optional) ----------
    def proxy(self, body):
        stream, tools = bool(body.get("stream")), bool(body.get("tools"))
        try:
            for name in order():
                if self.try_provider(name, body, stream, tools):
                    return
            log("❌ koi AI nahi chala")
            self._send(502, json.dumps({"error": {"message": "Mumbai: koi AI jawab nahi de paya"}}))
        except (BrokenPipeError, ConnectionResetError):
            pass

    def try_provider(self, name, body, stream, tools):
        try:
            with slot(name):
                return self._try_provider(name, body, stream, tools)
        except Cancelled:
            return False

    def _try_provider(self, name, body, stream, tools):
        cfg = APIS[name]
        t = time.time()
        if cfg["type"] == "oai":
            for model in models_of(cfg):
                started = False
                try:
                    r = upstream(cfg, model, body)
                    if stream:
                        first = r.readline()
                        if not first:
                            raise IOError("khali stream")
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-cache")
                        self.end_headers()
                        started = True
                        self.wfile.write(first)
                        self.wfile.flush()
                        for line in r:
                            self.wfile.write(line)
                            self.wfile.flush()
                    else:
                        data = r.read()
                        json.loads(data)
                        self._send(200, data)
                    log("← %s (%s) %.1fs" % (name, model, time.time() - t))
                    if COOL.pop(name, None) is not None:
                        save_cool()
                    rotated(name)
                    return True
                except urllib.error.HTTPError as e:
                    log("%s/%s HTTP %s" % (name, model, e.code))
                    if e.code in (400, 404):
                        continue
                    if e.code == 429 and cfg.get("rate_cool") and cfg["rate_cool"] < HARD_SECS:
                        COOL[name] = time.time() + cfg["rate_cool"]
                    elif e.code == 429 and cfg.get("rate_cool"):
                        lock(name, cfg["rate_cool"], "429")
                    else:
                        penalize(name, {429: 30, 401: 600, 403: 600}.get(e.code, 90))
                    return False
                except (BrokenPipeError, ConnectionResetError):
                    raise
                except Exception as e:
                    log("%s fail: %s" % (name, str(e)[:100]))
                    if started:
                        # jawab aadha ja chuka, usi socket par doosra jawab nahi bhej sakte; connection band
                        self.close_connection = True
                        return True
                    penalize(name, 90)
                    return False
            return False
        if tools:
            return False
        text = flat_text(body.get("messages", []))
        if len(text) > cfg.get("max_chars", 3000) or (
                cfg["type"] == "proxy_get" and len(urllib.parse.quote(text, safe="")) > cfg.get("max_url", 6000)):
            log("⏭ %s: prompt bada (%d char)" % (name, len(text)))
            return False
        try:
            out = PLAIN[cfg["type"]](cfg, models_of(cfg)[0], text)
            if not out.strip():
                raise IOError("khali jawab")
        except Exception as e:
            log("%s fail: %s" % (name, str(e)[:100]))
            penalize(name, 90)
            return False
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(sse(out))
        else:
            self._send(200, completion(out))
        log("← %s %.1fs" % (name, time.time() - t))
        return True

    def log_message(self, *a):
        pass


def setup():
    prov = {"mumbai": {
        "npm": "@ai-sdk/openai-compatible", "name": "Mumbai",
        "options": {"baseURL": "http://127.0.0.1:%d/v1" % PORT, "apiKey": "mumbai"},
        "models": {"mumbai": {"name": "Mumbai (auto fallback)"}}}}
    for name, cfg in APIS.items():
        if cfg["type"] == "oai" and ready(name):
            prov[name.lower()] = {
                "npm": "@ai-sdk/openai-compatible", "name": name,
                "options": {"baseURL": cfg["base"], "apiKey": cfg["key"]},
                "models": {m: {"name": m} for m in models_of(cfg)}}
    path = os.path.expanduser("~/.config/opencode/opencode.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conf = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                conf = json.load(f)
            if not isinstance(conf, dict):
                raise ValueError("object nahi hai")
        except ValueError as e:
            bak = path + ".toota-%d" % int(time.time())
            shutil.copy(path, bak)
            print("❌ %s kharab hai (%s). Maine kuch nahi badla, copy rakhi: %s" % (path, e, bak))
            print("   File theek karo ya hata do, phir --setup dobara chalao.")
            sys.exit(1)
    conf.setdefault("provider", {}).pop("paris", None)
    conf["provider"].update(prov)
    conf["model"] = "mumbai/mumbai"
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=2)
    os.chmod(path + ".tmp", 0o600)
    os.replace(path + ".tmp", path)
    print("OpenCode config likh di:", path)


# ================= 22.py: naya flow =================
# task -> 2 AI samjhein (Groq, DS; Gemini backup) -> user se sawal -> level 1-10 -> 3 AI plan -> Gemini merge -> kami jaanch
# -> Gemini shuru ke kadam banaye, aage JAAT/Groq/DS -> har file poori hone par 4 AI scan (Gemini sirf 2 baar)
# -> Groq har file ka chhota memory card MEMORY.md me rakhe -> aakhir me mismatch jaanch
CODE_FLOW = _env("CODE_FLOW", "1") != "0"        # 0 = purana flow
SCAN_ON_CHECK = True                             # code/android me review har @@EDIT par nahi, file poori hone par (@@CHECK / @@DONE)
EXAM_PREF = ("JAAT", "GEM", "GROQ", "GEM2", "GEM3", "DS")   # task dekhne wale (pehle 2 jo jawab dein)
PLANNER_PREF = ("DSX", "JAAT", "GROQ", "DS", "NV2", "DAI")   # plan banane wale 3
MERGER_PREF = ("DSX", "GEM", "GEM2", "GEM3", "JAAT")       # plan jodne wala: Gemini
SCAN_PREF = ("JAAT", "GROQ")                        # poori file wale scanner (2): JAAT, Groq. DS alag (bade prompt par tukdon me). NV2/DAI sirf tab jab DS bhi na ho
SCAN_BACKUP = ("NV2", "DAI")
DS_PARTS_MAX = 6                                      # DS badi file ke itne tukde tak scan karta hai
GEM_BIG_CHARS = 4000                                # Gemini sirf badi file par (ya aakhri @@DONE scan me)
QUICK_MIN_LINES, QUICK_MAX = 40, 8                 # bade @@WRITE/@@EDIT par halka Groq scan (salah ki tarah, @@CHECK nahi rokta)
GEM_PREF = ("GEM", "GEM2", "GEM3")
GEM_SCAN_MAX = 2                                    # Gemini ek kaam me bug-scan me sirf itni baar
GEM_BUILD_STEPS = 3                                 # shuru ke itne kadam Gemini banaye, phir JAAT/Groq/DS
PLAN_GAP_ROUNDS = 2
MEMO_FILE = "MEMORY.md"
CODE_WRITE_PREF = ("JAAT", "GROQ", "DS")
CODE_PLAN_PREF = ("GEM", "GEM2", "GEM3", "JAAT")
# level -> (lines kam se kam, lines zyada se zyada, files)
LEVEL_SCOPE = {1: (50, 100, 1), 2: (100, 200, 1), 3: (200, 400, 2), 4: (400, 700, 3), 5: (700, 1000, 4),
               6: (1000, 1500, 5), 7: (1500, 2500, 7), 8: (2500, 4000, 9), 9: (4000, 6000, 12), 10: (6000, 10000, 15)}


def pick_slots(prefs, n, size=0):
    """[(naam, model)] - jo provider abhi chalu aur cooldown me nahi, prefs ke order me. size diya to DS jaise chhote-limit wale bade prompt par chhod do."""
    out, now = [], time.time()
    for nm in prefs:
        if size and nm in APIS and not fits(nm, size):
            continue
        if nm in APIS and ready(nm) and COOL.get(nm, 0) <= now:
            ms = models_of(APIS[nm])
            if ms and (nm, ms[0]) not in out:
                out.append((nm, ms[0]))
        if len(out) >= n:
            break
    return out


def fits(nm, size):
    """Chhote plain proxy (DS) par bada prompt mat bhejo."""
    cfg = APIS[nm]
    if cfg["type"] == "oai":
        return True
    ok = size <= cfg.get("max_chars", 3000)
    if cfg["type"] == "proxy_get":
        ok = ok and size * 1.4 <= cfg.get("max_url", 6000)          # URL me %20/%0A se ~1.4 guna bada
    return ok


def par_calls(slots, system, user):
    """Alag-alag provider par ek saath: [text ya None]."""
    res = [None] * len(slots)

    def work(i):
        try:
            res[i] = call_slot(slots[i][0], slots[i][1], system, user)
        except Exception as e:
            log("par_calls %s: %s" % (slots[i][0], e))
    ths = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(len(slots))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return res


# ---------- 1) task ko 2 AI dekhein ----------
EXAM_SYS = ("You examine ONE task from a user of a phone coding/document assistant. Reply ONLY JSON, no prose: "
            '{"type":"light|code|android|chat","goal":"one line","level":5,"done":["3-6 finish conditions"],'
            '"questions":[{"q":"short question","options":["a","b","c"]}]}. '
            "type: android = Android app/APK; code = app, bot, website, script, tool; light = document/PDF/notes/writing/small edit/sending a message; "
            "chat = just talking or a simple question (then done=[]). "
            "level = size and difficulty from 1 to 10 (1 = tiny single file of 50-100 lines, 3 = 200-400 lines, 5 = 700-1000 lines over 4 files, "
            "8 = 2500-4000 lines, 10 = very large 6000+ lines). Light and chat tasks are level 1. "
            "Never ask about a language or platform the user already named; Cloudflare Worker means JavaScript and deploy with wrangler, not Render or Docker. "
            "A simple single-purpose bot, worker, script or small tool is level 2 or 3 (only a big multi-screen app is 4 or more). "
            "Each done item must be checkable by reading a file or running a command. "
            "questions: ONLY for code/android, max 3, ONLY choices the user did NOT already state: programming language or framework, "
            "how it will be built or run (for example GitHub Actions APK build, Termux, Docker), and one key feature or UI choice. "
            "2-4 short options each. If level is 3 or less ask at most 1 question; never ask how a link, button or message should look. For light/chat tasks questions=[]. Write in the user's language style (Hinglish ok).")
TYPE_RANK = {"android": 3, "code": 2, "light": 1, "chat": 0}


BUILD_VERBS = ("banao", "bnao", "banado", "banana", "bana", "bna", "banaiye", "banvao", "make", "create", "build", "develop", "design")
BUILD_NOUNS = ("app", "apps", "manager", "tool", "bot", "website", "site", "game", "system", "software", "program", "calculator", "editor",
               "player", "tracker", "dashboard", "api", "server", "script", "extension", "plugin", "scraper", "downloader", "launcher",
               "explorer", "browser", "clone", "application", "project", "software", "website", "chatbot")
LANG_SAID_RE = re.compile(r"python|javascript|\bjs\b|typescript|kotlin|java\b|\bc\+\+|\bc#|golang|\brust\b|php|html|flask|django|react|node|bash|\bgo\b", re.I)
RUN_SAID_RE = re.compile(r"terminal|cli\b|command|web\s?page|website|browser|android|apk|termux|telegram|desktop|gui|tkinter", re.I)


def _near(w, words):
    return w in words or bool(difflib.get_close_matches(w, words, n=1, cutoff=0.78))


def build_intent(task):
    """Typo/Hinglish bhi pakde: 'eak file mager bnao' = banane wala software kaam (light nahi)."""
    ws = re.findall(r"[a-z]{3,}", (task or "").lower())
    return any(_near(w, BUILD_VERBS) for w in ws) and any(_near(w, BUILD_NOUNS) for w in ws)


def default_questions(task):
    qs = []
    if not LANG_SAID_RE.search(task or ""):
        qs.append({"q": "Kaunsi language / framework me banau?", "options": ["Python", "JavaScript", "Kotlin (Android)", "Tum chuno"]})
    if not RUN_SAID_RE.search(task or ""):
        qs.append({"q": "Ye kahan chalega?", "options": ["Terminal (CLI)", "Web page", "Android app"]})
    return qs


def examine_task(task):
    """(dict, [providers]) ya None. 2 AI ke jawab jode: type, level, goal, done, questions."""
    user = "TASK:\n%s\n\nFILES: %s" % (task[:1500], ", ".join(list_files(40)) or "(none)")
    got = []
    first = pick_slots(EXAM_PREF, 2)
    for (nm, m), t in zip(first, par_calls(first, EXAM_SYS, user)):          # dono ek saath
        d = parse_json_reply(t) if t else None
        if isinstance(d, dict) and str(d.get("type", "")).strip().lower() in TYPE_RANK:
            got.append((nm, d))
    if not got:                                                             # dono fail: baaki ek-ek karke
        for nm, m in pick_slots(EXAM_PREF, 6):
            if STATE["cancel"] or len(got) >= 2:
                break
            if (nm, m) in first:
                continue
            t = call_slot(nm, m, EXAM_SYS, user)
            d = parse_json_reply(t) if t else None
            if isinstance(d, dict) and str(d.get("type", "")).strip().lower() in TYPE_RANK:
                got.append((nm, d))
    if not got:
        return None
    types = [str(d["type"]).strip().lower() for _, d in got]
    typ = types[0]
    if len(set(types)) > 1:                      # dono alag bole: shabdon wala andaza jo mile wahi
        tb = classify_task(task)
        typ = tb if tb in types else max(types, key=lambda x: TYPE_RANK[x])
    upg = False
    if typ in ("light", "chat") and build_intent(task):          # model ne galti se light/chat kaha: software banane wala kaam hai
        typ, upg = "code", True
    if typ == "code" and ANDROID_TASK_RE.search(task):
        typ = "android"
    lv = []
    for _, d in got:
        try:
            lv.append(max(1, min(10, int(round(float(d.get("level", 5)))))))
        except (TypeError, ValueError):
            pass
    level = int(round(sum(lv) / len(lv))) if lv else 5
    if upg:
        level = max(level, 2)
    goal = next((clip(str(d.get("goal", "")).strip(), 220) for _, d in got if str(d.get("goal", "")).strip()), "")
    done = next(([clip(str(x).strip(), 160) for x in d.get("done") if str(x).strip()][:6] for _, d in got if isinstance(d.get("done"), list) and d.get("done")), [])
    qs, seen = [], set()
    for _, d in got:
        for q in (d.get("questions") or []):
            if isinstance(q, dict) and str(q.get("q", "")).strip():
                key = re.sub(r"\W+", " ", str(q["q"]).lower()).strip()[:40]
                if key not in seen:
                    seen.add(key)
                    qs.append({"q": clip(str(q["q"]).strip(), 200), "options": [clip(str(o), 60) for o in (q.get("options") or [])][:4]})
    if typ in ("code", "android"):
        if not qs:
            qs = default_questions(task)
        if len(done) < 6:
            done = done + ["Galat input ya na-mili file par saaf error message aaye, crash na ho",
                           "Ek chhota asli test @@RUN se chale aur uska natija dikhe (sirf py_compile kaafi nahi)"][:6 - len(done)]
    return {"type": typ, "goal": goal, "level": level, "done": done, "questions": qs[:3]}, [n for n, _ in got]


# ---------- 2) 3 AI plan, Gemini jode, kami jaanch ----------
PLAN_SYS = ("You plan ONE software task as a senior engineer. Reply ONLY JSON, no prose: "
            '{"files":[{"path":"main.py","purpose":"what it does"}],"ui":["screens/pages/sections or CLI commands"],'
            '"advanced":["optional extra features, only if level is 4 or more"],'
            '"steps":["ordered build steps, ONE file or ONE feature per step, last step = verification (compile/@@VERIFY)"],'
            '"spec":["3-12 testable behaviours incl. empty or invalid input and error cases"]}. '
            "Size must match LEVEL (given lines and files): do not over-build small tasks, do not under-build big ones. "
            "Build ONLY what the TASK and the BRIEF ask for. Do NOT add deployment or hosting config, webhooks, CI, docs or other extras "
            "unless the task names them. Keep steps minimal. If the TASK says how many files to make, use exactly that many. "
            "Respect the user's answers. For an Android app step 1 is '@@TEMPLATE android <package> <AppName>' and the build is on GitHub Actions. "
            "Write short plain lines (Hinglish ok).")
MERGE_PLAN_SYS = ("You get several plans (JSON) for the SAME task. Merge them into ONE best plan: keep the SMALLEST set of files, UI, "
                  "steps and spec lines that fully does the TASK and BRIEF; drop duplicates and every extra the task did not ask for; keep the size right for LEVEL. Reply ONLY JSON with the same schema "
                  "{files,ui,advanced,steps,spec}.")
GAP_SYS = ("You check a PLAN against the TASK and BRIEF. List ONLY what is missing without which the requested thing will NOT work at all. "
           "Do NOT list deployment/hosting config, webhooks, docs, extra features, scaling or edge-case polish; stay inside the BRIEF. "
           "Each item max 12 words. Reply ONLY JSON: {\"missing\":[\"...\"]}. Use [] if nothing essential is missing (this is the normal answer).")
FIX_PLAN_SYS = ("You get a PLAN (JSON) and a MISSING list. Return the improved full plan as ONLY JSON with the same schema "
                "{files,ui,advanced,steps,spec}, with ONLY the missing items added; keep the plan small.")


def _plan_ok(d):
    return isinstance(d, dict) and isinstance(d.get("steps"), list) and len([x for x in d["steps"] if str(x).strip()]) >= 2


def make_brief(task, goal, level):
    """Chhota pakka brief jo har AI (planner, merger, checker, writer) ko dikhe: ek dimag."""
    ans = re.findall(r"\[User ke jawab: (.*?)\]", task, re.S)
    L = ["BRIEF (sab AI ke liye pakka, isse bahar mat jao):",
         "- Kaam: " + clip(goal, 200),
         "- Level %d/10: utna hi banao, extra nahi." % level]
    if ans:
        L.append("- User ke jawab: " + clip(ans[-1], 400))
    L.append("- Sirf wahi banao jo user ne maanga. Deploy/hosting config, webhook, CI, docs ya extra features tabhi jab task me likhe ho.")
    L.append("- Agar user ne 'sabhi / sabe / all' chuna ho to sabse simple EK option lo, saare nahi.")
    return "\n".join(L)


def plan_pipeline(task, goal, level, typ):
    """Plan banata hai, STATE/SPEC/PROJECT.md me rakhta hai. Text lautata hai jo AI ke pehle message me judta hai ('' = nahi bana)."""
    lo, hi, nf = LEVEL_SCOPE[level]
    brief = make_brief(task, goal, level)
    STATE["brief"] = brief
    n_plan = 1 if (level <= 3 or ready("DSX")) else (2 if level <= 6 else 3)   # DSX ho to akela DSX plan banata hai (GROQ/DS ke plan achhe nahi)          # chhota kaam = ek planner
    gap_rounds = 0 if level <= SMALL_LEVEL else (1 if level <= 6 else PLAN_GAP_ROUNDS)
    max_miss = 2 if level <= 3 else (4 if level <= 6 else 8)
    user = "%s\n\nTASK:\n%s\n\nGOAL: %s\nTYPE: %s\nLEVEL: %d/10 (about %d-%d lines, about %d files)\nEXISTING FILES: %s" % (
        brief, task[:3000], goal, typ, level, lo, hi, nf, ", ".join(list_files(40)) or "(none)")
    slots = pick_slots(PLANNER_PREF, n_plan, len(user) + len(PLAN_SYS))
    if not slots:
        return ""
    ev("note", text="🗳 plan: %s (level %d/10)" % (" + ".join(n for n, _ in slots), level))
    plans = []
    for (nm, _), t in zip(slots, par_calls(slots, PLAN_SYS, user)):
        d = parse_json_reply(t) if t else None
        if _plan_ok(d):
            plans.append((nm, d))
    if STATE["cancel"] or not plans:
        return ""
    plan = plans[0][1]
    mg = pick_slots(MERGER_PREF, 1)
    if len(plans) > 1 and mg:
        blob = "\n\n".join("PLAN %d (%s):\n%s" % (i + 1, n, json.dumps(d, ensure_ascii=False)[:4000]) for i, (n, d) in enumerate(plans))
        t = call_slot(mg[0][0], mg[0][1], MERGE_PLAN_SYS, user + "\n\n" + blob)
        d = parse_json_reply(t) if t else None
        if _plan_ok(d):
            plan = d
            ev("note", text="🧩 %s ne %d plan jod ke ek kiya" % (mg[0][0], len(plans)))
    for rnd in range(gap_rounds):                   # kuch bacha to nahi?
        if STATE["cancel"]:
            break
        ck = pick_slots(("DSX", "GROQ", "DS", "JAAT"), 4)
        ck = [(n, m) for n, m in ck if fits(n, 6000)][:1]
        if not ck:
            break
        t = call_slot(ck[0][0], ck[0][1], GAP_SYS, user + "\n\nPLAN:\n" + json.dumps(plan, ensure_ascii=False)[:5000])
        d = parse_json_reply(t) if t else None
        miss = [clip(str(x), 110) for x in ((d or {}).get("missing") or [])][:max_miss] if isinstance(d, dict) else []
        if not miss:
            break
        ev("note", text="🔎PLANCHK\n%s\n%s" % (ck[0][0], "\n".join(miss)))
        fx = pick_slots(("DSX", "JAAT", "GROQ"), 3)
        fx = [(n, m) for n, m in fx if fits(n, 9000)][:1]
        if not fx:
            break
        t = call_slot(fx[0][0], fx[0][1], FIX_PLAN_SYS, user + "\n\nPLAN:\n" + json.dumps(plan, ensure_ascii=False)[:5000] + "\n\nMISSING:\n- " + "\n- ".join(miss))
        d = parse_json_reply(t) if t else None
        if _plan_ok(d):
            plan = d
    items = [x for x in (clean_plan_item(str(l)) for l in plan.get("steps") or []) if x][:25]
    spec = [clip(str(x).strip(), 300) for x in (plan.get("spec") or []) if len(str(x).strip()) >= 8][:25]
    if len(items) < 2:
        return ""
    if len(spec) >= 3:
        save_spec(spec)
    STATE.update(plan_items=items, plan_ck=[])
    ev("plan", items=items)
    plan_pm(items, STATE["task"])
    files = [(str(f.get("path", "")).strip(), str(f.get("purpose", "")).strip()) for f in (plan.get("files") or []) if isinstance(f, dict) and str(f.get("path", "")).strip()][:25]
    ui = [clip(str(x), 120) for x in (plan.get("ui") or [])][:10]
    adv = [clip(str(x), 120) for x in (plan.get("advanced") or [])][:10]
    try:
        with open(os.path.join(WORK, "PROJECT.md"), "a", encoding="utf-8") as f:
            f.write("\n## Design (level %d/10)\nFiles:\n%s\nUI:\n%s\nAdvanced:\n%s\n" % (
                level, "\n".join("- %s: %s" % x for x in files) or "-", "\n".join("- " + x for x in ui) or "-", "\n".join("- " + x for x in adv) or "-"))
    except OSError:
        pass
    STATE["strong_n"] = GEM_BUILD_STEPS
    return ("\n" + brief + "\n[PLAN TAIYAR: %s ne plan banaya. Level %d/10 (kareeb %d-%d lines). FILES: %s. UI: %s. ADVANCED: %s. "
            "PLAN aur SPEC set ho chuke hain (PROJECT.md, SPEC.md): dobara mat banao, seedha kadam 1 se kaam shuru karo.]" % (
                ", ".join(n for n, _ in plans), level, lo, hi, "; ".join("%s (%s)" % (p, clip(w, 50)) for p, w in files) or "-",
                "; ".join(ui) or "-", "; ".join(adv) or "-"))


FAST_LANE_NOTE = ("\n[CHHOTA KAAM - FAST LANE: koi sawal ya plan nahi. Jo user ne bola usi se khud sahi andaza lagao aur andaza ek line me likh do. "
                  "Sabse kam files banao (2-3) aur SAARI files isi ek jawab me: har file ke liye alag @@WRITE (har ek 600 line tak). "
                  "Phir ASLI check chalao, andaze se 'theek hai' mat bolo: JS/Worker ho to package.json me \"type\":\"module\" rakho aur "
                  "@@RUN node --check <file> har JS file par; Cloudflare Worker ho to ek chhoti test.mjs likho jo naqli env aur naqli fetch se "
                  "fetch/scheduled handler chalaye aur @@RUN node test.mjs; Python ho to @@RUN python -m py_compile <file>. Error aaye to @@EDIT se theek karo "
                  "aur dobara chalao. Pass hone par ek @@NOTE me run/deploy ke steps likho aur @@DONE. Naya feature mat jodo jo user ne nahi maanga.]")


def do_understand(task):
    """Naya: 2 AI task dekhte hain, type+level tay, user se sawal, phir 3 AI plan."""
    if len(task.split()) < 4 and not build_intent(task):
        return ""
    forced = MODE_FORCE_RE.match(task)
    got = examine_task(task) if CODE_FLOW else None
    if not got:
        return _do_understand_orig(task)
    ex, who = got
    typ = forced.group(1).lower() if forced else ex["type"]
    goal, level, done = ex["goal"], ex["level"], (ex["done"] if typ != "chat" else [])
    if typ == "chat":
        typ = "light"
    if typ == "light":
        level = 1
    STATE["utype"], STATE["goal"], STATE["level"] = typ, goal, level
    STATE["criteria"] = ["D%d: %s" % (i + 1, x) for i, x in enumerate(done)]
    ev("understand", goal=("L%d/10 · " % level if typ != "light" else "") + goal, kind=typ, done=STATE["criteria"], prov=" + ".join(who))
    samajh = "\n[SAMAJH: %s | type=%s | level=%d/10%s]" % (goal, typ, level, (" | " + " ; ".join(STATE["criteria"])) if STATE["criteria"] else "")
    if typ not in ("code", "android"):
        return samajh
    if is_small() and not forced:
        ev("note", text="⚡ chhota kaam: sawal aur plan chhod ke seedha banata hoon")
        return samajh + FAST_LANE_NOTE
    extra = ""
    if not forced and not project_has_files():
        ans_l = []
        for q in ex["questions"][:1]:
            ans, how = ask_user(q["q"], q["options"], ctx="Task: " + task[:500])
            if how == "stop":
                return ""
            ans_l.append("%s -> %s" % (q["q"], ans))
        if ans_l:
            extra = "\n[User ke jawab: %s]" % " | ".join(ans_l)
            STATE["task"] = task + extra
    plan_txt = ""
    try:
        plan_txt = plan_pipeline(STATE["task"], goal, level, typ)
    except Exception as e:
        log("plan_pipeline galti: %s\n%s" % (e, traceback.format_exc()[-400:]))
    return extra + samajh + plan_txt


# ---------- 3) har file poori hone par 4 AI scan ----------
DS_SYS = ("You are a strict code reviewer. You get ONE PART of a file with line numbers (other parts are reviewed separately; ignore names "
          "or imports that are probably defined in other parts or files). Report ONLY real bugs in this part: syntax errors, clear logic errors, "
          "crashes, unhandled empty/invalid input. Reply ONLY a JSON array, no prose: "
          '[{"file":"path","line":N,"spec_id":"","bug":"what is wrong","fix":"exact change"}]. Reply [] if no real bug.')


def ds_chunks(rel, budget=5200):
    """File ko DS ke liye chhote tukdon me (line number saath), har tukda URL-encode ke baad budget ke andar."""
    lines = _read(rel, 60000).split("\n")
    out, cur, cur_n, start = [], [], len(urllib.parse.quote(DS_SYS)) + 80, 1
    for i, l in enumerate(lines, 1):
        row = "%d| %s" % (i, l[:300])
        n = len(urllib.parse.quote(row + "\n"))
        if cur and cur_n + n > budget:
            out.append((start, i - 1, "\n".join(cur)))
            cur, cur_n, start = [], len(urllib.parse.quote(DS_SYS)) + 80, i
        cur.append(row)
        cur_n += n
    if cur:
        out.append((start, len(lines), "\n".join(cur)))
    return out


def ds_scan(name, model, rel):
    """DS badi file ko chhote tukdon me padhta hai; sab ke bug ek JSON text me jodta hai (ya None)."""
    parts = ds_chunks(rel)
    bugs, got = [], False
    for k, (a, b, body) in enumerate(parts[:DS_PARTS_MAX]):
        if STATE["cancel"]:
            break
        t = call_slot(name, model, DS_SYS, "FILE: %s (part %d/%d, lines %d-%d)\n%s" % (rel, k + 1, len(parts), a, b, body))
        d = _norm_bugs(parse_json_reply(t), rel) if t else None
        if d is not None:
            got = True
            bugs += d
    if len(parts) > DS_PARTS_MAX:
        log("DS scan: %s ke %d me se pehle %d tukde dekhe" % (rel, len(parts), DS_PARTS_MAX))
    return json.dumps(bugs) if got else None


def scan_call(system, user, rel=""):
    """[(text, naam)] - JAAT + Groq poori file, DS usi file ke chhote tukdon me (sab ek saath). Gemini sirf badi file par ya aakhri
    @@DONE scan me, poore kaam me GEM_SCAN_MAX baar se zyada nahi (ek baar aakhri scan ke liye bachi rehti hai)."""
    size = len(system) + len(user)
    slots = pick_slots(SCAN_PREF, 2, size)
    ds = pick_slots(("DS",), 1)
    if len(slots) + len(ds) < 2:         # DS ki jagah NV2/DAI tabhi, jab JAAT/Groq/DS milkar 2 na bane
        slots += pick_slots(SCAN_BACKUP, 2 - len(slots) - len(ds), size)
    used = TRACK.get("gem_scans", 0)
    try:
        fsize = os.path.getsize(os.path.join(WORK, rel)) if rel else 0
    except OSError:
        fsize = 0
    final = bool(TRACK.get("scan_final"))
    gem = []
    if used < GEM_SCAN_MAX and (final or (fsize >= GEM_BIG_CHARS and used < GEM_SCAN_MAX - 1)):
        gem = pick_slots(GEM_PREF, 1)
        if gem:
            TRACK["gem_scans"] = used + 1
    todo = slots + ds + gem
    if not todo:
        return dual_call(system, user)
    ev("note", text="🔍 %s: scan %s%s" % (rel or "file", " + ".join(n for n, _ in todo), " (DS tukdon me)" if ds and not fits("DS", size) else ""))
    res = [None] * len(todo)

    def work(i):
        nm, m = todo[i]
        try:
            if nm == "DS" and rel and not fits("DS", size):
                res[i] = ds_scan(nm, m, rel)
            else:
                res[i] = call_slot(nm, m, system, user)
        except Exception as e:
            log("scan %s: %s" % (nm, e))
    ths = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(len(todo))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    outs = [(t, n) for (n, _), t in zip(todo, res) if t]
    return outs or dual_call(system, user)


def scan_dirty_files():
    """Jo files badli hain unka scan (har file ek baar poori), clean nikli to Groq ka memory card."""
    def _sz(r):
        try:
            return -os.path.getsize(os.path.join(WORK, r))
        except OSError:
            return 0
    dirty = sorted(TRACK.get("dirty_files") or [], key=_sz)      # sabse badi file pehle (Gemini ka hissa use mile)
    if not dirty:
        return ""
    TRACK["dirty_files"] = set()
    for rel in dirty:
        if STATE["cancel"]:
            break
        if not os.path.isfile(os.path.join(WORK, rel)):
            continue
        try:
            _review_hook_orig(rel)
        except Exception as e:
            log("scan galti %s: %s" % (rel, e))
            continue
        if not TRACK["bugs"].get(rel):
            try:
                file_memo(rel)
            except Exception as e:
                log("memo galti %s: %s" % (rel, e))
    return ""


_review_hook_orig = review_hook


QUICK_SYS = (REVIEW_SYS8 + " IMPORTANT: this file may be UNFINISHED (written in parts). Ignore names, imports or functions that are probably "
             "added in later parts or in other files. Report ONLY syntax errors, clear logic errors and crashes in the code shown. Max 5 items.")
_QUICK_SEEN = {}


def quick_scan(rel):
    """Bade @@WRITE/@@EDIT ke baad Groq (ya DS/JAAT) ka halka scan: sirf salah, @@CHECK ko nahi rokta, 'bahut der se bug' nahi."""
    if TRACK.get("quick_n", 0) >= QUICK_MAX:
        return ""
    txt = _read(rel, 60000)
    if txt.count("\n") < QUICK_MIN_LINES or _QUICK_SEEN.get(rel) == len(txt):
        return ""
    user = "SPEC:\n%s\n\n%s\n\nFILE: %s\n%s" % ("\n".join(TRACK["spec"]) or "(none)", all_files_line(), rel, _numbered(txt))
    sl = pick_slots(("GROQ", "DS", "JAAT"), 1, len(QUICK_SYS) + len(user))
    if not sl:
        return ""
    TRACK["quick_n"] = TRACK.get("quick_n", 0) + 1
    _QUICK_SEEN[rel] = len(txt)
    t = call_slot(sl[0][0], sl[0][1], QUICK_SYS + FILES_NOTE, user)
    bugs = _norm_bugs(parse_json_reply(t), rel) if t else None
    bugs = drop_phantom(bugs or [])[:5]
    if not bugs:
        return ""
    show_review(rel, False, "halka scan (%s): %d shak\n%s" % (sl[0][0], len(bugs), bug_text({rel: bugs})), sl[0][0])
    return ("[HALKA-SCAN %s] %s ne ye shak dikhaye (sirf salah, file adhoori ho to ignore; poora scan @@CHECK par hoga):\n%s"
            % (rel, sl[0][0], bug_text({rel: bugs})))


def review_hook(rel, writer=None):
    if is_small():
        return ""
    if CODE_FLOW and SCAN_ON_CHECK and cur_mode() in ("code", "android") and rel.lower().endswith(REVIEW_EXT):
        TRACK.setdefault("dirty_files", set()).add(rel)
        try:
            return quick_scan(rel)
        except Exception as e:
            log("quick_scan galti: %s" % e)
            return ""
    return _review_hook_orig(rel, writer)


# ---------- 4) memory file: har file me kya hai ----------
MEMO_SYS = ("You write a SHORT memory card for ONE source file so other files can be checked against it. Plain text, max 6 lines: "
            "line 1 = what the file is for; then its public names with signatures (functions, classes, routes, ids, exports, resource names); "
            "last line 'needs:' = what it imports or uses from OTHER project files, config or env. No code block, no prose.")


def memory_load():
    cards, cur = {}, None
    try:
        with open(os.path.join(WORK, MEMO_FILE), encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("## "):
                    cur = line[3:].strip()
                    cards[cur] = []
                elif cur is not None and line.strip():
                    cards[cur].append(line.rstrip())
    except OSError:
        pass
    return {k: "\n".join(v) for k, v in cards.items()}


def memory_text(limit=3000):
    cards = {k: v for k, v in memory_load().items() if os.path.isfile(os.path.join(WORK, k))}
    return clip("\n".join("## %s\n%s" % (k, v) for k, v in cards.items()), limit) if cards else ""


def file_memo(rel):
    txt = _read(rel, 12000)
    if not txt.strip():
        return
    user = "FILE: %s\n%s" % (rel, txt[:9000])
    card = ""
    for nm, m in pick_slots(("GROQ", "DS", "JAAT"), 3):
        if not fits(nm, len(MEMO_SYS) + len(user)):
            continue
        t = call_slot(nm, m, MEMO_SYS, user)
        if t and t.strip():
            card = re.sub(r"```\w*", "", clean_reply(t)).strip()
            break
    if not card:
        return
    cards = memory_load()
    cards[rel] = clip(card, 700)
    body = "# MEMORY (har file me kya hai; Groq likhta hai; @@DONE par cross-file mismatch jaanchne ke liye)\n\n" + "\n\n".join(
        "## %s\n%s" % (k, v) for k, v in cards.items() if os.path.isfile(os.path.join(WORK, k))) + "\n"
    with open(os.path.join(WORK, MEMO_FILE), "w", encoding="utf-8") as f:
        f.write(body)
    ev("note", text="🧠 %s ka memory card likha (Groq)" % rel)


_context_block_orig = context_block


def context_block():
    base = _context_block_orig()
    try:
        mem = memory_text(2500)
        if mem:
            base += "\nFILE MEMORY (har file me kya hai, naya code isi se milao):\n" + mem
    except Exception as e:
        log("memory block galti: %s" % e)
    return base


_final_payload_orig = _final_payload


def _final_payload(name):
    p = _final_payload_orig(name)
    mem = memory_text(4000)
    return p + ("\n\nMEMORY MAP (har file me kya hai; import/naam/signature/id/route ka mismatch jaancho):\n" + mem if mem else "")


FINAL_SYS = FINAL_SYS + " If a MEMORY MAP is given, also report any cross-file mismatch (wrong import, name, signature, resource id, route) as a bug."

_done_gate_orig = done_gate


SMALL_FINAL_MAX = int(_env("SMALL_FINAL_MAX", "2"))      # chhote kaam me @@DONE par poore project ki itni jaanch (fir jaane do)
SMALL_FINAL_SYS = ("You review a SMALL finished project, all files together, like a developer doing a last read-through before shipping. "
                   "Report ONLY real defects: syntax errors, crashes, wrong imports or names between files, config that makes it fail to start or deploy, "
                   "or logic that clearly does not do what the TASK asks. Do NOT report suggestions, extra features, style, hardening, "
                   "or things that are fine. Check each file against the others. Give the exact line from the numbered listing. "
                   'Reply with ONLY a JSON array, no prose, no code fence: [{"file":"path","line":N,"bug":"what is wrong","fix":"exact change"}]. '
                   "Reply [] if there is no real defect. Max 6 items.")


def small_final_review():
    """Chhote kaam ke liye: saari files ek saath, ek call, sirf asli bug. ([bug,...], provider) ya (None, '') agar reviewer nahi mila."""
    parts, total = [], 0
    for rel in list_files(None):
        if not rel.lower().endswith(REVIEW_EXT):
            continue
        txt = _read(rel, 30000)
        if not txt.strip() or total + len(txt) > PAYLOAD_MAX:
            continue
        total += len(txt)
        parts.append("FILE: %s\n%s" % (rel, _numbered(txt)))
    if not parts:
        return None, ""
    user = "TASK:\n%s\n\nDONE CONDITIONS:\n%s\n\n%s" % ((STATE.get("task") or "")[:2000], "\n".join(STATE.get("criteria") or []) or "(none)", "\n\n".join(parts))
    for nm, md in pick_slots(("GEM", "JAAT", "DS", "NV2", "GROQ"), 2, len(user) + len(SMALL_FINAL_SYS)):
        t = call_slot(nm, md, SMALL_FINAL_SYS, user)
        d = parse_json_reply(t) if t else None
        b = _norm_bugs(d, "")
        if b is not None:
            return drop_phantom(b), nm
    return None, ""


def done_gate():
    if is_small():
        n = TRACK.get("small_final_n", 0)
        if n >= SMALL_FINAL_MAX:
            return ""
        TRACK["small_final_n"] = n + 1
        ev("note", text="🔎 poora project ek saath padh ke aakhri jaanch (%d/%d)..." % (n + 1, SMALL_FINAL_MAX))
        bugs, prov = small_final_review()
        if not bugs:
            show_review("Poora project", True, "koi asli bug nahi mila" if bugs is not None else "reviewer nahi mila", prov)
            return ""
        show_review("Poora project", False, "%d bug\n%s" % (len(bugs), bug_text({"project": bugs})), prov)
        return ("DONE mana (aakhri jaanch %d/%d): saari files ek saath padhne par ye asli bug mile. Har bug @@EDIT se theek karo "
                "(galat lage to ek line me wajah likho), phir @@DONE:\n%s" % (n + 1, SMALL_FINAL_MAX, bug_text({"project": bugs})))
    if CODE_FLOW and SCAN_ON_CHECK:
        TRACK["scan_final"] = True
        try:
            scan_dirty_files()
        finally:
            TRACK["scan_final"] = False
        openb = {f: b for f, b in TRACK["bugs"].items() if b}
        if openb:
            return "DONE mana: reviewer ke bug khule hain, pehle inhe theek karo:\n" + bug_text(openb)
    return _done_gate_orig()


# ================= 22.py: asli AI sahayak (kam gate, zyada kaam, pehle research) =================
# LEAN=0 environment me do to purana poora gate/reviewer flow wapas. ASK_USER=1 do to sawal poochna wapas.
VERSION = "5.3"
LEAN = _env("LEAN", "1") != "0"
NEW_RULES = """
NAYE NIYAM (inhe sabse upar maano):
A. Tum asli AI sahayak ho, command-bot nahi. User ka maqsad samjho aur sawal mat poochho: tech stack, hosting, chhote faisle khud lo. User kahe "mujhe nahi pata" to faisla tumhara.
B. Naya app/bot/site banane ya library/tool/hosting chunne se pehle 1-2 baar @@SEARCH karo (abhi latest kya chal raha hai, kya free hai) aur sirf wahi chuno jo abhi sach me chal raha ho. Purani yaad par bharosa mat karo.
C. Jab tak user alag na bole, FREE service par chalne layak banao: kam dependency, ek hi service par chale, keys/token environment variable me.
D. Seedha kaam karo: bade @@WRITE (500-600 line tak) ek baar me. Wahi file baar-baar @@READ mat karo. SPEC/PLAN sirf bade kaam me, aur chhota. @@NOTE jawab dene ke liye nahi hai.
E. @@DONE ke saar me user se seedhi baat, saaf Hinglish me: kya bana, kaise chalana hai (steps), aur kahan FREE me host karein (kaun si service, kaise deploy, kya dhyan rakhna).
F. Sirf wahi banao jo user ne maanga. Ek cheez maangi to doosra project ya extra feature mat banao.
"""
if LEAN:
    READ_FIRST, REVIEW = False, False                 # file dobara padhne ki zabardasti band
    SPEC_GATE = REVIEW_LOOP = False                   # reviewer / SPEC / aakhri review: extra AI calls band
    SCAN_ON_CHECK = CODE_FLOW = False                 # 2 AI examine + 3 AI plan + 4 AI scan band
    SPLIT_ROLES = False                               # likhna chhote model se nahi: JAAT pehle, phir Gemini
    LIGHT_PREF = ("JAAT", "GEM", "GEM2", "GEM3")
    for _p in PROFILES.values():
        _p.update(spec_min=0, plan_min=0, need_spec=False, spec_review=False, check_review=False, flow=False, polish=False)
    MAX_BODY_LINES = int(_env("MAX_BODY_LINES", "600"))
    DOC_BODY_LINES = 600
    MAX_TOKENS = int(_env("MAX_TOKENS", "32000"))
    REPEAT_MAX, REPEAT_WAIT = 6, 60
    SYSTEM = SYSTEM.replace("120 line tak (.pdf/.docx/.md/.txt me 120)", "600 line tak") + NEW_RULES

_ask_user_prev, _do_understand_prev, _done_gate_prev = ask_user, do_understand, done_gate
_scan_prev, _hook_prev = scan_dirty_files, review_hook


def ask_user(question, options=None, ctx=""):
    """LEAN me user se sawal nahi: na intezaar, na 3-AI council (token bachte hain). Agent khud tay karta hai."""
    if not LEAN or _env("ASK_USER") == "1":
        return _ask_user_prev(question, options, ctx)
    ev("note", text="sawal chhod diya, khud tay kar raha hoon: " + (question or "")[:80])
    return ("User ko tech ki jaankari nahi, faisla tumhara. Sabse saada raasta lo jo FREE service par chal sake "
            "(pehle @@SEARCH se latest free options dekho), ek line @@NOTE me likho aur seedha kaam shuru karo."), "council"


DSX_PLAN = _env("DSX_PLAN", "1") != "0"                  # 0 = LEAN me DSX plan band
DSX_FINAL = _env("DSX_FINAL", "1") != "0"                # 0 = @@DONE par DSX ki aakhri jaanch band
DSX_FINAL_MAX = int(_env("DSX_FINAL_MAX", "2"))          # ek kaam me DSX itni baar poora project jaanch kar bug de sakta hai
DSX_FINAL_CHARS = int(_env("DSX_FINAL_CHARS", "1200000"))  # saari files milake itne char tak ek hi baar me (~300-400k token)


def dsx_up():
    return "DSX" in APIS and ready("DSX") and COOL.get("DSX", 0) <= time.time()


def do_understand(task):
    if LEAN:
        STATE["level"] = 0
    r = _do_understand_prev(task)
    if LEAN and DSX_PLAN and dsx_up() and len(task.split()) >= 6 and build_intent(task) and STATE.get("utype") in ("", None, "code", "android"):
        typ = STATE.get("utype") or "code"
        try:
            ev("note", text="🗳 DSX plan bana raha hai...")
            pt = plan_pipeline(STATE.get("task") or task, STATE.get("goal") or task[:200], 4, typ)
            if pt:
                r = (r or "") + pt
        except Exception as e:
            log("DSX plan galti: %s" % e)
    return r


DSX_FINAL_SYS = ("You are the final reviewer of a finished project. You get ALL source files together with line numbers, plus the TASK. "
                 "Read everything like a developer doing a last test run in your head before shipping: follow the real flow from start-up to each feature. "
                 "Report ONLY real defects: syntax errors, crashes, wrong imports/names/signatures between files, config that makes it fail to start or deploy, "
                 "missing files, and logic that clearly does not do what the TASK asks. Do NOT report style, suggestions, extra features or hardening. "
                 "Give the exact line from the numbered listing. "
                 'Reply with ONLY a JSON array, no prose, no code fence: [{"file":"path","line":N,"bug":"what is wrong","fix":"exact change"}]. '
                 "Reply [] if there is no real defect. Max 10 items, most serious first.")


def dsx_final_review():
    """DSX saari files ek saath padhta hai (ek call). ([bug,...], 'DSX') ya (None, '') agar jawab nahi aaya."""
    parts, total, skipped = [], 0, []
    for rel in list_files(None):
        if not rel.lower().endswith(REVIEW_EXT + (".json", ".md", ".txt", ".yml", ".yaml", ".sh")) and os.path.basename(rel) not in ("Dockerfile", "Procfile"):
            continue
        txt = _read(rel, 200000)
        if not txt.strip():
            continue
        if total + len(txt) > DSX_FINAL_CHARS:
            skipped.append(rel)
            continue
        total += len(txt)
        parts.append("FILE: %s\n%s" % (rel, _numbered(txt)))
    if not parts:
        return None, ""
    user = "TASK:\n%s\n\nDONE CONDITIONS:\n%s\n\n%s%s" % ((STATE.get("task") or "")[:3000], "\n".join(STATE.get("criteria") or []) or "(none)",
                                                         "\n\n".join(parts), ("\n\n(chhoti jagah ki wajah se ye files nahi dikhayi: %s)" % ", ".join(skipped)) if skipped else "")
    mdl = models_of(APIS["DSX"])[0]
    for t in range(2):
        if STATE["cancel"]:
            return None, ""
        cfg = dict(APIS["DSX"], models=[mdl], max_tokens=8192, timeout=300, total_timeout=600)
        try:
            res = ask_review("DSX", cfg, DSX_FINAL_SYS, user)
        except Exception as e:
            log("DSX final: %s" % e)
            res = None
        if res and res[0].strip():
            d = parse_json_reply(res[0])
            b = _norm_bugs(d, "")
            if b is not None:
                return drop_phantom(b), "DSX"
        if not nap(3):
            break
    return None, ""


def done_gate():
    if not LEAN:
        return _done_gate_prev()
    if DSX_FINAL and dsx_up():
        n = TRACK.get("dsx_final_n", 0)
        if n < DSX_FINAL_MAX:
            TRACK["dsx_final_n"] = n + 1
            ev("note", text="🔎 DSX poora project ek saath padh ke aakhri jaanch kar raha hai (%d/%d)..." % (n + 1, DSX_FINAL_MAX))
            bugs, prov = dsx_final_review()
            if bugs:
                show_review("DSX aakhri jaanch", False, "%d bug\n%s" % (len(bugs), bug_text({"project": bugs})), prov)
                return ("DONE mana (DSX aakhri jaanch %d/%d): saari files ek saath padhne par ye asli bug mile. Har bug @@EDIT se theek karo "
                        "(galat lage to ek line me wajah likh ke), phir @@DONE:\n%s" % (n + 1, DSX_FINAL_MAX, bug_text({"project": bugs})))
            show_review("DSX aakhri jaanch", True, "koi asli bug nahi mila" if bugs is not None else "DSX ka jawab nahi aaya", prov)
    return pdf_gate()


def scan_dirty_files():
    if not LEAN:
        return _scan_prev()


def review_hook(rel, writer=None):
    return "" if LEAN else _hook_prev(rel, writer)


_mode_note_prev = mode_note


def mode_note(short=False):
    if not LEAN:
        return _mode_note_prev(short)
    return "KAAM KA TYPE: %s - seedha kaam karo. SPEC zaroori nahi; kaam bada ho to chhota @@PLAN (3-6 bade kadam)." % cur_mode()


# ================= 24.py (5.4): DSX plan -> JAAT parallel likhai -> code-card -> chhoti jaanch -> DSX aakhri loop -> Gemini setup =================
# PIPE=0 do to 5.3 jaisa purana raasta. LEAN=0 ho to ye pipeline band hi rehti hai.
VERSION = "5.4"
PIPE = bool(LEAN) and _env("PIPE", "1") != "0"
PIPE_ROUNDS = max(1, int(_env("PIPE_ROUNDS", "5")))                 # DSX ke aakhri jaanch ke max round
PIPE_PAR = max(1, min(4, int(_env("PIPE_PAR", "3"))))               # JAAT ek saath itni files likhe
PIPE_EXACT_MAX = int(_env("PIPE_EXACT_MAX", "2"))                   # wahi bug 2+ baar aaye to DSX ko "exact fix do" hard prompt (kul itni baar)
PIPE_STEP_LINES = int(_env("PIPE_STEP_LINES", "500"))               # JAAT ek file me itni line se zyada nahi likh paata (~10k token)
PIPE_FILES_MAX = 25
PIPE_OUT = int(_env("PIPE_OUT_TOKENS", "64000"))                    # plan / bug-list / exact-fix me jawab ki upari hadd (provider ki apni hadd se zyada nahi jaata)
PLAN_PREF = ("DSX", "GEM", "GEM2", "GEM3", "JAAT")                  # plan: DSX, band ho to Gemini, phir JAAT
WRITE_PREF = ("JAAT", "GEM", "GEM2", "GEM3", "NV2")                 # likhai: JAAT, band ho to Gemini
FIX_PREF = ("JAAT", "GEM", "GEM2", "GEM3", "NV2")                   # bug theek karna: JAAT
CHECK_PREF = ("DS", "GROQ", "NV2")                                  # chhoti jaanch + "bug gaya ya nahi" confirm
FINAL_PREF = ("DSX", "GEM", "GEM2", "GEM3", "JAAT")                 # poore project ki aakhri jaanch
EXPLAIN_PREF = ("GEM", "GEM2", "GEM3", "JAAT", "NV2", "GROQ")       # end me setup samjhane wale
CHAT_PREF = ("DS", "GROQ", "NV2")                                   # sirf baat / chhota sawal
PIPE_ST = {}                                                        # chalti pipeline ki halat (disk par .mumbai/pipe.json me bhi)
PIPE_LOCK = threading.RLock()
CHECK_EXT = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".kt", ".java", ".go", ".rs", ".c", ".cpp", ".sh", ".css")
CARD_EXT = CHECK_EXT + (".json", ".toml", ".yml", ".yaml", ".gradle", ".kts", ".xml")
BAD_NEW_EXT = (".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".apk", ".jar", ".exe", ".so", ".bin", ".ico", ".mp3", ".mp4")


def set_conc():
    """JAAT par PIPE_PAR request ek saath (providers.json dobara padhne se max_conc wapas 1 ho sakta hai, isliye har phase me)."""
    if "JAAT" in APIS and int(APIS["JAAT"].get("max_conc") or 0) != PIPE_PAR:
        APIS["JAAT"]["max_conc"] = PIPE_PAR


# ---------- provider: kaun chalu hai, ek call, parallel chalana ----------
def rot_prefs(prefs):
    """Ek hi group (jaise teeno Gemini) ke members ko baari-baari pehle rakho: har kamyab call ke baad agla member pehle aata hai. Dusre providers ki jagah wahi rehti hai."""
    prefs = list(prefs)
    pos = {}
    for i, nm in enumerate(prefs):
        pos.setdefault(group_of(nm), []).append(i)
    for g, idx in pos.items():
        if len(idx) > 1:
            mem = [prefs[i] for i in idx]
            k = ROT.get(g, 0) % len(mem)
            for i, nm in zip(idx, mem[k:] + mem[:k]):
                prefs[i] = nm
    return prefs


def avail(prefs, size=0):
    """Prefs ke order me wo providers jo abhi chalu (key hai, cooldown nahi) aur jinme itna bada prompt (size char) aa sakta hai."""
    try:
        reload_conf()
    except Exception:
        pass
    now, out = time.time(), []
    for nm in rot_prefs(prefs):
        cfg = APIS.get(nm)
        if not cfg or not ready(nm) or COOL.get(nm, 0) > now or nm in out:
            continue
        if size:
            if cfg["type"] == "oai":
                if size + 1500 > ctx_limit(nm, cfg):
                    continue
            elif not fits(nm, size):
                continue
        out.append(nm)
    return out


def gen_call(nm, system, user, mt=None, tries=2):
    """(text, kata_hua?) ya None. Ek khaas provider se (ask_oai khud 429/413/530 sambhalta hai). mt = jawab ki hadd (provider ki apni hadd se zyada nahi)."""
    cfg0 = APIS.get(nm)
    if not cfg0:
        return None
    for t in range(tries):
        if STATE["cancel"]:
            return None
        cfg = dict(cfg0)
        if cfg["type"] == "oai":
            cap = int(cfg0.get("max_tokens") or MAX_TOKENS)
            cfg["max_tokens"] = min(int(mt), cap) if mt else cap
            cfg["timeout"] = max(cfg0.get("timeout", 60), 30)
            cfg["total_timeout"] = max(cfg0.get("total_timeout", 300), 120)
        try:
            res = ask_review(nm, cfg, system, user)
        except Exception as e:
            log("gen_call %s: %s" % (nm, e))
            res = None
        if res and res[0].strip():
            if is_refusal(res[0]):
                ev("note", text="↷ %s ne mana kar diya (%s)" % (nm, res[0].strip()[:50]), _log=False)
            else:
                if COOL.pop(nm, None) is not None:
                    save_cool()
                FAILS.pop(nm, None)
                rotated(nm)                                    # agli baar group ka agla member (Gemini 1 -> 2 -> 3)
                return res
        if COOL.get(nm, 0) - time.time() > 20 or t == tries - 1 or not nap(2 * (t + 1)):
            break
    return None


def par_run(fns, secs=1500):
    """fns (bina argument wale function) ek saath chalao; [result ya None] unhi ke order me. Cancel/timeout par wahin se wapas."""
    res = [None] * len(fns)
    gate = threading.Semaphore(8)

    def work(i):
        with gate:
            if STATE["cancel"]:
                return
            try:
                res[i] = fns[i]()
            except Exception as e:
                log("par_run %d: %s\n%s" % (i, e, traceback.format_exc()[-500:]))
    ths = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(len(fns))]
    for t in ths:
        t.start()
    end = time.time() + secs
    while any(t.is_alive() for t in ths):
        if STATE["cancel"] or time.time() > end:
            break
        for t in ths:
            t.join(0.3)
    return res


def quick_run(cmd, timeout=100):
    """(exit_code, output). Asli shell command (khatarnak ho to roka jata hai)."""
    why = risky(cmd)
    if why:
        return 126, "roka gaya (%s): %s" % (why, cmd[:80])
    code, out, stop = run_shell(cmd, timeout)
    if stop == "timeout":
        out += "\n(%d second me khatam nahi hua, roka gaya)" % timeout
        code = 124
    elif stop == "cancel":
        code = 130
    return (code if code is not None else 1), out


# ---------- file likhna / syntax jaanch ----------
def put_file(rel, text, who="mumbai", kind="WRITE", show=None):
    """Pipeline ke liye file likhna: undo me snapshot, chat me step dikhe."""
    p = safe(rel)
    if os.path.isdir(p):
        raise ValueError("%s folder hai" % rel)
    text = (text or "").replace("\r\n", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    snap(p)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    try:
        TRACK["seen"].add(rel)
    except Exception:
        pass
    wl("%s %s" % (kind, rel))
    ev("step", kind=kind, arg=rel[:120], out=clip(show if show is not None else text, 4000), ok=True, prov=who)
    return True


def syntax_error_of(rel, txt):
    """'' = theek. Warna chhota error text (asli compile / node --check)."""
    ext = os.path.splitext(rel)[1].lower()
    try:
        if ext == ".py":
            compile(txt, rel, "exec")
        elif ext in (".js", ".mjs", ".cjs"):
            return js_syntax_error(txt, ext)
        elif ext == ".json":
            json.loads(txt)
        elif ext in (".yml", ".yaml"):
            if re.search(r"^\t", txt, re.M):
                return "YAML me tab se indent hai (sirf space chalte hain)"
        elif ext == ".toml":
            import tomllib
            tomllib.loads(txt)
        elif ext in (".kt", ".java", ".kts", ".gradle"):
            s = _strip_lits(_nc(txt))
            for o, c in (("{", "}"), ("(", ")"), ("[", "]")):
                if s.count(o) != s.count(c):
                    return "%s aur %s barabar nahi (%d vs %d); file adhuri kati ho sakti hai" % (o, c, s.count(o), s.count(c))
    except SyntaxError as e:
        return "line %s: %s" % (e.lineno, e.msg)
    except ValueError as e:
        return str(e)[:160]
    except Exception as e:                      # tomllib.TOMLDecodeError wagairah
        return str(e)[:160]
    return ""


def _cross_file_of(msg):
    """'[CROSS] ...' message me file ka naam na ho to quote kiye naam se wo file dhundo jisme wo likha hai."""
    toks = re.findall(r"['\"`]([\w\-./]+)['\"`]", msg)
    for tk in toks:
        for f in list_files(None):
            if f.lower().endswith((".js", ".ts", ".html")) and tk in _read(f, 200000):
                return f
    return ""


def cross_bugs(rels=None):
    """cross_check (asli saboot, bina AI) ke natije ko bug-dict me badlo: [{file,line,bug,fix}]."""
    out = []
    try:
        items = cross_check(list_files(None))
    except Exception as e:
        log("cross_bugs galti: %s" % e)
        return out
    have = set(list_files(None))
    for lv, m in items:
        if lv != "E":
            continue
        m = re.sub(r"^\[CROSS\]\s*", "", m)
        mm = re.match(r"^(\S+?):(\d+)\s+(.*)$", m)
        if mm and mm.group(1) in have:
            f, ln, txt = mm.group(1), int(mm.group(2)), mm.group(3)
        else:
            tok = m.split(" ", 1)[0]
            f, ln, txt = (tok if tok in have else _cross_file_of(m)), 1, m
        if not f or (rels is not None and f not in rels):
            continue
        out.append({"file": f, "line": ln, "bug": "cross-file: " + clip(txt, 260), "fix": "naam/signature dusri file ke asli code ke hisaab se theek karo"})
    return out[:12]


def compile_check(rel):
    """(ok, output). Asli syntax jaanch + 'baaki code' placeholder + cross-file (naam/signature) jaanch."""
    try:
        txt = _read(rel, 600000)
    except Exception as e:
        return False, "file padh nahi payi: %s" % e
    out = []
    err = syntax_error_of(rel, txt)
    if err:
        out.append("%s: syntax galti: %s" % (rel, err))
    ext = os.path.splitext(rel)[1].lower()
    if ext in (".py", ".js", ".kt", ".java", ".ts", ".gradle") and LAZY_RE.search(txt):
        out.append("%s me 'baaki code' jaisa placeholder hai; file poori likho" % rel)
    if not err:
        out += [("%s:%s %s" % (b["file"], b["line"], b["bug"])) for b in cross_bugs({rel})]
    return (not out), "\n".join(out)


def compile_bug(rel, out):
    """compile_check ke output se ek bug-dict."""
    m = re.search(r"line (\d+)", out or "") or re.search(r"%s:(\d+)" % re.escape(rel), out or "")
    return {"file": rel, "line": int(m.group(1)) if m else 1, "bug": "jaanch fail: " + clip(out or "?", 400),
            "fix": "error ke hisaab se theek karo taaki syntax/import/naam sahi ho"}


def bug_lines(bl):
    return "\n".join("- line %s: %s -> %s" % (b.get("line", "?"), clip(str(b.get("bug", "")), 300), clip(str(b.get("fix", "")), 240)) for b in bl)


def pipe_path():
    return os.path.join(WORK, ".mumbai", "pipe.json")


def pipe_save():
    try:
        p = pipe_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with PIPE_LOCK:
            data = json.dumps(PIPE_ST, ensure_ascii=False)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, p)
    except (OSError, TypeError, ValueError) as e:
        log("pipe_save galti: %s" % e)


def pipe_load():
    try:
        with open(pipe_path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and isinstance(d.get("bp"), dict) and d.get("phase") else None


def pipe_pending():
    d = pipe_load()
    return d if d and d.get("phase") in ("build", "final", "end") else None


# ---------- memory card: function/class/route/id ke naam + line number code se (AI se andaza nahi) ----------
_CARD_LOCK = threading.Lock()
JS_DEF_RES = (
    re.compile(r"^\s*(export\s+(?:default\s+)?)?(async\s+)?function\s*\*?\s*(\w+)\s*\(([^)]{0,90})"),
    re.compile(r"^\s*(export\s+(?:default\s+)?)?(?:const|let|var)\s+(\w+)\s*=\s*(async\s+)?(?:function\b[^(]*)?\(?([^)=]{0,70})\)?\s*=>"),
    re.compile(r"^\s*(export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+(\w+)"),
)
JS_ROUTE_RE = re.compile(r"\b(?:app|router|server)\.(get|post|put|delete|patch|use)\(\s*['\"`]([^'\"`]{1,60})['\"`]")
ENV_RES = (re.compile(r"os\.environ(?:\.get)?[\[(]\s*['\"](\w+)['\"]"), re.compile(r"os\.getenv\(\s*['\"](\w+)['\"]"),
           re.compile(r"process\.env\.(\w+)"), re.compile(r"\benv\.([A-Z][A-Z0-9_]{2,})\b"))
KT_DEF_RE = re.compile(r"^\s*(?:(?:private|public|internal|protected|override|suspend|data|open|abstract|sealed|inline)\s+)*(fun|class|object|interface)\s+([\w<>.]+)\s*(\([^)]{0,80})?")


def _py_card(rel, txt, local):
    import ast
    names, needs = [], []
    try:
        tree = ast.parse(txt)
    except SyntaxError:
        return ["(syntax error: card nahi ban saka)"], needs
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                sig = ast.unparse(n.args)
            except Exception:
                sig = "..."
            ret = ""
            try:
                ret = " -> " + ast.unparse(n.returns) if n.returns else ""
            except Exception:
                pass
            deco = ""
            for d in n.decorator_list:
                dt = ast.unparse(d) if hasattr(ast, "unparse") else ""
                if re.search(r"route|get|post|put|delete|command|handler|on_", dt):
                    deco = " @" + clip(dt, 50)
                    break
            names.append("%sdef %s(%s)%s :%d%s" % ("async " if isinstance(n, ast.AsyncFunctionDef) else "", n.name, clip(sig, 80), ret, n.lineno, deco))
        elif isinstance(n, ast.ClassDef):
            meths = [m for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and not m.name.startswith("__")][:8]
            names.append("class %s :%d%s" % (n.name, n.lineno, (" {" + ", ".join("%s :%d" % (m.name, m.lineno) for m in meths) + "}") if meths else ""))
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and re.match(r"^[A-Z][A-Z0-9_]{1,}$", t.id):
                    names.append("%s :%d" % (t.id, n.lineno))
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            mods = [n.module or ""] if isinstance(n, ast.ImportFrom) else [a.name for a in n.names]
            for m in mods:
                if m.split(".")[0] in local and m.split(".")[0] not in needs:
                    needs.append(m.split(".")[0])
    return names, needs


def _js_card(rel, txt, local):
    names, needs = [], []
    for i, line in enumerate(txt.split("\n"), 1):
        if len(names) >= 40:
            break
        m = JS_DEF_RES[0].match(line)
        if m:
            names.append("%sfunction %s(%s) :%d" % ("export " if m.group(1) else "", m.group(3), m.group(4).strip(), i))
            continue
        m = JS_DEF_RES[1].match(line)
        if m:
            names.append("%s%s(%s) :%d" % ("export " if m.group(1) else "", m.group(2), (m.group(4) or "").strip(), i))
            continue
        m = JS_DEF_RES[2].match(line)
        if m:
            names.append("%sclass %s :%d" % ("export " if m.group(1) else "", m.group(2), i))
            continue
        for r in JS_ROUTE_RE.finditer(line):
            names.append("route %s %s :%d" % (r.group(1).upper(), r.group(2), i))
        if re.search(r"addEventListener\(\s*['\"](fetch|scheduled)['\"]|export\s+default\s*\{|module\.exports\s*=", line):
            names.append("%s :%d" % (clip(line.strip(), 70), i))
    for m in re.finditer(r"(?:from\s+|require\(\s*)['\"](\.{1,2}/[^'\"]+)['\"]", txt):
        t = m.group(1)
        if t not in needs:
            needs.append(t)
    return names, needs


def _html_card(rel, txt, local):
    ids = re.findall(r'\bid\s*=\s*["\']([\w-]+)', txt)
    names = ["ids: " + ", ".join(ids[:40])] if ids else []
    needs = [m for m in re.findall(r'<(?:script|link)[^>]+(?:src|href)\s*=\s*["\']([^"\':]+)["\']', txt)][:10]
    return names, needs


def _kt_card(rel, txt, local):
    names = []
    for i, line in enumerate(txt.split("\n"), 1):
        m = KT_DEF_RE.match(line)
        if m and len(names) < 40:
            names.append("%s %s%s :%d" % (m.group(1), m.group(2), (m.group(3) or "").strip(), i))
    return names, []


def code_card(rel, txt):
    """File ka card (text): naam+signature+line, needs, env. Code se nikalta hai, AI se nahi."""
    ext = os.path.splitext(rel)[1].lower()
    local = {os.path.splitext(os.path.basename(f))[0] for f in list_files(None) if f != rel}
    if ext == ".py":
        names, needs = _py_card(rel, txt, local)
    elif ext in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"):
        names, needs = _js_card(rel, txt, local)
    elif ext in (".html", ".htm"):
        names, needs = _html_card(rel, txt, local)
    elif ext in (".kt", ".java"):
        names, needs = _kt_card(rel, txt, local)
    elif ext == ".json":
        try:
            d = json.loads(txt)
            names = ["keys: " + ", ".join(list(d)[:20])] if isinstance(d, dict) else []
        except ValueError:
            names = []
        needs = []
    else:
        names, needs = [], []
    envs = []
    for rx in ENV_RES:
        for m in rx.finditer(txt):
            if m.group(1) not in envs:
                envs.append(m.group(1))
    lines = ["  " + n for n in names[:30]]
    if len(names) > 30:
        lines.append("  ...+%d naam aur" % (len(names) - 30))
    if needs:
        lines.append("needs: " + ", ".join(needs[:10]))
    if envs:
        lines.append("env: " + ", ".join(envs[:10]))
    lines.append("lines: %d" % (txt.count("\n") + 1))
    return "\n".join(lines)


def known_purpose(rel):
    bp = PIPE_ST.get("bp") or {}
    for f in bp.get("files") or []:
        if f.get("path") == rel and f.get("purpose"):
            return re.sub(r"\s+", " ", f["purpose"]).strip()
    old = memory_load().get(rel, "")
    m = re.match(r"purpose:\s*(.+)", old)
    return m.group(1).strip() if m else ""


def card_update(rel, ai=False):
    """MEMORY.md me is file ka card likho/badlo. Naam+line number code se; 'ye file kis kaam ki' plan se (na ho to ai=True par DS/Groq se ek line)."""
    try:
        if not rel.lower().endswith(CARD_EXT) or not os.path.isfile(safe(rel)):
            return
        txt = _read(rel, 400000)
        if not txt.strip():
            return
        body = code_card(rel, txt)
        purpose = known_purpose(rel)
        if not purpose and ai:
            try:
                purpose, _nm = small_gen("Write ONE short line (max 15 words) saying what this source file is for. Plain text only, no quotes.",
                                         "FILE: %s\n%s" % (rel, txt[:3000]), 80)
                purpose = re.sub(r"\s+", " ", purpose or "").strip()[:160]
            except Exception:
                purpose = ""
        card = ("purpose: %s\n" % purpose if purpose else "") + body
        with _CARD_LOCK:
            cards = memory_load()
            cards[rel] = clip(card, 1500)
            text = "# MEMORY (har file ka card: naam + line number code se nikale; asli code isi line number se padho)\n\n" + "\n\n".join(
                "## %s\n%s" % (k, v) for k, v in cards.items() if os.path.isfile(os.path.join(WORK, k))) + "\n"
            p = os.path.join(WORK, MEMO_FILE)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, p)
    except Exception as e:
        log("card_update galti %s: %s" % (rel, e))


# ---------- 2) DSX ka plan (blueprint) ----------
PLAN_SYS_PIPE = (
    "You are the lead architect of a software project. The files will be written by OTHER AI writers: each writer writes ONE file per request and several writers work at the same time, "
    "they cannot see each other's files. Reply with ONLY one JSON object, no prose, no code fence: "
    '{"summary":"one line: what is built","stack":"language/framework and where it runs","notes":"shared rules: naming, data formats, constants, ids, keys that more than one file uses",'
    '"files":[{"path":"main.py","purpose":"what this file does","group":1,"max_lines":300,"exports":["name(arg1, arg2) -> return type : meaning"],"imports":["other.py: nameA, nameB"]}],'
    '"commands":{"setup":["..."],"run":["..."],"deploy":["..."],"test":["..."]},"env":["NAME: where to get it"],"spec":["checkable behaviour"]}. '
    "RULES: "
    "1) Use the SMALLEST set of files that fully does the TASK. No extras the task did not ask for (no docs, CI or Docker unless the task needs them). Keep the user's own file names/folders if the task names them. "
    "2) One writer produces at most MAXL lines per file (about 10k tokens), so every file must stay at or below MAXL lines (set a realistic max_lines per file); split bigger features into modules. "
    "3) CONTRACT: for EVERY file list exports with exact names, parameter lists and return shape, and imports with the exact names it takes from other project files. "
    "Everything two files share (function names, route paths, element ids, JSON keys, storage keys, env variable names, message formats) must be fixed HERE (use notes for shared formats), because the writers cannot talk. "
    "4) group = 1,2,3...: files of the same group are written at the same time, so they must NOT import each other. A file that imports another file goes into a LATER group than that file. "
    "Group 1 = config, constants, manifests (package.json, requirements.txt, wrangler.toml ...). 2-3 files per group is ideal. "
    "5) Include every dependency/manifest file the project needs to start (requirements.txt, package.json with \"type\":\"module\" for ES modules, wrangler.toml, Procfile ...). "
    "6) commands: exact shell commands. setup = install, run = start, deploy = deploy to a FREE hosting that works today (keys and tokens only as environment variables), "
    "test = ONLY real checks that run offline (python -m py_compile f.py, node --check f.js, pytest, node test.mjs); if you list a test file command, that test file must be in files. "
    "7) env: every secret/environment variable and where to get it. 8) spec: 4-8 behaviours checkable by reading or running, including empty/invalid input and errors. "
    "Strings short and plain; identifiers in English; purpose may be Hinglish.")


def norm_path(p):
    p = str(p or "").strip().strip("`'\"").replace("\\", "/")
    p = re.sub(r"^\./+", "", p)
    if not p or p.startswith("/") or ".." in p.split("/") or len(p) > 120 or re.search(r"[\s<>|*?:]", p):
        return ""
    return p


def _lst(x, n, w):
    if isinstance(x, str):
        x = [x]
    return [clip(str(v).strip(), w) for v in (x or []) if str(v).strip()][:n]


def _dep_paths(f, files):
    """f ke imports me jin doosri project files ka zikr hai unke path (sirf ':' se pehle ka module hissa dekhta hai)."""
    out = []
    for imp in f.get("imports") or []:
        mod = str(imp).split(":", 1)[0]
        for g in files:
            if g["path"] == f["path"] or g["path"] in out:
                continue
            stem = os.path.splitext(os.path.basename(g["path"]))[0]
            if re.search(r"(?<![\w.-])(?:%s|%s)(?![\w-])" % (re.escape(g["path"]), re.escape(stem)), mod):
                out.append(g["path"])
    return out


def norm_bp(d):
    """DSX ke JSON ko pakka dhaancha do (galat path, group ka dependency order, hadd). None = kaam ka plan nahi."""
    if not isinstance(d, dict) or not isinstance(d.get("files"), list):
        return None
    files, seen = [], set()
    for f in d["files"]:
        if not isinstance(f, dict):
            continue
        p = norm_path(f.get("path"))
        if not p or p in seen or p.lower().endswith(BAD_NEW_EXT) or p in ("MEMORY.md", "PROJECT.md", "SPEC.md", "SETUP.md"):
            continue
        try:
            g = max(1, min(9, int(float(f.get("group") or 1))))
        except (TypeError, ValueError):
            g = 1
        try:
            ml = max(20, min(PIPE_STEP_LINES, int(float(f.get("max_lines") or 300))))
        except (TypeError, ValueError):
            ml = 300
        seen.add(p)
        files.append({"path": p, "purpose": clip(str(f.get("purpose") or "").strip(), 220), "group": g, "max_lines": ml,
                      "exports": _lst(f.get("exports"), 25, 170), "imports": _lst(f.get("imports"), 15, 170)})
        if len(files) >= PIPE_FILES_MAX:
            break
    if not files:
        return None
    byp = {f["path"]: f for f in files}
    deps = {f["path"]: _dep_paths(f, files) for f in files}
    grp = {}

    def level(p, stack=()):
        if p in grp:
            return grp[p]
        lv = byp[p]["group"]
        for dp in deps[p]:
            if dp not in stack and dp != p:
                lv = max(lv, level(dp, stack + (p,)) + 1)
        grp[p] = lv
        return lv
    for f in files:
        level(f["path"])
    order = sorted(set(grp.values()))
    for f in files:
        f["group"] = order.index(grp[f["path"]]) + 1
    groups = [[f["path"] for f in files if f["group"] == g] for g in range(1, len(order) + 1)]
    cm = d.get("commands") if isinstance(d.get("commands"), dict) else {}
    return {"summary": clip(str(d.get("summary") or "").strip(), 220), "stack": clip(str(d.get("stack") or "").strip(), 180),
            "notes": clip(str(d.get("notes") or "").strip(), 1200), "files": files, "groups": groups,
            "commands": {k: _lst(cm.get(k), 8, 220) for k in ("setup", "run", "deploy", "test")},
            "env": _lst(d.get("env"), 12, 200), "spec": [s for s in _lst(d.get("spec"), 10, 300) if len(s) >= 8]}


_norm_bp_5x = norm_bp


def norm_bp(d):
    """5.4: har file par action: 'modify' (file pehle se hai) ya 'new'. Faisla disk dekh ke hota hai, DSX ke kehne par nahi."""
    bp = _norm_bp_5x(d)
    if not bp:
        return bp
    ch = {}
    for f in (d.get("files") or []):
        if isinstance(f, dict):
            ch[norm_path(f.get("path"))] = clip(str(f.get("change") or "").strip(), 400)
    for f in bp["files"]:
        ex = os.path.isfile(os.path.join(WORK, f["path"]))
        f["action"] = "modify" if ex else "new"
        f["change"] = ch.get(f["path"]) or (f["purpose"] if ex else "")
    return bp


def bp_file(bp, rel):
    return next((f for f in bp["files"] if f["path"] == rel), None)


def contract_text(bp, limit=9000):
    L = []
    for f in bp["files"]:
        L.append("[group %d] %s%s - %s (max %d line)" % (f["group"], f["path"], (" [EXISTING FILE, CHANGE: %s]" % f.get("change")) if f.get("action") == "modify" else "", f["purpose"] or "-", f["max_lines"]))
        if f["exports"]:
            L.append("    exports: " + " | ".join(f["exports"]))
        if f["imports"]:
            L.append("    imports: " + " | ".join(f["imports"]))
    return clip("\n".join(L), limit)


def bp_plan_items(bp):
    items = ["Group %d: %s likho (JAAT, ek saath)" % (i + 1, ", ".join(g)) for i, g in enumerate(bp["groups"])]
    items.append("Aakhri jaanch: DSX poora project padhe, bug JAAT theek kare (max %d round)" % PIPE_ROUNDS)
    items.append("Setup samjhana (Gemini): kya bana, kaise chalana, kahan free host")
    return items


def bp_md(bp):
    L = ["", "## Plan (DSX)", "Kaam: " + (bp["summary"] or "-"), "Stack: " + (bp["stack"] or "-")]
    if bp["notes"]:
        L.append("Shared niyam: " + bp["notes"])
    L.append("Files:")
    L += ["- %s (group %d)%s: %s" % (f["path"], f["group"], " [purani file, badlegi]" if f.get("action") == "modify" else "", f["purpose"] or "-") for f in bp["files"]]
    for k in ("setup", "run", "deploy", "test"):
        if bp["commands"].get(k):
            L.append("%s: %s" % (k, " ; ".join(bp["commands"][k])))
    if bp["env"]:
        L.append("Env: " + " ; ".join(bp["env"]))
    return L


PLAN_EXISTING_RULES = (
    " EXISTING PROJECT: the project folder already has files (listed in EXISTING FILES with their real names). Rules for this case: "
    "A) List in files ONLY files that are NEW or MUST CHANGE. Do not list existing files that stay as they are. "
    "B) For an existing file that must change, use its exact existing path and add \"change\":\"exactly what to add/change in it\"; never rewrite it from scratch. "
    "C) exports/imports must use the REAL existing names (see the cards/outline) plus the new names you add. "
    "D) Keep the existing setup/run commands unless the task changes them. Do not delete or rename existing files.")


def existing_block():
    """Plan ke liye purane project ki jaankari: file + line, aur cards (nahi to outline)."""
    if not project_has_files():
        return "(none)"
    names = [f for f in list_files(80) if f.split(os.sep, 1)[0] != "uploads"]
    parts = []
    for f in names:
        n = ""
        if f.lower().endswith(CARD_EXT):
            try:
                n = "(%dL)" % (_read(f, 400000).count("\n") + 1)
            except Exception:
                n = ""
        parts.append(f + n)
    cards = memory_text(3500)
    if not cards:
        try:
            cards = outline(limit=3500)
        except Exception:
            cards = ""
    return ", ".join(parts) + (("\n\nCARDS/OUTLINE of the existing code (real names, line numbers):\n" + cards) if cards else "")


def make_blueprint(task):
    """(bp, provider) ya (None, ''). DSX plan banata hai; band/fail ho to Gemini, phir JAAT."""
    user = ("TASK:\n%s\n\nEXISTING FILES: %s\n\nHARD LIMITS: one writer writes at most %d lines per file. "
            "The user is not technical: choose the simplest stack that runs on a FREE service, secrets only as environment variables." % (
                (PIPE_ST.get("task") or task)[:4000], existing_block(), PIPE_STEP_LINES))
    sysmsg = PLAN_SYS_PIPE.replace("MAXL", str(PIPE_STEP_LINES)) + (PLAN_EXISTING_RULES if project_has_files() else "")
    for nm in rot_prefs(PLAN_PREF):
        if STATE["cancel"]:
            return None, ""
        if not avail((nm,), len(sysmsg) + len(user)):
            continue
        ev("note", text="🗳 %s plan bana raha hai (har file ka naam, kaam, naam-signature, group, commands)..." % nm)
        for t in range(2):
            if nm == "DSX":
                PIPE_ST["dsx_calls"] = PIPE_ST.get("dsx_calls", 0) + 1
            u = user if not t else user + "\n\n[Pichhla jawab sahi JSON nahi tha. Sirf ek JSON object do, files me kam se kam 1 file.]"
            res = gen_call(nm, sysmsg, u, mt=PIPE_OUT)
            if not res:
                break
            bp = norm_bp(parse_json_reply(res[0]))
            if bp:
                return bp, nm
    return None, ""


# ---------- 3) JAAT parallel likhai: ek group ki files alag-alag request me, ek saath ----------
WRITE_SYS = (
    "You write ONE source file of a multi-file project exactly as the PLAN/CONTRACT says. Other files are written by other writers at the same time, so use ONLY the names, "
    "signatures, routes, ids, keys and environment variables that the CONTRACT gives (and the real code/cards of ALREADY WRITTEN files, which are the truth). Never invent a different name "
    "for something the contract defines, and never import a name the contract does not list. Write the COMPLETE, working file: no placeholders, no 'rest of the code', no TODO stubs, "
    "real error handling for empty/invalid input. At most MAXL lines. Reply with ONLY the file content inside ONE fenced code block, nothing before or after it.")
CONT_SYS = (
    "A source file was cut off at the output limit. You get the last lines written so far. Continue EXACTLY with the next line and finish the file. "
    "Do not repeat any line already written. Reply with ONLY the remaining lines inside ONE fenced code block.")


def extract_code(reply, rel="", cut=False):
    """(file ka text, band-fence-mila?). Fence ho to uske andar ka, na ho to poora jawab."""
    t = clean_reply(reply or "").replace("\r\n", "\n")
    s = t.find("```")
    if s == -1:
        return t.strip("\n"), True
    nl = t.find("\n", s)
    if nl == -1:
        return "", False
    body = t[nl + 1:]
    ends = list(re.finditer(r"^```[ \t]*$", body, re.M))
    if not ends:
        return body.strip("\n"), False
    md = os.path.splitext(rel)[1].lower() in (".md", ".markdown", ".txt", ".rst")
    m = ends[-1] if (md or cut) else ends[0]
    if cut and m.end() < len(body.rstrip()) - 3 and not md:
        m = ends[0]
    return body[:m.start()].strip("\n"), True


def join_cont(code, more):
    """Aage ka hissa jodo; agar naye hisse ne pichhli kuch lines dohrayi to unhe hata do."""
    a, b = code.rstrip("\n").split("\n"), more.strip("\n").split("\n")
    for k in range(min(12, len(a), len(b)), 0, -1):
        if a[-k:] == b[:k]:
            b = b[k:]
            break
    return "\n".join(a + b)


def write_user(bp, f):
    deps = _dep_paths(f, bp["files"])
    real, used = [], 0
    for d in deps:
        if os.path.isfile(os.path.join(WORK, d)):
            t = _read(d, 60000)
            if used + len(t) <= 30000:
                real.append("=== %s (REAL CODE, already written) ===\n%s" % (d, t))
                used += len(t)
    cards = memory_text(4000)
    return "\n\n".join(x for x in [
        "TASK (user ka kaam):\n" + (PIPE_ST.get("task") or "")[:3000],
        "PROJECT: %s\nSTACK: %s\nSHARED RULES: %s\nENV VARIABLES: %s" % (bp["summary"], bp["stack"], bp["notes"] or "-", " ; ".join(bp["env"]) or "-"),
        "CONTRACT (saari files):\n" + contract_text(bp, 9000),
        ("ALREADY WRITTEN - jin files se ye file kuch import karti hai:\n" + "\n\n".join(real)) if real else "",
        ("CARDS of written files (real names + line numbers):\n" + cards) if cards else "",
        "YOUR FILE: %s\nPURPOSE: %s\nMUST EXPORT: %s\nMAY IMPORT: %s\nLIMIT: at most %d lines." % (
            f["path"], f["purpose"] or "-", " | ".join(f["exports"]) or "-", " | ".join(f["imports"]) or "-", f["max_lines"]),
    ] if x)


def _pst_add(key, val):
    with PIPE_LOCK:
        lst = PIPE_ST.setdefault(key, [])
        if val not in lst:
            lst.append(val)


def write_job(f):
    """Ek file likhwao (JAAT, band ho to Gemini), compile+cross jaanch, bug ho to theek, card. {'rel','ok','who','out'}."""
    rel, bp = f["path"], PIPE_ST["bp"]
    sysmsg = WRITE_SYS.replace("MAXL", str(f.get("max_lines") or 300))
    user = write_user(bp, f)
    names = avail(WRITE_PREF, len(sysmsg) + len(user))
    for _ in range(12):                                   # koi writer abhi cooldown me: thoda ruk ke dekho (cancel par turant nikal)
        if names or STATE["cancel"]:
            break
        if not nap(5):
            break
        names = avail(WRITE_PREF, len(sysmsg) + len(user))
    for nm in names:
        if STATE["cancel"]:
            break
        ev("note", text="✍ %s: %s likh raha hai (%d line tak)" % (nm, rel, f.get("max_lines") or 300), _log=False)
        res = gen_call(nm, sysmsg, user)
        if not res:
            continue
        code, closed = extract_code(res[0], rel, res[1])
        n_cont = 0
        while res[1] and n_cont < 2 and not STATE["cancel"]:          # jawab limit par kata: aage ka hissa maango
            n_cont += 1
            tail = "\n".join(code.split("\n")[-70:])
            cres = gen_call(nm, CONT_SYS, "FILE: %s\nPLAN: %s\n\nLAST LINES WRITTEN SO FAR:\n%s\n\nContinue with the NEXT line." % (rel, f["purpose"], tail))
            if not cres:
                break
            more, _c = extract_code(cres[0], rel, cres[1])
            code = join_cont(code, more)
            res = cres
        if len(code.strip()) < 5 or is_refusal(code):
            continue
        put_file(rel, code, nm)
        _pst_add("written", rel)
        ok, out = compile_check(rel)
        for _t in range(2):
            if ok or STATE["cancel"]:
                break
            if not fix_file(rel, [compile_bug(rel, out)]):
                break
            ok, out = compile_check(rel)
        card_update(rel, ai=False)
        if not ok:
            _pst_add("warn", "%s: jaanch abhi bhi fail: %s" % (rel, clip(out.replace("\n", " "), 110)))
        return {"rel": rel, "ok": ok, "who": nm, "out": out}
    _pst_add("failed", rel)
    ev("note", text="⚠ %s koi AI se nahi likhi ja saki (aakhir me dobara koshish hogi)" % rel)
    return {"rel": rel, "ok": False, "who": "", "out": "koi AI ne file nahi di"}


# ---------- purani file me badlav (naya feature): poori file dobara nahi, fix_file ka edit/full tareeka ----------
_write_job_5x = write_job


def modify_job(f):
    """Purani file badlo (JAAT, band ho to Gemini). {'rel','ok','who','out'} write_job jaisa."""
    rel = f["path"]
    change = f.get("change") or f.get("purpose") or "task ke hisaab se badlo"
    bug = {"file": rel, "line": 1, "bug": "REQUESTED CHANGE (not a bug): " + change,
           "fix": "implement it fully; the names other files will use are in the CONTRACT"}
    extra = ("THIS IS A FEATURE CHANGE, NOT A BUG FIX. TASK: %s\nImplement the change completely in this file, keep every existing behaviour "
             "that the change does not touch, and use exactly the names the CONTRACT lists for new/changed code." % (PIPE_ST.get("task") or "")[:1500])
    ev("note", text="✍ %s: purani file me badlav (%s)" % (rel, clip(change, 80)), _log=False)
    if not fix_file(rel, [bug], extra):
        _pst_add("failed", rel)
        _pst_add("warn", "%s me badlav nahi ho paya" % rel)
        ev("note", text="⚠ %s me badlav nahi ho paya (aakhri jaanch me dobara dekha jayega)" % rel)
        return {"rel": rel, "ok": False, "who": "", "out": "badlav nahi hua"}
    _pst_add("written", rel)
    ok, out = compile_check(rel)
    for _t in range(2):
        if ok or STATE["cancel"]:
            break
        if not fix_file(rel, [compile_bug(rel, out)]):
            break
        ok, out = compile_check(rel)
    card_update(rel, ai=False)
    if not ok:
        _pst_add("warn", "%s: jaanch abhi bhi fail: %s" % (rel, clip(out.replace("\n", " "), 110)))
    return {"rel": rel, "ok": ok, "who": "JAAT", "out": out}


def write_job(f):
    if f.get("action") == "modify" and os.path.isfile(os.path.join(WORK, f["path"])):
        return modify_job(f)
    return _write_job_5x(f)


# ---------- bug theek karna (JAAT) ----------
FIX_SYS = (
    "You fix real bugs in ONE file of a multi-file project. You get the PLAN/CONTRACT, CARDS of other files (their real names), the BUGS and the file with line numbers. "
    "Fix every listed bug at its real root cause and keep everything else unchanged. Names other files rely on must stay as in the CONTRACT/cards. "
    "Reply with ONLY edit blocks, one or more, exactly in this format:\n<<<<<<< SEARCH\n<old lines copied EXACTLY from the file, without line numbers>\n=======\n<new lines>\n>>>>>>> REPLACE\n"
    "SEARCH must be unique in the file and match character for character (indent too). No other text.")
FIX_FULL_SYS = (
    "You fix real bugs in ONE file of a multi-file project. You get the PLAN/CONTRACT, CARDS of other files (their real names), the BUGS and the file with line numbers. "
    "Fix every listed bug at its real root cause and keep everything else unchanged. Names other files rely on must stay as in the CONTRACT/cards. "
    "Reply with ONLY the complete corrected file (without line numbers) inside ONE fenced code block.")


def fix_file(rel, bugs, extra=""):
    """JAAT (band ho to Gemini) se bug theek karwa ke file me lagao. True = file badli."""
    if STATE["cancel"] or not bugs:
        return False
    old = _read(rel, 400000)
    nlines = old.count("\n") + 1
    bp = PIPE_ST.get("bp")
    user = "%sCARDS (real names of other files):\n%s\n\n%sBUGS TO FIX in %s:\n%s\n\nFILE %s (with line numbers):\n%s" % (
        ("PLAN/CONTRACT:\n%s\n\n" % contract_text(bp, 5000)) if bp else "", memory_text(3500) or "-", (extra + "\n\n") if extra else "",
        rel, bug_lines(bugs), rel, _numbered(old))
    strict = os.path.splitext(rel)[1].lower() in INDENT_EXT
    old_err = syntax_error_of(rel, old)
    modes = ("full",) if nlines <= 120 else ("edit", "full")
    tried = 0
    for nm in avail(FIX_PREF, len(user) + 1500):
        if STATE["cancel"] or tried >= 2:                      # JAAT + ek backup se zyada nahi (Gemini ka limit jaldi khatam hota hai)
            return False
        tried += 1
        for mode in modes:
            if mode == "full" and nlines > PIPE_STEP_LINES * 1.2:
                continue                                       # bahut badi file poori dobara nahi likhwate
            res = gen_call(nm, FIX_FULL_SYS if mode == "full" else FIX_SYS, user, mt=None if mode == "full" else 6000)
            if not res:
                break                                          # is provider se nahi, agla
            new, shown = None, res[0]
            if mode == "edit":
                new, err = apply_edit(old, clean_reply(res[0]), strict)
                if err:
                    res2 = gen_call(nm, FIX_SYS, user + "\n\n[Pichhla jawab lagu nahi hua: %s. SEARCH me file ka asli text hu-ba-hu (indent samet) copy karo.]" % err, mt=6000)
                    new, err = apply_edit(old, clean_reply(res2[0]), strict) if res2 else (None, "jawab nahi aaya")
                    shown = res2[0] if res2 else shown
                    if err:
                        new = None
            else:
                code, closed = extract_code(res[0], rel, res[1])
                if code.strip() and not (res[1] and not closed) and not (nlines > 40 and code.count("\n") + 1 < 0.6 * nlines):
                    new = code
            if new is None:
                continue
            if new.replace("\r\n", "\n").rstrip("\n") == old.replace("\r\n", "\n").rstrip("\n"):
                continue
            if syntax_error_of(rel, new) and not old_err:
                ev("note", text="↷ %s ka fix naya syntax error laga raha tha, chhod diya" % rel, _log=False)
                continue
            put_file(rel, new, nm, kind="EDIT" if mode == "edit" else "WRITE",
                     show=("badlav (%d bug):\n%s" % (len(bugs), clip(shown, 2800))) if mode == "edit" else None)
            return True
    return False


# ---------- chhoti jaanch: DS / Groq / NV2 alag-alag file par (DSX ko nahi) ----------
CHK_SYS = (
    "You check ONE freshly written file of a multi-file project against the PLAN/CONTRACT. Report ONLY real defects in THIS file: syntax errors, crashes, "
    "a name/signature/route/id/key that differs from the CONTRACT or from the real names in the CARDS of other files, exports the contract promises but the file lacks, "
    "leftover placeholders/TODO, clear logic errors. Do NOT report style, extras or things that are fine. "
    'Reply with ONLY a JSON array, no prose: [{"file":"path","line":N,"bug":"what is wrong","fix":"exact change"}]. Reply [] if no real defect. Max 5 items.')
CONF_SYS = (
    "You verify ONE reported bug in a source file. Decide whether the bug is STILL PRESENT in the code shown. Be strict and literal: read the exact lines. "
    'Reply with ONLY JSON: {"fixed":true or false,"why":"short reason"}. fixed=true only if the problem described no longer exists in the code shown.')


def _plain_ok(nm, system, user):
    """DS/DAI jaise chhote proxy par prompt (URL-encode ke baad bhi) limit me aata hai?"""
    cfg = APIS[nm]
    if cfg["type"] == "oai":
        return len(system) + len(user) + 1500 <= ctx_limit(nm, cfg)
    text = system + "\n\n" + user
    if len(text) > cfg.get("max_chars", 3000):
        return False
    return cfg["type"] != "proxy_get" or len(urllib.parse.quote(text, safe="")) <= cfg.get("max_url", 6000)


def _window(txt, ln, budget):
    """Line number samet file; badi ho to bug ki line ke aas-paas ka hissa."""
    rows = ["%d| %s" % (i, l[:240]) for i, l in enumerate(txt.split("\n"), 1)]
    full = "\n".join(rows)
    if len(full) <= budget:
        return full
    c = max(1, min(len(rows), ln or 1))
    lo = hi = c - 1
    used = len(rows[lo]) + 1
    while True:
        grew = False
        if lo > 0 and used + len(rows[lo - 1]) + 1 <= budget:
            lo -= 1
            used += len(rows[lo]) + 1
            grew = True
        if hi < len(rows) - 1 and used + len(rows[hi + 1]) + 1 <= budget:
            hi += 1
            used += len(rows[hi]) + 1
            grew = True
        if not grew:
            break
    return "(file ka hissa: line %d se %d, kul %d line)\n" % (lo + 1, hi + 1, len(rows)) + "\n".join(rows[lo:hi + 1])


_CHK_ROT = [0]                      # DS/Groq/NV2 me baari-baari (har file par alag checker)


def small_check_job(rel, idx):
    """(bug-list, provider) ya (None, ''). DS/Groq/NV2 me se baari-baari; badi file DS ko tukdon me."""
    txt = _read(rel, 100000)
    if not txt.strip() or not rel.lower().endswith(CHECK_EXT):
        return None, ""
    bp = PIPE_ST["bp"]
    user = "PLAN/CONTRACT:\n%s\n\nCARDS (real names of the other files):\n%s\n\nFILE: %s\n%s" % (
        contract_text(bp, 3500), memory_text(2500) or "-", rel, _numbered(txt))
    cands = [n for n in CHECK_PREF if avail((n,))]
    with PIPE_LOCK:
        _CHK_ROT[0] += 1
        k = _CHK_ROT[0] + idx
    cands = (cands[k % len(cands):] + cands[:k % len(cands)]) if cands else []
    for nm in cands:
        if STATE["cancel"]:
            return None, ""
        t = None
        if _plain_ok(nm, CHK_SYS, user):
            r = gen_call(nm, CHK_SYS, user, mt=2500)
            t = r[0] if r else None
        elif nm == "DS":
            m = models_of(APIS["DS"])
            t = ds_scan("DS", m[0], rel) if m else None       # badi file: chhote tukdon me
        d = _norm_bugs(parse_json_reply(t), rel) if t else None
        if d is not None:
            return drop_phantom(d)[:5], nm
    return None, ""


def confirm_fixed(rel, bug, first=""):
    """DS/Groq/NV2 se poochho: ye bug sach me gaya? True/False, koi jawab na de to None."""
    txt = _read(rel, 200000)
    if not txt.strip():
        return None
    ln = int(bug["line"]) if str(bug.get("line", "")).isdigit() else 0
    bt = "BUG: %s\nSUGGESTED FIX: %s" % (clip(str(bug.get("bug", "")), 500), clip(str(bug.get("fix", "")), 400))
    order_ = ([first] if first in CHECK_PREF else []) + [n for n in CHECK_PREF if n != first]
    for nm in order_:
        if STATE["cancel"]:
            return None
        if not avail((nm,)):
            continue
        cfg = APIS[nm]
        budget = (ctx_limit(nm, cfg) - 4000) if cfg["type"] == "oai" else int(min(cfg.get("max_chars", 3000), cfg.get("max_url", 6000) / 2.2)) - len(CONF_SYS) - len(bt) - 200
        budget = max(1200, min(budget, 30000))
        user = ""
        for _ in range(6):
            user = "FILE: %s\n%s\n\n%s" % (rel, _window(txt, ln, budget), bt)
            if _plain_ok(nm, CONF_SYS, user):
                break
            budget = int(budget * 0.7)
        else:
            continue
        r = gen_call(nm, CONF_SYS, user, mt=300)
        d = parse_json_reply(r[0]) if r else None
        if isinstance(d, dict) and "fixed" in d:
            v = d["fixed"]
            if isinstance(v, str):
                v = v.strip().lower() in ("true", "yes", "haan")
            return bool(v)
    return None


def group_check(paths):
    """Group ki files ki chhoti jaanch (alag-alag file par DS/Groq/NV2 ek saath + bina-AI cross-check); bug mile to JAAT theek kare."""
    todo = [p for p in paths if os.path.isfile(os.path.join(WORK, p)) and p.lower().endswith(CHECK_EXT)]
    if not todo:
        return
    ev("note", text="🔍 chhoti jaanch (DS/Groq/NV2, alag-alag file): %s" % ", ".join(todo))
    chk = [n for n in CHECK_PREF if avail((n,))] or list(CHECK_PREF)

    def job(rel, i):
        bugs, who = small_check_job(rel, i)
        cb = cross_bugs({rel})
        allb = list(bugs or [])
        for c in cb:
            if not any(str(c["line"]) == str(b.get("line")) and c["bug"][:40] == str(b.get("bug", ""))[:40] for b in allb):
                allb.append(c)
        if bugs is None and not cb:
            return None
        show_review("Chhoti jaanch: %s" % rel, not allb, ("%d bug\n%s" % (len(allb), bug_text({rel: allb}))) if allb else "koi asli bug nahi mila", who or "mumbai")
        if not allb or STATE["cancel"]:
            return None
        return fix_job(rel, allb[:6], "", chk[(i + 1) % len(chk)])
    par_run([(lambda r=r, i=i: job(r, i)) for i, r in enumerate(todo)], 1800)


def ensure_missing(bugs, rnd=0):
    """DSX ne jis file ko 'nahi hai' kaha aur wo disk par nahi: JAAT se banwao. Banayi gayi files ka set lautata hai."""
    bp = PIPE_ST["bp"]
    need = {}
    for b in bugs or []:
        rel = norm_path(b.get("file"))
        if not rel or rel in need or len(need) >= 4 or rel.lower().endswith(BAD_NEW_EXT) or os.path.isfile(os.path.join(WORK, rel)):
            continue
        try:
            safe(rel)
        except ValueError:
            continue
        need[rel] = bp_file(bp, rel) or {"path": rel, "purpose": clip("%s | %s" % (b.get("bug", ""), b.get("fix", "")), 240), "exports": [],
                                          "imports": [], "max_lines": 200, "group": 99}
    if not need or STATE["cancel"]:
        return set()
    set_conc()
    ev("note", text="📄 DSX ne jo file nahi hai bataya, JAAT banata hai: %s" % ", ".join(need))
    par_run([(lambda f=f: write_job(f)) for f in need.values()], 2400)
    return {r for r in need if os.path.isfile(os.path.join(WORK, r))}


# ---------- build phase: group-group, har group ki files ek saath ----------
def build_phase():
    bp = PIPE_ST["bp"]
    groups = bp["groups"]
    for gi in range(int(PIPE_ST.get("gdone", 0)), len(groups)):
        if STATE["cancel"]:
            pipe_save()
            return
        set_conc()
        paths = groups[gi]
        have = set(PIPE_ST.get("written") or [])
        todo = [bp_file(bp, p) for p in paths if not (p in have and os.path.isfile(os.path.join(WORK, p)))]
        todo = [f for f in todo if f]
        ev("note", text="🧱 group %d/%d: %s (JAAT ek saath %d tak)" % (gi + 1, len(groups), ", ".join(paths), PIPE_PAR))
        if todo:
            par_run([(lambda f=f: write_job(f)) for f in todo], 3300)
            if STATE["cancel"]:
                pipe_save()
                return
        pipe_save()
        group_check(paths)
        if STATE["cancel"]:
            pipe_save()
            return
        PIPE_ST["gdone"] = gi + 1
        if gi + 1 not in STATE["plan_ck"]:
            STATE["plan_ck"].append(gi + 1)
            ev("check", n=gi + 1)
        pipe_save()
    miss = [f for f in bp["files"] if not os.path.isfile(os.path.join(WORK, f["path"]))]
    if miss and not STATE["cancel"]:                                  # jo likhi na ja saki thi, ek baar aur
        ev("note", text="↻ likhi na gayi files dobara: %s" % ", ".join(f["path"] for f in miss))
        set_conc()
        par_run([(lambda f=f: write_job(f)) for f in miss], 3300)
        for f in miss:
            if os.path.isfile(os.path.join(WORK, f["path"])):
                with PIPE_LOCK:
                    if f["path"] in PIPE_ST.get("failed", []):
                        PIPE_ST["failed"].remove(f["path"])
    if STATE["cancel"]:
        pipe_save()
        return
    for f in bp["files"]:
        if not os.path.isfile(os.path.join(WORK, f["path"])):
            _pst_add("warn", "%s likhi nahi ja saki" % f["path"])
    PIPE_ST["phase"] = "final"
    pipe_save()


_build_phase_5x = build_phase


def recheck_warns():
    """Build ke dauran same group ki files ek saath badalti hain, to ek ki jaanch dusri ke beech me aa sakti hai. Ab sab ban chuka: jo warning ab sach nahi use hatao."""
    with PIPE_LOCK:
        warns = list(PIPE_ST.get("warn") or [])
    keep = []
    for w in warns:
        mt = re.match(r"^(\S+): jaanch abhi bhi fail", w)
        if mt and os.path.isfile(os.path.join(WORK, mt.group(1))):
            try:
                if compile_check(mt.group(1))[0]:
                    continue
            except Exception:
                pass
        keep.append(w)
    if len(keep) != len(warns):
        with PIPE_LOCK:
            PIPE_ST["warn"] = keep
        pipe_save()


def build_phase():
    _build_phase_5x()
    if not STATE["cancel"]:
        try:
            recheck_warns()
        except Exception as e:
            log("recheck_warns galti: %s" % e)


# ---------- 7) aakhri loop: DSX poora project -> bug -> JAAT fix -> DS/Groq/NV2 confirm -> tabhi DSX dobara (max 5 round) ----------
FINAL_SYS_PIPE = (
    "You are the final reviewer of a finished multi-file project. You get the TASK, the PLAN/CONTRACT, a MEMORY MAP and ALL source files with line numbers. "
    "Read everything like a developer doing a last test run in his head: follow the real flow from start-up through each feature, and check that the setup/run "
    "commands would work with these files. Report ONLY real defects: syntax errors, crashes, wrong imports/names/signatures/routes/ids between files, missing files "
    "or dependencies (requirements/package files), config that makes it fail to start or deploy, and logic that clearly does not do what the TASK asks. "
    "Do NOT report style, suggestions, extra features, hardening, or a difference between a name written in the notes/task and the real file or folder name. "
    "Do not repeat something listed under ALREADY REPORTED unless it is STILL really present in the code below. Give the exact line from the numbered listing. "
    'Reply with ONLY a JSON array, no prose, no code fence: [{"file":"path","line":N,"bug":"what is wrong","fix":"exact change"}]. '
    "Reply [] if there is no real defect. Max 10 items, most serious first.")
EXACT_SYS = (
    "Earlier fix attempts for the bugs below FAILED more than once. Think about the real root cause (the earlier approach may have been wrong). For each file give the "
    "EXACT fix as edit blocks. Format, per file: a line `FILE: path` and then one or more blocks:\n<<<<<<< SEARCH\n<old lines copied EXACTLY from the numbered listing, without "
    "line numbers>\n=======\n<corrected lines>\n>>>>>>> REPLACE\nSEARCH must be unique in that file. No other prose.")
FINAL_EXT = REVIEW_EXT + (".json", ".txt", ".yml", ".yaml", ".sh", ".toml", ".cfg", ".ini", ".env.example")
SKIP_FINAL = {"MEMORY.md", "PROJECT.md", "SPEC.md", "SETUP.md"}
CODE_EXT = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".kt", ".java", ".go", ".rs", ".c", ".cpp", ".sh")


def final_files():
    out = []
    for rel in list_files(None):
        if rel in SKIP_FINAL or rel.split("/", 1)[0] == "uploads" or rel.lower().endswith(".zip"):
            continue
        if rel.lower().endswith(FINAL_EXT) or os.path.basename(rel) in ("Dockerfile", "Procfile", ".gitignore"):
            out.append(rel)
    return out


def final_payload(nm, rnd):
    """DSX ke liye: task + contract + memory map + (pichhle round ke bug) + files. Jagah kam ho to badli nahi files sirf card ke roop me."""
    bp = PIPE_ST["bp"]
    budget = min(DSX_FINAL_CHARS, ctx_limit(nm, APIS[nm]) - 16000)          # DSX ho ya Gemini: provider ka asli (seekha hua) context bhi dekho
    budget = max(30000, budget)
    seen = PIPE_ST.setdefault("reviewed", {})
    head = ["TASK:\n" + (PIPE_ST.get("task") or "")[:3000], "PLAN / CONTRACT:\nProject: %s | Stack: %s | Niyam: %s\n%s" % (
        bp["summary"], bp["stack"], bp["notes"] or "-", contract_text(bp, 9000)),
        "COMMANDS: " + " || ".join("%s: %s" % (k, " ; ".join(v)) for k, v in bp["commands"].items() if v)]
    mem = memory_text(6000)
    if mem:
        head.append("MEMORY MAP (har file ka card; mismatch isse bhi milao):\n" + mem)
    prev = [h for h in PIPE_ST.get("hist", []) if h.get("last_round", 0) == rnd - 1]
    if prev:
        head.append("ALREADY REPORTED (round %d me; unke fix lag chuke hain):\n%s" % (rnd - 1, "\n".join(
            "- %s: %s" % (h["file"], clip(h["bug"], 160)) for h in prev)))
    if PIPE_ST.get("tests_out"):
        head.append("TEST OUTPUT (pichhli baar):\n" + clip(PIPE_ST["tests_out"], 1500))
    used = sum(len(x) for x in head)
    parts, skipped = [], []
    files = final_files()
    sizes = {r: len(_read(r, 300000)) for r in files}
    total = int(sum(sizes.values()) * 1.35)                    # line number lagne se text ~30% bada ho jata hai
    for rel in files:
        txt = _read(rel, 300000)
        if not txt.strip():
            continue
        num = _numbered(txt)
        try:
            st = os.stat(os.path.join(WORK, rel))
            sig = [st.st_mtime, st.st_size]
        except OSError:
            sig = None
        unchanged = rnd > 1 and seen.get(rel) == sig
        if total + used > budget and unchanged:
            skipped.append(rel)                                # badli nahi aur jagah kam: card hi kaafi
            continue
        if used + len(num) > budget:                              # line numbers samet asli size (context paar na ho)
            skipped.append(rel)
            continue
        used += len(num)
        parts.append("FILE: %s\n%s" % (rel, num))
        seen[rel] = sig
    return ("\n\n".join(head) + "\n\n" + "\n\n".join(parts) + (
        "\n\n(jagah kam thi: ye files poori nahi dikhayi, inka card MEMORY MAP me hai: %s)" % ", ".join(skipped) if skipped else ""))


def salvage_array(text):
    """Jawab token-hadd par kata ho to jitne poore {..} bug object mile utne lo (adhura aakhri object chhod do). None = kuch nahi mila."""
    t = clean_reply(text or "")
    t = re.sub(r"^```(?:json)?\s*", "", t.strip())
    i = t.find("[")
    if i == -1:
        return None
    dec, out, pos = json.JSONDecoder(), [], i + 1
    while True:
        j = t.find("{", pos)
        if j == -1:
            break
        try:
            o, end = dec.raw_decode(t, j)
        except ValueError:
            break
        if isinstance(o, dict):
            out.append(o)
        pos = end
    return out or None


def final_review(rnd):
    """([bug], provider) ya (None, ''). DSX; band/fail ho to Gemini, phir JAAT."""
    for nm in rot_prefs(FINAL_PREF):
        if STATE["cancel"]:
            break
        if not avail((nm,)):
            continue
        user = final_payload(nm, rnd)
        for t in range(2):
            if nm == "DSX":
                PIPE_ST["dsx_calls"] = PIPE_ST.get("dsx_calls", 0) + 1
            u = user if not t else user + "\n\n[Pichhla jawab sahi JSON nahi tha. Sirf JSON array do, max 6 bug, har ek chhota.]"
            res = gen_call(nm, FINAL_SYS_PIPE, u, mt=PIPE_OUT)
            if not res:
                break
            pj = parse_json_reply(res[0])
            if pj is None and res[1]:
                pj = salvage_array(res[0])                   # jawab kata tha: jitne poore bug mile utne lo
            d = _norm_bugs(pj, "")
            if d is not None:
                return drop_phantom(d)[:10], nm
    return None, ""


def norm_bug(b):
    return re.sub(r"[^a-z0-9 ]+", " ", str(b.get("bug", "")).lower()).strip()


def hist_note(bugs, rnd):
    """Har bug ko pichhle round ke bug se milao; kitni baar aa chuka (count). Naye round me wahi bug = count + 1."""
    hist = PIPE_ST.setdefault("hist", [])
    for b in bugs:
        key, h = norm_bug(b), None
        for x in hist:
            if x["file"] == str(b.get("file")) and difflib.SequenceMatcher(None, x["text"], key).ratio() >= 0.7:
                h = x
                break
        if h is None:
            h = {"file": str(b.get("file")), "text": key, "bug": str(b.get("bug", "")), "count": 0, "last_round": 0}
            hist.append(h)
        if h["last_round"] != rnd:
            h["count"] += 1
        h["last_round"], h["bug"] = rnd, str(b.get("bug", ""))
        b["_count"] = h["count"]
    del hist[:-60]


def dsx_exact_fix(rep):
    """Wahi bug 2+ baar: DSX ko hard prompt, exact SEARCH/REPLACE. {file: blocks text} ya {}."""
    by = {}
    for b in rep:
        by.setdefault(str(b.get("file")), []).append(b)
    parts = []
    for rel, bl in by.items():
        if os.path.isfile(os.path.join(WORK, rel)):
            parts.append("FILE: %s\nBUGS (ye fix %d baar fail hua):\n%s\n%s" % (
                rel, max(b.get("_count", 2) for b in bl) - 1, bug_lines(bl), _numbered(_read(rel, 300000))))
    if not parts or STATE["cancel"]:
        return {}
    for nm in ("DSX",) if avail(("DSX",)) else FINAL_PREF[1:]:
        if not avail((nm,)):
            continue
        if nm == "DSX":
            PIPE_ST["dsx_calls"] = PIPE_ST.get("dsx_calls", 0) + 1
        PIPE_ST["dsx_exact"] = PIPE_ST.get("dsx_exact", 0) + 1
        ev("note", text="🎯 %s ko hard prompt: ye bug baar-baar aa raha, exact purana/naya code do (%s)" % (nm, ", ".join(by)))
        res = gen_call(nm, EXACT_SYS, "MEMORY MAP:\n%s\n\n%s" % (memory_text(3000) or "-", "\n\n".join(parts)), mt=PIPE_OUT)
        if not res:
            continue
        out, cur = {}, None
        for line in clean_reply(res[0]).split("\n"):
            m = re.match(r"^\s*FILE:\s*(\S+)\s*$", line)
            if m:
                cur = m.group(1).strip("`'\"")
                out[cur] = []
            elif cur is not None:
                out[cur].append(line)
        out = {k: "\n".join(v) for k, v in out.items() if "<<<<<<<" in "\n".join(v)}
        if out:
            return out
    return {}


def fix_job(rel, bl, exact_text, first):
    """Ek file ke bug theek karo -> compile -> DS/Groq/NV2 confirm. {'rel','changed','open':[bug]}."""
    strict = os.path.splitext(rel)[1].lower() in INDENT_EXT
    changed, extra = False, ""
    if exact_text:
        old = _read(rel, 400000)
        new, e = apply_edit(old, exact_text, strict)
        if not e and not syntax_error_of(rel, new):
            put_file(rel, new, "DSX")
            changed = True
        else:
            extra = "DSX ka exact fix (isi ko lagao; SEARCH file ke asli text se milao):\n" + exact_text
    if not changed:
        changed = fix_file(rel, bl, extra)
    if not changed:
        return {"rel": rel, "changed": False, "open": list(bl)}
    ok, out = compile_check(rel)
    if not ok and not STATE["cancel"]:
        fix_file(rel, [compile_bug(rel, out)])
        ok, out = compile_check(rel)
    open_ = []
    for b in bl[:4]:
        if STATE["cancel"]:
            break
        if confirm_fixed(rel, b, first) is False:
            open_.append(b)
    if open_ and not STATE["cancel"]:
        fix_file(rel, open_, "DS/Groq/NV2 ki jaanch kehti hai ye bug abhi bhi theek nahi hua. Dhyan se dobara dekho aur sach me theek karo.")
        compile_check(rel)
        open_ = [b for b in open_ if confirm_fixed(rel, b, first) is False]
    if not ok:
        open_ = open_ + [compile_bug(rel, out)]
    card_update(rel, ai=False)
    return {"rel": rel, "changed": True, "open": open_}


TEST_ENV_RE = re.compile(r"ModuleNotFoundError|No module named|Cannot find module|command not found|not recognized|ENOENT.*(?:node_modules|package)", re.I)


def run_tests():
    """bp ke 'test' commands (sirf asli check wale) chalata hai. ([(cmd, out)] fail, skipped_env_note)."""
    bp, bad, env_skip = PIPE_ST["bp"], [], []
    for c in [x for x in bp["commands"].get("test", []) if VERIFY_CMD_RE.search(x)][:5]:
        if STATE["cancel"]:
            break
        code, out = quick_run(c, 100)
        ev("step", kind="RUN", arg=c[:120], out=clip(out, 1500) or "(saaf)", ok=(code == 0), prov="mumbai")
        if code != 0:
            (env_skip if TEST_ENV_RE.search(out) or code in (126, 127) else bad).append((c, out))
    return bad, env_skip


def final_phase():
    bp = PIPE_ST["bp"]
    if not (avail(FINAL_PREF)):
        PIPE_ST.setdefault("warn", []).append("aakhri jaanch ke liye koi AI (DSX/Gemini/JAAT) nahi mila")
        ev("note", text="⚠ aakhri jaanch ke liye koi AI nahi mila, ise chhod ke aage badh raha hoon")
        PIPE_ST["phase"] = "end"
        pipe_save()
        return
    for rnd in range(PIPE_ST.get("round", 0) + 1, PIPE_ROUNDS + 1):
        if STATE["cancel"]:
            return
        PIPE_ST["round"] = rnd
        ev("note", text="🔎 aakhri jaanch round %d/%d: poora project ek saath padh ke bug dhoondh raha hoon..." % (rnd, PIPE_ROUNDS))
        bugs, who = final_review(rnd)
        pipe_save()
        if bugs is None:
            PIPE_ST.setdefault("warn", []).append("round %d: aakhri jaanch ka jawab kisi AI se nahi aaya" % rnd)
            show_review("Aakhri jaanch", False, "kisi AI se jawab nahi aaya", "")
            break
        tests_fail = []
        if not bugs:
            tests_fail, env_skip = run_tests()
            PIPE_ST["tests_out"] = "\n".join("%s\n%s" % (c, clip(o, 600)) for c, o in tests_fail)[:1800]
            if env_skip:
                ev("note", text="ℹ test chhoda (is server me library/tool nahi): " + "; ".join(c[:50] for c, _ in env_skip))
            if not tests_fail:
                show_review("Aakhri jaanch (%s) round %d" % (who, rnd), True, "koi asli bug nahi mila" + (" · test pass" if bp["commands"].get("test") and not env_skip else ""), who)
                PIPE_ST["clean"] = True
                break
            for c, o in tests_fail:                               # test fail = bug; file error se milti ho to
                locs = error_files(o)
                if locs:
                    bugs.append({"file": locs[0][0], "line": locs[0][1], "bug": "test fail: %s\n%s" % (c, clip(o, 500)), "fix": "traceback ke hisaab se theek karo"})
            if not bugs:
                show_review("Test fail", False, "\n".join("%s\n%s" % (c, clip(o, 400)) for c, o in tests_fail), "mumbai")
                continue                                        # file pata nahi: agle round me DSX ko TEST OUTPUT dikhega
        hist_note(bugs, rnd)
        show_review("DSX jaanch round %d/%d (%s)" % (rnd, PIPE_ROUNDS, who), False, "%d bug\n%s" % (len(bugs), bug_text({"project": bugs})), who)
        rep = [b for b in bugs if b.get("_count", 1) >= 2]
        exact = {}
        if rep and PIPE_ST.get("dsx_exact", 0) < PIPE_EXACT_MAX:
            exact = dsx_exact_fix(rep)
        by = {}
        for b in bugs:
            rel = str(b.get("file") or "")
            if rel and os.path.isfile(os.path.join(WORK, rel)):
                by.setdefault(rel, []).append(b)
        if not by:
            continue
        names = [n for n in CHECK_PREF if avail((n,))] or ["DS"]
        ev("note", text="🔧 JAAT fix kar raha hai (ek saath %d tak): %s" % (PIPE_PAR, ", ".join(by)))
        fns = [(lambda r=r, bl=bl, i=i: fix_job(r, bl, exact.get(r, ""), names[i % len(names)])) for i, (r, bl) in enumerate(by.items())]
        results = par_run(fns, 1500)
        still = []
        for r in results:
            if r:
                still += r["open"]
        PIPE_ST["last_open"] = [{"file": b.get("file"), "bug": clip(str(b.get("bug", "")), 140)} for b in still]
        ok_n = sum(1 for r in results if r and r["changed"] and not r["open"])
        ev("note", text="✅ %d file ke bug theek (DS/Groq/NV2 ne confirm kiya), ⚠ %d bug abhi khule" % (ok_n, len(still)))
        pipe_save()
    if not PIPE_ST.get("clean") and not STATE["cancel"]:
        lo = PIPE_ST.get("last_open") or []
        if lo:
            PIPE_ST.setdefault("warn", []).append("%d bug abhi bhi khule: %s" % (len(lo), "; ".join("%s: %s" % (x["file"], x["bug"]) for x in lo[:4])))
        elif PIPE_ST.get("round", 0) >= PIPE_ROUNDS:
            PIPE_ST.setdefault("warn", []).append("DSX ke aakhri round ke bug JAAT ne theek kiye, par uske baad DSX ne dobara nahi dekha")
    if not STATE["cancel"]:
        PIPE_ST["phase"] = "end"
        pipe_save()


# ---------- 8) aakhir: Gemini sirf SETUP samjhaye ----------
EXPLAIN_SYS = (
    "Tum Mumbai ho. Ek non-technical user ka project taiyar hai. Sirf SETUP samjhao, saaf aasaan Hinglish me, chhote steps me. Ye likho (heading ke saath): "
    "1) Kya bana (2-3 line). 2) Kaise chalana hai: exact commands, ek-ek step (kahan type karna hai bhi batao). 3) Kaunsi keys/environment variables chahiye aur kahan se milengi. "
    "4) FREE me kahan host karein: 1-2 service ka naam, deploy ke chhote steps, dhyan rakhne ki baat. 5) Agar kuch adhura/khula hai to saaf batao. "
    "Code file-by-file mat samjhao. Jo jaankari neeche nahi hai wo mat banao. Max 350 shabd.")


def setup_explain():
    bp = PIPE_ST["bp"]
    user = "TASK:\n%s\n\nPROJECT: %s\nSTACK: %s\nFILES:\n%s\nCOMMANDS:\n%s\nENV: %s\n\nMEMORY CARDS:\n%s\n\nKHULI BAATEN: %s" % (
        (PIPE_ST.get("task") or "")[:1500], bp["summary"], bp["stack"],
        "\n".join("- %s: %s" % (f["path"], f["purpose"]) for f in bp["files"]),
        "\n".join("%s: %s" % (k, " ; ".join(v)) for k, v in bp["commands"].items() if v) or "-", " ; ".join(bp["env"]) or "-",
        memory_text(3500) or "-", "; ".join(PIPE_ST.get("warn") or []) or "koi nahi")
    for nm in avail(EXPLAIN_PREF, len(EXPLAIN_SYS) + len(user)):
        res = gen_call(nm, EXPLAIN_SYS, user, mt=3000)
        if res:
            return clean_reply(res[0]).strip(), nm
    return "", ""


def fallback_explain():
    bp = PIPE_ST["bp"]
    L = ["Bana: " + bp["summary"], "Stack: " + bp["stack"]]
    for k, t in (("setup", "Setup"), ("run", "Chalane ke commands"), ("deploy", "Host karna")):
        if bp["commands"].get(k):
            L.append("%s: %s" % (t, " ; ".join(bp["commands"][k])))
    if bp["env"]:
        L.append("Environment: " + " ; ".join(bp["env"]))
    return "\n".join(L)


def end_phase():
    ev("note", text="📝 Gemini setup samjha raha hai (kya bana, kaise chalana, kahan host)...")
    txt, who = setup_explain()
    if not txt:
        txt, who = fallback_explain(), "mumbai"
    try:
        with open(os.path.join(WORK, "SETUP.md"), "w", encoding="utf-8") as f:
            f.write("# Setup\n\n" + txt + "\n")
    except OSError:
        pass
    warn = PIPE_ST.get("warn") or []
    summary = txt + (("\n\n⚠ " + "\n⚠ ".join(warn)) if warn else "") + "\n\n(SETUP.md me ye sab likha hai; MEMORY.md me har file ka card)"
    PIPE_ST["phase"] = "done"
    PIPE_ST["summary"] = summary
    pipe_save()
    return summary, who


# ---------- pipeline chalana / dobara chalana ----------
def pipe_run():
    """build -> final -> end. STATE['pipe_summary'] me aakhri saar. Beech me Stop dabe to wahin ruk jaata hai (dobara 'aage badho' se aage)."""
    try:
        if PIPE_ST.get("phase") == "build":
            build_phase()
        if not STATE["cancel"] and PIPE_ST.get("phase") == "final":
            final_phase()
            if not STATE["cancel"]:
                _n = len(PIPE_ST["bp"]["groups"]) + 1
                if _n not in STATE["plan_ck"]:
                    STATE["plan_ck"].append(_n)
                    ev("check", n=_n)
        if STATE["cancel"]:
            pipe_save()
            return
        if PIPE_ST.get("phase") == "end":
            summary, who = end_phase()
            STATE["pipe_summary"] = summary
            STATE["pipe_who"] = who
            _n = len(PIPE_ST["bp"]["groups"]) + 2
            if _n not in STATE["plan_ck"]:
                STATE["plan_ck"].append(_n)
                ev("check", n=_n)
            n = len(PIPE_ST.get("written") or [])
            ev("note", text="✅ pipeline poori: %d file, DSX request %d" % (n, PIPE_ST.get("dsx_calls", 0)))
    except Exception as e:
        log("pipe_run galti: %s\n%s" % (e, traceback.format_exc()[-600:]))
        ev("err", text="⚠ pipeline beech me atki (%s: %s). Jo files ban chuki hain wo safe hain; 'aage badho' likho." % (type(e).__name__, str(e)[:120]))
        pipe_save()


def pipeline_entry(task):
    """None = pipeline nahi chali (purana raasta). Warna '' ya samajh-text (STATE['pipe_summary'] me aakhri saar)."""
    PIPE_ST.clear()
    PIPE_ST.update(task=task, phase="plan", written=[], failed=[], round=0, dsx_calls=0, dsx_exact=0, hist=[], warn=[], reviewed={})
    STATE["utype"] = "code"
    bp, who = make_blueprint(task)
    if STATE["cancel"]:
        return ""
    if not bp:
        ev("note", text="⚠ plan nahi ban paya (DSX/Gemini/JAAT), purane raaste se kaam kar raha hoon")
        return None
    PIPE_ST.update(bp=bp, phase="build", planner=who)
    STATE["goal"] = bp["summary"] or task[:200]
    STATE["criteria"] = ["D%d: %s" % (i + 1, s) for i, s in enumerate(bp["spec"][:6])]
    items = bp_plan_items(bp)
    STATE.update(plan_items=items, plan_ck=[])
    ev("plan", items=items)
    ev("understand", goal="%s (%d file, %d group)" % (bp["summary"], len(bp["files"]), len(bp["groups"])), kind="code", done=STATE["criteria"], prov=who)
    try:
        plan_pm(items, task)
        append_pm(bp_md(bp))
    except Exception as e:
        log("plan_pm galti: %s" % e)
    save_spec(bp["spec"]) if len(bp["spec"]) >= 1 else None
    pipe_save()
    pipe_run()
    if STATE.get("pipe_summary") is None and not STATE["cancel"]:
        # pipeline atki: purane agent ko bata do ki kya ban chuka hai
        return "\n[Pipeline ne ye files bana di hain: %s. Jo adhura ya galat ho use poora karo.]" % ", ".join(PIPE_ST.get("written") or []) if PIPE_ST.get("written") else None
    return ""


def pipe_resume(d=None):
    d = d or pipe_load()
    if not d:
        return
    PIPE_ST.clear()
    PIPE_ST.update(d)
    items = bp_plan_items(PIPE_ST["bp"])
    STATE["plan_items"] = items
    ev("note", text="▶ adhura pipeline wahin se chalu (phase: %s, round %s)" % (PIPE_ST.get("phase"), PIPE_ST.get("round", 0)))
    pipe_run()


PIPE_EXIST_WORDS = int(_env("PIPE_EXIST_WORDS", "8"))              # purane project me itne shabd se chhota message = purana agent
FEATURE_RE = re.compile(r"\b(?:add|jodo|jod\s*do|jodna|naya|nayi|naye|new|feature|badal\w*|change|update|modify|refactor|rewrite|convert|migrate|integrat\w*|support|upgrade|extend|implement|include|dalo|daal\w*|laga\w*|banao|bnao|bana\w*)\b", re.I)
BUGONLY_RE = re.compile(r"error|traceback|exception|crash|\bbug\b|\bfix\b|theek|thik\s*kar|galti|nahi\s*chal|not\s*working", re.I)


def pipe_eligible(task):
    """Naya project: build wala kaam (5+ shabd). Purana project: bada naya feature/badlav (8+ shabd, bug-fix nahi). Android hamesha purane raaste."""
    t = task or ""
    if not (PIPE and LEAN) or ANDROID_TASK_RE.search(t):
        return False
    if MODE_FORCE_RE.match(t) and MODE_FORCE_RE.match(t).group(1).lower() == "light":
        return False
    if not project_has_files():
        return bool(build_intent(t) and len(t.split()) >= 5)
    return bool(len(t.split()) >= PIPE_EXIST_WORDS and (FEATURE_RE.search(t) or build_intent(t)) and not BUGONLY_RE.search(t)
                and "```" not in t and "@@" not in t)


# ---------- 9) sirf baat / project ke sawal: JAAT aur DSX nahi ----------
CHAT_SYS = ("Tum Mumbai ho, user ke phone ka dost jaisa sahayak. Hinglish me chhota (1-4 line), seedha aur sach jawab do. Kisi file/tool ya @@command ki baat mat karo; "
            "jo pata nahi wo bol do ki pata nahi.")
ACTION_RE = re.compile(r"error|traceback|exception|crash|fail\w*|nahi\s*chal\w*|kaam\s*nahi|not\s*working|\bbug\b|galti|\bissue\b|problem|dikkat|"
                       r"banao|bnao|bana\w*|\bbna\b|likh\w*|\bwrite\b|\bcreate\b|\bmake\b|\bbuild\b|generate|\bedit\b|badl\w*|\bchange\b|\bfix\b|theek|thik\s*kar|\badd\b|jodo|jod\s*do|hatao|hata\s*do|"
                       r"\bdelete\b|\bremove\b|\brun\b|chala\w*|install|deploy|\bhost\b|upload|download|convert|translate|summari[sz]e|\bsearch\b|dhundh\w*|padh\w*|\bread\b|jaanch|"
                       r"\bcheck\b|\btest\b|\bzip\b|\bfile\b|\bfolder\b|\bproject\b|\brepo\b|github|@@|\.(?:py|js|ts|kt|java|html|css|json|md|txt|pdf|docx|xlsx|pptx|csv|zip|apk)\b", re.I)
SMALLTALK_RE = re.compile(r"^\W*(hi+|hello+|hey+|hii+|salaam|salam|namaste|thanks?|thank you|shukriya|ok(?:ay)?|haan|han|nahi|theek|accha|acha|kya haal|kaise ho|kese ho|good (?:morning|night|evening))\b", re.I)
CLS_SYS = ("Classify the user's message to a coding/document assistant. Reply with ONE word. CHAT = just talking, greeting, or a general/simple question that needs no files, "
           "code, documents, internet or tools. WORK = anything that needs the assistant to make, change, read, run or look up something.")


def small_gen(system, user, mt=500):
    for nm in avail(CHAT_PREF, len(system) + len(user)):
        res = gen_call(nm, system, user, mt=mt)
        if res:
            return clean_reply(res[0]).strip(), nm
    return "", ""


def route_task(task):
    """'chat' | 'qa' | 'work'. Shak ho to 'work' (purana raasta): galti se kaam ko chat samajhna nuksan karta hai."""
    t = (task or "").strip()
    if not t or MODE_FORCE_RE.match(t) or "```" in t or "@@" in t or len(t.split()) > 60 or len(t) > 600 or STATE.get("asking"):
        return "work"
    if build_intent(t) or ACTION_RE.search(t):
        return "work"
    small = len(t.split()) <= 5 and bool(SMALLTALK_RE.search(t))
    last_ai = next((m.get("content") for m in reversed(STATE["msgs"]) if m.get("role") == "assistant"), "")
    if plan_left() or (isinstance(last_ai, str) and last_ai.strip().endswith("?") and len(t.split()) <= 8):
        return "work"                                           # adhura kaam ya AI ne kuch poocha tha: jawab agent ko jaye
    if memory_text(50) and project_has_files() and not small:
        return "qa"
    if project_has_files() and len(t.split()) > 8 and not small:
        v, _ = small_gen(CLS_SYS, t, 8)
        return "chat" if v.strip().upper().startswith("CHAT") else "work"
    return "chat"


def recent_chat(n=6, w=350):
    out = []
    for m in STATE["msgs"][-n:]:
        c = m.get("content")
        if m.get("role") in ("user", "assistant") and isinstance(c, str) and not c.startswith("Natija:"):
            out.append("%s: %s" % ("User" if m["role"] == "user" else "Mumbai", clip(c.split("\n[")[0], w)))
    return "\n".join(out)


CHAT_SUM_AFTER = int(_env("CHAT_SUM_AFTER", "10"))         # itne se zyada chat message ho to purani baat ka saar banao
CHAT_KEEP = 4                                              # aakhri itne message poore dikhao
CHAT_HIST_MAX = 4200                                       # DS (6500 char) me bhi prompt samaye
CHAT_SUM_SYS = ("Tum ek chat ka saar likhte ho. Neeche purana saar (agar hai) aur naye messages hain. Dono milake EK naya saar likho: Hinglish, max 110 shabd. "
                "Ye zaroor rakho: user ne apne baare me/project ke baare me kya bataya (naam, pasand, faisle), kya sawal abhi khule hain. Bekaar baat mat likho. Sirf saar likho.")


def chat_pairs():
    out = []
    for m in STATE["msgs"]:
        c = m.get("content")
        if m.get("role") in ("user", "assistant") and isinstance(c, str) and not c.startswith("Natija:"):
            out.append(("User" if m["role"] == "user" else "Mumbai", c.split("\n[")[0].strip()))
    return out


def _hh(p):
    return hashlib.md5((p[0] + "|" + p[1]).encode("utf-8", "replace")).hexdigest()[:12]


def chat_history():
    """Chhoti chat: pichhle 6 message. Lambi chat: purane messages ka saar (DS/Groq se, 6+ naye message jama hone par naya) + aakhri 4 message poore."""
    pairs = chat_pairs()
    if len(pairs) <= CHAT_SUM_AFTER:
        return recent_chat()
    older, recent = pairs[:-CHAT_KEEP], pairs[-CHAT_KEEP:]
    cs = STATE.get("chat_sum") or {}
    hs = [_hh(p) for p in older]
    start = (hs.index(cs["h"]) + 1) if cs.get("h") in hs else 0
    pending, text = older[start:], cs.get("text") or ""
    if len(pending) >= 6:
        body = (("PURANA SAAR:\n%s\n\n" % text) if text else "") + "NAYE MESSAGES:\n" + "\n".join("%s: %s" % (r, clip(c, 300)) for r, c in pending[-14:])
        try:
            sm, _nm = small_gen(CHAT_SUM_SYS, clip(body, 5000), 400)
        except Exception as e:
            log("chat saar galti: %s" % e)
            sm = ""
        if sm:
            text = clip(sm.strip(), 1000)
            STATE["chat_sum"] = {"h": hs[-1], "text": text}
            pending = []
    tail = "Taaza baat:\n" + "\n".join("%s: %s" % (r, clip(c, 300)) for r, c in recent)
    head = []
    if text:
        head.append("Purani baat ka saar:\n" + text)
    if pending:
        head.append("Beech ki baat:\n" + "\n".join("%s: %s" % (r, clip(c, 200)) for r, c in pending[-6:]))
    h = "\n\n".join(head)
    room = max(0, CHAT_HIST_MAX - len(tail) - 2)
    return ((h[:room] + "\n\n") if h and room else "") + tail


_reset_chat_state_5x = reset_chat_state


def reset_chat_state():
    STATE.pop("chat_sum", None)
    return _reset_chat_state_5x()


def chat_reply(task):
    hist = chat_history()
    user = (("Pichhli baat:\n" + hist + "\n\n") if hist else "") + "User: " + task
    txt, nm = small_gen(CHAT_SYS, user, 700)
    txt = "\n".join(l for l in txt.split("\n") if not l.strip().startswith("@@")).strip()
    if not txt:
        return False
    add("user", task)
    add("assistant", txt)
    ev("ai", text=txt, prov=nm)
    return True


QA_SYS = ("Tum Mumbai ho. User is project ke baare me sawal puch raha hai. Neeche PLAN aur har file ke CARD (kaam, naam+signature+line number, needs) hain. "
          "Jawab pehle inhi se do, saaf aasaan Hinglish me. Agar jawab ke liye asli code chahiye to jawab ki jagah SIRF ye likho (max 3 line): NEED: <file> <a>-<b> "
          "(card ke line number se thodi range). Jo pata nahi wo mat banao.")
NEED_RE = re.compile(r"^\s*NEED:\s*(\S+)\s+(\d+)\s*-\s*(\d+)\s*$", re.M)


def read_range(rel, a, b, cap=7000):
    try:
        p = safe(rel)
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")
    except (OSError, ValueError):
        return ""
    a, b = max(1, a), min(len(lines), max(a, min(b, a + 250)))
    return clip("\n".join("%d| %s" % (i, lines[i - 1][:300]) for i in range(a, b + 1)), cap)


def project_qa(task):
    cards = memory_text(9000)
    if not cards:
        return False
    d = pipe_load() or {}
    bp = d.get("bp")
    plan = ("Project: %s | Stack: %s\nCommands: %s" % (bp["summary"], bp["stack"], " || ".join("%s: %s" % (k, " ; ".join(v)) for k, v in bp["commands"].items() if v))
            if bp else clip(_read("PROJECT.md", 3000), 2500))
    hist, extra = recent_chat(), ""
    for rnd in range(3):
        user = "PLAN:\n%s\n\nCARDS:\n%s\n\n%sUSER KA SAWAL: %s%s" % (plan, cards, ("PICHHLI BAAT:\n" + hist + "\n\n") if hist else "", task, extra)
        got = None
        for nm in avail(QA_PREF, len(QA_SYS) + len(user)):
            res = gen_call(nm, QA_SYS, user, mt=1500)
            if res:
                got = (clean_reply(res[0]).strip(), nm)
                break
        if not got:
            return False
        txt, nm = got
        needs = NEED_RE.findall(txt)
        if needs and rnd < 2:
            for f, a, b in needs[:3]:
                seg = read_range(f, int(a), int(b))
                extra += "\n\nASLI CODE %s %s-%s:\n%s" % (f, a, b, seg or "(padh nahi paya)")
            extra += "\n\n(Ab is code ke saath jawab do. NEED mat likho.)"
            continue
        ans = NEED_RE.sub("", txt).strip()
        if not ans:
            return False
        add("user", task)
        add("assistant", ans)
        ev("ai", text=ans, prov=nm)
        return True
    return False


def pre_route(task):
    """True = jawab de diya (agent loop ki zaroorat nahi)."""
    if not PIPE:
        return False
    r = route_task(task)
    if r == "chat":
        ev("note", text="💬 sirf baat: GROQ/DS/NV2 se (JAAT/DSX nahi)", _log=False)
        return chat_reply(task)
    if r == "qa":
        ev("note", text="📖 project ka sawal: cards/plan se (zarurat par file ka wahi hissa)", _log=False)
        return project_qa(task)
    return False


# ---------- 10) agent se jodna ----------
def pipe_hosting_web(bp):
    """Ek web search: abhi ke FREE hosting options (Gemini ko setup samjhane me kaam aaye). '' = nahi mila."""
    try:
        q = "best free hosting for %s project %s" % (clip(bp.get("stack") or "", 40), time.strftime("%Y"))
        txt, _c, ok, _x = do_search(q)
        if ok and txt and not str(txt).startswith(("ERROR", "⏹")):
            return clip(re.sub(r"\n\(kisi link.*$", "", txt, flags=re.S), 2200)
    except Exception as e:
        log("hosting search galti: %s" % e)
    return ""


def pipe_conclude(task, fresh=True):
    """Pipeline ke baad chat me saar + zip. True = kaam nipta (purana agent loop nahi chalana)."""
    if STATE["cancel"]:
        pipe_save()
        ev("note", text="⏹ roka gaya")
        return True
    s = STATE.get("pipe_summary")
    if not s:
        return True                                         # pipe_run ne atki halat ka err event de diya hai; 'aage badho' se aage
    if fresh:
        add("user", task)
    add("assistant", s)
    try:
        zr, _c, zok, _x = run_cmd("ZIP", STATE["proj"])
    except Exception as ex:
        zr, zok = "ERROR: %s" % ex, False
    wl("ZIP %s" % STATE["proj"])
    ev("step", kind="ZIP", arg=STATE["proj"], out=zr, ok=zok, prov="mumbai")
    ev("done", text=s, prov=STATE.get("pipe_who") or "mumbai", steps=len(PIPE_ST.get("written") or []), zip=True, proj=STATE["proj"])
    notify_bg("✅ %s: %s" % (STATE["proj"], s[:300]))
    return True


def pipe_front(task):
    """run_agent ke shuru me: True = Mumbai ne kaam khud nipta diya (baat/sawal ka jawab ya poori pipeline). False = purana agent loop."""
    if not PIPE:
        return False
    try:
        if is_continue(task) and STATE.get("task"):
            d = pipe_pending()
            if not d:
                return False
            STATE["pipe_summary"] = None
            pipe_resume(d)
            return pipe_conclude(task, False)
        if pre_route(task):
            return True
        if STATE["cancel"]:
            return True
        if pipe_eligible(task):
            STATE["task"] = task
            ev("note", text="🧭 bada kaam: DSX plan banayega, JAAT file-file ek saath likhega, aakhir me DSX poora project jaanchega")
            r = pipeline_entry(task)
            if STATE["cancel"]:
                pipe_save()
                ev("note", text="⏹ roka gaya")
                return True
            if r is None:
                return False                                # plan nahi ban paya: purana raasta
            return pipe_conclude(task, True)
    except Exception as e:
        log("pipe_front galti: %s\n%s" % (e, traceback.format_exc()[-700:]))
        if project_has_files():
            ev("err", text="⚠ pipeline me galti (%s: %s). Jo files ban chuki hain wo safe hain; 'aage badho' likho." % (type(e).__name__, str(e)[:120]))
            return True
    return False


_review_hook_5x = review_hook


def review_hook(rel, writer=None):
    """Purane agent ke @@WRITE/@@EDIT ke baad bhi code-card update (LEAN me bhi). Naam/line number code se; AI sirf naye card ki ek line ke liye."""
    if PIPE:
        try:
            card_update(rel, ai=(rel not in memory_load()))
        except Exception as e:
            log("card hook galti %s: %s" % (rel, e))
    return _review_hook_5x(rel, writer)


# ---------- 12) sirf sawal (project/file/check shabd ke saath bhi) -> cards se jawab, agent nahi ----------
_route_task_5x = route_task
WEAK_ACT_RE = re.compile(r"\b(?:project|files?|folder|repo|check|test|read|jaanch|padh\w*|search)\b|\S+\.(?:py|js|ts|kt|java|html|css|json|md|txt)\b", re.I)
QUESTION_RE = re.compile(r"\?|^\W*(?:kya|kaun|kaunsa|kaunsi|kahan|kidhar|kaise|kese|kyu|kyun|kitn\w*|how|what|where|which|why|who|when|explain|samjha\w*|bata\w*|btao|batao)\b", re.I)


def route_task(task):
    """5.4: 'work' aaya par asal me sirf sawal hai (koi banane/badalne/chalane wala shabd nahi) aur project ke cards maujood hain -> 'qa'."""
    r = _route_task_5x(task)
    if r != "work":
        return r
    t = (task or "").strip()
    try:
        if (t and len(t.split()) <= 40 and len(t) <= 400 and "```" not in t and "@@" not in t and not STATE.get("asking")
                and not MODE_FORCE_RE.match(t) and not build_intent(t) and QUESTION_RE.search(t)
                and not ACTION_RE.search(WEAK_ACT_RE.sub(" ", t))
                and memory_text(50) and project_has_files() and not plan_left()):
            return "qa"
    except Exception as e:
        log("route_task qa galti: %s" % e)
    return r


# ---------- 11) run_agent ke aage pipeline ka darwaza ----------
_run_agent_53 = run_agent


def run_agent(task):
    """PIPE chalu ho to pehle pipe_front (baat/sawal/bada kaam Mumbai khud nipta de); False aaye to purana 5.3 agent loop."""
    if PIPE:
        track_reset()
        UNDO_STACK.append({})
        del UNDO_STACK[:-10]
        if pipe_front(task):
            return
        if UNDO_STACK:
            UNDO_STACK.pop()                       # purana run_agent apni entry khud banata hai
    return _run_agent_53(task)


# ================= 25/26 (21.py, v5.6): JAAT sirf likhe | baat/sawal: Groq/DS/DAI, padhai: Gemini | shuru me user se poochho | galat bana: Gemini -> DSX =================
VERSION = "5.6"
ASK_WAIT = float(_env("ASK_WAIT", "600"))                           # user ke jawab ka intezaar 10 min (phir 3 AI faisla karte hain)
ASK_FIRST = _env("ASK_FIRST", "1") != "0"                           # 0 = naye kaam par shuru ke sawal band
CHAT_PREF = ("GROQ", "DS", "DAI", "NV2")                            # sirf baat: JAAT KABHI NAHI
STUDY_PREF = ("GROQ", "DS", "DAI", "NV2")                           # padhai / GK sawal: Gemini NAHI (uski limit bachao): Groq, DS, DeepAI, NV2
TRIAGE_PREF = ("GEM2", "GEM", "GEM3", "GROQ", "DS", "NV2")          # dikkat/photo dekhne wala: Gemini fast (GEM2 think=0 pehle)
QA_PREF = ("GROQ", "NV2", "GEM", "GEM2", "GEM3")                   # project ke sawal: pehle Groq/NV2, Gemini sirf backup
EXPLAIN_PREF = ("GEM", "GEM2", "GEM3", "GROQ", "DS", "NV2")         # setup samjhana / project ke sawal: JAAT nahi
EXAM_PREF = ("GEM", "GROQ", "GEM2", "GEM3", "DS")                   # task samajhne wale: JAAT nahi


def ask_user(question, options=None, ctx=""):
    """25: user se SACH me poochho (LEAN me bhi). ASK_USER=0 do to purana 'khud tay karo' wala raasta."""
    if _env("ASK_USER") == "0":
        ev("note", text="sawal chhod diya, khud tay kar raha hoon: " + (question or "")[:80])
        return ("User ko tech ki jaankari nahi, faisla tumhara. Sabse saada raasta lo jo FREE service par chal sake, "
                "ek line @@NOTE me likho aur seedha kaam shuru karo."), "council"
    return _ask_user_prev(question, options, ctx)


# ---------- A) baat / sawal: JAAT nahi ----------
NET_NOTE = ("Tumhare paas apna internet / web-search hai (jaise DeepSeek, Gemini ya DeepAI ke app me hota hai): taaza cheez ho (naya version, latest news, kaun kya hai abhi) "
            "to purani yaad se mat bolo, khud internet par dekh ke bolo.")
NET_NOTE_EN = ("You have your own internet / web search (like the DeepSeek, Gemini or DeepAI web apps). Before you answer or plan, look up anything that may have changed "
               "(latest library/framework versions, current free hosting limits, new APIs, recent news) and use the newest approach that really works today.")
CHAT_SYS = ("Tum Mumbai ho, user ke phone ka dost jaisa sahayak. Hinglish me chhota (1-4 line), seedha aur sach jawab do. Pyaar aur aaram se baat karo. Kisi file/tool ya @@command ki baat mat karo; "
            "jo pata nahi wo bol do ki pata nahi. " + NET_NOTE)
STUDY_SYS = ("Tum Mumbai ho, padhai ka dost. JALDI aur saaf aasaan Hinglish me jawab do: pehle seedha jawab, phir zaroorat ho to chhote steps/points (formula, example). "
             "Aaram aur pyaar se baat karo, bhari bhaarkam shabd nahi. Agar neeche 'INTERNET SE TAAZA JAANKARI' di ho to jawab usi se do. Jo pata nahi wo bol do. "
             "Kisi file/tool/@@command ki baat mat karo. " + NET_NOTE)
NOW_RE = re.compile(r"abhi|latest|naya\b|nayi\b|naye\b|\bnew\b|today|\baaj\b|current|recent|news|khabar|price|\brate\b|score|2025|2026|bnna|banna|kaun\s+hai|who\s+is\s+the|"
                    r"\bcm\b|chief minister|prime minister|\bpm\b|president|kitne\s+(?:state|district|rajya)", re.I)
QUESTION_STUDY_RE = re.compile(r"\?|\b(?:kya|kaun|kaunsa|kaunsi|kahan|kidhar|kaise|kese|kyu|kyun|kitn\w*|how|what|which|why|who|when|where|explain|samjha\w*|bata\w*|btao|batao|"
                               r"matlab|meaning|difference|farak|definition|formula|derive|theorem|chapter|notes|physics|chemistry|maths?|biology|history|geography|polity|economics|"
                               r"grammar|essay|exam|neet|jee|upsc|ssc|padhai|study)\b", re.I)


def fast_order(names):
    """Gemini fast: GEM2 ka pehla model 'gemini-3.6-flash@think=0' hai (bina sochne ke, sabse tez) - wo sabse aage. Baaki ka order wahi."""
    return sorted(names, key=lambda n: 0 if n == "GEM2" else 1)


def small_gen(system, user, mt=500, prefs=None, fast=False):
    """Baat/sawal ke liye: sirf prefs (default CHAT_PREF) me se. JAAT yahan kabhi nahi aata. fast=True: Gemini ka tez (think=0) wala pehle."""
    names = avail([p for p in (prefs or CHAT_PREF) if p != "JAAT"], len(system) + len(user))
    for nm in (fast_order(names) if fast else names):
        res = gen_call(nm, system, user, mt=mt)
        if res:
            return clean_reply(res[0]).strip(), nm
    return "", ""


_route_task_55 = route_task


def route_task(task):
    """'chat' (halki baat) | 'study' (sawal/padhai) | 'qa' (project ke sawal) | 'work'. Sawal kabhi JAAT ke agent loop me nahi jata."""
    r = _route_task_55(task)
    t = (task or "").strip()
    try:
        if r == "chat":
            small = len(t.split()) <= 5 and bool(SMALLTALK_RE.search(t))
            if not small and fresh_images():
                return "study"                                # photo ke saath baat: Gemini dekhe
            if not small and len(t.split()) >= 3 and (QUESTION_STUDY_RE.search(t) or NOW_RE.search(t)):
                return "study"
            return "chat"
        if r == "work":
            if (t and len(t.split()) <= 60 and len(t) <= 600 and "```" not in t and "@@" not in t and not STATE.get("asking")
                    and not MODE_FORCE_RE.match(t) and not build_intent(t) and not plan_left()
                    and QUESTION_STUDY_RE.search(t) and not ACTION_RE.search(WEAK_ACT_RE.sub(" ", t))):
                return "study"                              # asal me sirf sawal hai (banane/badalne/chalane wala shabd nahi)
    except Exception as e:
        log("route_task study galti: %s" % e)
    return r


IMG_FRESH_SECS = 900
IMG_ASK = ("This is a screenshot or photo sent by a non-technical user about their app/software. First transcribe ALL visible text exactly "
           "(error messages, logs, code, file names, line numbers, button labels). Then say in 2-3 lines what the problem or bug is. Be precise, no guessing.")


def fresh_images():
    """uploads/ ki taaza (15 min) image jo abhi kisi ne padhi nahi."""
    d, out, seen = os.path.join(WORK, "uploads"), [], STATE.setdefault("img_seen", [])
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for n in names:
        p = os.path.join(d, n)
        try:
            if os.path.isfile(p) and os.path.splitext(n)[1].lower() in IMG_MIME and n not in seen and time.time() - os.path.getmtime(p) < IMG_FRESH_SECS:
                out.append((os.path.getmtime(p), n))
        except OSError:
            pass
    return [n for _t, n in sorted(out)][-2:]


def read_images(names):
    """Gemini (vision) se image padho: text + bug. Padhi hui image dobara nahi padhi jaati."""
    texts = []
    for n in names:
        p = os.path.join(WORK, "uploads", n)
        try:
            with open(p, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        STATE.setdefault("img_seen", []).append(n)
        t = look_image(p, raw, IMG_ASK)
        if t:
            texts.append("[PHOTO uploads/%s]\n%s" % (n, clip(t, 2500)))
    return "\n\n".join(texts)


def chat_reply(task, study=False):
    hist = chat_history()
    extra = ""
    imgs = fresh_images() if study else []
    if imgs:
        ev("note", text="👁 Gemini aapki photo padh raha hai...", _log=False)
        extra += "\n\n" + read_images(imgs)
    if study and NOW_RE.search(task):                       # taaza baat (abhi/latest/naya): pehle internet, purani yaad se nahi
        try:
            txt, _c, ok, _x = do_search(clip(task, 160))
            if ok and txt and not str(txt).startswith(("ERROR", "⏹")):
                extra = "\n\nINTERNET SE TAAZA JAANKARI:\n" + clip(re.sub(r"\n\(kisi link.*$", "", str(txt), flags=re.S), 2500)
        except Exception as e:
            log("study search galti: %s" % e)
    user = (("Pichhli baat:\n" + hist + "\n\n") if hist else "") + "User: " + task + extra
    txt, nm = small_gen(STUDY_SYS if study else CHAT_SYS, user, 1500 if study else 700, STUDY_PREF if study else CHAT_PREF)
    txt = "\n".join(l for l in txt.split("\n") if not l.strip().startswith("@@")).strip()
    if not txt:
        return False
    add("user", task)
    add("assistant", txt)
    ev("ai", text=txt, prov=nm)
    return True


def pre_route(task):
    """True = jawab de diya. Baat/sawal ka jawab na ban paye tab bhi JAAT agent par nahi jaata: saaf error deta hai."""
    if not PIPE:
        return False
    r = route_task(task)
    if r == "qa":
        ev("note", text="📖 project ka sawal: cards/plan se (Groq/NV2)", _log=False)
        if project_qa(task):
            return True
        r = "study"
    if r in ("chat", "study"):
        ev("note", text=("📚 sawal: Groq/DS/DeepAI se (Gemini nahi)" if r == "study" else "💬 sirf baat: Groq/DS/DeepAI se") + " - JAAT nahi", _log=False)
        if chat_reply(task, study=(r == "study")):
            return True
        ev("err", text="⚠ Groq/DS/Gemini/DeepAI se abhi jawab nahi aaya (limit ya cooldown). Thodi der baad dobara bhejo. (JAAT sirf code likhta hai, baat nahi karta)")
        add("user", task)
        return True
    return False


# ---------- B) shuru me user se poochho ----------
PHONE_RE = re.compile(r"android|\bapk\b|phone|mobile|\bmob\b|play\s*store|termux app", re.I)
ANDROID_ANS_RE = re.compile(r"android|apk", re.I)
ASK_DONE_RE = re.compile(r"\[User ke jawab:")
NAME_STOP = set("eak ek one the and for with app apps application banao bnao banado banana bana bna banaiye banvao make create build develop design mere meri mera "
                "muja mujhe jo jisme jisse hai ho tha chahiye cheya chahie phone mobile android apk ka ki ke me mein ko se aur ya type new naya nayi simple basic".split())
PLAN_SYS_PIPE = PLAN_SYS_PIPE + " 9) " + NET_NOTE_EN + " Pick the newest stable, free, working choice and put the exact setup/deploy commands that work today in commands."
EXPLAIN_SYS = EXPLAIN_SYS + " " + NET_NOTE
FINAL_SYS_PIPE = FINAL_SYS_PIPE + " If you are unsure whether an API, library call or version exists today, check it on your own internet before you call it a defect."
ANDROID_RULES = (
    "\n[ANDROID RULES: Ye ANDROID app hai (phone par install hoga), browser/web app NAHI. Android template ban chuka hai (gradle, AndroidManifest.xml, theme, icon, "
    ".github/workflows/android.yml): unhe dobara mat likho, sirf zaroorat par badlo (jaise AndroidManifest me permission/activity). Stack = Kotlin + XML layouts + AndroidX + "
    "RecyclerView (template ki dependencies; extra library chahiye to app/build.gradle.kts me add karo). App ki files: MainActivity.kt (template wali file badlo), naye .kt classes, "
    "res/layout/*.xml, res/values/strings.xml. Har R.id / R.layout / R.string jo Kotlin me aaye wo res/ me maujood ho: sab ids notes me fix karo. Permissions (media/storage jaisi) "
    "manifest me likho aur runtime par bhi maango (Android 13+ aur purane dono). APK GitHub Actions banata hai (artifact 'app-debug-apk'): commands.deploy = 'git push' phir Actions tab se APK; "
    "commands.test me sirf offline check, na ho to khaali. Plan me koi .png/.apk binary file mat daalo. Template ke Gradle/AGP/Kotlin/compileSdk versions purane ho sakte hain: apne internet se latest STABLE aur ek-dusre ke saath compatible versions dekho; "
    "pakka ho tabhi settings/build.gradle.kts aur workflow ke gradle-version me badlo, warna template ke versions rehne do.]")


def app_name_for(task):
    """Task se app ka chhota naam (template ke liye). Na mile to 'My App'."""
    t = re.split(r"\[User ke jawab|\[ANDROID", task or "")[0]
    ws = [w for w in re.findall(r"[A-Za-z]{3,}", t) if w.lower() not in NAME_STOP and not _near(w.lower(), BUILD_VERBS)]
    return " ".join(w.capitalize() for w in ws[:3]) or "My App"


ASK_MAX = max(1, min(10, int(_env("ASK_MAX", "10") or 10)))         # Gemini max kitne sawal poochh sakta hai (1-10)
QGEN_SYS = (
    "You are the first helper (Gemini) of a NON-TECHNICAL user who wants software built by an AI team (DSX = architect who plans, JAAT = coder). "
    "READ THE USER MESSAGE CAREFULLY (it may be Hinglish with spelling mistakes). Before building starts, ask ONLY the questions whose answer is truly missing and would change what gets built. "
    "Never ask what the message already says or implies. Ask 0 questions if the message is already clear enough, otherwise 1 to " + str(ASK_MAX) + " (more only for big, vague ideas). "
    "Good topics: where it runs (phone app / web page / terminal-bot), main features, who uses it, language of the content, look/colours, save/share/login needs, limits (free only, offline). "
    "Each question: short, very simple Hinglish, no jargon, 2-4 short options, last option always 'Tum chuno'. "
    "Reply ONLY JSON, no prose: " + '{"questions":[{"q":"...","options":["...","...","Tum chuno"],"key":"platform|language|size|other"}]}' + " "
    "Use key 'platform' ONLY for the where-does-it-run question, 'language' for programming language.")
SPEC_SYS = (
    "You are Gemini, first helper of a NON-TECHNICAL user. Read the USER MESSAGE and the Q&A and write the requirement for DSX (the architect who will plan the files). "
    "Reply ONLY JSON: " + '{"platform":"android|web|cli|bot|other|unknown","spec":"one clear English paragraph: what to build, platform, EVERY feature named or implied, answers given, and for each \'Tum chuno\' answer say DSX decides the simplest free option"}' + ". "
    "Do not invent features the user did not ask for. No code.")


def default_questions_start(task):
    """Gemini na chale to purane 2 sawal."""
    qs = []
    if not (RUN_SAID_RE.search(task or "") or PHONE_RE.search(task or "")):
        qs.append({"q": "Ye kahan chalega? (phone ka app chahiye to Android chuno)", "options": ["Android app (phone, APK)", "Web page (browser)", "Terminal / Bot / Script", "Tum chuno"], "key": "platform"})
    qs.append({"q": "Kitna bada banau? Koi khaas cheez jo zaroor chahiye ho to neeche likh do.", "options": ["Simple (basic)", "Zyada features", "Tum chuno"], "key": "size"})
    return qs[:3]


def build_questions(task):
    """Gemini (fast) message dhyaan se padh ke 0-10 sawal banata hai (sirf jo sach me adhura ho). Gemini na chale to purane 2 sawal."""
    try:
        ev("note", text="🧠 Gemini aapka message padh raha hai, zaroori sawal soch raha hai...", _log=False)
        txt, nm = small_gen(QGEN_SYS, "USER MESSAGE:\n" + clip(task, 2500), 1400, TRIAGE_PREF, fast=True)
        d = parse_json_reply(txt) if txt else None
        items = d.get("questions") if isinstance(d, dict) else (d if isinstance(d, list) else None)
        if items is None:
            return default_questions_start(task)
        qs = []
        for it in items:
            if not isinstance(it, dict) or not str(it.get("q") or "").strip():
                continue
            opts = [clip(str(o).strip(), 60) for o in (it.get("options") or []) if str(o).strip()][:4]
            if opts and not any(re.search(r"tum\s*chuno", o, re.I) for o in opts):
                opts = opts[:3] + ["Tum chuno"]
            qs.append({"q": clip(str(it["q"]).strip(), 220), "options": opts, "key": str(it.get("key") or "other").strip().lower()})
        return qs[:ASK_MAX]
    except Exception as e:
        log("build_questions galti: %s" % e)
        return default_questions_start(task)


def gem_spec(task, answers):
    """Gemini: message + jawab se DSX ke liye saaf requirement. '' = nahi bana."""
    try:
        user = "USER MESSAGE:\n%s\n\nQ&A:\n%s" % (clip(task, 2500), "\n".join(answers) or "-")
        txt, nm = small_gen(SPEC_SYS, user, 1200, TRIAGE_PREF, fast=True)
        d = parse_json_reply(txt) if txt else None
        if isinstance(d, dict):
            return str(d.get("platform") or "").lower(), clip(str(d.get("spec") or "").strip(), 1500)
    except Exception as e:
        log("gem_spec galti: %s" % e)
    return "", ""


def wants_questions(task):
    t = task or ""
    return bool(ASK_FIRST and build_intent(t) and not ASK_DONE_RE.search(t) and not MODE_FORCE_RE.match(t) and "```" not in t and "@@" not in t
                and not project_has_files() and not STATE.get("asking"))


def ask_first(task):
    """(task + jawab + Gemini spec, platform, stop?). platform = 'android' | ''."""
    answers, plat = [], ("android" if (ANDROID_TASK_RE.search(task) or PHONE_RE.search(task)) else "")
    for q in build_questions(task):
        ans, how = ask_user(q["q"], q["options"], ctx="Task: " + task[:500])
        if how == "stop":
            return task, plat, True
        answers.append("%s -> %s" % (q["q"], ans))
        if q.get("key") == "platform":
            plat = "android" if ANDROID_ANS_RE.search(ans or "") else ""
            if not plat and re.search(r"terminal|bot|script", ans or "", re.I) and not LANG_SAID_RE.search(task):
                a2, h2 = ask_user("Kaunsi language me banau?", ["Python", "JavaScript", "Tum chuno"], ctx="Task: " + task[:500])
                if h2 == "stop":
                    return task, plat, True
                answers.append("Kaunsi language me banau? -> %s" % a2)
    gp, spec = gem_spec(task, answers)                      # Gemini ki saaf requirement DSX tak
    if not plat and gp == "android":
        plat = "android"
    out = task + ("\n[User ke jawab: %s]" % " | ".join(answers) if answers else "\n[User ke jawab: (koi sawal nahi poochha, message saaf tha)]")
    if spec:
        out += "\n[GEMINI SPEC - DSX ke liye user ki requirement (plan khud tay karo): %s]" % spec
    return out, plat, False


def android_prepare(task_plain):
    """Android template (gradle, manifest, theme, icon, GitHub Actions) pehle banao: pipeline ab sirf app ki files likhegi."""
    name = app_name_for(task_plain)
    ev("note", text="📱 Android app: pehle template (gradle, manifest, GitHub Actions) bana raha hoon, phir DSX plan, JAAT likhega")
    res, made, ok, _c = do_template("android " + name)
    ev("step", kind="TEMPLATE", arg="android " + name, out=clip(res, 700), ok=bool(ok), prov="mumbai")
    wl("TEMPLATE android %s" % name)
    return ANDROID_RULES


def run_pipeline_for(task_plain, task_for_plan, plat):
    """True = nipta; False = pipeline plan nahi bana paya (purana agent loop)."""
    STATE["task"] = task_plain
    ev("note", text="🧭 bada kaam: DSX plan banayega, JAAT file-file ek saath likhega, aakhir me DSX poora project jaanchega")
    r = pipeline_entry(task_for_plan)
    if STATE["cancel"]:
        pipe_save()
        ev("note", text="⏹ roka gaya")
        return True
    if r is None:
        return False
    return pipe_conclude(task_plain, True)


# ---------- C) user ki dikkat / shikayat / photo: Gemini (fast) pehle dekhe -> bada ulta-pulta ho to DSX ko mote jankari, chhota ho to khud bataye ----------
COMPLAINT_RE = re.compile(r"galat\s*(?:bana|bna|banaya)|\bwrong\b|ye\s*nahi|yeh\s*nahi|(?:nhi|nahi)\s*(?:muja|mujhe|mena|maine|mere|mera)|"
                          r"(?:muja|mujhe|mena|maine)\b.{0,50}\b(?:cheya|chahiye|chaiye|chahie|chahta|chahti|wanted)|\b(?:cheya|chahiye|chaiye|chahie)\s*(?:tha|thi)\b|i\s*wanted|not\s*what\s*i", re.I)
TRIAGE_SYS = (
    "You are the first helper of a NON-TECHNICAL user who is building software with an AI team (DSX = the lead architect who plans, JAAT = the one who types code). "
    "The user reports a problem: something built wrong, a bug, an error, or sent a photo/screenshot. Decide quickly and calmly. Use the OLD TASK, WHAT WAS BUILT, CHAT, "
    "NEW MESSAGE and PHOTO TEXT (text read from the user's photo, may be empty). " + NET_NOTE_EN + " Reply ONLY JSON, no prose: "
    '{"severity":"major|minor|question",'
    '"platform":"android|web|cli|bot|other|unknown",'
    '"task":"for major: one full clear paragraph in English: what the user REALLY wants built (platform + every feature named or implied)",'
    '"wrong":"one line: what was built wrong or what is broken",'
    '"bug":"exact bug from the message or photo: error text, file, line if visible (empty if none)",'
    '"brief_for_dsx":"for major: ONLY the rough picture for DSX in 3-6 short lines (what the user wanted, what is wrong, what must change). Not the full logs.",'
    '"fix":"for minor: exact instruction for the coder: which file/function and what to change",'
    '"reply":"2-5 lines in friendly calm Hinglish to the user: what you understood (say it in simple words) and what will happen next. Never blame the user, no jargon.",'
    '"question":"ONE short question ONLY if something essential is unclear, else empty string"}. '
    "severity: major = almost everything is upside down (wrong platform like a web page instead of an Android app, wrong idea, many parts wrong) so the plan must be redone. "
    "minor = small bug, typo, one wrong button/colour/behaviour, small change - the existing code just needs a small fix. "
    "question = the user only asks something or wants an explanation (also reading a photo for them), nothing needs changing. For minor and question you answer yourself; do NOT involve DSX.")


def is_triage(task, has_img):
    t = (task or "").strip()
    if not t or "@@" in t or len(t) > 6000 or MODE_FORCE_RE.match(t) or STATE.get("asking") or is_continue(t):
        return False
    if not (STATE.get("task") or project_has_files()):
        return False
    if "```" not in t and len(t.split()) <= 80 and COMPLAINT_RE.search(t):
        return True
    if has_img or BUGONLY_RE.search(t):
        return True
    return False


def archive_old():
    """Purani (galat) files uploads/old_<time>/ me rakh do (hatati nahi): naya kaam saaf folder par, purana safe. uploads/ aage ki jaanch me nahi aata."""
    dst = os.path.join("uploads", time.strftime("old_%m%d_%H%M%S"))
    moved = 0
    for rel in list_files(None):
        if rel.split(os.sep, 1)[0] == "uploads" or rel.lower().endswith(".zip"):
            continue
        try:
            to = safe(os.path.join(dst, rel))
            os.makedirs(os.path.dirname(to), exist_ok=True)
            shutil.move(safe(rel), to)
            moved += 1
        except (OSError, ValueError) as e:
            log("archive_old %s: %s" % (rel, e))
    for root, dirs, files in os.walk(WORK, topdown=False):                      # khaali folder hata do
        if root != WORK and not os.listdir(root) and os.path.relpath(root, WORK).split(os.sep, 1)[0] not in ("uploads", ".mumbai"):
            try:
                os.rmdir(root)
            except OSError:
                pass
    return dst, moved


def triage(task):
    """True = nipta (jawab de diya / pipeline chali / roka gaya). False = purane raaste par jao; STATE['task_override'] me Gemini ka hint ho sakta hai."""
    old = STATE.get("task") or ""
    bp = (PIPE_ST.get("bp") if PIPE_ST else None) or (pipe_load() or {}).get("bp") or {}
    built = ("%s | stack: %s | files: %s" % (bp.get("summary", ""), bp.get("stack", ""), ", ".join(f["path"] for f in (bp.get("files") or [])[:15]))
             if bp else ", ".join(f for f in list_files(40) if f.split(os.sep, 1)[0] != "uploads")) or "(pata nahi)"
    imgs = fresh_images()
    img_txt = ""
    if imgs:
        ev("note", text="👁 Gemini aapki photo padh raha hai (error/bug kya hai)...")
        img_txt = read_images(imgs)
    ev("note", text="🧠 Gemini (fast) aapki dikkat dekh raha hai; bada ulta-pulta hua to DSX ko mote jankari jayegi, chhota hua to Gemini khud batayega", _log=False)
    user = "OLD TASK:\n%s\n\nWHAT WAS BUILT:\n%s\n\nCHAT:\n%s\n\nPHOTO TEXT:\n%s\n\nNEW MESSAGE FROM USER:\n%s" % (
        clip(re.split(r"\[ANDROID", old)[0], 1500), clip(built, 1200), recent_chat(8, 400) or "-", clip(img_txt, 2500) or "-", clip(task, 2500))
    txt, nm = small_gen(TRIAGE_SYS, user, 1500, TRIAGE_PREF, fast=True)
    d = parse_json_reply(txt) if txt else None
    if not isinstance(d, dict):
        if img_txt:                                         # Gemini ka JSON nahi aaya par photo padh li: purane agent ko photo ka text do
            STATE["task_override"] = task + "\n\n" + img_txt
        return False
    sev = str(d.get("severity") or "").strip().lower()
    reply = clip(str(d.get("reply") or "").strip(), 900)
    bug = clip(str(d.get("bug") or "").strip(), 500)
    wrong = clip(str(d.get("wrong") or "").strip(), 300)
    if reply:
        ev("ai", text=reply, prov=nm)
    ev("note", text="🧠 %s ne dekha: %s%s" % (nm, {"major": "bahut kuch galat (DSX naya plan banayega)", "minor": "chhoti dikkat (Gemini ne bataya, JAAT theek karega)",
                                                  "question": "sirf jaankari (Gemini ne khud bataya)"}.get(sev, sev), (" | bug: " + bug) if bug else ""), _log=False)
    if sev == "question":
        if not reply:
            return False
        add("user", task)
        add("assistant", reply)
        return True
    if sev != "major":                                       # minor (ya samajh na aaya): Gemini ne bata diya, JAAT chhota fix type kare
        fx = clip(str(d.get("fix") or bug or wrong).strip(), 800)
        STATE["task_override"] = ("CHHOTA FIX (Gemini ne jaanch ke bataya): %s\nUser ne kaha: %s%s" % (fx or "user ki baat dekho", clip(task, 1500), ("\n\n" + img_txt) if img_txt else ""))
        return False
    # ---- major: platform tay, zaroorat par ek sawal, phir DSX ko MOTI jankari ----
    plat = str(d.get("platform") or "unknown").lower()
    want = str(d.get("task") or "").strip() or (clip(old, 400) + " | USER KI NAYI BAAT: " + clip(task, 600))
    if plat == "unknown" and PHONE_RE.search(task):
        plat = "android"
    q = str(d.get("question") or "").strip()
    if q:
        ans, how = ask_user(q, [], ctx="Task: " + want[:400])
        if how == "stop":
            return True
        want += " | USER KA JAWAB (%s): %s" % (q, ans)
        if plat == "unknown" and ANDROID_ANS_RE.search(ans or ""):
            plat = "android"
    if plat == "unknown" and not q:
        ans, how = ask_user("Aap ye kahan chalana chahte ho?", ["Android app (phone, APK)", "Web page (browser)", "Terminal / Bot / Script"], ctx="Task: " + want[:400])
        if how == "stop":
            return True
        plat = "android" if ANDROID_ANS_RE.search(ans or "") else "other"
        want += " | USER KA JAWAB: platform -> %s" % ans
    is_and = plat == "android"
    rebuild = project_has_files() and is_and != is_android_project(list_files(None))      # platform badla (jaise web -> Android): naye sire se
    if rebuild:
        dst, n = archive_old()
        ev("note", text="📦 purani galat files (%d) %s me rakh di (hatayi nahi); naya kaam saaf folder par" % (n, dst))
    brief = clip(str(d.get("brief_for_dsx") or "").strip(), 700)
    plain = want + "\n[User ke jawab: platform -> %s | shikayat -> %s]" % ("Android app" if is_and else plat, clip(task, 200))
    plan_task = (plain + "\n[GEMINI KI RAY - DSX ke liye mote jankari (aap khud sahi plan tay karo): %s | galat bana: %s%s]" % (
        brief or "-", wrong or "-", (" | bug: " + bug) if bug else "")) + (android_prepare(plain) if is_and else "")
    PIPE_ST.clear()
    STATE["pipe_summary"] = None
    ok = run_pipeline_for(plain, plan_task, "android" if is_and else "")
    if not ok:
        STATE["task_override"] = plain + (ANDROID_RULES if is_and else "")
    return ok


# ---------- D) front: complaint -> baat/sawal -> shuru ke sawal -> pipeline ----------
def pipe_front(task):
    """True = Mumbai ne khud nipta diya. False = purana agent loop (JAAT likhta/badalta hai; baat/sawal yahan kabhi nahi aate)."""
    if not PIPE:
        return False
    try:
        if is_continue(task) and STATE.get("task"):
            d = pipe_pending()
            if not d:
                return False
            STATE["pipe_summary"] = None
            pipe_resume(d)
            return pipe_conclude(task, False)
        if is_triage(task, bool(fresh_images())):
            try:
                if triage(task):
                    return True
            except Exception as e:
                log("triage galti: %s\n%s" % (e, traceback.format_exc()[-500:]))
            if STATE["cancel"]:
                return True
            if STATE.get("task_override"):
                return False                                # Gemini ne chhota fix bata diya: JAAT type karega
        if pre_route(task):
            return True
        if STATE["cancel"]:
            return True
        if wants_questions(task):
            full, plat, stop = ask_first(task)
            if stop or STATE["cancel"]:
                return True
            plan_task = full + (android_prepare(full) if plat == "android" else "")
            PIPE_ST.clear()
            STATE["pipe_summary"] = None
            if run_pipeline_for(full, plan_task, plat):
                return True
            STATE["task_override"] = full + (ANDROID_RULES if plat == "android" else "")
            return False
        if pipe_eligible(task):
            STATE["task"] = task
            ev("note", text="🧭 bada kaam: DSX plan banayega, JAAT file-file ek saath likhega, aakhir me DSX poora project jaanchega")
            r = pipeline_entry(task)
            if STATE["cancel"]:
                pipe_save()
                ev("note", text="⏹ roka gaya")
                return True
            if r is None:
                return False
            return pipe_conclude(task, True)
    except Exception as e:
        log("pipe_front galti: %s\n%s" % (e, traceback.format_exc()[-700:]))
        if project_has_files():
            ev("err", text="⚠ pipeline me galti (%s: %s). Jo files ban chuki hain wo safe hain; 'aage badho' likho." % (type(e).__name__, str(e)[:120]))
            return True
    return False


# ---------- CHHOTA FIX MODE: chhota bug = chhota kaam (plan nahi, gradle/android jaanch nahi, kam kadam) ----------
MINI_STEPS = int(_env("MINI_STEPS", "12") or 12)                    # chhote fix me max kadam
MINI_RULES = ("\n[CHHOTA FIX MODE: sirf bataya hua bug theek karo, aur kuch nahi. (1) Sirf wahi file @@READ karo jisme bug hai. "
              "(2) @@EDIT se sirf galat hissa badlo; poori file @@WRITE mat karo (file toot-ti dikhe to pehle chhoti line range @@READ karke dobara dekho). "
              "(3) Nayi file, gradle, manifest, icon, docs, MEMORY.md, workflow mat banao/badlo. (4) Ek @@VERIFY, phir @@DONE. "
              "(5) Tool ka natija khud mat likho, Mumbai ke natija ka intezaar karo.]")

_do_understand_full = do_understand


def do_understand(task):
    if STATE.get("mini_fix"):                                          # na 6 criteria, na DSX plan
        STATE.update(utype="code", goal=clip(task, 200), level=1, criteria=["D1: bataya hua bug theek hua, naya kuch nahi badla"],
                     plan_items=[], plan_ck=[])
        ev("note", text="🔧 chhota fix: seedha file padh ke theek karunga (plan aur poori Android jaanch nahi)", _log=False)
        return MINI_RULES
    return _do_understand_full(task)


_verify_project_full = verify_project


def verify_project(force_android=False):
    if STATE.get("mini_fix"):                                          # sirf syntax / copy-paste jaanch: gradle-icon-manifest nahi
        files, items = list_files(None), []
        check_generic(files, items)
        return [(lv, m) for lv, m in items]
    return _verify_project_full(force_android)


_check_generic_base = check_generic


def check_generic(files, items):
    _check_generic_base(files, items)
    for f in files:                                                    # copy-paste galtiyan: AI ki file me aksar dohrav aa jata hai
        if not f.lower().endswith((".dart", ".kt", ".java", ".js", ".ts", ".py")):
            continue
        txt = _read(f, 400000)
        if not txt:
            continue
        lines, run = txt.split("\n"), 1
        for i in range(1, len(lines) + 1):
            cur = lines[i].strip() if i < len(lines) else None
            prev = lines[i - 1].strip()
            if cur is not None and cur == prev and len(cur) >= 12:
                run += 1
                continue
            if run >= 3:
                items.append(("E", "%s line %d: ek hi line %d baar lagatar likhi hai: '%s' (copy-paste galti, ek rakho)" % (f, i - run + 1, run, clip(prev, 60))))
            run = 1
        if f.lower().endswith(".dart"):
            cnt = {}
            for m in re.finditer(r"^\s*factory\s+([A-Za-z_]\w*(?:\.\w+)?)\s*\(", txt, re.M):
                cnt[m.group(1)] = cnt.get(m.group(1), 0) + 1
            for k, v in cnt.items():
                if v > 1:
                    items.append(("E", "%s me 'factory %s' %d baar hai; sirf ek chahiye" % (f, k, v)))


def run_agent(task):
    """PIPE chalu ho to pehle pipe_front; False aaye to purana agent loop. 'CHHOTA FIX' ho to mini mode (kam kadam, kam jaanch)."""
    global MAX_STEPS
    if PIPE:
        track_reset()
        UNDO_STACK.append({})
        del UNDO_STACK[:-10]
        if pipe_front(task):
            return
        if UNDO_STACK:
            UNDO_STACK.pop()                       # purana run_agent apni entry khud banata hai
    ov = STATE.pop("task_override", None)
    mini = bool(ov and ov.startswith("CHHOTA FIX"))
    old_steps = MAX_STEPS
    STATE["mini_fix"] = mini
    if mini:
        MAX_STEPS = MINI_STEPS
    try:
        return _run_agent_53(ov or task)
    finally:
        MAX_STEPS = old_steps
        STATE["mini_fix"] = False


if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup()
    else:
        try:
            with open(os.path.join(ROOT, ".mumbai", "last")) as f:
                last_proj = clean_proj(f.read()) or "main"
        except OSError:
            last_proj = "main"
        set_project(last_proj)
        reload_conf()
        replay(load_state())
        print("Mumbai %s chal raha hai, port %d" % (VERSION, PORT))
        print("Kaam ka folder:", WORK)
        print("AI chalu:", ", ".join(n for n in PRIORITY if ready(n)) or "koi nahi (keys environment me daalo)")
        print("AI band (key nahi):", ", ".join(n for n in PRIORITY if not ready(n)) or "-")
        if PASSWORD == "8888":
            print("⚠ password abhi 8888 hai; MUMBAI_PASSWORD environment variable se badal sakte ho")
        ThreadingHTTPServer((BIND, PORT), H).serve_forever()
