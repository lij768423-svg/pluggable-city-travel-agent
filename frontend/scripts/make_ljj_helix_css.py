# 用法：python3 scripts/make_ljj_helix_css.py ljj-rules.json src/ljj-helix.css（输入由 extract_ljj_helix_rules.cjs 导出）
import json, re, sys
d = json.load(open(sys.argv[1]))
SCOPE = ".ljj-scope"

def split_top(s, sep=","):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([": depth += 1
        if ch in ")]": depth -= 1
        if ch == sep and depth == 0:
            out.append(cur); cur = ""
        else:
            cur += ch
    out.append(cur)
    return [p.strip() for p in out]

def scope_rule(text):
    # text: "[@media ... { ]selector { body }[ }]"
    m = re.match(r"^(@media[^{]+|@supports[^{]+)\{\s*(.*)\s*\}$", text, re.S)
    if m:
        return f"{m.group(1).strip()} {{ {scope_rule(m.group(2).strip())} }}"
    brace = text.index("{")
    selector, body = text[:brace].strip(), text[brace:]
    scoped = ", ".join(f"{SCOPE} {sel}" for sel in split_top(selector))
    return scoped + " " + body

lines = ["/* ljj.world /projects 双螺旋页样式的逐条副本（2026-10-21 用户：“完全按照原版排版，一个字不改”）。",
         "   由线上页面 CSSOM 中命中该页元素的全部规则导出，只在每个选择器前加了 .ljj-scope 作用域，规则内容未改。 */",
         SCOPE + " { " + " ".join(v for v in d["varVals"] if not v.endswith(": ;")) +
         ' font-family: "Geist Variable", "Noto Sans CJK SC", "Noto Sans SC", sans-serif; color: var(--text); background: var(--bg); }']
lines += [scope_rule(r) for r in d["rules"]]
lines += d["kf"]
open(sys.argv[2], "w").write("\n".join(lines) + "\n")
print(len(lines), "lines")
