"""화면 문구 사전(web/js/i18n.js) 점검: 중복 키 금지, 화면 코드가 쓰는 고정 키는 모두 사전에 있어야 한다.

JS 객체 리터럴은 같은 키가 두 번 나오면 뒤의 값이 앞의 값을 덮어써서 다른 화면 문구가 조용히 바뀐다.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web" / "js"
KEY_RE = re.compile(r"^\s*'([^']+)': \[", re.M)


def _keys() -> list[str]:
    return KEY_RE.findall((WEB / "i18n.js").read_text(encoding="utf-8"))


def test_no_duplicate_keys():
    keys = _keys()
    dup = sorted({k for k in keys if keys.count(k) > 1})
    assert not dup, f"i18n.js 중복 키: {dup}"


def test_static_keys_used_by_views_exist():
    keys = set(_keys()) | set(re.findall(r"'([A-Za-z_]+\.[A-Za-z_.]+)': \[",
                                         (WEB / "i18n.js").read_text(encoding="utf-8")))
    missing = {}
    for f in [*WEB.glob("*.js"), *WEB.glob("views/*.js"), *WEB.glob("components/*.js")]:
        used = set(re.findall(r"\bt\('([^'`]+)'", f.read_text(encoding="utf-8")))
        if bad := sorted(k for k in used if k not in keys):
            missing[f.name] = bad
    assert not missing, f"사전에 없는 키: {missing}"
