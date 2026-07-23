"""浏览器指纹随机化（多浏览器家族）。

每次注册调用 generate_fingerprint() 生成一套一致的指纹组合：
  - TLS impersonate（curl_cffi 用）
  - User-Agent
  - sec-ch-ua / sec-ch-ua-platform / sec-ch-ua-mobile（仅 Chrome）
  - 屏幕分辨率
  - Accept-Language
  - browser_type 标识（mac_safari / ios_safari / chrome / firefox）
  - fallback_impersonates 同家族回退列表
"""
from __future__ import annotations

import random

# ---------------------------------------------------------------------------
# macOS Safari（保留原有）
# ---------------------------------------------------------------------------
_SAFARI_VERSIONS = [
    {
        "impersonate": "safari15_3",
        "safari_ver": "15.3",
        "webkit_ver": "605.1.15",
        "macos_versions": ["10_15_7", "12_0", "12_1"],
    },
    {
        "impersonate": "safari15_5",
        "safari_ver": "15.5",
        "webkit_ver": "605.1.15",
        "macos_versions": ["10_15_7", "12_4", "12_5"],
    },
    {
        "impersonate": "safari17_0",
        "safari_ver": "17.0",
        "webkit_ver": "605.1.15",
        "macos_versions": ["13_6", "14_0", "14_1"],
    },
    {
        "impersonate": "safari18_0",
        "safari_ver": "18.0",
        "webkit_ver": "605.1.15",
        "macos_versions": ["14_4", "14_5", "15_0", "15_1"],
    },
]

_MAC_SCREENS = [
    "1440x900",
    "1512x982",
    "1728x1117",
    "2560x1440",
    "1920x1080",
]

# ---------------------------------------------------------------------------
# iOS Safari
# ---------------------------------------------------------------------------
_IOS_SAFARI_VERSIONS = [
    {
        "impersonate": "safari17_2_ios",
        "safari_ver": "17.2",
        "webkit_ver": "605.1.15",
        "ios_versions": ["17_1_2", "17_2"],
    },
    {
        "impersonate": "safari18_0_ios",
        "safari_ver": "18.0",
        "webkit_ver": "605.1.15",
        "ios_versions": ["18_0", "18_1", "18_1_1"],
    },
]

_IPHONE_SCREENS = [
    "390x844",   # iPhone 13 / 14
    "393x852",   # iPhone 14 Pro / 15
    "428x926",   # iPhone 13 Pro Max / 14 Plus
    "430x932",   # iPhone 14 Pro Max / 15 Plus
]

# ---------------------------------------------------------------------------
# Chrome (Windows)
# ---------------------------------------------------------------------------
_CHROME_VERSIONS = [
    {
        "impersonate": "chrome136",
        "ver": "136",
        "full_ver": "136.0.0.0",
        "not_a_brand": '"Not.A/Brand";v="99"',
    },
    {
        "impersonate": "chrome142",
        "ver": "142",
        "full_ver": "142.0.0.0",
        "not_a_brand": '"Not/A)Brand";v="8"',
    },
    {
        "impersonate": "chrome146",
        "ver": "146",
        "full_ver": "146.0.0.0",
        "not_a_brand": '"Not?A_Brand";v="99"',
    },
]

_WIN_SCREENS = [
    "1920x1080",
    "1366x768",
    "2560x1440",
    "1536x864",
    "1440x900",
]

# ---------------------------------------------------------------------------
# Firefox (Windows)
# ---------------------------------------------------------------------------
_FIREFOX_VERSIONS = [
    {"impersonate": "firefox133", "ver": "133.0"},
    {"impersonate": "firefox144", "ver": "144.0"},
]

# ---------------------------------------------------------------------------
# 共享
# ---------------------------------------------------------------------------
_LANGUAGES = [
    ("en-US", "en-US,en;q=0.9"),
    ("en-US", "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"),
    ("en-GB", "en-GB,en;q=0.9,en-US;q=0.8"),
    ("en-US", "en-US,en;q=0.9,ja;q=0.8"),
]

_BROWSER_WEIGHTS = [
    ("mac_safari", 30),
    ("ios_safari", 15),
    ("chrome",     35),
    ("firefox",    20),
]

_BROWSER_TYPES = [t for t, _ in _BROWSER_WEIGHTS]
_WEIGHTS = [w for _, w in _BROWSER_WEIGHTS]


# ---------------------------------------------------------------------------
# 指纹生成
# ---------------------------------------------------------------------------

def _gen_mac_safari(r: random.Random) -> dict:
    safari = r.choice(_SAFARI_VERSIONS)
    macos_ver = r.choice(safari["macos_versions"])
    others = [s["impersonate"] for s in _SAFARI_VERSIONS if s["impersonate"] != safari["impersonate"]]
    return {
        "browser_type": "mac_safari",
        "impersonate": safari["impersonate"],
        "fallback_impersonates": [safari["impersonate"]] + r.sample(others, min(2, len(others))),
        "user_agent": (
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X {macos_ver}) "
            f"AppleWebKit/{safari['webkit_ver']} (KHTML, like Gecko) "
            f"Version/{safari['safari_ver']} Safari/{safari['webkit_ver']}"
        ),
        "sec_ch_ua": "",
        "sec_ch_ua_platform": "",
        "sec_ch_ua_mobile": "",
        "screen": r.choice(_MAC_SCREENS),
    }


def _gen_ios_safari(r: random.Random) -> dict:
    safari = r.choice(_IOS_SAFARI_VERSIONS)
    ios_ver = r.choice(safari["ios_versions"])
    others = [s["impersonate"] for s in _IOS_SAFARI_VERSIONS if s["impersonate"] != safari["impersonate"]]
    fallbacks = [safari["impersonate"]] + others
    return {
        "browser_type": "ios_safari",
        "impersonate": safari["impersonate"],
        "fallback_impersonates": fallbacks,
        "user_agent": (
            f"Mozilla/5.0 (iPhone; CPU iPhone OS {ios_ver} like Mac OS X) "
            f"AppleWebKit/{safari['webkit_ver']} (KHTML, like Gecko) "
            f"Version/{safari['safari_ver']} Mobile/15E148 Safari/604.1"
        ),
        "sec_ch_ua": "",
        "sec_ch_ua_platform": "",
        "sec_ch_ua_mobile": "",
        "screen": r.choice(_IPHONE_SCREENS),
    }


def _gen_chrome(r: random.Random) -> dict:
    chrome = r.choice(_CHROME_VERSIONS)
    others = [c["impersonate"] for c in _CHROME_VERSIONS if c["impersonate"] != chrome["impersonate"]]
    sec_ch_ua = (
        f'"Chromium";v="{chrome["ver"]}", '
        f'"Google Chrome";v="{chrome["ver"]}", '
        f'{chrome["not_a_brand"]}'
    )
    return {
        "browser_type": "chrome",
        "impersonate": chrome["impersonate"],
        "fallback_impersonates": [chrome["impersonate"]] + r.sample(others, min(2, len(others))),
        "user_agent": (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome['full_ver']} Safari/537.36"
        ),
        "sec_ch_ua": sec_ch_ua,
        "sec_ch_ua_platform": '"Windows"',
        "sec_ch_ua_mobile": "?0",
        "screen": r.choice(_WIN_SCREENS),
    }


def _gen_firefox(r: random.Random) -> dict:
    ff = r.choice(_FIREFOX_VERSIONS)
    others = [f["impersonate"] for f in _FIREFOX_VERSIONS if f["impersonate"] != ff["impersonate"]]
    fallbacks = [ff["impersonate"]] + others
    return {
        "browser_type": "firefox",
        "impersonate": ff["impersonate"],
        "fallback_impersonates": fallbacks,
        "user_agent": (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{ff['ver']}) "
            f"Gecko/20100101 Firefox/{ff['ver']}"
        ),
        "sec_ch_ua": "",
        "sec_ch_ua_platform": "",
        "sec_ch_ua_mobile": "",
        "screen": r.choice(_WIN_SCREENS),
    }


_GENERATORS = {
    "mac_safari": _gen_mac_safari,
    "ios_safari": _gen_ios_safari,
    "chrome": _gen_chrome,
    "firefox": _gen_firefox,
}


def generate_fingerprint(rng: random.Random | None = None) -> dict:
    """生成一套一致的浏览器指纹。

    返回 dict:
        browser_type: str     — 浏览器家族
        impersonate: str      — curl_cffi TLS 指纹名
        fallback_impersonates: list[str] — 同家族回退 impersonate 列表
        user_agent: str       — 完整 UA 字符串
        sec_ch_ua: str        — Client Hints（仅 Chrome 非空）
        sec_ch_ua_platform: str
        sec_ch_ua_mobile: str
        screen: str           — 屏幕分辨率 (WxH)
        lang: str             — 主语言
        lang_full: str        — 完整 Accept-Language
    """
    r = rng or random
    browser_type = r.choices(_BROWSER_TYPES, weights=_WEIGHTS, k=1)[0]
    fp = _GENERATORS[browser_type](r)

    lang, lang_full = r.choice(_LANGUAGES)
    fp["lang"] = lang
    fp["lang_full"] = lang_full
    return fp


# ---------------------------------------------------------------------------
# impersonate → UA 映射（TLS 旋转用）
# ---------------------------------------------------------------------------

_ALL_IMPERSONATES: dict[str, dict] = {}

for s in _SAFARI_VERSIONS:
    _ALL_IMPERSONATES[s["impersonate"]] = {"type": "mac_safari", "data": s}
for s in _IOS_SAFARI_VERSIONS:
    _ALL_IMPERSONATES[s["impersonate"]] = {"type": "ios_safari", "data": s}
for c in _CHROME_VERSIONS:
    _ALL_IMPERSONATES[c["impersonate"]] = {"type": "chrome", "data": c}
for f in _FIREFOX_VERSIONS:
    _ALL_IMPERSONATES[f["impersonate"]] = {"type": "firefox", "data": f}


def ua_for_impersonate(impersonate: str, current_ua: str) -> str:
    """根据 impersonate 名生成匹配的 UA。"""
    entry = _ALL_IMPERSONATES.get(impersonate)
    if not entry:
        return current_ua

    t, d = entry["type"], entry["data"]

    if t == "mac_safari":
        macos_ver = random.choice(d["macos_versions"])
        return (
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X {macos_ver}) "
            f"AppleWebKit/{d['webkit_ver']} (KHTML, like Gecko) "
            f"Version/{d['safari_ver']} Safari/{d['webkit_ver']}"
        )
    elif t == "ios_safari":
        ios_ver = random.choice(d["ios_versions"])
        return (
            f"Mozilla/5.0 (iPhone; CPU iPhone OS {ios_ver} like Mac OS X) "
            f"AppleWebKit/{d['webkit_ver']} (KHTML, like Gecko) "
            f"Version/{d['safari_ver']} Mobile/15E148 Safari/604.1"
        )
    elif t == "chrome":
        return (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{d['full_ver']} Safari/537.36"
        )
    elif t == "firefox":
        return (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{d['ver']}) "
            f"Gecko/20100101 Firefox/{d['ver']}"
        )
    return current_ua
