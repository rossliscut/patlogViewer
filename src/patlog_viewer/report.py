"""Self-contained bilingual HTML for one finished run."""

from __future__ import annotations

import json
from pathlib import Path

from patlog_viewer import __version__

STATIC = Path(__file__).resolve().parent / "static"


def build_report(run: dict, panels: list[dict]) -> str:
    payload = json.dumps({"panels": panels}, ensure_ascii=False).replace("<", "\\u003c")
    chart = (STATIC / "chart.js").read_text(encoding="utf-8")
    summary_zh, summary_en = _summary(panels)
    table = _table(panels)
    clock = (run.get("created_at") or run["id"]).replace("T", " ")
    source = run.get("source") or ("local" if run.get("window_label") == "local" else "online")
    title = f"Patlog Viewer · {clock}"
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)}</title>
<style>
:root{{--bg:#fbfbfd;--fg:#1d1d1f;--mut:#666;--acc:#0b5cad;--bd:#ddd}}
*{{box-sizing:border-box}}
body{{font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",Roboto,sans-serif;margin:0;background:var(--bg);color:var(--fg);line-height:1.55}}
main{{max-width:1180px;margin:0 auto;padding:24px 28px 80px}}
h1{{font-size:26px;margin:8px 0 4px}}
h2{{margin-top:36px;border-bottom:2px solid var(--acc);padding-bottom:4px;font-size:21px}}
.meta{{color:var(--mut);font-size:13px}}
#tools{{position:fixed;top:12px;right:16px;z-index:9;display:flex;gap:8px}}
#tools button{{background:var(--acc);color:#fff;border:0;border-radius:16px;padding:6px 14px;cursor:pointer;font-size:14px}}
html[lang=zh] .en{{display:none}}
html[lang=en] .zh{{display:none}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0}}
th,td{{border:1px solid var(--bd);padding:5px 8px;vertical-align:top;text-align:left}}
th{{background:#eef3f9}}
.box{{background:#f4f7fb;border-left:4px solid var(--acc);padding:10px 14px;margin:12px 0}}
.legend{{font-size:13px;color:#333}}
.swatch{{display:inline-block;width:14px;height:3px;margin:0 4px 2px 10px;vertical-align:middle}}
canvas{{width:100%;display:block}}
</style>
</head>
<body>
<div id="tools">
<button id="downloadbtn" type="button"><span class="zh">下载</span><span class="en">Download</span></button>
<button id="langbtn" type="button">English</button>
</div>
<main>
<h1>{_escape(title)}</h1>
<p class="meta"><span class="zh">Patlog Viewer {__version__} · {_escape(clock)} · {_escape(source)} · {_escape(run["window_label"])}</span><span class="en">Patlog Viewer {__version__} · {_escape(clock)} · {_escape(source)} · {_escape(run["window_label"])}</span></p>
<p class="meta"><span class="zh">时间约定：横轴是日志文件名里的现场时间，不做时区换算。</span><span class="en">Time convention: the axis is the clock written in the log file name, with no timezone conversion.</span></p>
<h2><span class="zh">1. 摘要</span><span class="en">1. Summary</span></h2>
<div class="box"><span class="zh">{summary_zh}</span><span class="en">{summary_en}</span></div>
<h2><span class="zh">2. 数字</span><span class="en">2. Numbers</span></h2>
{table}
<p class="meta"><span class="zh">卡死占比按相邻样本的时间间隔累加。间隔 ≤0.5 s 才算覆盖时间；其中 |az − (−39.127)| &lt; 0.05 m/s² 的间隔算卡死。每个 ImuArray 包取一帧加速度，约 10 Hz。</span><span class="en">Pinned share sums the gaps between successive samples. A gap counts only when it is ≤0.5 s, and it counts as pinned when |az − (−39.127)| &lt; 0.05 m/s². One acceleration sample is taken from each ImuArray packet, about 10 Hz.</span></p>
<h2><span class="zh">3. 三轴加速度</span><span class="en">3. Acceleration</span></h2>
<p class="legend">
<span class="swatch" style="background:#2563eb"></span>ax
<span class="swatch" style="background:#0f766e"></span>ay
<span class="swatch" style="background:#b45309"></span>az
<span class="swatch" style="background:#dc2626"></span><span class="zh">−39.127</span><span class="en">−39.127</span>
<span class="swatch" style="background:#16a34a"></span>+9.8
</p>
<canvas id="chart"></canvas>
<p class="meta"><span class="zh">横轴去掉了超过 1 秒没有样本的间隔，刻度仍是真实钟点。滚轮缩放当前这幅图，拖拽平移，双击全部恢复。悬停显示现场时间和三轴值。</span><span class="en">Gaps longer than 1 s are removed from the time axis. Tick labels stay on the real clock. Scroll to zoom the panel under the pointer, drag to pan, double-click to reset. The readout is the log time and the three axes.</span></p>
<h2><span class="zh">4. 怎么读</span><span class="en">4. How to read it</span></h2>
<ul>
<li><span class="zh">每台机器人一幅图，ax、ay、az 共用同一套时间。贴在红色虚线上的 az 水平段是卡在 −39.127 m/s²。</span><span class="en">Each robot is one panel. ax, ay, and az share one clock. A flat az run on the red dashed line is the −39.127 m/s² pin.</span></li>
<li><span class="zh">健康时 az 靠近绿色虚线（+9.8）。ax、ay 不做这套卡死判定。</span><span class="en">When healthy, az sits near the green dashed line (+9.8). ax and ay are not scored with the pin rule.</span></li>
<li><span class="zh">各幅图的钟点不要当成同一时刻，横轴各自独立。</span><span class="en">Do not read the clocks as the same moment. Each panel has its own time axis.</span></li>
</ul>
</main>
<script>
const DATA = {payload};
{chart}
mountChart(document.getElementById("chart"), DATA.panels, function () {{ return document.documentElement.lang; }});
function setLang(lang) {{
  document.documentElement.lang = lang;
  localStorage.setItem("patlogViewerLang", lang);
  document.getElementById("langbtn").textContent = lang === "zh" ? "English" : "中文";
  if (window.__redraw) window.__redraw();
}}
document.getElementById("langbtn").addEventListener("click", function () {{
  setLang(document.documentElement.lang === "zh" ? "en" : "zh");
}});
document.getElementById("downloadbtn").addEventListener("click", function () {{
  var html = "<!DOCTYPE html>\\n" + document.documentElement.outerHTML;
  var blob = new Blob([html], {{ type: "text/html;charset=utf-8" }});
  var link = document.createElement("a");
  var name = document.title.replace(/[\\\\/:*?"<>|]/g, "-") + ".html";
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}});
(function () {{
  var saved = localStorage.getItem("patlogViewerLang");
  setLang(saved || ((navigator.language || "en").toLowerCase().indexOf("zh") === 0 ? "zh" : "en"));
}})();
</script>
</body>
</html>
"""


def _summary(panels: list[dict]) -> tuple[str, str]:
    failed: list[tuple[str, str]] = []
    idle: list[str] = []
    stuck: list[tuple[str, float]] = []
    clear: list[str] = []
    for panel in panels:
        name = _escape(panel["id"])
        stats = panel.get("stats")
        if not stats:
            if panel.get("idle"):
                idle.append(name)
            else:
                failed.append((name, _escape(panel.get("error") or "")))
            continue
        if stats["pinned"] > 0:
            stuck.append((name, stats["pinned"]))
        else:
            clear.append(name)
    zh: list[str] = []
    en: list[str] = []
    if clear:
        names = "、".join(clear)
        zh.append(f"{names} 正常。")
        verb = "is" if len(clear) == 1 else "are"
        en.append(f"{', '.join(clear)} {verb} normal.")
    if stuck:
        zh.append("，".join(f"{name} 卡死 {pct:.1f}%" for name, pct in stuck) + "。")
        en.append(" ".join(f"{name} is pinned {pct:.1f}%." for name, pct in stuck))
    if idle:
        names = "、".join(idle)
        zh.append(f"{names} 这段时间没有运动，patlog 未记录 IMU。")
        en.append(f"{', '.join(idle)} did not move in this window. The patlog has no IMU record.")
    for name, error in failed:
        if error:
            text = error.rstrip("。")
            zh.append(f"{text}。")
            en.append(f"{name}: {text}.")
        else:
            zh.append(f"{name} 没有加速度样本。")
            en.append(f"{name} has no acceleration samples.")
    if not zh:
        return "没有机器人。", "No robots."
    return "".join(zh), " ".join(en)


def _table(panels: list[dict]) -> str:
    head = (
        "<table><tr>"
        "<th><span class=\"zh\">车</span><span class=\"en\">Robot</span></th>"
        "<th><span class=\"zh\">时间</span><span class=\"en\">Time</span></th>"
        "<th><span class=\"zh\">样本</span><span class=\"en\">Samples</span></th>"
        "<th>ax min</th><th>ax max</th><th>ax median</th>"
        "<th>ay min</th><th>ay max</th><th>ay median</th>"
        "<th>az min</th><th>az max</th><th>az median</th>"
        "<th><span class=\"zh\">az 卡在 −39.127</span><span class=\"en\">az pinned at −39.127</span></th>"
        "</tr>"
    )
    rows = []
    for panel in panels:
        stats = panel.get("stats")
        if not stats:
            if panel.get("idle"):
                note = (
                    "<span class=\"zh\">这段时间没有运动，patlog 未记录 IMU。</span>"
                    "<span class=\"en\">No motion in this window. The patlog has no IMU record.</span>"
                )
            else:
                note = _escape(panel.get("error") or "")
            rows.append(
                "<tr>"
                f"<td>{_escape(panel['id'])}</td>"
                f"<td colspan=\"12\">{note}</td>"
                "</tr>"
            )
            continue
        rows.append(
            "<tr>"
            f"<td>{_escape(panel['id'])}</td>"
            f"<td>{_escape(stats['span'])}</td>"
            f"<td>{stats['n']}</td>"
            f"<td>{stats['ax']['min']:.3f}</td><td>{stats['ax']['max']:.3f}</td><td>{stats['ax']['median']:+.3f}</td>"
            f"<td>{stats['ay']['min']:.3f}</td><td>{stats['ay']['max']:.3f}</td><td>{stats['ay']['median']:+.3f}</td>"
            f"<td>{stats['az']['min']:.3f}</td><td>{stats['az']['max']:.3f}</td><td>{stats['az']['median']:+.3f}</td>"
            f"<td>{stats['pinned']:.1f}%</td>"
            "</tr>"
        )
    return head + "".join(rows) + "</table>"


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
