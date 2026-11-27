"""資料 repo 的個資掃描。"""
from __future__ import annotations

import re
from pathlib import Path

from .common import TRIPS
from .gitops import added_lines, files_to_scan


PII_OK = "pii-ok"
URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&'*+,;=%]+")  # 只吃 ASCII 網址字元：中文緊接在網址後面，也要掃
# 網址本身不掃（路徑裡的數字太多），但訂位管理之類的連結會把代號、姓名、聯絡方式放在參數裡
URL_PII_RE = re.compile(
    r"[?&#;](?i:pnr|confirmation\w*|conf_?(?:no|num|number|code)|(?:reservation|booking)_?(?:id|no|num|number|ref|reference|code)"
    r"|(?:last|first|family|given|sur)_?name|surname|passport\w*)=[^&#\s]"
    r"|[?&#;](?i:\w*(?:phone|mobile)\w*|tel(?:_?(?:no|num|number))?)=(?:[^&#\s\d]*\d){6}"  # 電話、信箱看值：mobile=1 這類開關不算
    r"|[?&#;](?i:\w*mail\w*)=[^&#\s]*(?:@|%40)")
CARD_RE = re.compile(r"(?<!\d)(?:\d{13,19}|\d{4}(?:[ -]\d{4}){2}[ -]\d{1,7}|\d{4}[ -]\d{6}[ -]\d{5})(?!\d)")
SAY = r"\s*(?:是|為|は|[:：=#])?\s*"  # 標籤和值之間：冒號、「是」「為」，或什麼都沒有
# 代號：全大寫英數字、含數字的英數字（排除 number 這種英文字），或用 - . 分段的（12-3456-7890、4012.345.678）；
# 日期（2026-12-01、12-01）不算：「確認碼 12-01 前寄到」
CODE = r"(?!(?:\d{4}-)?\d{1,2}-\d{1,2}(?![\w.-]))(?:[A-Z0-9]{2,}(?:[-.][A-Z0-9]{2,})+|[A-Z0-9]{5,}|(?=[A-Za-z]*\d)[A-Za-z0-9]{5,})"
PII_COMMON: list[tuple[str, re.Pattern[str]]] = [  # 不分國家：靠前面的標籤判斷
    ("護照號碼", re.compile(rf"(?i:護照|passport|旅券)\s*(?i:號碼|號|番号|number|no\.?|#)?{SAY}(?i:[A-Z]{{0,2}})\d{{7,9}}(?!\d)")),
    ("信用卡資訊", re.compile(rf"(?i:信用卡號|卡號|card number|CVV|CVC|安全碼){SAY}\d")),
    ("密碼", re.compile(rf"(?:密碼|パスワード|暗証番号){SAY}[A-Za-z0-9][^\s，。、）)」]{{3,}}")),
    # 英文的標籤要有冒號，或「is」後面接含數字的值：避免 password manager、the password is required
    ("密碼", re.compile(r"(?i:password|passwd)\s*(?:(?i:is)\s+(?=\S*\d)\S{4,}|[:：=]\s*\S)")),
    ("密碼", re.compile(r"(?<![A-Za-z])(?i:PIN)(?![A-Za-z])\s*(?:碼|(?i:code))?\s*(?:(?i:is)\s+|是|為|は|[:：=#])?\s*\d{4,}")),
    ("生日", re.compile(r"(?i:生日|出生日期|出生年月日|生年月日|誕生日|birthday|date of birth|\bDOB\b)\s*(?:是|為|[:：])?\s*\d")),
    ("電話號碼", re.compile(r"(?i:手機|電話號碼|phone number|mobile)\s*(?:是|為|[:：])\s*\+?\d")),
    # 人的市話：只認住家、緊急聯絡人這類標籤（店家、駐外館處的「緊急聯絡電話」是公開的），號碼至少 8 碼
    ("電話號碼", re.compile(r"(?i:(?:住家|家裡|家用|我家|緊急聯絡人)電話|home phone)\s*(?:號碼)?\s*(?:是|為|[:：])?\s*[(（+]?\d[\d\s()（）-]{6,}\d")),
    ("訂位代號／確認碼", re.compile(
        r"(?i:訂位代號|訂位代碼|訂房代號|訂單編號|訂單號碼|確認碼|確認號碼|預約編號|予約番号|確認番号"
        r"|confirmation(?:\s*(?:number|code|no\.?|#))?|booking\s*(?:reference|ref\.?|number|no\.?|id|code)"
        rf"|reservation\s*(?:number|code|no\.?|id)|\bPNR\b){SAY}{CODE}"
    )),
    ("住家地址", re.compile(r"(?i:住家地址|家裡地址|自宅住所|home address"  # 飯店住址、店家的住址是營業場所的
                            r"|(?<![店館宿廳場站點社院寺園司])(?<![店館宿廳場站點社院寺園司]的)(?<!店家|商家)(?<!店家的|商家的)住址)"
                            r"\s*(?:是|為|[:：])")),
]
PUBLIC_PHONE_RE = re.compile(r'"phone"\s*:\s*"[^"\n]*"')  # places/*.json 的 contact.phone 是店家公開的電話，不是個資
PII_REGIONS: dict[str, list[tuple[str, re.Pattern[str]]]] = {  # 各國的證件、手機號碼格式；新的國家加在這裡並補測試
    "TW": [
        ("身分證字號", re.compile(r"(?<![A-Za-z0-9])[A-Z][1289]\d{8}(?!\d)")),  # 第二碼 8、9 是新式居留證
        ("手機號碼", re.compile(r"(?<![\d+])(?:\+?886[ -]?9|09)\d{2}[ -]?\d{3}[ -]?\d{3}(?!\d)")),
    ],
    "JP": [
        ("手機號碼", re.compile(r"(?<![\d+])(?:\+?81[ -]?|0)[789]0[ -]?\d{4}[ -]?\d{4}(?!\d)")),
        ("個人番號", re.compile(r"(?i:マイナンバー|個人番号|my ?number)\s*[:：]?\s*\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)")),
    ],
    "MY": [  # MyKad：YYMMDD-PB-###G；沒有連字號的 12 位數字太常見，只認前面有標籤的
        ("身分證字號", re.compile(r"(?<![\d-])\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-\d{2}-\d{4}(?![\d-])")),
        ("身分證字號", re.compile(r"(?i:MyKad|NRIC|IC ?(?:no\.?|number)|kad pengenalan|身分證|身份證)\s*[:：]?\s*\d{12}(?!\d)")),
        # 手機：有國碼的都算；沒國碼的只認馬來西亞的寫法（012-3456789、012-345 6789、0123456789）和 11 位的號碼。
        # 日本 01 開頭區碼的市話（011、0138 等）寫成 011-222-3333，是 10 位、兩個連字號，不算。
        ("手機號碼", re.compile(
            r"(?<![\d+])(?:\+?60[ -]?1\d[ -]?\d{3,4}[ -]?\d{4}|01\d(?:-\d{7,8}|[ -]?\d{3,4} \d{4}|\d{7,8})"
            r"|01\d[ -]?\d{4}[ -]?\d{4})(?!\d)")),
    ],
}
PII_PATTERNS = PII_COMMON + [p for patterns in PII_REGIONS.values() for p in patterns]


def luhn_ok(digits: str) -> bool:
    checksum = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        checksum += d
    return checksum % 10 == 0


def scan_text(text: str, rel: str = "") -> list[tuple[int, str]]:
    """回傳 [(行號, 疑似個資種類)]。含 pii-ok 的行略過，網址內容不掃；rel 是地點檔時，店家電話（"phone" 的值）不掃。"""
    hits = []
    place = rel.startswith(f"{TRIPS}/") and "/places/" in rel and rel.endswith(".json")
    for n, line in enumerate(text.splitlines(), 1):
        if PII_OK in line:
            continue
        clean = URL_RE.sub(" ", line)
        if any(URL_PII_RE.search(url) for url in URL_RE.findall(line)):
            hits.append((n, "網址參數裡的個資（訂位代號、姓名、聯絡方式）"))
        if place:
            clean = PUBLIC_PHONE_RE.sub('"phone": ""', clean)
        kinds = [kind for kind, pattern in PII_PATTERNS if pattern.search(clean)]
        for m in CARD_RE.finditer(clean):
            digits = re.sub(r"\D", "", m.group())
            if 13 <= len(digits) <= 19 and luhn_ok(digits):
                kinds.append("信用卡號")
        hits += [(n, kind) for kind in dict.fromkeys(kinds)]
    return hits


def check_pii(root: Path) -> list[str]:
    problems = []
    for path in files_to_scan(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(root).as_posix()
        problems += [
            f"{rel}:{n}：疑似{kind}。個資不能存進 repo，請刪除；確定不是個資時，經使用者同意後在該行加上 {PII_OK}"
            for n, kind in scan_text(text, rel)
        ]
    return problems


def added_pii(root: Path, sha: str) -> list[str]:
    """commit 新增的行裡疑似個資的位置，供 git pre-push 轉接使用。"""
    added = added_lines(root, sha)
    return [f"{rel}:{n} {kind}" for rel, lines in added.items()
            for n, text in lines for _, kind in scan_text(text, rel)]
