"""
loki/dashboard/server.py
========================
Loki 结果可视化 Dashboard。

基于 Python 内置 http.server，无需安装额外依赖。
加载 reports/ 目录下的所有结果 JSON，渲染成可交互的网页。

用法：
  python -m loki.dashboard.server --reports reports/ --port 8080
  # 然后打开浏览器访问 http://localhost:8080

支持的数据文件：
  - reports/loki/loki_iterations.json   迭代历史（来自 LokiOrchestrator）
  - reports/sandbox_results/*/agent_stdout.txt  各 case 的 Agent 日志
  - 任意 EvalResult JSON 文件（通过 API 查询）
"""

import json
import os
import argparse
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger("loki.dashboard")


# ─────────────────────────────────────────
# 数据加载层
# ─────────────────────────────────────────
class DataLoader:
    """从 reports 目录加载所有结果数据。"""

    def __init__(self, reports_dir: str):
        self.reports_dir = Path(reports_dir)

    def load_iterations(self) -> list[dict]:
        """加载 LokiOrchestrator 生成的迭代历史。"""
        f = self.reports_dir / "loki" / "loki_iterations.json"
        if not f.exists():
            return []
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"加载迭代历史失败: {e}")
            return []

    def load_sandbox_results(self) -> list[dict]:
        """从 sandbox_results 目录加载各 case 的执行结果。"""
        results_dir = self.reports_dir / "sandbox_results"
        if not results_dir.exists():
            return []

        results = []
        for sandbox_dir in sorted(results_dir.iterdir()):
            if not sandbox_dir.is_dir():
                continue
            entry = {"sandbox_dir": sandbox_dir.name}

            stdout_file = sandbox_dir / "agent_stdout.txt"
            if stdout_file.exists():
                entry["stdout_preview"] = stdout_file.read_text(
                    encoding="utf-8", errors="replace"
                )[:500]

            # side_effects.json
            sef = sandbox_dir / "side_effects.json"
            if sef.exists():
                try:
                    entry["side_effects"] = json.loads(sef.read_text())
                except Exception:
                    pass

            entry["files"] = [
                f.name for f in sandbox_dir.iterdir()
                if f.is_file() and not f.name.startswith(".")
            ]
            results.append(entry)

        return results

    def load_env_report(self) -> list[dict]:
        """加载环境检查报告（如果存在）。"""
        f = self.reports_dir / "loki" / "env_report.json"
        if not f.exists():
            return []
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []

    def get_summary(self, iterations: list[dict]) -> dict:
        """计算汇总统计。"""
        if not iterations:
            return {
                "total": 0, "success": 0, "failed": 0,
                "success_rate": 0, "avg_iterations": 0,
            }
        total   = len(iterations)
        success = sum(1 for h in iterations if h.get("final_success"))
        avg_iter = sum(h.get("total_iterations", 0) for h in iterations) / total
        return {
            "total":         total,
            "success":       success,
            "failed":        total - success,
            "success_rate":  round(success / total * 100, 1),
            "avg_iterations": round(avg_iter, 1),
        }


# ─────────────────────────────────────────
# HTTP 处理层
# ─────────────────────────────────────────
class DashboardHandler(BaseHTTPRequestHandler):

    # 由 server 注入
    data_loader: DataLoader = None

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._serve_html()
        elif path == "/api/summary":
            self._serve_json_api(self._api_summary())
        elif path == "/api/iterations":
            self._serve_json_api(self._api_iterations())
        elif path == "/api/sandbox":
            self._serve_json_api(self._api_sandbox())
        elif path == "/api/env_report":
            self._serve_json_api(self._api_env_report())
        else:
            self._send_404()

    def log_message(self, format, *args):
        pass  # 静默 access log

    # ── API ────────────────────────────────────────────────────
    def _api_summary(self) -> dict:
        iters = self.data_loader.load_iterations()
        return self.data_loader.get_summary(iters)

    def _api_iterations(self) -> list:
        return self.data_loader.load_iterations()

    def _api_sandbox(self) -> list:
        return self.data_loader.load_sandbox_results()

    def _api_env_report(self) -> list:
        return self.data_loader.load_env_report()

    # ── 响应工具 ───────────────────────────────────────────────
    def _serve_json_api(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",  "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        html_file = Path(__file__).parent / "index.html"
        if html_file.exists():
            body = html_file.read_bytes()
        else:
            body = _INLINE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",  "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_404(self):
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not Found")


# ─────────────────────────────────────────
# 内联 HTML（无需外部文件）
# ─────────────────────────────────────────
_INLINE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Loki Security Bench Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f1117; color: #e2e8f0; min-height: 100vh; }
  header { background: #1a1d2e; border-bottom: 1px solid #2d3748; padding: 16px 24px;
           display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 20px; font-weight: 700; color: #f7fafc; }
  header .badge { background: #e53e3e; color: #fff; font-size: 11px;
                  padding: 2px 8px; border-radius: 12px; font-weight: 600; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
  .card { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 8px; padding: 20px; }
  .card h3 { font-size: 12px; color: #718096; text-transform: uppercase;
              letter-spacing: 0.05em; margin-bottom: 8px; }
  .card .value { font-size: 36px; font-weight: 700; }
  .card .value.success { color: #48bb78; }
  .card .value.danger  { color: #fc8181; }
  .card .value.neutral { color: #63b3ed; }
  .card .value.warn    { color: #f6ad55; }
  .section { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 8px;
             padding: 20px; margin-bottom: 20px; }
  .section h2 { font-size: 16px; font-weight: 600; margin-bottom: 16px;
                display: flex; align-items: center; gap: 8px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; font-size: 12px; color: #718096;
       text-transform: uppercase; letter-spacing: 0.05em;
       border-bottom: 1px solid #2d3748; }
  td { padding: 10px 12px; font-size: 13px; border-bottom: 1px solid #1e2433; }
  tr:hover td { background: #212537; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .tag.success { background: #1c4532; color: #48bb78; }
  .tag.fail    { background: #4a1515; color: #fc8181; }
  .tag.warn    { background: #4a3000; color: #f6ad55; }
  .iter-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
              margin-right: 2px; }
  .iter-dot.s { background: #48bb78; }
  .iter-dot.f { background: #fc8181; }
  .progress-bar { height: 6px; background: #2d3748; border-radius: 3px; overflow: hidden; }
  .progress-fill { height: 100%; background: linear-gradient(90deg, #e53e3e, #f6ad55); border-radius: 3px; }
  .loading { color: #718096; text-align: center; padding: 40px; }
  .empty { color: #718096; text-align: center; padding: 20px; font-style: italic; }
  .refresh-btn { background: #2d3748; border: none; color: #e2e8f0; padding: 6px 16px;
                 border-radius: 6px; cursor: pointer; font-size: 13px; }
  .refresh-btn:hover { background: #3d4a5f; }
</style>
</head>
<body>
<header>
  <div>
    <h1>🔒 Loki Security Bench</h1>
  </div>
  <div class="badge">RESEARCH</div>
  <div style="margin-left:auto">
    <button class="refresh-btn" onclick="loadAll()">↻ 刷新</button>
  </div>
</header>

<div class="container">
  <!-- 汇总卡片 -->
  <div class="grid-4" id="summary-cards">
    <div class="card"><h3>总用例数</h3><div class="value neutral" id="s-total">—</div></div>
    <div class="card"><h3>攻击成功</h3><div class="value success" id="s-success">—</div></div>
    <div class="card"><h3>成功率</h3><div class="value warn" id="s-rate">—</div></div>
    <div class="card"><h3>平均迭代</h3><div class="value neutral" id="s-avg">—</div></div>
  </div>

  <!-- 迭代历史表格 -->
  <div class="section">
    <h2>📋 迭代测试历史</h2>
    <div id="iter-table"><div class="loading">加载中...</div></div>
  </div>

  <!-- 沙箱执行结果 -->
  <div class="section">
    <h2>🐳 沙箱执行记录</h2>
    <div id="sandbox-table"><div class="loading">加载中...</div></div>
  </div>

  <!-- 环境检查报告 -->
  <div class="section">
    <h2>🔧 环境检查报告</h2>
    <div id="env-table"><div class="loading">加载中...</div></div>
  </div>
</div>

<script>
async function fetchJSON(url) {
  const resp = await fetch(url);
  return resp.json();
}

async function loadSummary() {
  const d = await fetchJSON('/api/summary');
  document.getElementById('s-total').textContent   = d.total || 0;
  document.getElementById('s-success').textContent = d.success || 0;
  document.getElementById('s-rate').textContent    = (d.success_rate || 0) + '%';
  document.getElementById('s-avg').textContent     = d.avg_iterations || 0;
}

async function loadIterations() {
  const data = await fetchJSON('/api/iterations');
  const el = document.getElementById('iter-table');
  if (!data || !data.length) {
    el.innerHTML = '<div class="empty">暂无迭代数据（运行 LokiOrchestrator 后生效）</div>';
    return;
  }
  let rows = data.map(h => {
    const dots = (h.iterations || []).map(it =>
      `<span class="iter-dot ${it.result && it.result.attack_success ? 's':'f'}" title="第${it.iteration}轮"></span>`
    ).join('');
    const tag = h.final_success
      ? '<span class="tag success">成功</span>'
      : '<span class="tag fail">失败</span>';
    const lastReason = (h.iterations && h.iterations.length)
      ? h.iterations[h.iterations.length-1].failure_reason || '' : '';
    return `<tr>
      <td style="font-family:monospace">${h.case_id}</td>
      <td>${tag}</td>
      <td>${h.total_iterations || 0}</td>
      <td>${dots}</td>
      <td style="color:#718096;font-size:12px">${lastReason}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `<table>
    <thead><tr>
      <th>用例 ID</th><th>最终结果</th><th>迭代轮数</th><th>轮次轨迹</th><th>最终失败原因</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function loadSandbox() {
  const data = await fetchJSON('/api/sandbox');
  const el = document.getElementById('sandbox-table');
  if (!data || !data.length) {
    el.innerHTML = '<div class="empty">暂无沙箱记录（运行 BenchmarkRunner 后生效）</div>';
    return;
  }
  let rows = data.slice(0,20).map(r => {
    const preview = (r.stdout_preview || '').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const files   = (r.files || []).join(', ');
    return `<tr>
      <td style="font-family:monospace;font-size:12px">${r.sandbox_dir}</td>
      <td style="font-size:11px;color:#718096">${files.substring(0,80)}</td>
      <td style="font-size:11px;max-width:400px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">${preview}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `<table>
    <thead><tr><th>沙箱目录</th><th>文件列表</th><th>日志预览</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function loadEnvReport() {
  const data = await fetchJSON('/api/env_report');
  const el = document.getElementById('env-table');
  if (!data || !data.length) {
    el.innerHTML = '<div class="empty">暂无环境检查报告（运行 EnvRepairAgent 后生效）</div>';
    return;
  }
  let rows = data.map(r => {
    const hasIssues = r.has_issues;
    const tag = hasIssues
      ? '<span class="tag fail">有问题</span>'
      : '<span class="tag success">正常</span>';
    const issues = (r.issues || []).map(i =>
      `<span class="tag ${i.severity==='error'?'fail':'warn'}" style="margin:1px">${i.issue_type}</span>`
    ).join(' ');
    return `<tr>
      <td style="font-family:monospace">${r.case_id}</td>
      <td>${tag}</td>
      <td>${issues || '—'}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `<table>
    <thead><tr><th>用例 ID</th><th>状态</th><th>问题标签</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function loadAll() {
  await Promise.all([loadSummary(), loadIterations(), loadSandbox(), loadEnvReport()]);
}

loadAll();
setInterval(loadAll, 30000); // 每30秒自动刷新
</script>
</body>
</html>"""


# ─────────────────────────────────────────
# 服务器启动
# ─────────────────────────────────────────
def make_handler(data_loader: DataLoader):
    class Handler(DashboardHandler):
        pass
    Handler.data_loader = data_loader
    return Handler


def start_server(reports_dir: str, port: int = 8080):
    loader  = DataLoader(reports_dir)
    handler = make_handler(loader)
    server  = HTTPServer(("0.0.0.0", port), handler)
    print(f"[Loki Dashboard] 启动成功！")
    print(f"  → http://localhost:{port}")
    print(f"  → 数据目录: {reports_dir}")
    print(f"  → 按 Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Loki Dashboard] 已停止")


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Loki Dashboard 服务器")
    parser.add_argument("--reports", default="reports", help="报告目录路径")
    parser.add_argument("--port",    type=int, default=8080, help="HTTP 端口")
    args = parser.parse_args()
    start_server(args.reports, args.port)


if __name__ == "__main__":
    main()
